from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from wave_alpha.elliott.models import WaveCount

_IMPULSE_FAMILY = {"impulse", "leading_diagonal", "ending_diagonal"}
_CORRECTIVE_FAMILY = {"zigzag", "flat", "expanded_flat", "contracting_triangle"}


def _family(name: str) -> str:
    if name in _IMPULSE_FAMILY:
        return "impulse"
    if name in _CORRECTIVE_FAMILY:
        return "corrective"
    return "unknown"


def predicted_direction(c: WaveCount) -> str | None:
    """The direction of the most recent segment indicates the current trend.

    Public helper — used by coherence scoring and by downstream consumers
    (scan orchestrator, backtest count layer) that need to derive a "this
    count is bullish/bearish" label from a WaveCount.
    """
    segs = c.segments()
    if not segs:
        return None
    return "up" if segs[-1].is_up else "down"


def coherence_score(a: WaveCount, b: WaveCount) -> float:
    """Pairwise coherence between two counts at different intervals.

    Spec divergence (tracked as v0.2 follow-up): the design doc §7 lists FOUR
    independent dimensions — family (0.4), nesting (0.3), direction (0.2),
    pivot drift (0.1). v0.1 collapses nesting+direction onto one boolean (same
    predicted next direction → +0.5), because actual sub-wave nesting check
    isn't implemented yet (the enumerator emits flat counts). Two counts that
    point the same direction without truly nesting therefore get +0.5 instead
    of +0.2. Spec and implementation will reconverge once nested counts ship.
    """
    score = 0.0
    if _family(a.pattern) == _family(b.pattern):
        score += 0.4
    if predicted_direction(a) == predicted_direction(b):
        score += 0.3 + 0.2  # direction + nesting proxy collapsed (see docstring)
    if a.pivots and b.pivots:
        drift = abs((a.pivots[-1].timestamp - b.pivots[-1].timestamp).days)
        if drift <= 5:
            score += 0.1
    return min(1.0, score)


def coherence_multiplier(score: float) -> float:
    """Map [0, 1] coherence to [0.5, 1.2] multiplier."""
    return 0.5 + 0.7 * max(0.0, min(1.0, score))


class CoherenceInputs(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    weekly: WaveCount | None
    daily: WaveCount | None
    hourly4: WaveCount | None


@dataclass
class CoherenceResult:
    score: float
    multiplier: float
    pairwise: dict[str, float]
    fallback_used: str | None


def three_way(inputs: CoherenceInputs) -> CoherenceResult:
    pairwise: dict[str, float] = {}
    fallback: str | None = None

    if inputs.weekly is None or inputs.daily is None:
        return CoherenceResult(
            score=0.5,
            multiplier=coherence_multiplier(0.5),
            pairwise={},
            fallback_used="insufficient_history",
        )

    pairwise["weekly_daily"] = coherence_score(inputs.weekly, inputs.daily)

    if inputs.hourly4 is None:
        fallback = "no_4h_history"
        score = pairwise["weekly_daily"]
    else:
        pairwise["daily_4h"] = coherence_score(inputs.daily, inputs.hourly4)
        pairwise["weekly_4h"] = coherence_score(inputs.weekly, inputs.hourly4)
        score = (
            0.5 * pairwise["weekly_daily"]
            + 0.35 * pairwise["daily_4h"]
            + 0.15 * pairwise["weekly_4h"]
        )

    return CoherenceResult(
        score=score,
        multiplier=coherence_multiplier(score),
        pairwise=pairwise,
        fallback_used=fallback,
    )
