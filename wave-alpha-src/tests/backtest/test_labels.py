# tests/backtest/test_labels.py
"""Tests for pure-function labelers in wave_alpha.backtest.labels.

NOTE on lookahead: forward_direction_label reads FUTURE bars by construction
— that is its purpose. The lookahead guard (PointInTimeView) belongs at the
call-site in the evaluators (Phase C/D), not here in the labeler.
"""

from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.backtest.labels import (
    forward_direction_label,
    pivot_confirmation_label,
)
from wave_alpha.domain import Bar
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def _bar(d_off: int, close: str) -> Bar:
    d = date(2024, 1, 1) + timedelta(days=d_off)
    return Bar(
        timestamp=d,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=1_000,
    )


def test_forward_direction_up_when_close_rises_after_n_bars() -> None:
    bars = [_bar(i, str(100 + i)) for i in range(30)]
    label = forward_direction_label(bars, as_of=date(2024, 1, 1), n_bars=20)
    assert label == "up"


def test_forward_direction_down_when_close_falls() -> None:
    bars = [_bar(i, str(100 - i)) for i in range(30)]
    label = forward_direction_label(bars, as_of=date(2024, 1, 1), n_bars=20)
    assert label == "down"


def test_forward_direction_none_when_insufficient_forward_history() -> None:
    bars = [_bar(i, "100") for i in range(10)]
    # Need 20 forward bars from as_of, only 9 forward bars exist
    label = forward_direction_label(bars, as_of=date(2024, 1, 1), n_bars=20)
    assert label is None


def test_forward_direction_none_when_as_of_not_in_series() -> None:
    bars = [_bar(i, "100") for i in range(30)]
    # as_of before first bar
    assert forward_direction_label(bars, as_of=date(2023, 12, 1), n_bars=5) is None


def test_forward_direction_flat_close_returns_down() -> None:
    """Equal closes → 'down' by convention (no rise = no bullish edge)."""
    bars = [_bar(i, "100") for i in range(30)]
    assert forward_direction_label(bars, as_of=date(2024, 1, 1), n_bars=10) == "down"


def test_pivot_confirmation_label_promoted_when_state_advances() -> None:
    """A tentative pivot at degree d on date t was confirmed if a later snapshot
    of the same pivot timeline contains the same (degree, timestamp) pivot in
    CONFIRMED state."""
    from wave_alpha.domain import Interval

    initial = PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=date(2024, 1, 5),
        price=Decimal("110"),
        type=PivotType.HIGH,
        state=PivotState.TENTATIVE,
        confirmed_at=None,
    )
    later_timeline = [
        PivotEvent(
            interval=Interval.DAILY,
            degree=2,
            timestamp=date(2024, 1, 5),
            price=Decimal("110"),
            type=PivotType.HIGH,
            state=PivotState.CONFIRMED,
            confirmed_at=date(2024, 1, 8),
        )
    ]
    label = pivot_confirmation_label(initial, later_pivots=later_timeline)
    assert label == "confirmed"


def test_pivot_confirmation_label_invalidated_when_pivot_disappears() -> None:
    """Pivot tentative at t disappears from later snapshot → invalidated."""
    from wave_alpha.domain import Interval

    initial = PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=date(2024, 1, 5),
        price=Decimal("110"),
        type=PivotType.HIGH,
        state=PivotState.TENTATIVE,
        confirmed_at=None,
    )
    later_timeline = [
        PivotEvent(
            interval=Interval.DAILY,
            degree=2,
            timestamp=date(2024, 1, 6),  # different date
            price=Decimal("112"),
            type=PivotType.HIGH,
            state=PivotState.CONFIRMED,
            confirmed_at=date(2024, 1, 9),
        )
    ]
    label = pivot_confirmation_label(initial, later_pivots=later_timeline)
    assert label == "invalidated"


def test_pivot_confirmation_label_pending_when_still_tentative() -> None:
    """Same (degree, timestamp) but still TENTATIVE → label is pending (None)."""
    from wave_alpha.domain import Interval

    initial = PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=date(2024, 1, 5),
        price=Decimal("110"),
        type=PivotType.HIGH,
        state=PivotState.TENTATIVE,
        confirmed_at=None,
    )
    later_timeline = [
        PivotEvent(
            interval=Interval.DAILY,
            degree=2,
            timestamp=date(2024, 1, 5),
            price=Decimal("110"),
            type=PivotType.HIGH,
            state=PivotState.TENTATIVE,
            confirmed_at=None,
        )
    ]
    assert pivot_confirmation_label(initial, later_pivots=later_timeline) is None
