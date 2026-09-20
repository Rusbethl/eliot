from datetime import date
from decimal import Decimal

from wave_alpha.domain import Interval
from wave_alpha.elliott.models import WaveCount
from wave_alpha.elliott.templates import PatternTemplate
from wave_alpha.elliott.validator import validate
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def _piv(d: date, price: str, t: PivotType) -> PivotEvent:
    return PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=d,
        price=Decimal(price),
        type=t,
        state=PivotState.CONFIRMED,
        confirmed_at=d,
    )


GOOD_PIVOTS = [
    _piv(date(2024, 1, 1), "100", PivotType.LOW),
    _piv(date(2024, 1, 5), "110", PivotType.HIGH),
    _piv(date(2024, 1, 8), "104", PivotType.LOW),
    _piv(date(2024, 1, 15), "130", PivotType.HIGH),
    _piv(date(2024, 1, 18), "118", PivotType.LOW),
    _piv(date(2024, 1, 25), "140", PivotType.HIGH),
]


def test_validate_passes_clean_impulse() -> None:
    tpl = PatternTemplate.load_bundled("impulse")
    count = WaveCount(pattern="impulse", degree=2, pivots=GOOD_PIVOTS)
    validated = validate(count, tpl)
    assert validated.hard_violations == []


def test_validate_flags_wave_4_overlap() -> None:
    bad = list(GOOD_PIVOTS)
    bad[4] = _piv(date(2024, 1, 18), "108", PivotType.LOW)
    bad[5] = _piv(date(2024, 1, 25), "140", PivotType.HIGH)

    tpl = PatternTemplate.load_bundled("impulse")
    count = WaveCount(pattern="impulse", degree=2, pivots=bad)
    validated = validate(count, tpl)
    assert "wave_4_no_overlap" in validated.hard_violations


def test_validate_flags_short_wave_3() -> None:
    pivots = [
        _piv(date(2024, 1, 1), "100", PivotType.LOW),
        _piv(date(2024, 1, 5), "110", PivotType.HIGH),
        _piv(date(2024, 1, 8), "104", PivotType.LOW),
        _piv(date(2024, 1, 12), "108", PivotType.HIGH),
        _piv(date(2024, 1, 15), "106", PivotType.LOW),
        _piv(date(2024, 1, 25), "128", PivotType.HIGH),
    ]
    tpl = PatternTemplate.load_bundled("impulse")
    count = WaveCount(pattern="impulse", degree=2, pivots=pivots)
    validated = validate(count, tpl)
    assert "wave_3_not_shortest" in validated.hard_violations
