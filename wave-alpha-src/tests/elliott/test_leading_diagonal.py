from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.domain import Interval
from wave_alpha.elliott.enumerator import enumerate_counts
from wave_alpha.elliott.templates import PatternTemplate, load_bundled_templates
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def test_leading_diagonal_template_loads_from_bundled_dir():
    tpl = PatternTemplate.load_bundled("leading_diagonal")
    assert tpl.name == "leading_diagonal"
    assert tpl.waves == ["1", "2", "3", "4", "5"]
    assert tpl.direction_alternates is True


def test_leading_diagonal_appears_in_load_bundled_templates():
    names = {t.name for t in load_bundled_templates()}
    assert "leading_diagonal" in names


def test_leading_diagonal_constraint_set():
    tpl = PatternTemplate.load_bundled("leading_diagonal")
    names = {c.name for c in tpl.constraints}
    assert names == {
        "wave_3_not_shortest",
        "wave_2_max_retrace",
        "wave_2_min_retrace",
        "wave_4_overlaps_wave_1",
        "wave_2_deep_retrace",
        "wave_2_not_excessive",
        "wave_3_not_extended",
    }


def test_leading_diagonal_severity_split():
    tpl = PatternTemplate.load_bundled("leading_diagonal")
    sev = {c.name: c.severity for c in tpl.constraints}
    assert sev["wave_3_not_shortest"] == "hard"
    assert sev["wave_2_max_retrace"] == "hard"
    assert sev["wave_2_min_retrace"] == "hard"
    assert sev["wave_4_overlaps_wave_1"] == "hard"
    assert sev["wave_2_deep_retrace"] == "soft"
    assert sev["wave_2_not_excessive"] == "soft"
    assert sev["wave_3_not_extended"] == "soft"


def test_leading_diagonal_sub_pattern_allowlist():
    tpl = PatternTemplate.load_bundled("leading_diagonal")
    odd = ["zigzag", "impulse"]
    even = ["zigzag", "flat"]
    assert tpl.allowed_sub_patterns["1"] == odd
    assert tpl.allowed_sub_patterns["2"] == even
    assert tpl.allowed_sub_patterns["3"] == odd
    assert tpl.allowed_sub_patterns["4"] == even
    assert tpl.allowed_sub_patterns["5"] == odd


def _pivot(day: int, price: str, kind: PivotType, *, degree: int = 2) -> PivotEvent:
    """Build a degree-N PivotEvent at 2026-01-01 + ``day`` with the given price."""
    return PivotEvent(
        interval=Interval.DAILY,
        degree=degree,
        timestamp=date(2026, 1, 1) + timedelta(days=day),
        price=Decimal(price),
        type=kind,
        state=PivotState.CONFIRMED,
        confirmed_at=None,
    )


def _ld_only_templates():
    """Return a [leading_diagonal] template list for restricted enumeration."""
    return [PatternTemplate.load_bundled("leading_diagonal")]


def test_ld_hard_rejects_w3_shortest():
    """W3 strictly shorter than min(W1, W5) - the hard wave_3_not_shortest
    constraint must fire and the engine must not emit an LD candidate."""
    pivots = [
        _pivot(0, "100", PivotType.LOW),
        _pivot(1, "120", PivotType.HIGH),
        _pivot(2, "110", PivotType.LOW),
        _pivot(3, "115", PivotType.HIGH),
        _pivot(4, "108", PivotType.LOW),
        _pivot(5, "133", PivotType.HIGH),
    ]
    counts = enumerate_counts(pivots, degree=2, templates=_ld_only_templates(), top_k=5)
    assert all(c.pattern != "leading_diagonal" for c in counts), (
        f"LD should be rejected on W3<min(W1,W5); got: {[c.pattern for c in counts]}"
    )


def test_ld_hard_rejects_w2_full_retrace():
    """W2 retrace >= 1.0 - the hard wave_2_max_retrace must fire."""
    pivots = [
        _pivot(0, "100", PivotType.LOW),
        _pivot(1, "120", PivotType.HIGH),
        _pivot(2, "100", PivotType.LOW),
        _pivot(3, "130", PivotType.HIGH),
        _pivot(4, "115", PivotType.LOW),
        _pivot(5, "140", PivotType.HIGH),
    ]
    counts = enumerate_counts(pivots, degree=2, templates=_ld_only_templates(), top_k=5)
    assert all(c.pattern != "leading_diagonal" for c in counts), (
        f"LD should be rejected on full W2 retrace; got: {[c.pattern for c in counts]}"
    )


def test_ld_hard_rejects_w2_too_shallow():
    """W2 retrace < 0.3 - the hard wave_2_min_retrace must fire.
    A shallow W2 belongs to an impulse with a tiny pullback, not an LD.
    This is the fail-fast filter added during the v1 iteration to keep
    LD out of the top-K on non-diagonal windows."""
    # W1: 100 -> 120, length=20
    # W2: 120 -> 115, retrace=5/20=0.25 (< 0.3 -> HARD FAIL)
    # W3-W5 shaped so the only hard constraint that can fire is the new one.
    pivots = [
        _pivot(0, "100", PivotType.LOW),
        _pivot(1, "120", PivotType.HIGH),
        _pivot(2, "115", PivotType.LOW),
        _pivot(3, "130", PivotType.HIGH),
        _pivot(4, "118", PivotType.LOW),
        _pivot(5, "135", PivotType.HIGH),
    ]
    counts = enumerate_counts(pivots, degree=2, templates=_ld_only_templates(), top_k=5)
    assert all(c.pattern != "leading_diagonal" for c in counts), (
        f"LD should be rejected on W2 retrace < 0.3; got: {[c.pattern for c in counts]}"
    )


def test_ld_hard_rejects_when_w4_does_not_overlap_w1():
    """The wave_4_overlaps_wave_1 constraint is now hard (promoted from
    soft during the v1 iteration). A 5-wave motive window without W4
    overlap is structurally an impulse, not an LD; LD must be rejected."""
    # W1: 100 -> 120, length=20
    # W2: 120 -> 110, retrace=0.5 (passes min and max)
    # W3: 110 -> 135, length=25 (>= min(W1,W5))
    # W4: 135 -> 125, range [125, 135] - no overlap with W1 range [100, 120]
    # W5: 125 -> 145
    pivots = [
        _pivot(0, "100", PivotType.LOW),
        _pivot(1, "120", PivotType.HIGH),
        _pivot(2, "110", PivotType.LOW),
        _pivot(3, "135", PivotType.HIGH),
        _pivot(4, "125", PivotType.LOW),
        _pivot(5, "145", PivotType.HIGH),
    ]
    counts = enumerate_counts(pivots, degree=2, templates=_ld_only_templates(), top_k=5)
    assert all(c.pattern != "leading_diagonal" for c in counts), (
        f"LD should be rejected when W4 does not overlap W1; got: {[c.pattern for c in counts]}"
    )


def _ld_and_ed_templates():
    return [
        PatternTemplate.load_bundled("leading_diagonal"),
        PatternTemplate.load_bundled("ending_diagonal"),
    ]


def _score_for(counts, pattern_name: str) -> float | None:
    for c in counts:
        if c.pattern == pattern_name:
            return c.score
    return None


def test_ld_outscores_ed_on_deep_w2():
    """W2 retrace approx 0.7 should trigger LD's wave_2_deep_retrace soft
    AND avoid wave_2_not_excessive; ED has no such soft, so LD scores higher."""
    pivots = [
        _pivot(0, "100", PivotType.LOW),
        _pivot(1, "120", PivotType.HIGH),
        _pivot(2, "106", PivotType.LOW),
        _pivot(3, "128", PivotType.HIGH),
        _pivot(4, "112", PivotType.LOW),
        _pivot(5, "130", PivotType.HIGH),
    ]
    counts = enumerate_counts(pivots, degree=2, templates=_ld_and_ed_templates(), top_k=5)
    ld_score = _score_for(counts, "leading_diagonal")
    ed_score = _score_for(counts, "ending_diagonal")
    assert ld_score is not None, f"LD missing from {[c.pattern for c in counts]}"
    assert ed_score is not None, f"ED missing from {[c.pattern for c in counts]}"
    assert ld_score > ed_score, (
        f"deep-W2 window: LD must outscore ED. got LD={ld_score:.4f} ED={ed_score:.4f}"
    )


def test_ed_outscores_ld_on_shallow_w2():
    """W2 retrace approx 0.38 fires NEITHER of LD's deep-W2 softs, so LD
    accrues two extra soft violations vs. ED -> ED scores higher."""
    pivots = [
        _pivot(0, "100", PivotType.LOW),
        _pivot(1, "120", PivotType.HIGH),
        _pivot(2, "112.4", PivotType.LOW),
        _pivot(3, "128", PivotType.HIGH),
        _pivot(4, "118", PivotType.LOW),
        _pivot(5, "132", PivotType.HIGH),
    ]
    counts = enumerate_counts(pivots, degree=2, templates=_ld_and_ed_templates(), top_k=5)
    ld_score = _score_for(counts, "leading_diagonal")
    ed_score = _score_for(counts, "ending_diagonal")
    assert ld_score is not None, f"LD missing from {[c.pattern for c in counts]}"
    assert ed_score is not None, f"ED missing from {[c.pattern for c in counts]}"
    assert ed_score > ld_score, (
        f"shallow-W2 window: ED must outscore LD. got LD={ld_score:.4f} ED={ed_score:.4f}"
    )


def test_ld_dedupes_per_template_across_windows():
    """Multiple LD-eligible windows in a longer pivot list collapse to the
    highest-scoring one before the global top-K cut. Mirrors existing
    best_per_template behavior; this test exists so future enumerator
    changes that break dedupe also fail here."""
    pivots = [
        _pivot(0, "100", PivotType.LOW),
        _pivot(1, "120", PivotType.HIGH),
        _pivot(2, "106", PivotType.LOW),
        _pivot(3, "128", PivotType.HIGH),
        _pivot(4, "112", PivotType.LOW),
        _pivot(5, "130", PivotType.HIGH),
        _pivot(6, "118", PivotType.LOW),
        _pivot(7, "135", PivotType.HIGH),
    ]
    counts = enumerate_counts(pivots, degree=2, templates=_ld_only_templates(), top_k=5)
    ld_counts = [c for c in counts if c.pattern == "leading_diagonal"]
    assert len(ld_counts) == 1, f"per-template dedupe broken: got {len(ld_counts)} LD entries"


def _ld_impulse_ed_templates():
    return [
        PatternTemplate.load_bundled("leading_diagonal"),
        PatternTemplate.load_bundled("impulse"),
        PatternTemplate.load_bundled("ending_diagonal"),
    ]


def test_ld_survives_where_impulse_dies_on_w4_overlap():
    """A 5-wave window where W4 overlaps W1: impulse's hard wave_4_no_overlap
    rejects it, but LD and ED do not. The enumerated output must include
    leading_diagonal and exclude impulse."""
    pivots = [
        _pivot(0, "100", PivotType.LOW),
        _pivot(1, "120", PivotType.HIGH),
        _pivot(2, "106", PivotType.LOW),
        _pivot(3, "128", PivotType.HIGH),
        _pivot(4, "112", PivotType.LOW),
        _pivot(5, "130", PivotType.HIGH),
    ]
    counts = enumerate_counts(pivots, degree=2, templates=_ld_impulse_ed_templates(), top_k=5)
    patterns = {c.pattern for c in counts}
    assert "leading_diagonal" in patterns, f"LD missing from {patterns}"
    assert "impulse" not in patterns, f"impulse should be rejected on W4 overlap; got: {patterns}"
