from datetime import date
from decimal import Decimal

from wave_alpha.domain import Interval
from wave_alpha.elliott.models import WaveCount, WaveSegment
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def _piv(d: date, price: str, ptype: PivotType) -> PivotEvent:
    return PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=d,
        price=Decimal(price),
        type=ptype,
        state=PivotState.CONFIRMED,
        confirmed_at=d,
    )


def test_wave_segment_length_is_signed_price_distance() -> None:
    seg = WaveSegment(
        start=_piv(date(2024, 1, 1), "100", PivotType.LOW),
        end=_piv(date(2024, 1, 10), "115", PivotType.HIGH),
    )
    assert seg.length == Decimal("15")
    assert seg.is_up


def test_wave_count_sub_count_lookup() -> None:
    wc = WaveCount(
        pattern="impulse",
        degree=2,
        pivots=[
            _piv(date(2024, 1, 1), "100", PivotType.LOW),
            _piv(date(2024, 1, 5), "110", PivotType.HIGH),
            _piv(date(2024, 1, 8), "105", PivotType.LOW),
            _piv(date(2024, 1, 15), "125", PivotType.HIGH),
            _piv(date(2024, 1, 18), "118", PivotType.LOW),
            _piv(date(2024, 1, 25), "135", PivotType.HIGH),
        ],
        score=0.7,
    )
    assert len(wc.segments()) == 5
    seg1 = wc.segment_by_label("1")
    assert seg1.length == Decimal("10")
