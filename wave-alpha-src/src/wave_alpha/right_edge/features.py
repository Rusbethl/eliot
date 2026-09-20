from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from wave_alpha.domain import Bar
from wave_alpha.elliott.fib_features import fib_features
from wave_alpha.elliott.models import WaveCount
from wave_alpha.pivots.models import PivotEvent, PivotType


class RightEdgeFeatures(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Original 5 (legacy schema — old artifacts depend on these names)
    retracement_progress: float
    bars_since_candidate: int
    volume_decline_pct: float
    higher_degree_zone_match: float
    atr_regime: float

    # Fib raw ratios (Phase 2 — defaulted; populated when caller supplies a top count)
    fib_w2_w1_retrace: float = 0.0
    fib_w3_w1_extension: float = 0.0
    fib_w4_w3_retrace: float = 0.0
    fib_w5_w1_ratio: float = 0.0
    fib_w5_thrust_extension: float = 0.0
    fib_b_a_retrace: float = 0.0
    fib_c_a_ratio: float = 0.0

    # Fib presence indicators (1.0 if the corresponding ratio was populated by
    # the caller, else 0.0 — distinguishes "ratio not applicable" from "ratio
    # is exactly 0", which never happens for length ratios but the indicator
    # is necessary for ML semantics).
    fib_w2_w1_retrace_present: float = 0.0
    fib_w3_w1_extension_present: float = 0.0
    fib_w4_w3_retrace_present: float = 0.0
    fib_w5_w1_ratio_present: float = 0.0
    fib_w5_thrust_extension_present: float = 0.0
    fib_b_a_retrace_present: float = 0.0
    fib_c_a_ratio_present: float = 0.0


_FIB_FIELD_NAMES: tuple[str, ...] = (
    "w2_w1_retrace",
    "w3_w1_extension",
    "w4_w3_retrace",
    "w5_w1_ratio",
    "w5_thrust_extension",
    "b_a_retrace",
    "c_a_ratio",
)


def extract_features(
    *,
    candidate: PivotEvent,
    bars: list[Bar],
    atr_at_candidate: Decimal,
    required_move: Decimal,
    higher_degree_zone_match: float = 0.0,
    top_count: WaveCount | None = None,
) -> RightEdgeFeatures:
    """Compute features for a tentative pivot using only bars up through latest.

    Caller is responsible for ensuring ``bars`` is point-in-time correct.
    When ``top_count`` is provided, the seven Fib ratio fields and their
    presence indicators are populated from ``fib_features(top_count)``.
    Without ``top_count``, all fib fields remain at their default zero values.
    """
    if not bars:
        raise ValueError("bars cannot be empty")

    after = [b for b in bars if b.timestamp > candidate.timestamp]
    bars_since = len(after)

    if candidate.type is PivotType.HIGH:
        opposite_extreme = min((b.low for b in after), default=candidate.price)
        moved = max(Decimal(0), candidate.price - opposite_extreme)
    else:
        opposite_extreme = max((b.high for b in after), default=candidate.price)
        moved = max(Decimal(0), opposite_extreme - candidate.price)

    progress = float(min(Decimal(1), moved / required_move)) if required_move > 0 else 0.0

    vol_decline = _volume_decline(bars, recent_window=5, baseline_window=20)

    atr_regime = float(atr_at_candidate / required_move) if required_move > 0 else 1.0

    fib_kwargs = _fib_kwargs(top_count)

    return RightEdgeFeatures(
        retracement_progress=progress,
        bars_since_candidate=bars_since,
        volume_decline_pct=vol_decline,
        higher_degree_zone_match=higher_degree_zone_match,
        atr_regime=atr_regime,
        **fib_kwargs,
    )


def _fib_kwargs(top_count: WaveCount | None) -> dict[str, float]:
    """Translate a WaveCount's fib_features into 14 RightEdgeFeatures kwargs."""
    if top_count is None:
        return {}
    feats = fib_features(top_count)
    out: dict[str, float] = {}
    for name in _FIB_FIELD_NAMES:
        value = getattr(feats, name)
        if value is None:
            out[f"fib_{name}"] = 0.0
            out[f"fib_{name}_present"] = 0.0
        else:
            out[f"fib_{name}"] = float(value)
            out[f"fib_{name}_present"] = 1.0
    return out


def _avg_volume(bars: list[Bar]) -> Decimal:
    if not bars:
        return Decimal(0)
    return Decimal(sum(b.volume for b in bars)) / Decimal(len(bars))


def _volume_decline(bars: list[Bar], *, recent_window: int, baseline_window: int) -> float:
    """Volume decline ratio: (baseline_avg - recent_avg) / baseline_avg.

    Returns 0.0 when there isn't enough history (fewer than recent + baseline
    bars available) or when the baseline average is zero. The previous
    implementation silently took whatever Python's slice returned, which mixed
    overlapping slices for short inputs and produced misleading values.
    """
    if len(bars) < recent_window + baseline_window:
        return 0.0
    recent = bars[-recent_window:]
    baseline = bars[-(recent_window + baseline_window) : -recent_window]
    baseline_avg = _avg_volume(baseline)
    if baseline_avg == 0:
        return 0.0
    recent_avg = _avg_volume(recent)
    return float((baseline_avg - recent_avg) / baseline_avg)
