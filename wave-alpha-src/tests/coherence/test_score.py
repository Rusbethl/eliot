from datetime import date
from decimal import Decimal

from wave_alpha.coherence.score import (
    CoherenceInputs,
    coherence_multiplier,
    coherence_score,
    three_way,
)
from wave_alpha.domain import Interval
from wave_alpha.elliott.models import WaveCount
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def _piv(d: date, p: str, t: PivotType, interval: Interval = Interval.DAILY) -> PivotEvent:
    return PivotEvent(
        interval=interval,
        degree=2,
        timestamp=d,
        price=Decimal(p),
        type=t,
        state=PivotState.CONFIRMED,
        confirmed_at=d,
    )


def _impulse(interval: Interval = Interval.DAILY) -> WaveCount:
    return WaveCount(
        pattern="impulse",
        degree=2,
        pivots=[
            _piv(date(2024, 1, 1), "100", PivotType.LOW, interval),
            _piv(date(2024, 1, 5), "110", PivotType.HIGH, interval),
            _piv(date(2024, 1, 8), "104", PivotType.LOW, interval),
            _piv(date(2024, 1, 15), "130", PivotType.HIGH, interval),
            _piv(date(2024, 1, 18), "118", PivotType.LOW, interval),
            _piv(date(2024, 1, 25), "140", PivotType.HIGH, interval),
        ],
    )


def _zigzag() -> WaveCount:
    return WaveCount(
        pattern="zigzag",
        degree=2,
        pivots=[
            _piv(date(2024, 1, 1), "140", PivotType.HIGH),
            _piv(date(2024, 1, 5), "120", PivotType.LOW),
            _piv(date(2024, 1, 10), "130", PivotType.HIGH),
            _piv(date(2024, 1, 20), "100", PivotType.LOW),
        ],
    )


def test_coherence_full_agreement_high_score() -> None:
    s = coherence_score(_impulse(Interval.DAILY), _impulse(Interval.WEEKLY))
    assert s > 0.7


def test_coherence_disagreement_low_score() -> None:
    s = coherence_score(_impulse(Interval.DAILY), _zigzag())
    assert s < 0.4


def test_three_way_falls_back_when_4h_insufficient() -> None:
    inputs = CoherenceInputs(
        weekly=_impulse(Interval.WEEKLY),
        daily=_impulse(Interval.DAILY),
        hourly4=None,
    )
    blended = three_way(inputs)
    assert blended.fallback_used == "no_4h_history"
    assert 0.7 <= blended.score <= 1.0


def test_multiplier_disagreement_lowers_below_one() -> None:
    assert coherence_multiplier(0.0) < 1.0
    assert coherence_multiplier(1.0) > 1.0
    assert coherence_multiplier(0.0) >= 0.5 and coherence_multiplier(1.0) <= 1.2
