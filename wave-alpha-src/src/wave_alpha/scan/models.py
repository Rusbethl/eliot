from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ScanRow(BaseModel):
    """One ticker's scan summary. None on optional fields means the engine
    couldn't produce that piece (no counts, no signal, etc); `error` is set
    only on hard failures (provider down, exception)."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    as_of: date
    coherence: float | None = None
    top_pattern: str | None = None
    top_score: float | None = None
    direction: Literal["up", "down"] | None = None
    signal_rr: Decimal | None = None
    signal_entry: Decimal | None = None
    signal_stop: Decimal | None = None
    signal_target: Decimal | None = None
    right_edge_proba: float | None = None
    notes: str | None = None
    error: str | None = None

    @property
    def has_signal(self) -> bool:
        return self.signal_rr is not None

    @property
    def has_error(self) -> bool:
        return self.error is not None


class ScanReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    scan_id: str
    started_at: datetime
    completed_at: datetime
    as_of: date
    rows: list[ScanRow]

    @property
    def duration_seconds(self) -> int:
        return int((self.completed_at - self.started_at).total_seconds())

    @property
    def n_total(self) -> int:
        return len(self.rows)

    @property
    def n_with_signal(self) -> int:
        return sum(1 for r in self.rows if r.has_signal)

    @property
    def n_errored(self) -> int:
        return sum(1 for r in self.rows if r.has_error)
