"""Canonical Fibonacci bands per (pattern, wave-pair).

Bands are expert-curated EW values, not user config — kept as Python constants
so they version-control alongside the math that consumes them. Per-pattern
weights sum to 0.20, matching the existing confluence weight in
``elliott/enumerator.WEIGHT_CONFLUENCE``."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Band:
    """Trapezoidal scoring band for a single Fib ratio.

    The trapezoid is 1.0 inside ``[ideal_lo, ideal_hi]``, ramps linearly from
    0 at ``acceptable_lo`` to 1 at ``ideal_lo`` and from 1 at ``ideal_hi``
    back to 0 at ``acceptable_hi``, and is 0 outside the acceptable range.

    ``weight`` is this ratio's share of the per-pattern confluence budget
    (which sums to 0.20 — same as the legacy single-ratio weight).
    """

    ideal_lo: float
    ideal_hi: float
    acceptable_lo: float
    acceptable_hi: float
    weight: float


IMPULSE_BANDS: dict[str, Band] = {
    "w2_w1_retrace": Band(0.382, 0.618, 0.236, 0.786, 0.06),
    "w3_w1_extension": Band(1.382, 2.000, 1.000, 2.618, 0.06),
    "w4_w3_retrace": Band(0.236, 0.382, 0.146, 0.500, 0.04),
    "w5_w1_ratio": Band(0.618, 1.382, 0.500, 1.618, 0.02),
    "w5_thrust_extension": Band(0.382, 0.618, 0.236, 1.000, 0.02),
}

ZIGZAG_BANDS: dict[str, Band] = {
    "b_a_retrace": Band(0.500, 0.618, 0.382, 0.786, 0.12),
    "c_a_ratio": Band(1.000, 1.272, 0.618, 1.618, 0.08),
}

CURATED_PATTERNS: frozenset[str] = frozenset({"impulse", "zigzag"})


def trapezoid(value: float, band: Band) -> float:
    """0..1 score for ``value`` against a trapezoidal band.

    1.0 inside ``[band.ideal_lo, band.ideal_hi]``; linear ramp from 0 at
    ``band.acceptable_lo`` to 1 at ``band.ideal_lo``; linear decay from 1 at
    ``band.ideal_hi`` to 0 at ``band.acceptable_hi``; 0 outside the
    acceptable range. Degenerate band edges (e.g., ideal_lo == acceptable_lo)
    collapse the affected ramp; the implementation handles this without
    division-by-zero.
    """
    if value < band.acceptable_lo or value > band.acceptable_hi:
        return 0.0
    if band.ideal_lo <= value <= band.ideal_hi:
        return 1.0
    if value < band.ideal_lo:
        denom = band.ideal_lo - band.acceptable_lo
        return 0.0 if denom <= 0 else (value - band.acceptable_lo) / denom
    # value > band.ideal_hi
    denom = band.acceptable_hi - band.ideal_hi
    return 0.0 if denom <= 0 else (band.acceptable_hi - value) / denom
