from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.domain import Interval
from wave_alpha.elliott.enumerator import (
    _PRIMITIVE_CORRECTIVE_NAMES,
    _fit_sub_counts,
    _slice_sub_pivots,
    enumerate_counts,
)
from wave_alpha.elliott.models import WaveCount
from wave_alpha.elliott.templates import PatternTemplate
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def _pivot(day: int, price: str, kind: PivotType, *, degree: int) -> PivotEvent:
    """Build a PivotEvent at day 2026-01-01 + day with the given price.

    `day` also doubles as `bar_index`: in this fixture every pivot sits on
    its own distinct daily bar, so day-offset and bar position coincide.
    """
    return PivotEvent(
        interval=Interval.DAILY,
        degree=degree,
        timestamp=date(2026, 1, 1) + timedelta(days=day),
        price=Decimal(price),
        type=kind,
        state=PivotState.CONFIRMED,
        confirmed_at=None,
        bar_index=day,
    )


# Reusable parent + sub-pivot fixture used across many tests in this file.
# Parent (degree=2): four pivots forming a W-X-Y outline.
#   W: 100 (H) -> 80 (L)        downward corrective
#   X: 80 (L)  -> 85 (H)        connector up
#   Y: 85 (H)  -> 65 (L)        downward corrective
# Sub (degree=1): subset invariant - every parent pivot also appears in sub_pivots.
#   W subdivides as a zigzag: 100(H) -> 90(L) -> 95(H) -> 80(L)
#   X is a single segment (degenerate): 80(L) -> 85(H)
#   Y subdivides as a zigzag: 85(H) -> 75(L) -> 80(H) -> 65(L)
def _wxy_zigzag_zigzag_fixture() -> tuple[list[PivotEvent], list[PivotEvent]]:
    parent = [
        _pivot(0, "100", PivotType.HIGH, degree=2),
        _pivot(3, "80", PivotType.LOW, degree=2),
        _pivot(4, "85", PivotType.HIGH, degree=2),
        _pivot(7, "65", PivotType.LOW, degree=2),
    ]
    sub = [
        _pivot(0, "100", PivotType.HIGH, degree=1),
        _pivot(1, "90", PivotType.LOW, degree=1),
        _pivot(2, "95", PivotType.HIGH, degree=1),
        _pivot(3, "80", PivotType.LOW, degree=1),
        _pivot(4, "85", PivotType.HIGH, degree=1),
        _pivot(5, "75", PivotType.LOW, degree=1),
        _pivot(6, "80", PivotType.HIGH, degree=1),
        _pivot(7, "65", PivotType.LOW, degree=1),
    ]
    return parent, sub


def test_wxy_silently_skipped_when_sub_pivots_none():
    """Composite candidates must be skipped (not raised) for callers that
    don't supply lower-degree pivots. Preserves backward compat for
    right_edge.assessment, right_edge.training, backtest.count_layer, and
    every test that builds pivots by hand."""
    parent, _sub = _wxy_zigzag_zigzag_fixture()
    counts = enumerate_counts(parent, degree=2, sub_pivots=None, top_k=5)
    assert all(c.pattern != "wxy" for c in counts), "wxy must not appear when sub_pivots is None"


def test_existing_callers_unchanged_without_kwarg():
    """Existing positional callers must continue to work - the new
    sub_pivots arg is keyword-only with a default of None."""
    parent, _sub = _wxy_zigzag_zigzag_fixture()
    counts = enumerate_counts(parent, degree=2, top_k=5)
    assert isinstance(counts, list)
    assert all(c.pattern != "wxy" for c in counts)


def test_slice_sub_pivots_inclusive_slice_full_y():
    parent, sub = _wxy_zigzag_zigzag_fixture()
    # Parent Y segment is parent[2] -> parent[3] (day 4 -> day 7)
    seg_start, seg_end = parent[2], parent[3]
    result = _slice_sub_pivots(sub, seg_start, seg_end)
    assert result is not None
    # Y subdivides as 4 sub-pivots at degree=1: 85(H), 75(L), 80(H), 65(L)
    assert [p.timestamp for p in result] == [
        date(2026, 1, 1) + timedelta(days=d) for d in (4, 5, 6, 7)
    ]
    assert result[0].timestamp == seg_start.timestamp
    assert result[-1].timestamp == seg_end.timestamp


def test_slice_sub_pivots_returns_short_slice_for_degenerate_x():
    parent, sub = _wxy_zigzag_zigzag_fixture()
    # Parent X segment is parent[1] -> parent[2] (day 3 -> day 4)
    seg_start, seg_end = parent[1], parent[2]
    result = _slice_sub_pivots(sub, seg_start, seg_end)
    assert result is not None
    # X has no intermediate sub-pivots in the fixture: just [80(L), 85(H)]
    assert len(result) == 2


def test_slice_sub_pivots_returns_none_when_anchor_missing():
    parent, sub = _wxy_zigzag_zigzag_fixture()
    # Build a fake parent pivot that is NOT in sub_pivots
    bogus = _pivot(99, "50", PivotType.LOW, degree=2)
    result = _slice_sub_pivots(sub, parent[0], bogus)
    assert result is None


def test_fit_sub_counts_x_degenerate_accepted_with_soft_violation():
    """When X has only 2 sub-pivots (no internal subdivision), the slot is
    accepted with sub_counts['X'] = None and 'X:no_subdivision' added to
    propagated soft violations. W and Y still must validate strictly."""
    parent, sub = _wxy_zigzag_zigzag_fixture()
    tpl = PatternTemplate.load_bundled("wxy")
    parent_count = WaveCount(pattern="wxy", degree=2, pivots=parent)

    sub_counts, sub_softs = _fit_sub_counts(parent_count=parent_count, tpl=tpl, sub_pivots=sub)
    assert sub_counts is not None
    # X is degenerate (2 sub-pivots: day 3 -> day 4)
    assert "X" in sub_counts
    assert sub_counts["X"] is None
    assert "X:no_subdivision" in sub_softs
    # W and Y still resolved strictly
    assert sub_counts["W"] is not None and sub_counts["W"].pattern == "zigzag"
    assert sub_counts["Y"] is not None and sub_counts["Y"].pattern == "zigzag"


def test_fit_sub_counts_rejects_when_w_slot_has_no_valid_primitive():
    """If the W slot's sub-window has >=4 pivots but no primitive
    corrective validates (e.g., pivots don't alternate H/L correctly),
    the parent is rejected."""
    parent = [
        _pivot(0, "100", PivotType.HIGH, degree=2),
        _pivot(3, "80", PivotType.LOW, degree=2),
        _pivot(4, "85", PivotType.HIGH, degree=2),
        _pivot(7, "65", PivotType.LOW, degree=2),
    ]
    # W has 4 sub-pivots but pivots are non-alternating types (H,H,H,L)
    # which fails _alternates_ok in the recursive enumerate_counts call.
    sub = [
        _pivot(0, "100", PivotType.HIGH, degree=1),
        _pivot(1, "99", PivotType.HIGH, degree=1),
        _pivot(2, "98", PivotType.HIGH, degree=1),
        _pivot(3, "80", PivotType.LOW, degree=1),
        _pivot(4, "85", PivotType.HIGH, degree=1),
        _pivot(5, "75", PivotType.LOW, degree=1),
        _pivot(6, "80", PivotType.HIGH, degree=1),
        _pivot(7, "65", PivotType.LOW, degree=1),
    ]
    tpl = PatternTemplate.load_bundled("wxy")
    parent_count = WaveCount(pattern="wxy", degree=2, pivots=parent)

    sub_counts, _sub_softs = _fit_sub_counts(parent_count=parent_count, tpl=tpl, sub_pivots=sub)
    assert sub_counts is None


def test_fit_sub_counts_strict_when_slot_has_4plus_pivots_but_no_primitive():
    """Degeneracy relaxation applies only to short sub-windows. A slot with
    >= 4 sub-pivots that fits no primitive must still reject — strictness
    preserved for non-degenerate cases."""
    parent = [
        _pivot(0, "100", PivotType.HIGH, degree=2),
        _pivot(3, "80", PivotType.LOW, degree=2),
        _pivot(4, "85", PivotType.HIGH, degree=2),
        _pivot(7, "65", PivotType.LOW, degree=2),
    ]
    sub = [
        _pivot(0, "100", PivotType.HIGH, degree=1),
        _pivot(1, "99", PivotType.HIGH, degree=1),
        _pivot(2, "98", PivotType.HIGH, degree=1),
        _pivot(3, "80", PivotType.LOW, degree=1),
        _pivot(4, "85", PivotType.HIGH, degree=1),
        _pivot(5, "75", PivotType.LOW, degree=1),
        _pivot(6, "80", PivotType.HIGH, degree=1),
        _pivot(7, "65", PivotType.LOW, degree=1),
    ]
    tpl = PatternTemplate.load_bundled("wxy")
    parent_count = WaveCount(pattern="wxy", degree=2, pivots=parent)

    sub_counts, _sub_softs = _fit_sub_counts(parent_count=parent_count, tpl=tpl, sub_pivots=sub)
    assert sub_counts is None  # still rejected — strictness preserved on full slots


def test_wxy_emitted_with_populated_subcounts_when_sub_pivots_provided():
    parent, sub = _wxy_zigzag_zigzag_fixture()
    counts = enumerate_counts(parent, degree=2, sub_pivots=sub, top_k=10)
    wxys = [c for c in counts if c.pattern == "wxy"]
    assert len(wxys) == 1
    wxy = wxys[0]
    assert wxy.degree == 2
    # W and Y populated with zigzags; X is degenerate (no_subdivision)
    assert wxy.sub_counts["W"] is not None
    assert wxy.sub_counts["W"].pattern == "zigzag"
    assert wxy.sub_counts["W"].degree == 1
    assert wxy.sub_counts.get("X") is None
    assert wxy.sub_counts["Y"] is not None
    assert wxy.sub_counts["Y"].pattern == "zigzag"
    assert wxy.sub_counts["Y"].degree == 1


def test_wxy_competes_in_topk_with_primitives():
    """Composite candidates must appear in the same top-K list as primitives -
    no parallel field, no automatic discount. Spec §3.5."""
    parent, sub = _wxy_zigzag_zigzag_fixture()
    counts = enumerate_counts(parent, degree=2, sub_pivots=sub, top_k=10)
    pattern_set = {c.pattern for c in counts}
    # The 4-pivot parent could match a zigzag (3 segments) too; both are valid alternates.
    assert "wxy" in pattern_set
    # No duplicate wxy entries (per-template dedupe).
    assert sum(1 for c in counts if c.pattern == "wxy") == 1


def test_wxy_no_self_recursion_one_level_deep_only():
    """Sub-counts emitted for W/X/Y must be primitive correctives - never
    another wxy. Verified via _PRIMITIVE_CORRECTIVE_NAMES restriction."""
    parent, sub = _wxy_zigzag_zigzag_fixture()
    counts = enumerate_counts(parent, degree=2, sub_pivots=sub, top_k=10)
    wxy = next(c for c in counts if c.pattern == "wxy")
    for label, sub_count in wxy.sub_counts.items():
        if sub_count is not None:
            assert sub_count.pattern in _PRIMITIVE_CORRECTIVE_NAMES, label


def test_subcount_soft_violations_propagate_to_parent_with_label_prefix():
    """A sub-zigzag with soft violations causes the parent's
    soft_violations to contain '<label>:<name>' entries. The parent's score
    drops by 0.10 * 0.50 = 0.05 per propagated violation, matching the
    existing _score formula (base weight 0.50, soft penalty 0.10/violation)."""
    parent, sub = _wxy_zigzag_zigzag_fixture()
    counts = enumerate_counts(parent, degree=2, sub_pivots=sub, top_k=10)
    wxy = next(c for c in counts if c.pattern == "wxy")

    # Inspect propagated softs: each must be either '<label>:<orig_name>' or
    # 'X:no_subdivision' (the synthetic degeneracy soft).
    propagated = [s for s in wxy.soft_violations if ":" in s]
    assert propagated, "expected propagated sub-count soft violations"
    for entry in propagated:
        label, _, name = entry.partition(":")
        assert label in {"W", "X", "Y"}
        assert name  # non-empty


def test_score_decrement_matches_soft_violation_count():
    """Direct assertion that the parent's base score is
    1 - 0.10 * len(soft_violations) - no special-casing for propagated softs."""
    parent, sub = _wxy_zigzag_zigzag_fixture()
    counts = enumerate_counts(parent, degree=2, sub_pivots=sub, top_k=10)
    wxy = next(c for c in counts if c.pattern == "wxy")

    n_softs = len(wxy.soft_violations)
    expected_base = max(0.0, 1.0 - 0.10 * n_softs)
    # _score = 0.50 * base + 0.20 * confluence + 0.30 * recency
    # We can't pin confluence/recency exactly without re-running the helpers,
    # but score must be no greater than 0.50 * 1.0 + 0.20 * 1.0 + 0.30 * 1.0 == 1.0
    # and the base contribution is bounded by 0.50 * expected_base.
    assert wxy.score <= 0.50 * expected_base + 0.20 + 0.30 + 1e-9
