from datetime import date, timedelta
from decimal import Decimal

import pytest

from wave_alpha.domain import Interval
from wave_alpha.elliott.fib_features import FibFeatures, confluence_score, fib_features
from wave_alpha.elliott.models import WaveCount
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def test_fib_features_has_expected_fields() -> None:
    f = FibFeatures(pattern="impulse", is_curated_pattern=True)
    # Defaults None for every ratio
    for name in (
        "w2_w1_retrace",
        "w3_w1_extension",
        "w4_w3_retrace",
        "w5_w1_ratio",
        "w5_thrust_extension",
        "b_a_retrace",
        "c_a_ratio",
    ):
        assert getattr(f, name) is None


def test_fib_features_is_frozen() -> None:
    f = FibFeatures(pattern="impulse", is_curated_pattern=True)
    with pytest.raises(AttributeError):
        f.pattern = "zigzag"  # type: ignore[misc]


def _piv(d_off: int, price: str, t: PivotType) -> PivotEvent:
    return PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=date(2024, 1, 1) + timedelta(days=d_off),
        price=Decimal(price),
        type=t,
        state=PivotState.CONFIRMED,
        confirmed_at=date(2024, 1, 1) + timedelta(days=d_off + 1),
    )


def _impulse_count() -> WaveCount:
    """Hand-built textbook impulse: W1=10, W2 retraces 0.5 of W1 = 5,
    W3 extends 1.618 of W1 = 16.18, W4 retraces 0.382 of W3 = ~6.18,
    W5 = 1.0 of W1 = 10."""
    return WaveCount(
        pattern="impulse",
        degree=2,
        pivots=[
            _piv(0, "100.00", PivotType.LOW),  # W0
            _piv(4, "110.00", PivotType.HIGH),  # end W1: |W1| = 10
            _piv(7, "105.00", PivotType.LOW),  # end W2: |W2| = 5  -> W2/W1 = 0.5
            _piv(14, "121.18", PivotType.HIGH),  # end W3: |W3| = 16.18 -> W3/W1 = 1.618
            _piv(17, "115.00", PivotType.LOW),  # end W4: |W4| = 6.18 -> W4/W3 = 0.382
            _piv(24, "125.00", PivotType.HIGH),  # end W5: |W5| = 10 -> W5/W1 = 1.0
        ],
    )


def test_fib_features_impulse_emits_five_ratios() -> None:
    f = fib_features(_impulse_count())
    assert f.pattern == "impulse"
    assert f.is_curated_pattern is True
    assert f.w2_w1_retrace == pytest.approx(0.5, abs=1e-6)
    assert f.w3_w1_extension == pytest.approx(1.618, abs=1e-6)
    assert f.w4_w3_retrace == pytest.approx(0.382, abs=1e-3)
    assert f.w5_w1_ratio == pytest.approx(1.0, abs=1e-6)
    # W5 thrust: |W5| / |W3.end - W1.start| = 10 / 21.18 = 0.4722...
    assert f.w5_thrust_extension == pytest.approx(10.0 / 21.18, abs=1e-3)
    # Zigzag fields are None on an impulse count
    assert f.b_a_retrace is None
    assert f.c_a_ratio is None


def _zigzag_count() -> WaveCount:
    """Textbook zigzag: A=10 down, B=5 retrace (0.5), C=10 (equality with A)."""
    return WaveCount(
        pattern="zigzag",
        degree=2,
        pivots=[
            _piv(0, "100.00", PivotType.HIGH),  # start A
            _piv(4, "90.00", PivotType.LOW),  # end A: |A| = 10
            _piv(7, "95.00", PivotType.HIGH),  # end B: |B| = 5  -> B/A = 0.5
            _piv(14, "85.00", PivotType.LOW),  # end C: |C| = 10 -> C/A = 1.0
        ],
    )


def test_fib_features_zigzag_emits_two_ratios() -> None:
    f = fib_features(_zigzag_count())
    assert f.pattern == "zigzag"
    assert f.is_curated_pattern is True
    assert f.b_a_retrace == pytest.approx(0.5, abs=1e-6)
    assert f.c_a_ratio == pytest.approx(1.0, abs=1e-6)
    # Impulse fields are None on a zigzag count
    assert f.w2_w1_retrace is None
    assert f.w3_w1_extension is None


def test_fib_features_flat_is_not_curated() -> None:
    flat = WaveCount(
        pattern="flat",
        degree=2,
        pivots=[
            _piv(0, "100", PivotType.HIGH),
            _piv(4, "90", PivotType.LOW),
            _piv(7, "100", PivotType.HIGH),
            _piv(14, "92", PivotType.LOW),
        ],
    )
    f = fib_features(flat)
    assert f.pattern == "flat"
    assert f.is_curated_pattern is False
    # No ratios populated for non-curated patterns
    for name in (
        "w2_w1_retrace",
        "w3_w1_extension",
        "w4_w3_retrace",
        "w5_w1_ratio",
        "w5_thrust_extension",
        "b_a_retrace",
        "c_a_ratio",
    ):
        assert getattr(f, name) is None


def test_fib_features_handles_short_impulse_defensively() -> None:
    # Only 3 pivots -> 2 segments -> not enough for impulse 5-ratio extraction.
    short = WaveCount(
        pattern="impulse",
        degree=2,
        pivots=[
            _piv(0, "100", PivotType.LOW),
            _piv(4, "110", PivotType.HIGH),
            _piv(7, "105", PivotType.LOW),
        ],
    )
    f = fib_features(short)
    assert f.pattern == "impulse"
    assert f.is_curated_pattern is True
    assert f.w2_w1_retrace is None  # not enough segments


def test_confluence_score_textbook_impulse_is_one() -> None:
    """Every ratio sits at the midpoint of its ideal band -> score 1.0."""
    f = fib_features(_impulse_count())
    assert confluence_score(f) == pytest.approx(1.0, abs=1e-3)


def test_confluence_score_textbook_zigzag_is_one() -> None:
    f = fib_features(_zigzag_count())
    # B:A = 0.5 (in ideal [0.5, 0.618]), C:A = 1.0 (in ideal [1.0, 1.272])
    assert confluence_score(f) == pytest.approx(1.0, abs=1e-3)


def test_confluence_score_off_textbook_impulse_is_low() -> None:
    """Several ratios outside acceptable bands drag the score below 0.5.

    A single off-textbook ratio (e.g., only W2 out of band) leaves the
    redistributed score around 0.7-0.8 — band weights are roughly even.
    Driving the score below 0.5 takes 3+ ratios outside their acceptable
    bands, so the fixture is constructed accordingly.
    """
    bad = WaveCount(
        pattern="impulse",
        degree=2,
        pivots=[
            _piv(0, "100.00", PivotType.LOW),
            _piv(4, "110.00", PivotType.HIGH),
            _piv(7, "100.50", PivotType.LOW),  # W2/W1 = 0.95 (out of 0.236-0.786)
            _piv(14, "105.00", PivotType.HIGH),  # W3/W1 = 0.45 (out of 1.0-2.618)
            _piv(17, "102.00", PivotType.LOW),  # W4/W3 = 0.667 (out of 0.146-0.5)
            _piv(24, "104.00", PivotType.HIGH),  # W5/W1 = 0.2 (out of 0.5-1.618)
        ],
    )
    f = fib_features(bad)
    # 4 of 5 ratios out of acceptable; only w5_thrust_extension stays inside.
    assert f.w2_w1_retrace is not None and f.w2_w1_retrace > 0.9
    assert confluence_score(f) < 0.5


def test_confluence_score_non_curated_is_zero() -> None:
    flat = WaveCount(
        pattern="flat",
        degree=2,
        pivots=[
            _piv(0, "100", PivotType.HIGH),
            _piv(4, "90", PivotType.LOW),
            _piv(7, "100", PivotType.HIGH),
            _piv(14, "92", PivotType.LOW),
        ],
    )
    assert confluence_score(fib_features(flat)) == 0.0


def test_confluence_score_redistributes_weight_when_ratio_missing() -> None:
    """If a ratio is None (e.g., short impulse), the remaining ratios cover
    the full pattern budget — max possible score stays 1.0."""
    short = WaveCount(
        pattern="impulse",
        degree=2,
        pivots=[
            _piv(0, "100.00", PivotType.LOW),
            _piv(4, "110.00", PivotType.HIGH),
            _piv(7, "105.00", PivotType.LOW),
        ],
    )
    f = fib_features(short)
    # Manually set just w2_w1_retrace at midpoint of ideal so we can assert
    # redistribution makes the single populated ratio carry full weight.
    f = FibFeatures(pattern=f.pattern, is_curated_pattern=True, w2_w1_retrace=0.5)
    assert confluence_score(f) == pytest.approx(1.0, abs=1e-9)
