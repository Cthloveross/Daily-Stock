from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from starlette.testclient import TestClient

from api.v1.endpoints import journal_ai


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(journal_ai.router, prefix="/api/v1/journal")
    return TestClient(app)


def _result() -> dict:
    return {
        "data_state": "ready",
        "analysis_mode": "model_enhanced",
        "analysis_source": "configured_llm",
        "episode_id": 41,
        "build_id": 7,
        "generated_at": datetime(2026, 7, 22, tzinfo=timezone.utc),
        "evidence_markdown": "## 一句话事实结论\n\n确定性证据层。",
        "model_analysis_markdown": "## 一句话结论\n\n只使用已知事实。",
        # Deprecated compatibility field intentionally mirrors model output.
        "analysis_markdown": "## 一句话结论\n\n只使用已知事实。",
        "market_context": {
            "benchmark": "SPY",
            "timeframe": "5m",
            "window_start": "2026-07-20T14:30:00Z",
            "window_end": "2026-07-20T15:00:00Z",
            "underlying_return_pct": 1.25,
            "benchmark_return_pct": 0.25,
            "relative_return_pct": 1.0,
            "regime_label": "standard",
            "provenance": ["MSFT 5m · MoomooFetcher"],
        },
        "warnings": ["单笔案例不能证明 edge"],
    }


def test_ai_review_uses_latest_episode_without_persisting(monkeypatch) -> None:
    detail = SimpleNamespace()
    captured = {}
    review_kwargs = {}

    def latest_detail(**kwargs):
        captured.update(kwargs)
        return detail

    def generate(value, **kwargs):
        review_kwargs.update(kwargs)
        return _result() if value is detail else None

    monkeypatch.setattr(journal_ai, "get_latest_position_episode_detail", latest_detail)
    monkeypatch.setattr(journal_ai, "generate_episode_ai_review", generate)

    response = _client().post("/api/v1/journal/v2/position-episodes/41/ai-review")

    assert response.status_code == 200
    assert captured == {
        "episode_id": 41,
        "account_key": "default_moomoo_us",
    }
    assert review_kwargs == {
        "enhance_with_model": True,
        "user_context": None,
    }
    body = response.json()
    assert body["data_state"] == "ready"
    assert body["analysis_mode"] == "model_enhanced"
    assert body["analysis_source"] == "configured_llm"
    assert body["market_context"]["benchmark"] == "SPY"
    assert body["evidence_markdown"].startswith("## 一句话事实结论")
    assert body["model_analysis_markdown"] == body["analysis_markdown"]
    assert body["analysis_markdown"].startswith("## 一句话结论")


def test_ai_review_honours_explicit_build(monkeypatch) -> None:
    captured = {}

    def selected_detail(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(journal_ai, "get_position_episode_detail", selected_detail)
    monkeypatch.setattr(
        journal_ai,
        "generate_episode_ai_review",
        lambda _detail, **_kwargs: _result(),
    )

    response = _client().post(
        "/api/v1/journal/v2/position-episodes/41/ai-review",
        params={"build_id": 7},
    )

    assert response.status_code == 200
    assert captured == {
        "episode_id": 41,
        "build_id": 7,
        "account_key": "default_moomoo_us",
    }


def test_ai_review_returns_404_for_unknown_episode(monkeypatch) -> None:
    monkeypatch.setattr(
        journal_ai,
        "get_latest_position_episode_detail",
        lambda **_kwargs: None,
    )

    response = _client().post("/api/v1/journal/v2/position-episodes/999/ai-review")

    assert response.status_code == 404
    assert "latest build" in response.json()["detail"]


def test_ai_review_returns_safe_context_when_llm_degrades(monkeypatch) -> None:
    result = _result()
    result.update(
        {
            "data_state": "llm_unavailable",
            "analysis_mode": "deterministic",
            "analysis_source": "local_evidence_engine",
            "evidence_markdown": "## 一句话事实结论\n\n成交事实仍可审阅。",
            "model_analysis_markdown": None,
            "analysis_markdown": "## 一句话事实结论\n\n成交事实仍可审阅。",
            "warnings": ["模型增强暂不可用；已生成本地证据复盘"],
        }
    )
    monkeypatch.setattr(
        journal_ai,
        "get_latest_position_episode_detail",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        journal_ai,
        "generate_episode_ai_review",
        lambda _detail, **_kwargs: result,
    )

    response = _client().post("/api/v1/journal/v2/position-episodes/41/ai-review")

    assert response.status_code == 200
    body = response.json()
    assert body["data_state"] == "llm_unavailable"
    assert body["analysis_mode"] == "deterministic"
    assert body["analysis_source"] == "local_evidence_engine"
    assert body["market_context"]["benchmark"] == "SPY"
    assert body["model_analysis_markdown"] is None
    assert body["analysis_markdown"] == body["evidence_markdown"]
    assert "事实结论" in body["analysis_markdown"]


def test_ai_review_can_request_evidence_only_mode(monkeypatch) -> None:
    captured = {}

    def generate(_detail, **kwargs):
        captured.update(kwargs)
        result = _result()
        result.update(
            {
                "analysis_mode": "deterministic",
                "analysis_source": "local_evidence_engine",
                "model_analysis_markdown": None,
                "analysis_markdown": result["evidence_markdown"],
            }
        )
        return result

    monkeypatch.setattr(
        journal_ai,
        "get_latest_position_episode_detail",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(journal_ai, "generate_episode_ai_review", generate)

    response = _client().post(
        "/api/v1/journal/v2/position-episodes/41/ai-review",
        params={"enhance": "false"},
    )

    assert response.status_code == 200
    assert captured["enhance_with_model"] is False
    assert captured["user_context"] is None
    body = response.json()
    assert body["analysis_mode"] == "deterministic"
    assert body["model_analysis_markdown"] is None
    assert body["analysis_markdown"] == body["evidence_markdown"]


def test_ai_review_accepts_trimmed_non_persisted_user_context(monkeypatch) -> None:
    captured = {}

    def generate(_detail, **kwargs):
        captured.update(kwargs)
        return _result()

    monkeypatch.setattr(
        journal_ai,
        "get_latest_position_episode_detail",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(journal_ai, "generate_episode_ai_review", generate)

    response = _client().post(
        "/api/v1/journal/v2/position-episodes/41/ai-review",
        json={
            "user_context": {
                "setup_thesis": "  趋势延续  ",
                "entry_trigger": "  ",
                "invalidation_plan": "跌破前低",
                "position_rationale": "Delta 与风险预算匹配",
                "exit_reason": "达到目标位",
                "post_trade_reflection": "  执行符合预案。\n  ",
            }
        },
    )

    assert response.status_code == 200
    assert captured["user_context"] == {
        "setup_thesis": "趋势延续",
        "invalidation_plan": "跌破前低",
        "position_rationale": "Delta 与风险预算匹配",
        "exit_reason": "达到目标位",
        "post_trade_reflection": "执行符合预案。",
    }


def test_ai_review_rejects_user_context_over_length_limit(monkeypatch) -> None:
    model_called = False

    def generate(*_args, **_kwargs):
        nonlocal model_called
        model_called = True
        return _result()

    monkeypatch.setattr(
        journal_ai,
        "get_latest_position_episode_detail",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(journal_ai, "generate_episode_ai_review", generate)

    response = _client().post(
        "/api/v1/journal/v2/position-episodes/41/ai-review",
        json={"user_context": {"setup_thesis": "x" * 2001}},
    )

    assert response.status_code == 422
    assert model_called is False


def test_ai_review_enforces_trimmed_user_context_total_limit(monkeypatch) -> None:
    captured = {}

    def generate(_detail, **kwargs):
        captured.update(kwargs)
        return _result()

    monkeypatch.setattr(
        journal_ai,
        "get_latest_position_episode_detail",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(journal_ai, "generate_episode_ai_review", generate)

    at_limit = _client().post(
        "/api/v1/journal/v2/position-episodes/41/ai-review",
        json={
            "user_context": {
                "setup_thesis": f"  {'a' * 2000}  ",
                "entry_trigger": "b" * 2000,
                "invalidation_plan": "c" * 2000,
            }
        },
    )
    assert at_limit.status_code == 200
    assert sum(len(value) for value in captured["user_context"].values()) == 6000

    over_limit = _client().post(
        "/api/v1/journal/v2/position-episodes/41/ai-review",
        json={
            "user_context": {
                "setup_thesis": "a" * 2000,
                "entry_trigger": "b" * 2000,
                "invalidation_plan": "c" * 2000,
                "position_rationale": "d",
            }
        },
    )
    assert over_limit.status_code == 422


def test_ai_review_openapi_marks_legacy_analysis_as_deprecated() -> None:
    schema = _client().get("/openapi.json").json()
    response_schema = schema["components"]["schemas"][
        "PositionEpisodeAiReviewResponse"
    ]

    properties = response_schema["properties"]
    assert properties["analysis_markdown"]["deprecated"] is True
    assert "evidence_markdown" in response_schema["required"]
    assert "model_analysis_markdown" not in response_schema["required"]
