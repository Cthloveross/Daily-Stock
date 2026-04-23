# -*- coding: utf-8 -*-
"""
News digest service
===================

Responsibilities
1. Aggregate stock news (reuse SearchService output).
2. Call the configured LLM once to obtain:
   - per-item sentiment score in [-2, +2]
   - overall sentiment score + Chinese label
   - a one-sentence Chinese overall tone
   - 3-5 Chinese bullet points of the most important triggers
3. In-memory TTL cache keyed by stock_code (10 min), independent from the
   SearchService news-results cache so we don't re-run the LLM on every tab
   click.

The endpoint layer is responsible for HTTP shaping; this module stays framework
free so it is easy to unit test.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 10 分钟 TTL，和 SearchService 内置 cache 对齐
_CACHE_TTL_SECONDS = 600

# 中文标签映射（score 决定显示）
_OVERALL_LABELS = {
    2: "强烈利好",
    1: "偏利好",
    0: "中性",
    -1: "偏利空",
    -2: "强烈利空",
}


@dataclass
class NewsDigest:
    stock_code: str
    news_count: int
    overall_score: int
    overall_label: str
    summary: str
    bullets: List[str]
    items: List[Dict[str, object]]  # {url, score, reason}
    cached: bool
    generated_at: str


class NewsDigestService:
    def __init__(self) -> None:
        self._cache: Dict[str, tuple[float, NewsDigest]] = {}
        self._lock = threading.RLock()

    # ---- cache ----

    def _get_cached(self, stock_code: str) -> Optional[NewsDigest]:
        with self._lock:
            entry = self._cache.get(stock_code.upper())
            if entry is None:
                return None
            ts, digest = entry
            if time.time() - ts > _CACHE_TTL_SECONDS:
                self._cache.pop(stock_code.upper(), None)
                return None
            # mark cached
            return NewsDigest(
                stock_code=digest.stock_code,
                news_count=digest.news_count,
                overall_score=digest.overall_score,
                overall_label=digest.overall_label,
                summary=digest.summary,
                bullets=list(digest.bullets),
                items=list(digest.items),
                cached=True,
                generated_at=digest.generated_at,
            )

    def _store(self, stock_code: str, digest: NewsDigest) -> None:
        with self._lock:
            self._cache[stock_code.upper()] = (time.time(), digest)

    # ---- public ----

    def build_digest(
        self,
        stock_code: str,
        stock_name: str,
        news_items: List[Dict[str, object]],
        *,
        force_refresh: bool = False,
    ) -> NewsDigest:
        """Build a news digest for `stock_code`.

        Args:
            stock_code: ticker (case insensitive, stored uppercase in cache key).
            stock_name: human readable name (falls back to ticker if empty).
            news_items: list of dicts with keys: title, snippet, url, source, published_at.
            force_refresh: bypass the TTL cache and re-run the LLM.

        Returns a NewsDigest. Never raises on LLM failure — instead returns a
        neutral digest with an explanatory summary so the UI stays usable.
        """
        if not force_refresh:
            cached = self._get_cached(stock_code)
            if cached is not None:
                return cached

        if not news_items:
            empty = NewsDigest(
                stock_code=stock_code,
                news_count=0,
                overall_score=0,
                overall_label=_OVERALL_LABELS[0],
                summary="暂无相关新闻，无法生成总结。",
                bullets=[],
                items=[],
                cached=False,
                generated_at=_now_iso(),
            )
            # 空结果不缓存，下次重试仍可命中新数据
            return empty

        prompt = _build_prompt(stock_code, stock_name, news_items)

        raw = _call_llm(prompt)
        parsed = _parse_llm_json(raw) if raw else None

        if parsed is None:
            # 兜底：不抛异常，回退成中性 + 说明性文案
            logger.warning(
                "news_digest LLM 调用失败或解析失败，返回兜底摘要 (stock=%s)", stock_code
            )
            fallback = NewsDigest(
                stock_code=stock_code,
                news_count=len(news_items),
                overall_score=0,
                overall_label=_OVERALL_LABELS[0],
                summary="LLM 调用失败，暂时无法生成中文总结。请稍后重试。",
                bullets=[],
                items=[
                    {"url": str(it.get("url", "")), "score": 0, "reason": None}
                    for it in news_items
                ],
                cached=False,
                generated_at=_now_iso(),
            )
            # 兜底不缓存，避免短期内都拿到坏结果
            return fallback

        digest = _normalize_parsed(stock_code, news_items, parsed)
        self._store(stock_code, digest)
        return digest


# ---- module level helpers ----

_PROMPT_TEMPLATE = """你是美股新闻情感分析助手。下面是关于股票 {stock_name}({stock_code}) 的最新 {count} 条新闻。

请完成两件事：
1. 为每条新闻打一个 sentiment 分数，区间 [-2, -1, 0, 1, 2]，其中 -2 强烈利空 / -1 利空 / 0 中性 / 1 利好 / 2 强烈利好。判分基于对该股票股价短期（1-5 个交易日）的影响预期。
2. 综合这些新闻，生成一段中文简报：
   - 一个整体打分（同样 -2..2）
   - 一句话总评（不超过 40 个汉字）
   - 3-5 条 bullet，每条 ≤ 30 个汉字，按重要度排序，概括最关键的触发点

只输出 JSON，不要任何额外说明、不要代码块围栏。JSON schema：

{{
  "overall_score": <int in [-2,-1,0,1,2]>,
  "summary": "<string, 一句话总评>",
  "bullets": ["<string>", "<string>", ...],
  "items": [
    {{"url": "<string 原样回传>", "score": <int>, "reason": "<string, 20 字内>"}}
  ]
}}

新闻列表：
{news_block}

再次强调：只输出上面 schema 的 JSON，不要包裹任何文字或代码块。
"""


def _build_prompt(
    stock_code: str,
    stock_name: str,
    news_items: List[Dict[str, object]],
) -> str:
    display_name = stock_name or stock_code
    lines: List[str] = []
    for i, it in enumerate(news_items, start=1):
        title = str(it.get("title") or "").strip()
        snippet = str(it.get("snippet") or "").strip()
        source = str(it.get("source") or "").strip()
        published = str(it.get("published_at") or "").strip()
        url = str(it.get("url") or "").strip()
        lines.append(
            f"[{i}] url={url}\n    source={source} time={published}\n    title={title}\n    snippet={snippet}"
        )
    return _PROMPT_TEMPLATE.format(
        stock_code=stock_code,
        stock_name=display_name,
        count=len(news_items),
        news_block="\n".join(lines),
    )


def _call_llm(prompt: str) -> Optional[str]:
    try:
        from src.analyzer import get_analyzer
    except Exception as e:
        logger.error("news_digest: 无法导入 analyzer: %s", e)
        return None

    try:
        analyzer = get_analyzer()
    except Exception as e:
        logger.error("news_digest: analyzer 初始化失败: %s", e)
        return None

    try:
        # Gemini 3 models 警告 temperature<1.0 会退化；1.0 对 2.5 也安全
        return analyzer.generate_text(prompt, max_tokens=1200, temperature=1.0)
    except Exception as e:
        logger.error("news_digest: generate_text 失败: %s", e)
        return None


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _parse_llm_json(raw: str) -> Optional[Dict[str, object]]:
    if not raw:
        return None
    text = raw.strip()
    # 有时 LLM 会加围栏
    m = _JSON_FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    # 兜底：截到第一对大括号
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("news_digest JSON 解析失败: %s; 原文首 200 字符: %r", e, text[:200])
        return None
    if not isinstance(data, dict):
        return None
    return data


def _clip_int(value: object, lo: int, hi: int, default: int = 0) -> int:
    try:
        v = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _normalize_parsed(
    stock_code: str,
    news_items: List[Dict[str, object]],
    parsed: Dict[str, object],
) -> NewsDigest:
    overall = _clip_int(parsed.get("overall_score"), -2, 2, default=0)
    summary = str(parsed.get("summary") or "").strip() or "无明显倾向。"
    bullets_raw = parsed.get("bullets") or []
    bullets: List[str] = []
    if isinstance(bullets_raw, list):
        for b in bullets_raw:
            s = str(b).strip()
            if s:
                bullets.append(s)
            if len(bullets) >= 5:
                break

    items_raw = parsed.get("items") or []
    # 用 url 作为匹配键；LLM 没返回/匹配不上的条目用 0 兜底
    score_by_url: Dict[str, Dict[str, object]] = {}
    if isinstance(items_raw, list):
        for entry in items_raw:
            if not isinstance(entry, dict):
                continue
            url = str(entry.get("url") or "").strip()
            if not url:
                continue
            score_by_url[url] = {
                "score": _clip_int(entry.get("score"), -2, 2, default=0),
                "reason": str(entry.get("reason") or "").strip() or None,
            }

    items_out: List[Dict[str, object]] = []
    for it in news_items:
        url = str(it.get("url") or "").strip()
        match = score_by_url.get(url, {"score": 0, "reason": None})
        items_out.append({"url": url, "score": int(match["score"]), "reason": match["reason"]})

    return NewsDigest(
        stock_code=stock_code,
        news_count=len(news_items),
        overall_score=overall,
        overall_label=_OVERALL_LABELS.get(overall, _OVERALL_LABELS[0]),
        summary=summary,
        bullets=bullets,
        items=items_out,
        cached=False,
        generated_at=_now_iso(),
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# singleton
_service: Optional[NewsDigestService] = None
_service_lock = threading.Lock()


def get_news_digest_service() -> NewsDigestService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = NewsDigestService()
    return _service
