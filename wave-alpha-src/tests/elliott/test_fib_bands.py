import pytest

from wave_alpha.elliott.fib_bands import (
    CURATED_PATTERNS,
    IMPULSE_BANDS,
    ZIGZAG_BANDS,
    Band,
    trapezoid,
)


def test_band_has_expected_fields() -> None:
    b = Band(ideal_lo=0.382, ideal_hi=0.618, acceptable_lo=0.236, acceptable_hi=0.786, weight=0.06)
    assert b.ideal_lo == 0.382
    assert b.weight == 0.06


def test_impulse_bands_cover_five_canonical_ratios() -> None:
    assert set(IMPULSE_BANDS.keys()) == {
        "w2_w1_retrace",
        "w3_w1_extension",
        "w4_w3_retrace",
        "w5_w1_ratio",
        "w5_thrust_extension",
    }


def test_zigzag_bands_cover_two_canonical_ratios() -> None:
    assert set(ZIGZAG_BANDS.keys()) == {"b_a_retrace", "c_a_ratio"}


def test_impulse_band_weights_sum_to_confluence_budget() -> None:
    assert sum(b.weight for b in IMPULSE_BANDS.values()) == pytest.approx(0.20, abs=1e-9)


def test_zigzag_band_weights_sum_to_confluence_budget() -> None:
    assert sum(b.weight for b in ZIGZAG_BANDS.values()) == pytest.approx(0.20, abs=1e-9)


def test_curated_patterns_set() -> None:
    assert frozenset({"impulse", "zigzag"}) == CURATED_PATTERNS


def test_band_ordering_invariant() -> None:
    """For every band: acc_lo <= ideal_lo <= ideal_hi <= acc_hi."""
    for name, b in {**IMPULSE_BANDS, **ZIGZAG_BANDS}.items():
        assert b.acceptable_lo <= b.ideal_lo <= b.ideal_hi <= b.acceptable_hi, name


W2_BAND = IMPULSE_BANDS["w2_w1_retrace"]


def test_trapezoid_inside_ideal_band_is_one() -> None:
    assert trapezoid(0.5, W2_BAND) == 1.0
    assert trapezoid(W2_BAND.ideal_lo, W2_BAND) == 1.0
    assert trapezoid(W2_BAND.ideal_hi, W2_BAND) == 1.0


def test_trapezoid_at_acceptable_boundaries_is_zero() -> None:
    assert trapezoid(W2_BAND.acceptable_lo, W2_BAND) == 0.0
    assert trapezoid(W2_BAND.acceptable_hi, W2_BAND) == 0.0


def test_trapezoid_outside_acceptable_is_zero() -> None:
    assert trapezoid(0.0, W2_BAND) == 0.0
    assert trapezoid(1.5, W2_BAND) == 0.0
    assert trapezoid(-0.5, W2_BAND) == 0.0


def test_trapezoid_lower_ramp_is_linear() -> None:
    midpoint = (W2_BAND.acceptable_lo + W2_BAND.ideal_lo) / 2
    assert trapezoid(midpoint, W2_BAND) == pytest.approx(0.5, abs=1e-9)


def test_trapezoid_upper_decay_is_linear() -> None:
    midpoint = (W2_BAND.ideal_hi + W2_BAND.acceptable_hi) / 2
    assert trapezoid(midpoint, W2_BAND) == pytest.approx(0.5, abs=1e-9)
