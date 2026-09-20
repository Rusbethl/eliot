from datetime import date, timedelta
from decimal import Decimal

import pytest

from wave_alpha.domain import Bar, Interval
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
from wave_alpha.right_edge.features import extract_features


def _bar(i: int, c: float) -> Bar:
    cd = Decimal(f"{c:.4f}")
    return Bar(
        timestamp=date(2024, 1, 1) + timedelta(days=i),
        open=cd,
        high=cd + Decimal("0.50"),
        low=cd - Decimal("0.50"),
        close=cd,
        volume=1_000_000 - i * 1000,
    )


def test_features_capture_retracement_progress() -> None:
    bars = [_bar(i, 100 + i) for i in range(30)]  # rising
    bars.extend(_bar(30 + i, 130 - i * 0.5) for i in range(6))  # pull back from 130
    candidate = PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=bars[29].timestamp,
        price=bars[29].close,
        type=PivotType.HIGH,
        state=PivotState.TENTATIVE,
    )
    feats = extract_features(
        candidate=candidate,
        bars=bars,
        atr_at_candidate=Decimal("1.0"),
        required_move=Decimal("5.0"),
    )
    assert 0.0 <= feats.retracement_progress <= 1.0
    assert feats.bars_since_candidate == 6
    assert 0.0 <= feats.atr_regime <= 5.0
    # Dropped stub fields must not exist on the model
    assert not hasattr(feats, "fib_zone_match")
    assert not hasattr(feats, "rsi_divergence_strength")


def test_features_volume_decline_zero_when_history_too_short() -> None:
    """Regression: previously a too-short bar history silently produced a
    misleading vol_decline from overlapping recent/baseline slices."""
    bars = [_bar(i, 100 + i) for i in range(10)]  # 10 < recent (5) + baseline (20)
    candidate = PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=bars[-1].timestamp,
        price=bars[-1].close,
        type=PivotType.HIGH,
        state=PivotState.TENTATIVE,
    )
    feats = extract_features(
        candidate=candidate,
        bars=bars,
        atr_at_candidate=Decimal("1.0"),
        required_move=Decimal("5.0"),
    )
    assert feats.volume_decline_pct == 0.0


def test_right_edge_features_has_fib_fields_with_defaults() -> None:
    from wave_alpha.right_edge.features import RightEdgeFeatures

    f = RightEdgeFeatures(
        retracement_progress=0.0,
        bars_since_candidate=0,
        volume_decline_pct=0.0,
        higher_degree_zone_match=0.0,
        atr_regime=1.0,
    )
    # Raw ratios default to 0.0
    for name in (
        "fib_w2_w1_retrace",
        "fib_w3_w1_extension",
        "fib_w4_w3_retrace",
        "fib_w5_w1_ratio",
        "fib_w5_thrust_extension",
        "fib_b_a_retrace",
        "fib_c_a_ratio",
    ):
        assert getattr(f, name) == 0.0
    # Indicators default to 0.0 (absent)
    for name in (
        "fib_w2_w1_retrace_present",
        "fib_w3_w1_extension_present",
        "fib_w4_w3_retrace_present",
        "fib_w5_w1_ratio_present",
        "fib_w5_thrust_extension_present",
        "fib_b_a_retrace_present",
        "fib_c_a_ratio_present",
    ):
        assert getattr(f, name) == 0.0


def test_extract_features_with_impulse_top_count_populates_fib_fields() -> None:
    from datetime import date, timedelta
    from decimal import Decimal

    from wave_alpha.domain import Bar, Interval
    from wave_alpha.elliott.models import WaveCount
    from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
    from wave_alpha.right_edge.features import extract_features

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

    impulse = WaveCount(
        pattern="impulse",
        degree=2,
        pivots=[
            piv(0, "100.00", PivotType.LOW),
            piv(4, "110.00", PivotType.HIGH),
            piv(7, "105.00", PivotType.LOW),
            piv(14, "121.18", PivotType.HIGH),
            piv(17, "115.00", PivotType.LOW),
            piv(24, "125.00", PivotType.HIGH),
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
    candidate = impulse.pivots[-1]
    f = extract_features(
        candidate=candidate,
        bars=bars,
        atr_at_candidate=Decimal("2.0"),
        required_move=Decimal("4.0"),
        top_count=impulse,
    )
    assert f.fib_w2_w1_retrace == pytest.approx(0.5, abs=1e-6)
    assert f.fib_w2_w1_retrace_present == 1.0
    assert f.fib_b_a_retrace == 0.0  # not applicable to impulse
    assert f.fib_b_a_retrace_present == 0.0


def test_extract_features_without_top_count_keeps_fib_zero() -> None:
    from datetime import date, timedelta
    from decimal import Decimal

    from wave_alpha.domain import Bar, Interval
    from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
    from wave_alpha.right_edge.features import extract_features

    candidate = PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=date(2024, 1, 20),
        price=Decimal("100"),
        type=PivotType.HIGH,
        state=PivotState.TENTATIVE,
        confirmed_at=date(2024, 1, 20),
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
    f = extract_features(
        candidate=candidate,
        bars=bars,
        atr_at_candidate=Decimal("2.0"),
        required_move=Decimal("4.0"),
    )
    # All fib slots stay at default zeros; all indicators 0
    for name in (
        "fib_w2_w1_retrace",
        "fib_w2_w1_retrace_present",
        "fib_b_a_retrace",
        "fib_b_a_retrace_present",
    ):
        assert getattr(f, name) == 0.0
