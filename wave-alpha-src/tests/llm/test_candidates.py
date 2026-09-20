from datetime import date
from decimal import Decimal

from wave_alpha.domain import Interval
from wave_alpha.elliott.models import WaveCount
from wave_alpha.llm.candidates import serialize_candidate, serialize_candidate_set
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


def _impulse() -> WaveCount:
    return WaveCount(
        pattern="impulse",
        degree=2,
        score=0.72,
        pivots=[
            _piv(date(2024, 1, 1), "100", PivotType.LOW),
            _piv(date(2024, 1, 5), "110", PivotType.HIGH),
            _piv(date(2024, 1, 8), "104", PivotType.LOW),
            _piv(date(2024, 1, 15), "130", PivotType.HIGH),
            _piv(date(2024, 1, 18), "118", PivotType.LOW),
        ],
    )


def test_serialize_candidate_includes_pattern_score_and_pivots() -> None:
    c = _impulse()
    text = serialize_candidate(c, candidate_id="C1")
    assert "C1" in text
    assert "impulse" in text
    assert "0.72" in text
    # All five pivots referenced (compact: P1..P5)
    for ts in ("2024-01-01", "2024-01-15"):
        assert ts in text
    assert "100" in text and "130" in text


def test_serialize_candidate_set_uses_stable_ids() -> None:
    a = _impulse()
    b = _impulse().model_copy(update={"score": 0.65})
    block = serialize_candidate_set([a, b])
    assert "C1" in block and "C2" in block
    # Highest score first preserved (caller's order)
    first = block.index("C1")
    second = block.index("C2")
    assert first < second


def test_empty_candidate_set_yields_empty_block() -> None:
    assert serialize_candidate_set([]) == ""
