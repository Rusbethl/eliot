# src/wave_alpha/backtest/labels.py
from __future__ import annotations

from datetime import date
from typing import Literal

from wave_alpha.domain import Bar
from wave_alpha.pivots.models import PivotEvent, PivotState

Direction = Literal["up", "down"]
PivotOutcome = Literal["confirmed", "invalidated"]


def forward_direction_label(bars: list[Bar], *, as_of: date, n_bars: int) -> Direction | None:
    """Compare close at as_of vs close N business bars later.

    Returns 'up' if forward close > as_of close, 'down' otherwise (including
    equality — flat close offers no bullish edge). Returns None when as_of is
    not in the series or there are fewer than n_bars of forward history.

    NOTE on lookahead: this function intentionally reads FUTURE bars relative
    to as_of — that is its purpose as a labeler. The lookahead guard
    (PointInTimeView) belongs at the call-site in the evaluators, not here.
    """
    idx_at = next((i for i, b in enumerate(bars) if b.timestamp == as_of), None)
    if idx_at is None or idx_at + n_bars >= len(bars):
        return None
    base = bars[idx_at].close
    forward = bars[idx_at + n_bars].close
    return "up" if forward > base else "down"


def pivot_confirmation_label(
    initial: PivotEvent, *, later_pivots: list[PivotEvent]
) -> PivotOutcome | None:
    """Did a tentative pivot get confirmed in a later snapshot?

    'confirmed'   — same (degree, timestamp, type) appears CONFIRMED later.
    'invalidated' — same (degree, timestamp, type) is missing from the later
                    timeline.
    None          — same (degree, timestamp, type) is still TENTATIVE; outcome
                    unknown (pending).
    """
    if initial.state != PivotState.TENTATIVE:
        raise ValueError("pivot_confirmation_label expects a TENTATIVE input")
    match = next(
        (
            p
            for p in later_pivots
            if p.degree == initial.degree
            and p.timestamp == initial.timestamp
            and p.type == initial.type
        ),
        None,
    )
    if match is None:
        return "invalidated"
    if match.state == PivotState.CONFIRMED:
        return "confirmed"
    return None  # still tentative
