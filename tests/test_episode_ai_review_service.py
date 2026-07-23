from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import Barrier, BoundedSemaphore, Event, Lock
from time import monotonic, sleep
from types import SimpleNamespace

import pytest

import src.agent.llm_adapter as llm_adapter_module
import src.services.episode_ai_review_service as review_service
from src.agent.llm_adapter import LLMResponse, LLMToolAdapter
from src.services.episode_ai_review_service import (
    _clear_history_cache_for_tests,
    _compact_market_slice,
    _default_history_loader,
    _default_llm_caller,
    _load_market_histories,
    _market_return,
    generate_episode_ai_review,
)


def _complete_review() -> str:
    return "\n".join(
        (
            "## 一句话结论",
            "## 已知事实",
            "## 进场与交易逻辑",
            "## 出场与风险",
            "## 可以保留 / 应该修正",
            "## 下次复盘要回答的问题",
            "单笔案例不能证明策略 edge",
        )
    )


def _detail(*, aggregate_only: bool = False):
    episode = SimpleNamespace(
        episode_id=1441,
        build_id=1,
        raw_symbol="MSFT260722C405000",
        underlying="MSFT",
        asset_type="option",
        direction="long",
        lifecycle_status="closed",
        opened_at=datetime(2026, 7, 20, 18, 38, 21, tzinfo=timezone.utc),
        closed_at=datetime(2026, 7, 20, 18, 43, 25, tzinfo=timezone.utc),
        hold_seconds=304,
        opened_quantity=Decimal("12"),
        closed_quantity=Decimal("12"),
        average_entry_price=Decimal("3.55"),
        average_exit_price=Decimal("3.00"),
        realized_pnl_net=Decimal("-699.43"),
        total_fee=Decimal("39.43"),
        opening_boundary_policy="assumed_flat_unverified",
        left_boundary_verified=False,
        completeness_status="complete",
    )
    evidence = (
        SimpleNamespace(
            evidence_kind="order" if aggregate_only else "fill",
            event_role="open",
            evidence_time=episode.opened_at,
            allocated_quantity=Decimal("12"),
            allocated_cash_flow=Decimal("-4260"),
            allocated_fee=Decimal("19.66"),
            allocation_evidence={
                "timing_precision": "order_time_proxy" if aggregate_only else "fill_time"
            },
        ),
        SimpleNamespace(
            evidence_kind="order" if aggregate_only else "fill",
            event_role="close",
            evidence_time=episode.closed_at,
            allocated_quantity=Decimal("12"),
            allocated_cash_flow=Decimal("3600"),
            allocated_fee=Decimal("19.77"),
            allocation_evidence={
                "timing_precision": "order_time_proxy" if aggregate_only else "fill_time"
            },
        ),
    )
    return SimpleNamespace(episode=episode, evidence=evidence)


def _history(symbol: str, period: str, _days: int):
    prices = (100.0, 102.0, 999.0) if symbol == "MSFT" else (500.0, 501.0, 999.0)
    return {
        "stock_code": symbol,
        "period": period,
        "source": "MoomooFetcher",
        "coverage_start": "2026-07-20T14:30:00-04:00",
        "coverage_end": "2026-07-20T14:40:00-04:00",
        "data": [
            {
                "date": "2026-07-20T14:30:00-04:00",
                "open": prices[0],
                "high": prices[0],
                "low": prices[0],
                "close": prices[0],
            },
            {
                "date": "2026-07-20T14:35:00-04:00",
                "open": prices[1],
                "high": prices[1],
                "low": prices[1],
                "close": prices[1],
            },
            {
                "date": "2026-07-20T14:40:00-04:00",
                "open": prices[2],
                "high": prices[2],
                "low": prices[2],
                "close": prices[2],
            },
        ],
    }


def test_market_return_uses_only_recently_completed_intraday_bars() -> None:
    history = {
        "period": "5m",
        "data": [
            {"date": "2026-07-20T09:45:00-04:00", "close": 100},
            # At 09:54 this bar has not finalized; its close must not leak.
            {"date": "2026-07-20T09:50:00-04:00", "close": 999},
            # At 11:15:14 this bar has been final for 14 seconds and is fresh.
            {"date": "2026-07-20T11:10:00-04:00", "close": 110},
        ],
    }

    value, metric = _market_return(
        history,
        datetime(2026, 7, 20, 13, 54, tzinfo=timezone.utc),
        datetime(2026, 7, 20, 15, 15, 14, tzinfo=timezone.utc),
    )

    assert value == 10.0
    assert "成交前最近已完成 5m bar" in metric
    assert "不是期权成交价" in metric


def test_market_return_is_unknown_without_a_fresh_completed_bar() -> None:
    history = {
        "period": "5m",
        "data": [{"date": "2026-07-20T09:50:00-04:00", "close": 100}],
    }

    no_completed, _metric = _market_return(
        history,
        datetime(2026, 7, 20, 13, 54, tzinfo=timezone.utc),
        datetime(2026, 7, 20, 13, 54, 30, tzinfo=timezone.utc),
    )
    stale, _metric = _market_return(
        history,
        datetime(2026, 7, 20, 14, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 20, 14, 1, 30, tzinfo=timezone.utc),
    )

    assert no_completed is None
    assert stale is None


def test_review_uses_deidentified_execution_and_market_facts() -> None:
    captured = {}

    def llm(system: str, prompt: str) -> str:
        captured["system"] = system
        captured["prompt"] = prompt
        return "## 一句话结论\n\n这是一条有证据边界的复盘。"

    result = generate_episode_ai_review(
        _detail(),
        history_loader=_history,
        regime_loader=lambda _date: {
            "date": "2026-07-20",
            "label": "standard",
            "score": 42,
        },
        llm_caller=llm,
        now=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )

    assert result["data_state"] == "ready"
    assert result["analysis_mode"] == "model_enhanced"
    assert result["analysis_source"] == "configured_llm"
    assert result["analysis_markdown"] == result["model_analysis_markdown"]
    assert result["model_analysis_markdown"].startswith("## 一句话结论")
    assert "## 一句话事实结论" in result["evidence_markdown"]
    assert result["market_context"]["timeframe"] == "5m"
    assert result["market_context"]["underlying_return_pct"] == 2.0
    assert result["market_context"]["benchmark_return_pct"] == 0.2
    assert result["market_context"]["relative_return_pct"] == 1.8
    assert result["market_context"]["regime_label"] == "standard"
    warnings = " ".join(result["warnings"])
    assert "期初持仓边界未验证" in warnings
    assert "未保存生成 as-of 时间" in warnings
    assert "无生成 as-of；开仓时可见性未知" in " ".join(
        result["market_context"]["provenance"]
    )
    assert "user_original_intent_known" in captured["prompt"]
    assert '"user_original_intent_known": false' in captured["prompt"]
    assert '"generated_as_of": null' in captured["prompt"]
    assert '"known_at_entry": "unknown"' in captured["prompt"]
    assert '"regime_known_at_entry": null' in captured["prompt"]
    assert '"underlying_price_bars"' in captured["prompt"]
    assert '"finalized_at": "2026-07-20T18:35:00Z"' in captured["prompt"]
    assert '"2026-07-20T14:35:00-04:00"' in captured["prompt"]
    assert "account_key" not in captured["prompt"]
    assert "raw broker" not in captured["prompt"]


def test_review_returns_market_context_when_llm_is_unavailable() -> None:
    def unavailable(_system: str, _prompt: str) -> str:
        raise RuntimeError("provider offline")

    result = generate_episode_ai_review(
        _detail(aggregate_only=True),
        history_loader=_history,
        regime_loader=lambda _date: None,
        llm_caller=unavailable,
        now=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )

    assert result["data_state"] == "llm_unavailable"
    assert result["analysis_mode"] == "deterministic"
    assert result["analysis_source"] == "local_evidence_engine"
    assert result["model_analysis_markdown"] is None
    assert result["analysis_markdown"] == result["evidence_markdown"]
    assert result["market_context"]["underlying_return_pct"] == 2.0
    warnings = " ".join(result["warnings"])
    assert "订单时间代理" in warnings
    assert "Regime" in warnings
    assert "模型增强暂不可用" in warnings
    assert "provider offline" not in warnings
    markdown = result["analysis_markdown"]
    assert "## 一句话事实结论" in markdown
    assert "条件口径净 P&L -$699.43" in markdown
    assert "期权成交均价从 $3.55 变为 $3.00（-15.49%）" in markdown
    assert "执行角色：开仓 1 条、平仓 1 条" in markdown
    assert "MSFT +2.00%" in markdown
    assert "SPY +0.20%" in markdown
    assert "相差 +1.80 个百分点" in markdown
    assert "订单时间代理" in markdown
    assert "不能单独还原进场动机" in markdown
    assert "## 证据缺口" in markdown
    assert "## 下次复盘要回答的问题" in markdown


def test_review_can_skip_external_model_and_return_ready_evidence_review() -> None:
    def model_must_not_run(_system: str, _prompt: str) -> str:
        raise AssertionError("evidence-only review must not call a model")

    result = generate_episode_ai_review(
        _detail(),
        history_loader=_history,
        regime_loader=lambda _date: None,
        llm_caller=model_must_not_run,
        enhance_with_model=False,
        now=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )

    assert result["data_state"] == "ready"
    assert result["analysis_mode"] == "deterministic"
    assert result["analysis_source"] == "local_evidence_engine"
    assert result["model_analysis_markdown"] is None
    assert result["analysis_markdown"] == result["evidence_markdown"]
    assert "## 一句话事实结论" in result["analysis_markdown"]
    assert "## 用户记录的交易逻辑" not in result["analysis_markdown"]
    assert "模型增强暂不可用" not in " ".join(result["warnings"])


def test_deterministic_review_labels_user_plan_and_post_trade_notes() -> None:
    result = generate_episode_ai_review(
        _detail(),
        history_loader=_history,
        regime_loader=lambda _date: None,
        enhance_with_model=False,
        user_context={
            "setup_thesis": "  MSFT 突破前高后延续  ",
            "entry_trigger": "5 分钟收盘站上前高",
            "exit_reason": "看到冲高回落后主动退出",
            "post_trade_reflection": "  仓位过大，影响执行。\n下次先写风险预算。  ",
        },
        now=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )

    markdown = result["analysis_markdown"]
    assert "## 用户记录的交易逻辑" in markdown
    assert "### 事后回忆的进场前计划（用户自述）" in markdown
    assert "交易设定 / 核心假设：MSFT 突破前高后延续" in markdown
    assert "### 事后记录（用户自述）" in markdown
    assert "实际出场原因：看到冲高回落后主动退出" in markdown
    assert "仓位过大，影响执行。 下次先写风险预算。" in markdown
    assert "未经成交或行情独立验证" in markdown
    assert "系统没有这些内容在进场前已被记录的证据" in markdown
    assert "不能证明原计划在进场前存在" in markdown
    assert "用户上下文仍未填写：计划失效条件、仓位与合约选择理由" in markdown
    assert "未记录原始交易计划与盘中决策" not in markdown


def test_model_prompt_treats_user_context_as_unverified_and_missing_as_unknown() -> None:
    captured = {}

    def llm(system: str, prompt: str) -> str:
        captured.update(system=system, prompt=prompt)
        return _complete_review()

    result = generate_episode_ai_review(
        _detail(),
        history_loader=_history,
        regime_loader=lambda _date: None,
        llm_caller=llm,
        user_context={
            "setup_thesis": "趋势延续",
            "post_trade_reflection": "复盘后认为入场偏晚",
        },
        now=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )

    assert result["analysis_mode"] == "model_enhanced"
    assert "事后复盘请求中补录、未经独立验证的自述" in captured["system"]
    assert "recorded_in_system_before_entry=false" in captured["system"]
    assert "事后反思不能倒推" in captured["system"]
    assert "未填写的 user_context 字段一律保持未知" in captured["system"]
    assert '"source": "user_supplied_during_post_trade_review_request"' in captured["prompt"]
    assert '"verification_status": "self_report_not_independently_verified"' in captured["prompt"]
    assert '"recorded_in_system_before_entry": false' in captured["prompt"]
    assert '"post_trade_recall_of_pre_trade_plan": {"setup_thesis": "趋势延续"}' in captured["prompt"]
    assert '"post_trade_notes": {"post_trade_reflection": "复盘后认为入场偏晚"}' in captured["prompt"]
    assert '"entry_trigger"' in captured["prompt"]
    assert '"exit_reason"' in captured["prompt"]


def test_user_context_service_validation_runs_before_market_io() -> None:
    history_calls = []

    def history(*args):
        history_calls.append(args)
        return {}

    with pytest.raises(ValueError, match="at most 2000 characters"):
        generate_episode_ai_review(
            _detail(),
            history_loader=history,
            user_context={"setup_thesis": "x" * 2001},
            enhance_with_model=False,
        )

    with pytest.raises(ValueError, match="at most 6000 characters in total"):
        generate_episode_ai_review(
            _detail(),
            history_loader=history,
            user_context={
                "setup_thesis": "a" * 2000,
                "entry_trigger": "b" * 2000,
                "invalidation_plan": "c" * 2000,
                "position_rationale": "d",
            },
            enhance_with_model=False,
        )

    assert history_calls == []


def test_user_context_service_accepts_exact_total_limit_after_trimming() -> None:
    result = generate_episode_ai_review(
        _detail(),
        history_loader=_history,
        regime_loader=lambda _date: None,
        user_context={
            "setup_thesis": f"  {'a' * 2000}  ",
            "entry_trigger": "b" * 2000,
            "invalidation_plan": "c" * 2000,
        },
        enhance_with_model=False,
        now=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )

    assert result["data_state"] == "ready"
    assert result["model_analysis_markdown"] is None
    assert "## 用户记录的交易逻辑" in result["evidence_markdown"]


def test_deterministic_review_keeps_missing_results_and_market_data_explicit() -> None:
    detail = _detail()
    detail.episode.average_exit_price = None
    detail.episode.realized_pnl_net = None
    detail.episode.total_fee = None
    detail.episode.hold_seconds = None
    detail.episode.opening_boundary_policy = "observed_flat"
    detail.episode.left_boundary_verified = True

    def empty_history(symbol: str, period: str, _days: int):
        return {"stock_code": symbol, "period": period, "data": []}

    result = generate_episode_ai_review(
        detail,
        history_loader=empty_history,
        regime_loader=lambda _date: None,
        llm_caller=lambda _system, _prompt: (_ for _ in ()).throw(
            RuntimeError("offline")
        ),
        now=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )

    markdown = result["analysis_markdown"]
    assert result["analysis_mode"] == "deterministic"
    assert "记录净 P&L 未提供" in markdown
    assert "期权进出场均价不完整" in markdown
    assert "持有 未知" in markdown
    assert "MSFT、SPY 行情未完整覆盖持仓窗口" in markdown
    assert "导致了期权盈亏" not in markdown


def test_default_llm_caller_rejects_adapter_error_text(monkeypatch) -> None:
    class FailedAdapter:
        is_available = True

        def __init__(self, _config, *, models_to_try=None) -> None:
            pass

        def call_text(self, *_args, **_kwargs):
            return SimpleNamespace(
                content="All LLM models failed. Last error: provider secret",
                provider="error",
            )

    monkeypatch.setattr("src.agent.llm_adapter.LLMToolAdapter", FailedAdapter)
    monkeypatch.setattr("src.config.get_config", lambda: object())

    with pytest.raises(RuntimeError, match="所有已配置的 AI 模型调用失败"):
        _default_llm_caller("system", "prompt")


def test_default_llm_caller_rejects_truncated_review(monkeypatch) -> None:
    class TruncatedAdapter:
        is_available = True

        def __init__(self, _config, *, models_to_try=None) -> None:
            pass

        def call_text(self, *_args, **_kwargs):
            return SimpleNamespace(
                content="## 一句话结论\n\n内容在中途被截断。",
                provider="gemini",
            )

    monkeypatch.setattr("src.agent.llm_adapter.LLMToolAdapter", TruncatedAdapter)
    monkeypatch.setattr("src.config.get_config", lambda: object())

    with pytest.raises(RuntimeError, match="复盘结构不完整"):
        _default_llm_caller("system", "prompt")


def test_default_llm_caller_uses_interactive_budget_and_structure_gate(
    monkeypatch,
) -> None:
    captured = {}

    class CapturingAdapter:
        is_available = True

        def __init__(self, _config, *, models_to_try=None) -> None:
            captured["models_to_try"] = models_to_try

        def call_text(self, *_args, **kwargs):
            captured.update(kwargs)
            response = SimpleNamespace(
                content=_complete_review(),
                provider="configured-provider",
            )
            assert kwargs["response_validator"](response) is True
            return response

    monkeypatch.setattr("src.agent.llm_adapter.LLMToolAdapter", CapturingAdapter)
    monkeypatch.setattr("src.config.get_config", lambda: object())

    assert _default_llm_caller("system", "prompt") == _complete_review()
    assert captured["timeout"] == 16.0
    assert captured["per_model_timeout"] == 10.0
    assert captured["max_tokens"] == 2400
    assert captured["models_to_try"] == []


def test_default_llm_caller_passes_dedicated_journal_chain_to_adapter(
    monkeypatch,
) -> None:
    config = SimpleNamespace(agent_litellm_model="agent/unchanged")
    expected_chain = ["provider/journal", "provider/fallback"]
    captured = {}

    class CapturingAdapter:
        is_available = True

        def __init__(self, received_config, *, models_to_try=None) -> None:
            captured["config"] = received_config
            captured["models_to_try"] = models_to_try

        def call_text(self, *_args, **_kwargs):
            return SimpleNamespace(
                content=_complete_review(),
                provider="configured-provider",
            )

    monkeypatch.setattr("src.agent.llm_adapter.LLMToolAdapter", CapturingAdapter)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    monkeypatch.setattr(
        "src.config.get_effective_journal_ai_models_to_try",
        lambda received_config: (
            expected_chain
            if received_config is config
            else pytest.fail("unexpected config instance")
        ),
    )

    assert _default_llm_caller("system", "prompt") == _complete_review()
    assert captured == {
        "config": config,
        "models_to_try": expected_chain,
    }
    assert config.agent_litellm_model == "agent/unchanged"


def test_default_llm_caller_enforces_bounded_wall_clock(monkeypatch) -> None:
    release_worker = Event()

    class BlockingAdapter:
        is_available = True

        def __init__(self, _config, *, models_to_try=None) -> None:
            release_worker.wait(timeout=1)

        def call_text(self, *_args, **_kwargs):
            return SimpleNamespace(
                content=_complete_review(),
                provider="configured-provider",
            )

    slots = BoundedSemaphore(1)
    monkeypatch.setattr("src.agent.llm_adapter.LLMToolAdapter", BlockingAdapter)
    monkeypatch.setattr("src.config.get_config", lambda: object())
    monkeypatch.setattr(review_service, "_LLM_HARD_DEADLINE_SECONDS", 0.03)
    monkeypatch.setattr(review_service, "_llm_hard_deadline_slots", slots)

    started_at = monotonic()
    with pytest.raises(RuntimeError, match="服务端墙钟截止"):
        _default_llm_caller("system", "prompt")
    assert monotonic() - started_at < 0.2

    # The timed-out worker retains the only slot, so repeated requests fail
    # fast instead of leaking another daemon on every retry.
    retry_started_at = monotonic()
    with pytest.raises(RuntimeError, match="繁忙"):
        _default_llm_caller("system", "prompt")
    assert monotonic() - retry_started_at < 0.1

    release_worker.set()
    deadline = monotonic() + 0.5
    while monotonic() < deadline:
        if slots.acquire(blocking=False):
            slots.release()
            break
        sleep(0.005)
    else:  # pragma: no cover - explicit cleanup assertion
        pytest.fail("hard-deadline worker did not release its slot")


def test_llm_adapter_hard_timeout_continues_to_fallback(monkeypatch) -> None:
    adapter = object.__new__(LLMToolAdapter)
    adapter._config = SimpleNamespace()
    release_primary = Event()
    attempts = []

    monkeypatch.setattr(
        "src.agent.llm_adapter.get_effective_agent_models_to_try",
        lambda _config: ["provider/primary", "provider/fallback"],
    )

    def call_model(_messages, _tools, model, **_kwargs):
        attempts.append(model)
        if model.endswith("primary"):
            release_primary.wait(timeout=1)
            return LLMResponse(content="late", provider="provider")
        return LLMResponse(content=_complete_review(), provider="provider")

    monkeypatch.setattr(adapter, "_call_litellm_model", call_model)

    started_at = monotonic()
    response = adapter.call_text(
        [{"role": "user", "content": "review"}],
        timeout=0.25,
        per_model_timeout=0.03,
    )
    elapsed = monotonic() - started_at
    release_primary.set()

    assert response.content == _complete_review()
    assert attempts == ["provider/primary", "provider/fallback"]
    assert elapsed < 0.2


def test_llm_adapter_instance_model_chain_override_is_isolated(monkeypatch) -> None:
    config = SimpleNamespace(agent_litellm_model="agent/original")
    monkeypatch.setattr(
        LLMToolAdapter,
        "_register_custom_model_pricing",
        lambda _self: None,
    )
    monkeypatch.setattr(LLMToolAdapter, "_init_litellm", lambda _self: None)
    adapter = LLMToolAdapter(
        config,
        models_to_try=(
            "provider/journal-primary",
            "provider/journal-fallback",
        ),
    )
    attempts = []

    def inherited_chain_must_not_be_read(_config):
        raise AssertionError("instance override must bypass the global Agent chain")

    monkeypatch.setattr(
        "src.agent.llm_adapter.get_effective_agent_models_to_try",
        inherited_chain_must_not_be_read,
    )

    def call_model(_messages, _tools, model, **_kwargs):
        attempts.append(model)
        if model.endswith("primary"):
            raise RuntimeError("try Journal fallback")
        return LLMResponse(content=_complete_review(), provider="provider")

    monkeypatch.setattr(adapter, "_call_litellm_model", call_model)

    response = adapter.call_text([{"role": "user", "content": "review"}])

    assert response.content == _complete_review()
    assert attempts == ["provider/journal-primary", "provider/journal-fallback"]
    assert adapter._config.agent_litellm_model == "agent/original"


def test_llm_adapter_hard_timeout_worker_slots_are_bounded(monkeypatch) -> None:
    adapter = object.__new__(LLMToolAdapter)
    adapter._config = SimpleNamespace()
    calls = []
    slots = BoundedSemaphore(1)
    assert slots.acquire(blocking=False)

    monkeypatch.setattr(llm_adapter_module, "_hard_timeout_slots", slots)
    monkeypatch.setattr(
        "src.agent.llm_adapter.get_effective_agent_models_to_try",
        lambda _config: ["provider/primary"],
    )
    monkeypatch.setattr(
        adapter,
        "_call_litellm_model",
        lambda *_args, **_kwargs: calls.append("called"),
    )

    response = adapter.call_text(
        [{"role": "user", "content": "review"}],
        timeout=0.25,
        per_model_timeout=0.03,
    )
    slots.release()

    assert response.provider == "error"
    assert "capacity is exhausted" in (response.content or "")
    assert calls == []


def test_litellm_local_cost_map_default_is_set_before_import() -> None:
    source = Path(llm_adapter_module.__file__).read_text(encoding="utf-8")
    guard = 'os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")'
    assert guard in source
    assert source.index(guard) < source.index("\nimport litellm")


def test_llm_adapter_falls_back_when_validator_rejects_response(monkeypatch) -> None:
    adapter = object.__new__(LLMToolAdapter)
    adapter._config = SimpleNamespace()
    attempts = []
    clock = iter((0.0, 0.0, 20.0))

    monkeypatch.setattr(
        "src.agent.llm_adapter.get_effective_agent_models_to_try",
        lambda _config: ["provider/primary", "provider/fallback"],
    )
    monkeypatch.setattr(
        "src.agent.llm_adapter.time",
        SimpleNamespace(time=lambda: next(clock), sleep=lambda _seconds: None),
    )

    def call_model(_messages, _tools, model, **kwargs):
        attempts.append((model, kwargs["timeout"]))
        if model.endswith("primary"):
            return LLMResponse(
                content="## 一句话结论\n结构不完整",
                provider="provider",
            )
        return LLMResponse(content=_complete_review(), provider="provider")

    monkeypatch.setattr(adapter, "_call_litellm_model", call_model)

    response = adapter.call_text(
        [{"role": "user", "content": "review"}],
        timeout=30,
        per_model_timeout=15,
        response_validator=lambda value: value.content == _complete_review(),
    )

    assert response.content == _complete_review()
    assert attempts == [
        ("provider/primary", 15.0),
        ("provider/fallback", 10.0),
    ]


def test_llm_adapter_tool_path_remains_backward_compatible(monkeypatch) -> None:
    adapter = object.__new__(LLMToolAdapter)
    adapter._config = SimpleNamespace()
    captured = {}
    expected = LLMResponse(provider="provider", tool_calls=[])

    monkeypatch.setattr(
        "src.agent.llm_adapter.get_effective_agent_models_to_try",
        lambda _config: ["provider/primary"],
    )

    def call_model(messages, tools, model, **kwargs):
        captured.update(
            messages=messages,
            tools=tools,
            model=model,
            timeout=kwargs["timeout"],
        )
        return expected

    monkeypatch.setattr(adapter, "_call_litellm_model", call_model)

    response = adapter.call_with_tools(
        [{"role": "user", "content": "use tool"}],
        [{"type": "function", "function": {"name": "lookup"}}],
        timeout=7,
    )

    assert response is expected
    assert captured["model"] == "provider/primary"
    assert captured["timeout"] <= 7
    assert captured["tools"][0]["function"]["name"] == "lookup"


def test_ai_history_loader_skips_unneeded_stock_name_lookup(monkeypatch) -> None:
    captured = {}

    class FakeStockService:
        def get_history_data(self, symbol, **kwargs):
            captured.update(symbol=symbol, **kwargs)
            return {"stock_code": symbol, "period": kwargs["period"], "data": []}

    monkeypatch.setattr("src.services.stock_service.StockService", FakeStockService)

    result = _default_history_loader("MSFT", "5m", 24)

    assert result["stock_code"] == "MSFT"
    assert captured == {
        "symbol": "MSFT",
        "period": "5m",
        "days": 24,
        "include_stock_name": False,
    }


def test_market_histories_are_loaded_concurrently() -> None:
    both_started = Barrier(2, timeout=1)

    def blocking_history(symbol: str, period: str, _days: int):
        both_started.wait()
        return {
            "stock_code": symbol,
            "period": period,
            "data": [{"date": "2026-07-20T14:35:00-04:00", "close": 1}],
        }

    histories, warnings = _load_market_histories(
        ("MSFT", "SPY"),
        "5m",
        7,
        blocking_history,
        cache_enabled=False,
    )

    assert warnings == []
    assert set(histories) == {"MSFT", "SPY"}
    assert all(history["data"] for history in histories.values())


def test_default_market_history_deadline_is_bounded_without_worker_leak(
    monkeypatch,
) -> None:
    _clear_history_cache_for_tests()
    both_started = Barrier(2, timeout=1)
    release_workers = Event()
    slots = BoundedSemaphore(2)

    def blocking_history(symbol: str, period: str, _days: int):
        both_started.wait()
        release_workers.wait(timeout=1)
        return {
            "stock_code": symbol,
            "period": period,
            "data": [{"date": "2026-07-20T14:30:00-04:00", "close": 1}],
        }

    monkeypatch.setattr(review_service, "_HISTORY_HARD_DEADLINE_SECONDS", 0.03)
    monkeypatch.setattr(review_service, "_history_hard_deadline_slots", slots)

    try:
        started_at = monotonic()
        histories, warnings = _load_market_histories(
            ("MSFT", "SPY"),
            "5m",
            7,
            blocking_history,
            cache_enabled=True,
        )
        assert monotonic() - started_at < 0.2
        assert all(not history["data"] for history in histories.values())
        assert len(warnings) == 2
        assert all("超时或繁忙" in warning for warning in warnings)

        retry_started_at = monotonic()
        _histories, retry_warnings = _load_market_histories(
            ("QQQ",),
            "5m",
            7,
            blocking_history,
            cache_enabled=True,
        )
        assert monotonic() - retry_started_at < 0.1
        assert retry_warnings == [
            "QQQ 行情读取超时或繁忙，AI 不得补写该部分市场结论。"
        ]
    finally:
        release_workers.set()

    deadline = monotonic() + 0.5
    while monotonic() < deadline:
        if slots.acquire(blocking=False):
            if slots.acquire(blocking=False):
                slots.release()
                slots.release()
                break
            slots.release()
        sleep(0.005)
    else:  # pragma: no cover - explicit cleanup assertion
        pytest.fail("market-history workers did not release bounded slots")
    _clear_history_cache_for_tests()


def test_compact_market_slice_caps_prompt_without_dropping_exit_context() -> None:
    first_bar = datetime(2026, 7, 20, 13, 0, tzinfo=timezone.utc)
    rows = [
        {
            "date": (first_bar + timedelta(minutes=5 * index)).isoformat(),
            "open": index,
            "high": index,
            "low": index,
            "close": index,
        }
        for index in range(60)
    ]
    opened_at = first_bar + timedelta(minutes=60)
    closed_at = first_bar + timedelta(minutes=250)

    selected = _compact_market_slice(
        {"period": "5m", "data": rows},
        opened_at,
        closed_at,
    )

    assert len(selected) == 24
    assert selected[0]["time"] == rows[6]["date"]
    assert selected[0]["finalized_at"] == (
        first_bar + timedelta(minutes=35)
    ).isoformat().replace("+00:00", "Z")
    assert selected[0]["availability"].endswith("finalized_at")
    assert selected[-1]["time"] == rows[56]["date"]
    assert any(item["time"] == rows[50]["date"] for item in selected)


def test_default_market_history_uses_short_process_cache(monkeypatch) -> None:
    _clear_history_cache_for_tests()
    calls = []
    calls_lock = Lock()

    def counted_history(symbol: str, period: str, _days: int):
        with calls_lock:
            calls.append(symbol)
        return {
            "stock_code": symbol,
            "period": period,
            "source": "test",
            "data": [{"date": "2026-07-20T14:35:00-04:00", "close": 1}],
        }

    monkeypatch.setattr(review_service, "_default_history_loader", counted_history)
    kwargs = {
        "regime_loader": lambda _date: None,
        "llm_caller": lambda _system, _prompt: _complete_review(),
        "now": datetime(2026, 7, 22, tzinfo=timezone.utc),
    }
    try:
        generate_episode_ai_review(_detail(), **kwargs)
        generate_episode_ai_review(_detail(), **kwargs)
    finally:
        _clear_history_cache_for_tests()

    assert sorted(calls) == ["MSFT", "SPY"]


def test_review_keeps_market_gaps_explicit() -> None:
    def empty_history(symbol: str, period: str, _days: int):
        return {"stock_code": symbol, "period": period, "data": []}

    result = generate_episode_ai_review(
        _detail(),
        history_loader=empty_history,
        regime_loader=lambda _date: None,
        llm_caller=lambda _system, _prompt: "## 已知事实\n\n行情缺失。",
        now=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )

    assert result["data_state"] == "ready"
    assert result["market_context"]["underlying_return_pct"] is None
    assert result["market_context"]["benchmark_return_pct"] is None
    assert result["market_context"]["relative_return_pct"] is None
    assert any("无法计算标的收益代理" in warning for warning in result["warnings"])
