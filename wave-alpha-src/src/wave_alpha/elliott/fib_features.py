"""Pattern-aware Fibonacci feature extraction for wave counts.

Single source of truth for ratio computation: the engine score consumes a
weighted ``confluence_score``; the right-edge model consumes the raw ratios
from ``FibFeatures`` directly. Pure functions, no I/O."""

from __future__ import annotations

from dataclasses import dataclass

from wave_alpha.elliott.fib_bands import (
    CURATED_PATTERNS,
    IMPULSE_BANDS,
    ZIGZAG_BANDS,
    Band,
    trapezoid,
)
from wave_alpha.elliott.models import WaveCount


@dataclass(frozen=True)
class FibFeatures:
    """Named Fib ratios for a single wave count.

    Ratio fields are ``None`` when they don't apply to the count's pattern
    (e.g., zigzag counts have ``b_a_retrace`` set but ``w2_w1_retrace`` None).
    Consumers must distinguish ``None`` (not applicable) from ``0.0`` (a real
    ratio that happens to be zero, which never occurs for length ratios).
    """

    pattern: str
    is_curated_pattern: bool

    # Impulse ratios (None for non-impulse counts)
    w2_w1_retrace: float | None = None
    w3_w1_extension: float | None = None
    w4_w3_retrace: float | None = None
    w5_w1_ratio: float | None = None
    w5_thrust_extension: float | None = None

    # Zigzag ratios (None for non-zigzag counts)
    b_a_retrace: float | None = None
    c_a_ratio: float | None = None


def fib_features(count: WaveCount) -> FibFeatures:
    """Compute named Fib ratios for ``count``.

    Dispatches on ``count.pattern``; impulse counts emit 5 ratios, zigzag
    counts emit 2, all other patterns emit none (with
    ``is_curated_pattern=False``).
    """
    is_curated = count.pattern in CURATED_PATTERNS
    base = FibFeatures(pattern=count.pattern, is_curated_pattern=is_curated)
    if count.pattern == "impulse":
        return _impulse_features(count, base)
    if count.pattern == "zigzag":
        return _zigzag_features(count, base)
    return base


def _impulse_features(count: WaveCount, base: FibFeatures) -> FibFeatures:
    segs = count.segments()
    if len(segs) < 5:
        return base
    w1, w2, w3, w4, w5 = segs[:5]
    if w1.length == 0 or w3.length == 0:
        return base  # degenerate; cannot form ratios
    thrust = abs(w3.end.price - w1.start.price)  # W0 -> W3 peak
    return FibFeatures(
        pattern=base.pattern,
        is_curated_pattern=base.is_curated_pattern,
        w2_w1_retrace=float(w2.length / w1.length),
        w3_w1_extension=float(w3.length / w1.length),
        w4_w3_retrace=float(w4.length / w3.length),
        w5_w1_ratio=float(w5.length / w1.length),
        w5_thrust_extension=float(w5.length / thrust) if thrust > 0 else None,
    )


def _zigzag_features(count: WaveCount, base: FibFeatures) -> FibFeatures:
    segs = count.segments()
    if len(segs) < 3:
        return base
    a, b, c = segs[:3]
    if a.length == 0:
        return base
    return FibFeatures(
        pattern=base.pattern,
        is_curated_pattern=base.is_curated_pattern,
        b_a_retrace=float(b.length / a.length),
        c_a_ratio=float(c.length / a.length),
    )


def confluence_score(features: FibFeatures) -> float:
    """Weighted trapezoidal score for a ``FibFeatures``, normalized to 0..1.

    Returns 0 for non-curated patterns (caller falls back to legacy generic
    confluence). Also returns 0 for curated patterns with no populated
    ratios — the partial-count path that ``fib_features`` returns when a
    count has too few segments to form any ratio. Ratios that are ``None``
    are excluded from the sum AND from the weight denominator — i.e., a
    ratio's weight is redistributed across the populated ratios. A
    textbook count therefore scores 1.0 even when a single ratio is
    missing; missing data does not penalize the count.
    """
    bands = _bands_for(features.pattern)
    if not bands:
        return 0.0

    populated: list[tuple[str, float]] = []
    for name in bands:
        v = getattr(features, name)
        if v is not None:
            populated.append((name, v))
    if not populated:
        return 0.0

    total_weight = sum(bands[name].weight for name, _ in populated)
    if total_weight <= 0:
        return 0.0

    score = 0.0
    for name, value in populated:
        band = bands[name]
        score += (band.weight / total_weight) * trapezoid(value, band)
    return score


def _bands_for(pattern: str) -> dict[str, Band] | None:
    """Return the band definitions for a pattern, or None if not curated."""
    if pattern == "impulse":
        return IMPULSE_BANDS
    if pattern == "zigzag":
        return ZIGZAG_BANDS
    return None
