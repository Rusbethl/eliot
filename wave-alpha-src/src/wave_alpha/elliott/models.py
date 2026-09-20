from __future__ import annotations

from decimal import Decimal
from itertools import pairwise

from pydantic import BaseModel, ConfigDict, Field

from wave_alpha.pivots.models import PivotEvent

_PATTERN_LABELS: dict[str, list[str]] = {
    "impulse": ["1", "2", "3", "4", "5"],
    "leading_diagonal": ["1", "2", "3", "4", "5"],
    "ending_diagonal": ["1", "2", "3", "4", "5"],
    "zigzag": ["A", "B", "C"],
    "flat": ["A", "B", "C"],
    "expanded_flat": ["A", "B", "C"],
    "contracting_triangle": ["A", "B", "C", "D", "E"],
}


class WaveSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: PivotEvent
    end: PivotEvent

    @property
    def length(self) -> Decimal:
        return abs(self.end.price - self.start.price)

    @property
    def signed_length(self) -> Decimal:
        return self.end.price - self.start.price

    @property
    def is_up(self) -> bool:
        return self.signed_length > 0


class WaveCount(BaseModel):
    """A pattern instance bound to a sequence of pivots, with optional sub-counts."""

    model_config = ConfigDict(frozen=True)

    pattern: str
    degree: int
    pivots: list[PivotEvent]
    sub_counts: dict[str, WaveCount] = Field(default_factory=dict)
    score: float = 0.0
    # Decomposed sub-scores populated by ``enumerate_counts`` during scoring;
    # None for counts built by hand (test fixtures) or by callers that skip
    # the scorer. ``template_fit_component`` is the raw 1 - 0.10*N(soft)
    # penalty and may be negative for counts with > 10 soft violations
    # (the legacy ``score`` is still clamped to 0 at the bottom).
    template_fit_component: float | None = None
    fib_component: float | None = None
    recency_component: float | None = None
    hard_violations: list[str] = Field(default_factory=list)
    soft_violations: list[str] = Field(default_factory=list)

    def segments(self) -> list[WaveSegment]:
        return [WaveSegment(start=a, end=b) for a, b in pairwise(self.pivots)]

    def segment_by_label(self, label: str) -> WaveSegment:
        labels = _PATTERN_LABELS[self.pattern]
        idx = labels.index(label)
        segs = self.segments()
        return segs[idx]
