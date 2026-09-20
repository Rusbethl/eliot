from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

from wave_alpha.domain import Bar


class LookaheadError(AssertionError):
    """Raised when code tries to read a Bar with timestamp > as_of."""


class UnsortedBarsError(AssertionError):
    """Raised when PointInTimeView receives bars that are not in ascending
    timestamp order. The lookahead invariant relies on monotonic ordering."""


@dataclass(frozen=True)
class PointInTimeView:
    """Immutable point-in-time accessor over a sorted bar series.

    The single source of truth for both backtest and live pipelines.
    Hard-asserts: (1) bars are sorted ascending by timestamp; (2) no bar with
    timestamp > as_of is observable through indexed access or `latest()`.
    """

    bars: Sequence[Bar]
    as_of: date

    def __post_init__(self) -> None:
        for a, b in pairwise(self.bars):
            if a.timestamp > b.timestamp:
                raise UnsortedBarsError(
                    f"bars must be in ascending timestamp order; {a.timestamp} > {b.timestamp}"
                )

    def visible(self) -> list[Bar]:
        return [b for b in self.bars if b.timestamp <= self.as_of]

    def latest(self) -> Bar:
        vis = self.visible()
        if not vis:
            raise LookaheadError(
                f"no bars visible at as_of={self.as_of}; earliest is "
                f"{self.bars[0].timestamp if self.bars else 'none'}"
            )
        return vis[-1]

    def __getitem__(self, idx: int) -> Bar:
        bar = self.bars[idx]
        if bar.timestamp > self.as_of:
            raise LookaheadError(f"lookahead violation: bar {bar.timestamp} > as_of {self.as_of}")
        return bar

    def __len__(self) -> int:
        return len(self.visible())
