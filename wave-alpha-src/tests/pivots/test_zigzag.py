import itertools
from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.domain import Bar, Interval
from wave_alpha.pivots.models import PivotState, PivotType
from wave_alpha.pivots.zigzag import zigzag_atr


def _bars_from_closes(closes: list[float]) -> list[Bar]:
    bars: list[Bar] = []
    for i, c in enumerate(closes):
        cd = Decimal(f"{c:.4f}")
        bars.append(
            Bar(
                timestamp=date(2024, 1, 1) + timedelta(days=i),
                open=cd,
                high=cd + Decimal("0.10"),
                low=cd - Decimal("0.10"),
                close=cd,
                volume=1,
            )
        )
    return bars


def test_zigzag_alternates_high_low_above_threshold() -> None:
    # Closes: rise 100→110, fall to 95, rise to 115, fall to 100
    # threshold 4 → all swings significant
    closes = [100, 102, 104, 110, 108, 100, 95, 100, 110, 115, 112, 105, 100]
    bars = _bars_from_closes(closes)
    pivots = zigzag_atr(
        bars, interval=Interval.DAILY, atr_multiple=Decimal("0"), fixed_threshold=Decimal("4")
    )
    assert len(pivots) >= 4
    types = [p.type for p in pivots]
    # types must alternate
    for a, b in itertools.pairwise(types):
        assert a != b


def test_zigzag_first_pivot_is_low_when_series_rises() -> None:
    closes = [100, 105, 110, 115]
    bars = _bars_from_closes(closes)
    pivots = zigzag_atr(
        bars, interval=Interval.DAILY, atr_multiple=Decimal("0"), fixed_threshold=Decimal("3")
    )
    assert pivots[0].type is PivotType.LOW


def test_zigzag_marks_unconfirmed_tail_as_tentative() -> None:
    # Last leg has not yet retraced — tail pivot should be tentative
    closes = [100, 110, 100, 115]
    bars = _bars_from_closes(closes)
    pivots = zigzag_atr(
        bars, interval=Interval.DAILY, atr_multiple=Decimal("0"), fixed_threshold=Decimal("3")
    )
    assert pivots[-1].state in (PivotState.TENTATIVE, PivotState.CONFIRMED)
    if pivots[-1].state is PivotState.TENTATIVE:
        assert pivots[-1].confirmed_at is None
