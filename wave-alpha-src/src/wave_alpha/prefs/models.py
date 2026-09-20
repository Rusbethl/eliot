from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LLMConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    api_key: str | None = None
    scan_model: str = "claude-haiku-4-5-20251001"
    deep_model: str = "claude-sonnet-4-6"
    alpha: float = Field(default=0.6, ge=0.0, le=1.0)


class RightEdgeOutcomeTrainingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    n_target_per_degree: int = Field(default=100, ge=1)
    include_time_stops: bool = True
    time_stop_label: float = Field(default=0.0, ge=0.0, le=1.0)


class RightEdgeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: Literal["auto", "heuristic", "calibrated_heuristic", "logistic"] = "auto"
    outcome_training: RightEdgeOutcomeTrainingConfig = Field(
        default_factory=RightEdgeOutcomeTrainingConfig
    )


class EngineRankerOutcomeTrainingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    walk_forward_split: float = Field(default=0.7, gt=0.0, lt=1.0)
    """Fraction of distinct as_of dates assigned to the training split."""

    class_balance_low: float = Field(default=0.35, ge=0.0, le=1.0)
    class_balance_high: float = Field(default=0.65, ge=0.0, le=1.0)
    """Training sets a balanced class weight when observed target rate falls
    outside [class_balance_low, class_balance_high]."""

    include_time_stops: bool = True
    """If True, time_stop outcomes are treated as the negative class. If
    False, time_stop rows are dropped entirely."""

    min_resolved_per_degree: int = Field(default=30, ge=1)
    """Promotion gate elevates verdict to INSUFFICIENT_DATA if any observed
    degree has fewer than this many resolved rows."""


class EngineRankerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["auto", "heuristic", "logistic"] = "auto"
    outcome_training: EngineRankerOutcomeTrainingConfig = Field(
        default_factory=EngineRankerOutcomeTrainingConfig
    )


class DataConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    stooq_api_key: str | None = None


class HistoryConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    retention_days: int = Field(default=730, ge=0)
    """Auto-prune snapshots older than this many days. 0 disables auto-prune."""


class TemplatesConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enable_leading_diagonal: bool = False
    """Opt-in for the leading_diagonal pattern.

    Default is False because the 2026-05-10 count-layer backtest gate
    showed enabling leading_diagonal regresses top-3 hit rate (-6 pp)
    and runtime (+23%) on the curated 50-ticker universe. See
    docs/superpowers/specs/2026-05-10-leading-diagonal-design.md §10.
    Users who specifically want to detect leading diagonals on charts
    where they expect them can flip this to True.
    """

    def enabled_opt_in_names(self) -> frozenset[str]:
        """Return the set of opt-in template names that are enabled."""
        names: set[str] = set()
        if self.enable_leading_diagonal:
            names.add("leading_diagonal")
        return frozenset(names)


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    llm: LLMConfig = Field(default_factory=LLMConfig)
    right_edge: RightEdgeConfig = Field(default_factory=RightEdgeConfig)
    engine_ranker: EngineRankerConfig = Field(default_factory=EngineRankerConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    templates: TemplatesConfig = Field(default_factory=TemplatesConfig)
