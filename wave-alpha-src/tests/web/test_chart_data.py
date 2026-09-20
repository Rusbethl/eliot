from datetime import date
from decimal import Decimal

from wave_alpha.domain import Bar, Interval
from wave_alpha.elliott.models import WaveCount
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
from wave_alpha.web.chart_data import build_chart_payload


def _bar(d: date, c: float) -> Bar:
    p = Decimal(f"{c:.2f}")
    return Bar(timestamp=d, open=p, high=p, low=p, close=p, volume=1000)


def _piv(d: date, price: str, t: PivotType, state: PivotState = PivotState.CONFIRMED) -> PivotEvent:
    return PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=d,
        price=Decimal(price),
        type=t,
        state=state,
        confirmed_at=d if state is PivotState.CONFIRMED else None,
    )


def test_build_chart_payload_serializes_bars() -> None:
    bars = [_bar(date(2024, 1, i + 1), 100.0 + i) for i in range(5)]
    payload = build_chart_payload(bars=bars, top_count=None)
    assert len(payload["bars"]) == 5
    assert payload["bars"][0] == {
        "time": "2024-01-01",
        "open": 100.0,
        "high": 100.0,
        "low": 100.0,
        "close": 100.0,
    }


def test_build_chart_payload_includes_pivot_markers_when_count_present() -> None:
    bars = [_bar(date(2024, 1, i + 1), 100.0 + i) for i in range(20)]
    count = WaveCount(
        pattern="impulse",
        degree=2,
        score=0.7,
        pivots=[
            _piv(date(2024, 1, 1), "100", PivotType.LOW),
            _piv(date(2024, 1, 5), "110", PivotType.HIGH),
            _piv(date(2024, 1, 8), "104", PivotType.LOW),
            _piv(date(2024, 1, 15), "130", PivotType.HIGH, state=PivotState.TENTATIVE),
        ],
    )
    payload = build_chart_payload(bars=bars, top_count=count)
    assert "markers" in payload
    assert len(payload["markers"]) == 4
    confirmed = next(m for m in payload["markers"] if m["text"] == "P1")
    tentative = next(m for m in payload["markers"] if m["text"] == "P4")
    assert confirmed["shape"] == "circle"
    assert tentative["shape"] == "square"  # tentative is dashed/different shape


def test_build_chart_payload_no_count_yields_empty_markers() -> None:
    bars = [_bar(date(2024, 1, 1), 100.0)]
    payload = build_chart_payload(bars=bars, top_count=None)
    assert payload["markers"] == []
