from datetime import date
from decimal import Decimal

import pytest

from wave_alpha.domain import Bar, Interval
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
from wave_alpha.right_edge.extractor import FeatureExtractor


def _bar(d: str, c: float, v: int = 1000) -> Bar:
    return Bar(
        timestamp=date.fromisoformat(d),
        open=Decimal(str(c)),
        high=Decimal(str(c + 0.5)),
        low=Decimal(str(c - 0.5)),
        close=Decimal(str(c)),
        volume=v,
    )


def test_feature_extractor_returns_5_fields_for_high_pivot() -> None:
    bars = [_bar(f"2025-01-{i:02d}", 100.0 + i) for i in range(1, 21)]
    pivot = PivotEvent(
        interval=Interval.DAILY,
        degree=1,
        timestamp=bars[10].timestamp,
        price=bars[10].close,
        type=PivotType.HIGH,
        state=PivotState.TENTATIVE,
        confirmed_at=None,
    )
    fx = FeatureExtractor().extract(
        candidate=pivot,
        point_in_time_bars=bars,
        atr_at_candidate=Decimal("1.0"),
        required_move=Decimal("2.0"),
        higher_degree_zone_match=0.3,
    )
    assert 0.0 <= fx.retracement_progress <= 1.0
    assert fx.bars_since_candidate == 9  # bars after the pivot
    assert fx.higher_degree_zone_match == 0.3
    assert fx.atr_regime == 0.5  # 1.0 / 2.0


def test_feature_extractor_returns_5_fields_for_low_pivot() -> None:
    # Falling series: 120 down to 101; pivot at day 10 (LOW), then rising
    bars = [_bar(f"2025-01-{i:02d}", 120.0 - i) for i in range(1, 21)]
    pivot = PivotEvent(
        interval=Interval.DAILY,
        degree=1,
        timestamp=bars[10].timestamp,
        price=bars[10].close,
        type=PivotType.LOW,
        state=PivotState.TENTATIVE,
        confirmed_at=None,
    )
    fx = FeatureExtractor().extract(
        candidate=pivot,
        point_in_time_bars=bars,
        atr_at_candidate=Decimal("1.0"),
        required_move=Decimal("4.0"),
        higher_degree_zone_match=0.0,
    )
    assert 0.0 <= fx.retracement_progress <= 1.0
    assert fx.bars_since_candidate == 9  # 9 bars after index 10 in a 20-bar series
    assert fx.atr_regime == 0.25  # 1.0 / 4.0
    assert fx.higher_degree_zone_match == 0.0


def test_feature_extractor_rejects_empty_bars() -> None:
    import pytest

    pivot = PivotEvent(
        interval=Interval.DAILY,
        degree=1,
        timestamp=date(2025, 1, 1),
        price=Decimal("100"),
        type=PivotType.HIGH,
        state=PivotState.TENTATIVE,
        confirmed_at=None,
    )
    with pytest.raises(ValueError, match="empty"):
        FeatureExtractor().extract(
            candidate=pivot,
            point_in_time_bars=[],
            atr_at_candidate=Decimal("1.0"),
            required_move=Decimal("2.0"),
            higher_degree_zone_match=0.0,
        )


def test_extractor_accepts_top_count_kwarg_and_populates_fib_fields() -> None:
    from datetime import date, timedelta
    from decimal import Decimal

    from wave_alpha.domain import Bar, Interval
    from wave_alpha.elliott.models import WaveCount
    from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
    from wave_alpha.right_edge.extractor import FeatureExtractor

    def piv(d_off, price, t):
        return PivotEvent(
            interval=Interval.DAILY,
            degree=2,
            timestamp=date(2024, 1, 1) + timedelta(days=d_off),
            price=Decimal(price),
            type=t,
            state=PivotState.CONFIRMED,
            confirmed_at=date(2024, 1, 1) + timedelta(days=d_off + 1),
        )

    zigzag = WaveCount(
        pattern="zigzag",
        degree=2,
        pivots=[
            piv(0, "100", PivotType.HIGH),
            piv(4, "90", PivotType.LOW),
            piv(7, "95", PivotType.HIGH),
            piv(14, "85", PivotType.LOW),
        ],
    )
    bars = [
        Bar(
            timestamp=date(2024, 1, 1) + timedelta(days=i),
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=1000,
        )
        for i in range(40)
    ]
    candidate = zigzag.pivots[-1]
    f = FeatureExtractor().extract(
        candidate=candidate,
        point_in_time_bars=bars,
        atr_at_candidate=Decimal("2.0"),
        required_move=Decimal("4.0"),
        higher_degree_zone_match=0.0,
        top_count=zigzag,
    )
    assert f.fib_b_a_retrace == pytest.approx(0.5, abs=1e-6)
    assert f.fib_b_a_retrace_present == 1.0
    # Impulse fields zero
    assert f.fib_w2_w1_retrace == 0.0
    assert f.fib_w2_w1_retrace_present == 0.0
