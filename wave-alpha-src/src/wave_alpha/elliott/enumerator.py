from __future__ import annotations

from itertools import pairwise

from wave_alpha.elliott.fib import fib_confluence_score
from wave_alpha.elliott.fib_features import confluence_score, fib_features
from wave_alpha.elliott.models import WaveCount
from wave_alpha.elliott.templates import (
    PatternTemplate,
    load_bundled_templates,
    load_default_templates,
)
from wave_alpha.elliott.validator import validate
from wave_alpha.pivots.models import PivotEvent

# Score weights — must sum to 1.0. Public so engine_ranker.heuristic
# can stay in lockstep with the enumerator's hand-tuned formula.
WEIGHT_BASE = 0.50  # template fit (1 - 0.10 * soft violations)
WEIGHT_CONFLUENCE = 0.20  # W2-vs-W1 fib confluence
WEIGHT_RECENCY = 0.30  # how close the candidate's last pivot is to "now"

# Composite templates require sub_pivots and recurse into degree N-1 to fit
# primitive corrective sub-counts. See spec 2026-05-10-complex-corrections-design.md §3.
_COMPOSITE_TEMPLATE_NAMES: frozenset[str] = frozenset({"wxy"})

# Primitive corrective templates eligible to fill a composite slot (W, X, Y).
# Excludes impulse and ending_diagonal (motive structures, semantically wrong
# inside a corrective combination).
_PRIMITIVE_CORRECTIVE_NAMES: frozenset[str] = frozenset(
    {"zigzag", "flat", "expanded_flat", "contracting_triangle"}
)


def enumerate_counts(
    pivots: list[PivotEvent],
    *,
    degree: int,
    top_k: int = 5,
    templates: list[PatternTemplate] | None = None,
    sub_pivots: list[PivotEvent] | None = None,
    enabled_opt_in_templates: frozenset[str] = frozenset(),
) -> list[WaveCount]:
    """Try each template against pivot windows; return top_k by score.

    A swing-trader-friendly ranker: each candidate's score blends template fit,
    fib confluence at W2's retrace, and recency (how close the candidate's last
    pivot is to the latest input pivot). Multiple windows of the same template
    are deduplicated to the highest-scoring one before the global top_k cut, so
    one template family cannot crowd out alternates.

    Composite templates (those whose name is in _COMPOSITE_TEMPLATE_NAMES) are
    skipped when ``sub_pivots`` is None — graceful degrade for callers that
    operate on a single degree (right_edge feature extractor, count-layer
    backtest, hand-built test fixtures).

    Opt-in templates (``OPT_IN_TEMPLATE_NAMES`` in ``elliott.templates``) are
    excluded from the default template set unless their names appear in
    ``enabled_opt_in_templates``. Production callers pass the names from
    ``AppConfig.templates.enabled_opt_in_names()``. Callers that supply
    ``templates`` explicitly bypass this filter — the explicit list is used
    verbatim.

    Implementation: window-major loop grouped by
    ``(n_required, tuple(waves))`` so templates that share a window slice
    AND bind identical wave labels can share a per-window constraint-eval
    cache. See spec 2026-05-11-enumerator-eval-cache-design.md.
    """
    if not pivots:
        return []
    templates = templates or load_default_templates(enabled_opt_in=enabled_opt_in_templates)
    latest_idx = len(pivots) - 1

    # Group templates by (n_required, tuple(waves)) so the per-window
    # cache only sees templates with identical wave-label bindings.
    groups: dict[tuple[int, tuple[str, ...]], list[PatternTemplate]] = {}
    for tpl in templates:
        if tpl.name in _COMPOSITE_TEMPLATE_NAMES and sub_pivots is None:
            continue  # composite needs lower-degree pivots; graceful degrade
        key = (len(tpl.waves) + 1, tuple(tpl.waves))
        groups.setdefault(key, []).append(tpl)

    best_per_template: dict[str, WaveCount] = {}
    for (n_required, _labels), group_templates in groups.items():
        if len(pivots) < n_required:
            continue
        for end_idx in range(len(pivots), n_required - 1, -1):
            window = pivots[end_idx - n_required : end_idx]
            if not _alternates_ok(window):
                continue
            eval_cache: dict[str, bool] = {}  # fresh per window
            for tpl in group_templates:
                count = WaveCount(pattern=tpl.name, degree=degree, pivots=window)
                count = validate(count, tpl, eval_cache=eval_cache)
                if count.hard_violations:
                    continue
                if tpl.name in _COMPOSITE_TEMPLATE_NAMES:
                    assert sub_pivots is not None  # gated above
                    sub_counts, sub_softs = _fit_sub_counts(
                        parent_count=count, tpl=tpl, sub_pivots=sub_pivots
                    )
                    if sub_counts is None:
                        continue  # sub-count fitting rejected the parent
                    count = count.model_copy(
                        update={
                            "sub_counts": {k: v for k, v in sub_counts.items() if v is not None},
                            "soft_violations": count.soft_violations + sub_softs,
                        }
                    )
                base = 1.0 - 0.10 * len(count.soft_violations)
                confluence = _confluence_bonus(count, tpl)
                recency = _recency_factor(last_window_idx=end_idx - 1, latest_idx=latest_idx)
                raw = WEIGHT_BASE * base + WEIGHT_CONFLUENCE * confluence + WEIGHT_RECENCY * recency
                score = max(0.0, raw)
                count = count.model_copy(
                    update={
                        "score": score,
                        "template_fit_component": base,
                        "fib_component": confluence,
                        "recency_component": recency,
                    }
                )

                current = best_per_template.get(tpl.name)
                if current is None or score > current.score:
                    best_per_template[tpl.name] = count

    candidates = sorted(best_per_template.values(), key=lambda c: c.score, reverse=True)
    return candidates[:top_k]


def _alternates_ok(window: list[PivotEvent]) -> bool:
    types = [p.type for p in window]
    return all(a != b for a, b in pairwise(types))


def _confluence_bonus(count: WaveCount, tpl: PatternTemplate) -> float:
    """Pattern-aware Fib confluence with generic fallback.

    Curated patterns (impulse, zigzag) use the multi-ratio
    ``fib_features.confluence_score``. Other patterns fall back to the
    legacy single-ratio ``fib_confluence_score(W2, reference=W1)`` so their
    scores stay stable until they're promoted to curated.
    """
    feats = fib_features(count)
    if feats.is_curated_pattern:
        return confluence_score(feats)
    segs = count.segments()
    if len(segs) < 2:
        return 0.0
    return fib_confluence_score(target_price=segs[1].end.price, reference=segs[0])


def _recency_factor(*, last_window_idx: int, latest_idx: int) -> float:
    """1.0 when the window ends at the most recent pivot; decays linearly to 0.2
    over a 16-pivot gap. Old patterns aren't useless, but a swing trader needs
    actionable now-counts ranked above twenty-year-old setups."""
    if latest_idx <= 0:
        return 1.0
    gap = max(0, latest_idx - last_window_idx)
    return max(0.2, 1.0 - 0.05 * gap)


def _slice_sub_pivots(
    sub_pivots: list[PivotEvent],
    seg_start: PivotEvent,
    seg_end: PivotEvent,
) -> list[PivotEvent] | None:
    """Return the inclusive slice of ``sub_pivots`` between two parent
    boundary pivots, matched by ``(bar_index, type)``.

    The multi_degree subset invariant guarantees that every degree-N parent
    pivot is also present in degree-(N-1) sub_pivots with the same
    ``(bar_index, type)``. bar_index (not timestamp) is the identity key
    because both degrees run over the identical bars list — timestamp alone
    collides for sub-daily intervals (e.g. HOURLY_4 on a 24/7 market has
    several bars per calendar date, since Bar.timestamp only carries a
    date), which would silently merge two distinct same-day bars here. A
    None return indicates a corrupted pipeline state (e.g. caller passed
    sub_pivots from a different bar set); the parent composite candidate
    should be rejected.
    """
    start_key = (seg_start.bar_index, seg_start.type)
    end_key = (seg_end.bar_index, seg_end.type)
    start_idx: int | None = None
    end_idx: int | None = None
    for i, p in enumerate(sub_pivots):
        key = (p.bar_index, p.type)
        if key == start_key and start_idx is None:
            start_idx = i
        elif key == end_key and start_idx is not None:
            end_idx = i
            break
    if start_idx is None or end_idx is None:
        return None
    return sub_pivots[start_idx : end_idx + 1]


def _fit_sub_counts(
    parent_count: WaveCount,
    tpl: PatternTemplate,
    sub_pivots: list[PivotEvent],
) -> tuple[dict[str, WaveCount | None] | None, list[str]]:
    """Fit a primitive corrective sub-count into each label slot of a
    composite template.

    Returns ``(sub_counts, prefixed_soft_violations)``. A None first element
    signals rejection (a slot's sub-window failed to produce a valid primitive
    sub-count, or boundary alignment failed).

    A degenerate slot (sub-window of < 4 pivots, i.e. fewer than three
    segments — common for the connector X-wave) is accepted with
    ``sub_counts[label] = None`` and a ``f"{label}:no_subdivision"`` soft
    violation. Spec §3.3 step 3.
    """
    primitive_templates = [
        t for t in load_bundled_templates() if t.name in _PRIMITIVE_CORRECTIVE_NAMES
    ]

    sub_counts: dict[str, WaveCount | None] = {}
    prefixed_softs: list[str] = []
    for label, seg in zip(tpl.waves, parent_count.segments(), strict=True):
        sub_window = _slice_sub_pivots(sub_pivots, seg.start, seg.end)
        if sub_window is None:
            return None, []  # corrupted pipeline state — reject
        if len(sub_window) < 4:
            # Degenerate slot: no primitive corrective fits in fewer than 4
            # sub-pivots (3 segments). Common for the connector X-wave.
            # Accept the slot with sub_counts[label] = None and emit a
            # synthetic soft violation. Spec §3.3 step 3.
            sub_counts[label] = None
            prefixed_softs.append(f"{label}:no_subdivision")
            continue
        candidates = enumerate_counts(
            sub_window,
            degree=parent_count.degree - 1,
            templates=primitive_templates,
            top_k=1,
            sub_pivots=None,  # one level deep only
        )
        spanning = [
            c
            for c in candidates
            if c.pivots[0].bar_index == seg.start.bar_index
            and c.pivots[0].type == seg.start.type
            and c.pivots[-1].bar_index == seg.end.bar_index
            and c.pivots[-1].type == seg.end.type
        ]
        if not spanning:
            return None, []
        chosen = spanning[0]
        allowed = tpl.allowed_sub_patterns.get(label, [])
        if chosen.pattern not in allowed:
            return None, []
        sub_counts[label] = chosen
        for soft in chosen.soft_violations:
            prefixed_softs.append(f"{label}:{soft}")
    return sub_counts, prefixed_softs
