"""Tests for the enumerator constraint-eval cache optimization.

See docs/superpowers/specs/2026-05-11-enumerator-eval-cache-design.md
for the design.
"""

import ast
from datetime import date
from decimal import Decimal

from wave_alpha.domain import Interval
from wave_alpha.elliott.dsl import evaluate, evaluate_parsed
from wave_alpha.elliott.enumerator import enumerate_counts
from wave_alpha.elliott.models import WaveCount, WaveSegment
from wave_alpha.elliott.templates import Constraint, PatternTemplate
from wave_alpha.elliott.validator import validate
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def test_constraint_parsed_caches_ast():
    """Constraint.parsed must return the same AST instance on repeat access.

    @cached_property guarantees one parse per Constraint instance per
    process. Locks the §3.1 contract.
    """
    c = Constraint(
        name="t",
        severity="soft",
        expr="length(W1) > Decimal('0')",
    )
    first = c.parsed
    second = c.parsed
    assert first is second, "Constraint.parsed must cache the parsed AST"
    assert isinstance(first, ast.Expression)


def test_constraint_parsed_for_each_bundled_template():
    """Every bundled constraint expression parses without raising.

    Catches a YAML edit that introduces a syntax error in `expr`.
    """
    from wave_alpha.elliott.templates import load_bundled_templates

    for tpl in load_bundled_templates():
        for c in tpl.constraints:
            tree = c.parsed
            assert isinstance(tree, ast.Expression), (
                f"{tpl.name}/{c.name}: parse did not produce ast.Expression"
            )


def _seg(start_day: int, start_price: str, end_day: int, end_price: str) -> WaveSegment:
    """Build a tiny WaveSegment between two pivots for DSL tests."""

    def pivot(day: int, price: str) -> PivotEvent:
        return PivotEvent(
            interval=Interval.DAILY,
            degree=2,
            timestamp=date(2026, 1, 1).replace(day=1 + day),
            price=Decimal(price),
            type=PivotType.HIGH if Decimal(price) > Decimal("100") else PivotType.LOW,
            state=PivotState.CONFIRMED,
            confirmed_at=None,
        )

    return WaveSegment(start=pivot(start_day, start_price), end=pivot(end_day, end_price))


def test_evaluate_parsed_matches_evaluate():
    """For any (expr, bindings), evaluate_parsed(parsed_tree, bindings) must
    return the same (ok, diagnostic) as evaluate(expr, bindings)."""
    seg1 = _seg(0, "100", 5, "120")
    seg2 = _seg(5, "120", 10, "108")
    bindings = {"W1": seg1, "W2": seg2}

    for expr in [
        "length(W1) > Decimal('10')",
        "retrace(W2, W1) <= Decimal('0.99')",
        "length(W1) >= min(length(W1), length(W2))",
    ]:
        ok_s, _ = evaluate(expr, bindings=bindings)
        import ast as _ast

        tree = _ast.parse(expr, mode="eval")
        ok_p, _ = evaluate_parsed(tree, bindings=bindings)
        assert ok_s == ok_p, f"divergence on {expr!r}: evaluate={ok_s} evaluate_parsed={ok_p}"


def test_evaluate_parsed_handles_dsl_error():
    """A disallowed AST node (attribute access) must surface as
    (False, 'dsl error: ...')."""
    import ast as _ast

    tree = _ast.parse("W1.length", mode="eval")
    ok, diag = evaluate_parsed(tree, bindings={"W1": _seg(0, "100", 5, "120")})
    assert ok is False
    assert "dsl error" in diag


def _make_window_pivots() -> list[PivotEvent]:
    """A 6-pivot 5-wave LD-shape window. Both impulse-overlap-failure
    and LD-overlap-pass apply here so the validator runs over a
    realistic set of constraints."""
    days_prices = [
        (0, "100", PivotType.LOW),
        (1, "120", PivotType.HIGH),
        (2, "106", PivotType.LOW),
        (3, "128", PivotType.HIGH),
        (4, "112", PivotType.LOW),
        (5, "130", PivotType.HIGH),
    ]
    return [
        PivotEvent(
            interval=Interval.DAILY,
            degree=2,
            timestamp=date(2026, 1, 1).replace(day=1 + d),
            price=Decimal(p),
            type=t,
            state=PivotState.CONFIRMED,
            confirmed_at=None,
        )
        for d, p, t in days_prices
    ]


def test_validate_without_eval_cache_unchanged():
    """Calling validate() without eval_cache preserves the pre-cache
    semantics: every constraint evaluated freshly."""
    pivots = _make_window_pivots()
    impulse = PatternTemplate.load_bundled("impulse")
    count = WaveCount(pattern="impulse", degree=2, pivots=pivots)

    validated = validate(count, impulse)  # no eval_cache kwarg
    # impulse hard wave_4_no_overlap fires on this LD-shape window
    # (W4 from 128 to 112 overlaps W1 from 100 to 120).
    assert "wave_4_no_overlap" in validated.hard_violations


def test_validate_populates_eval_cache():
    """When given an empty eval_cache, validate populates it with
    one entry per constraint expression."""
    pivots = _make_window_pivots()
    impulse = PatternTemplate.load_bundled("impulse")
    count = WaveCount(pattern="impulse", degree=2, pivots=pivots)
    cache: dict[str, bool] = {}

    validate(count, impulse, eval_cache=cache)
    expected_exprs = {c.expr for c in impulse.constraints}
    assert set(cache.keys()) == expected_exprs


def test_validate_reuses_eval_cache_across_templates():
    """A pre-populated cache with a shared-expr result is reused — the
    underlying evaluator is not called again for that expression."""
    pivots = _make_window_pivots()
    impulse = PatternTemplate.load_bundled("impulse")
    leading = PatternTemplate.load_bundled("leading_diagonal")
    count_imp = WaveCount(pattern="impulse", degree=2, pivots=pivots)
    count_ld = WaveCount(pattern="leading_diagonal", degree=2, pivots=pivots)
    cache: dict[str, bool] = {}

    validate(count_imp, impulse, eval_cache=cache)
    keys_after_impulse = set(cache.keys())

    # impulse and LD share wave_3_not_shortest with identical expr text.
    shared_expr = "length(W3) >= min(length(W1), length(W5))"
    assert shared_expr in keys_after_impulse

    # Now monkey-patch evaluate_parsed to count invocations during LD's pass.
    import wave_alpha.elliott.validator as validator_mod

    call_log: list[str] = []
    original = validator_mod.evaluate_parsed

    def counting(tree, bindings):
        # Find back to expr via ast.unparse for a stable log entry.
        import ast as _ast

        call_log.append(_ast.unparse(tree))
        return original(tree, bindings)

    validator_mod.evaluate_parsed = counting
    try:
        validate(count_ld, leading, eval_cache=cache)
    finally:
        validator_mod.evaluate_parsed = original

    # The shared constraint must NOT appear in the call log — it was
    # served from cache.
    unparsed_shared = "length(W3) >= min(length(W1), length(W5))"
    assert unparsed_shared not in call_log, (
        f"shared constraint should have been cached; was re-evaluated. call_log={call_log}"
    )
    # LD's W2-overlap-related new constraints should have been evaluated.
    assert any("retrace(W2, W1)" in entry for entry in call_log)


def test_eval_cache_dedup_5wave_motive_window(monkeypatch):
    """On a single 6-pivot window with [impulse, leading_diagonal,
    ending_diagonal] templates, the shared expression
    'length(W3) >= min(length(W1), length(W5))' is evaluated EXACTLY
    ONCE — once for impulse, then served from cache for LD and ED."""
    import wave_alpha.elliott.validator as validator_mod

    call_log: list[str] = []
    original = validator_mod.evaluate_parsed

    def counting(tree, bindings):
        import ast as _ast

        call_log.append(_ast.unparse(tree))
        return original(tree, bindings)

    monkeypatch.setattr(validator_mod, "evaluate_parsed", counting)

    pivots = _make_window_pivots()
    templates = [
        PatternTemplate.load_bundled("impulse"),
        PatternTemplate.load_bundled("leading_diagonal"),
        PatternTemplate.load_bundled("ending_diagonal"),
    ]
    enumerate_counts(pivots, degree=2, templates=templates, top_k=5)

    shared = "length(W3) >= min(length(W1), length(W5))"
    n_shared_calls = sum(1 for entry in call_log if entry == shared)
    assert n_shared_calls == 1, (
        f"shared constraint should evaluate once per window across templates; "
        f"got {n_shared_calls} evaluations. call_log={call_log}"
    )


def test_eval_cache_isolated_per_window(monkeypatch):
    """A constraint evaluated on window A must NOT serve a cached result
    on window B (caches reset per window)."""
    import wave_alpha.elliott.validator as validator_mod

    call_log: list[str] = []
    original = validator_mod.evaluate_parsed

    def counting(tree, bindings):
        import ast as _ast

        call_log.append(_ast.unparse(tree))
        return original(tree, bindings)

    monkeypatch.setattr(validator_mod, "evaluate_parsed", counting)

    # 8 pivots so enumerate_counts iterates 3 different 6-pivot windows
    # (end_idx = 8, 7, 6). The shared constraint must appear at least 3
    # times in the call log — once per distinct window.
    days_prices = [
        (0, "100", PivotType.LOW),
        (1, "120", PivotType.HIGH),
        (2, "106", PivotType.LOW),
        (3, "128", PivotType.HIGH),
        (4, "112", PivotType.LOW),
        (5, "130", PivotType.HIGH),
        (6, "118", PivotType.LOW),
        (7, "135", PivotType.HIGH),
    ]
    pivots = [
        PivotEvent(
            interval=Interval.DAILY,
            degree=2,
            timestamp=date(2026, 1, 1).replace(day=1 + d),
            price=Decimal(p),
            type=t,
            state=PivotState.CONFIRMED,
            confirmed_at=None,
        )
        for d, p, t in days_prices
    ]
    impulse = PatternTemplate.load_bundled("impulse")
    enumerate_counts(pivots, degree=2, templates=[impulse], top_k=10)

    shared = "length(W3) >= min(length(W1), length(W5))"
    n_shared = sum(1 for entry in call_log if entry == shared)
    assert n_shared >= 3, (
        f"shared constraint should evaluate at least once per distinct "
        f"window; got {n_shared}. call_log={call_log}"
    )


def test_eval_cache_isolated_per_wave_count_group(monkeypatch):
    """A 3-wave corrective template (e.g., zigzag) does not share a cache
    with a 5-wave motive template (e.g., impulse). They iterate
    different window sizes; their per-window caches are independent."""
    import wave_alpha.elliott.validator as validator_mod

    call_log: list[str] = []
    original = validator_mod.evaluate_parsed

    def counting(tree, bindings):
        import ast as _ast

        call_log.append(_ast.unparse(tree))
        return original(tree, bindings)

    monkeypatch.setattr(validator_mod, "evaluate_parsed", counting)

    days_prices = [
        (0, "100", PivotType.LOW),
        (1, "120", PivotType.HIGH),
        (2, "106", PivotType.LOW),
        (3, "128", PivotType.HIGH),
        (4, "112", PivotType.LOW),
        (5, "130", PivotType.HIGH),
        (6, "118", PivotType.LOW),
        (7, "135", PivotType.HIGH),
    ]
    pivots = [
        PivotEvent(
            interval=Interval.DAILY,
            degree=2,
            timestamp=date(2026, 1, 1).replace(day=1 + d),
            price=Decimal(p),
            type=t,
            state=PivotState.CONFIRMED,
            confirmed_at=None,
        )
        for d, p, t in days_prices
    ]
    impulse = PatternTemplate.load_bundled("impulse")
    zigzag = PatternTemplate.load_bundled("zigzag")
    enumerate_counts(pivots, degree=2, templates=[impulse, zigzag], top_k=10)

    # Both templates' constraint exprs should appear in the log. The
    # cache is per-window, so impulse's length(W1) does not satisfy
    # zigzag's length(WA).
    assert any("W1" in entry for entry in call_log), "impulse constraints absent"
    assert any("WA" in entry for entry in call_log), "zigzag constraints absent"
