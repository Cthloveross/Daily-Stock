"""Schemas for the non-persisted, read-only PositionEpisode AI review."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_USER_CONTEXT_TOTAL_MAX_LENGTH = 6000


class EpisodeAiUserContext(BaseModel):
    """Optional, non-persisted user notes supplied for one review request."""

    model_config = ConfigDict(extra="forbid")

    setup_thesis: Optional[str] = Field(default=None, max_length=2000)
    entry_trigger: Optional[str] = Field(default=None, max_length=2000)
    invalidation_plan: Optional[str] = Field(default=None, max_length=2000)
    position_rationale: Optional[str] = Field(default=None, max_length=2000)
    exit_reason: Optional[str] = Field(default=None, max_length=2000)
    post_trade_reflection: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("*", mode="before")
    @classmethod
    def strip_optional_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def validate_total_length(self) -> "EpisodeAiUserContext":
        total_length = sum(
            len(value)
            for value in (
                self.setup_thesis,
                self.entry_trigger,
                self.invalidation_plan,
                self.position_rationale,
                self.exit_reason,
                self.post_trade_reflection,
            )
            if value is not None
        )
        if total_length > _USER_CONTEXT_TOTAL_MAX_LENGTH:
            raise ValueError(
                "user_context must contain at most "
                f"{_USER_CONTEXT_TOTAL_MAX_LENGTH} characters in total"
            )
        return self


class PositionEpisodeAiReviewRequest(BaseModel):
    """Request-only context; no field in this model is persisted."""

    model_config = ConfigDict(extra="forbid")

    user_context: Optional[EpisodeAiUserContext] = None


class EpisodeAiMarketContext(BaseModel):
    benchmark: str
    timeframe: str
    window_start: datetime
    window_end: datetime
    underlying_return_pct: Optional[float] = None
    benchmark_return_pct: Optional[float] = None
    relative_return_pct: Optional[float] = None
    regime_label: Optional[str] = None
    provenance: list[str] = Field(default_factory=list)


class PositionEpisodeAiReviewResponse(BaseModel):
    data_state: Literal["ready", "llm_unavailable"]
    analysis_mode: Literal["model_enhanced", "deterministic"] = "model_enhanced"
    analysis_source: Literal["configured_llm", "local_evidence_engine"] = (
        "configured_llm"
    )
    episode_id: int
    build_id: int
    generated_at: datetime
    evidence_markdown: str
    model_analysis_markdown: Optional[str] = None
    # Deprecated compatibility field: model output on success, otherwise evidence.
    analysis_markdown: str = Field(
        description=(
            "Deprecated compatibility field. Use evidence_markdown and "
            "model_analysis_markdown instead."
        ),
        deprecated=True,
    )
    market_context: EpisodeAiMarketContext
    warnings: list[str] = Field(default_factory=list)


__all__ = [
    "EpisodeAiUserContext",
    "EpisodeAiMarketContext",
    "PositionEpisodeAiReviewRequest",
    "PositionEpisodeAiReviewResponse",
]
