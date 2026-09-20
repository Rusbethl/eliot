"""Pydantic models and frozen dataclasses for history snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SnapshotPivot(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp: date
    price: Decimal


class SnapshotCount(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: str
    pattern: str
    degree: int
    last_pivot_label: str
    pivots: list[SnapshotPivot]
    engine_score: float
    llm_prob: float | None
    final_score: float
    engine_prob: float | None = None
    """Calibrated P(target) from the logistic engine ranker, when active.
    None for rows produced under the heuristic-fallback path (old rows
    or fresh installs without a logistic artifact)."""


class SnapshotSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    direction: Literal["long", "short"]
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    rr: float
    count_id: str | None = None
    count_pattern: str | None = None
    wave_label: str | None = None
    degree: int | None = None
    confidence: float | None = None
    expected_bars_to_target: int | None = None
    max_holding_bars: int | None = None


class SnapshotCoherence(BaseModel):
    model_config = ConfigDict(frozen=True)

    score: float
    multiplier: float
    pairwise: dict[str, float]
    fallback_used: str | None


class SnapshotOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["target", "stop", "time_stop", "pending"]
    exit_date: date | None
    exit_price: Decimal | None
    bars_held: int | None
    realized_R: float | None  # noqa: N815
    last_observed_date: date
    bars_elapsed: int
    unrealized_R: float  # noqa: N815
    mfe_R: float  # noqa: N815
    mae_R: float  # noqa: N815
    resolved_at: datetime


class Snapshot(BaseModel):
    """A single (ticker, as_of) record. Pydantic ignores unknown fields by
    default, which is the forward-compat hook for v2 additive fields."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    as_of: date
    engine_version: str
    top_3: list[SnapshotCount] = Field(..., min_length=1, max_length=3)
    signal: SnapshotSignal | None
    coherence: SnapshotCoherence | None
    top_count_hash: str
    created_at: datetime
    outcome: SnapshotOutcome | None = None


@dataclass(frozen=True)
class StabilityBadge:
    streak: int
    matches_in_window: int
    window: int
    last_changed_on: date | None


@dataclass(frozen=True)
class RecentHistory:
    snapshots: list[Snapshot]
    badge: StabilityBadge | None


@dataclass(frozen=True)
class OutcomeBadge:
    resolved_count: int  # target + stop + time_stop
    target_count: int
    hit_rate: float  # target_count / resolved_count when resolved_count > 0 else 0.0
    avg_R: float  # noqa: N815 — mean realized_R across resolved
    in_flight_count: int  # pending
