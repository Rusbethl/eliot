from datetime import date
from decimal import Decimal

import pytest

from wave_alpha.domain import Interval
from wave_alpha.elliott.fib import fib_confluence_score, fib_extension, fib_retracement
from wave_alpha.elliott.models import WaveSegment
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def _piv(d: date, p: str, t: PivotType) -> PivotEvent:
    return PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=d,
        price=Decimal(p),
        type=t,
        state=PivotState.CONFIRMED,
        confirmed_at=d,
    )


W1 = WaveSegment(
    start=_piv(date(2024, 1, 1), "100", PivotType.LOW),
    end=_piv(date(2024, 1, 5), "110", PivotType.HIGH),
)


def test_fib_retracement_levels_are_descending_for_up_wave() -> None:
    levels = fib_retracement(W1)
    assert levels[Decimal("0.500")] == Decimal("105.0")
    # 0.618 retrace from 110 over a 10-point wave = 110 - 6.18 = 103.82
    assert levels[Decimal("0.618")] == pytest.approx(Decimal("103.82"), rel=Decimal("0.001"))


def test_fib_extension_levels_above_for_up_wave() -> None:
    levels = fib_extension(W1, anchor_price=Decimal("104"))
    # 1.618 extension from 104 = 104 + 1.618 * 10 = 120.18
    assert levels[Decimal("1.618")] == pytest.approx(Decimal("120.18"), rel=Decimal("0.001"))


def test_confluence_score_high_when_price_near_618() -> None:
    score = fib_confluence_score(target_price=Decimal("103.82"), reference=W1)
    assert score > 0.7
    far = fib_confluence_score(target_price=Decimal("90"), reference=W1)
    assert far < 0.3
