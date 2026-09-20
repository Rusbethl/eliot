from __future__ import annotations

from wave_alpha.elliott.dsl import evaluate, evaluate_parsed
from wave_alpha.elliott.models import WaveCount, WaveSegment
from wave_alpha.elliott.templates import PatternTemplate


def validate(
    count: WaveCount,
    template: PatternTemplate,
    *,
    eval_cache: dict[str, bool] | None = None,
) -> WaveCount:
    """Run all template constraints and return a copy with violation
    lists populated.

    ``eval_cache`` (when provided) memoizes constraint-expression results
    keyed by ``c.expr``. The caller owns the cache lifetime — typically
    one cache per enumeration window, shared across templates that
    iterate the same window with identical wave-label bindings. See
    spec §3.3. Default ``None`` preserves the pre-cache semantics for
    external callers that don't supply one.
    """
    bindings = _segment_bindings(count, template)
    hard: list[str] = []
    soft: list[str] = []
    for c in template.constraints:
        if eval_cache is not None and c.expr in eval_cache:
            ok = eval_cache[c.expr]
        else:
            ok, _ = evaluate_parsed(c.parsed, bindings=bindings)
            if eval_cache is not None:
                eval_cache[c.expr] = ok
        if not ok:
            (hard if c.severity == "hard" else soft).append(c.name)
    return count.model_copy(update={"hard_violations": hard, "soft_violations": soft})


def _segment_bindings(count: WaveCount, template: PatternTemplate) -> dict[str, WaveSegment]:
    segs = count.segments()
    bindings: dict[str, WaveSegment] = {}
    for label, seg in zip(template.waves, segs, strict=False):
        bindings[f"W{label}"] = seg
    return bindings


# Re-export ``evaluate`` for any external callers that imported it via
# ``wave_alpha.elliott.validator``. Internal callers use evaluate_parsed.
__all__ = ["evaluate", "validate"]
