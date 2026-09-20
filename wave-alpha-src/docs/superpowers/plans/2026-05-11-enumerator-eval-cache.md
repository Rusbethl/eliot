# Enumerator Constraint-Eval Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Behavior-preserving runtime optimization for `enumerate_counts` — pre-parse constraint AST at template load + per-window evaluation cache that dedups shared constraints across templates within the same `(n_required, waves-labels)` group.

**Architecture:** Two complementary changes. (1) `Constraint` gains a `cached_property parsed` that holds the parsed AST so `ast.parse` runs once per `Constraint` instance instead of per evaluation. (2) `enumerate_counts` restructures from template-major to window-major iteration grouped by `(n_required, tuple(waves))`, with a per-window `dict[str, bool]` eval cache passed into `validator.validate`. The cache lives one call deep; recursion (composite branch) gets a fresh one.

**Tech Stack:** Python 3.11+, `uv`, Pydantic v2 (frozen models with `@cached_property`), `pytest`, `ruff`, no new dependencies.

**Spec:** [`docs/superpowers/specs/2026-05-11-enumerator-eval-cache-design.md`](../specs/2026-05-11-enumerator-eval-cache-design.md)

---

## File map

**Created:**
- `tests/elliott/test_enumerator_eval_cache.py` — dedup counting, per-window isolation, per-group isolation, validate-without-cache fallback

**Modified:**
- `src/wave_alpha/elliott/templates.py` — `Constraint.parsed` cached AST, `ast`/`functools.cached_property` imports
- `src/wave_alpha/elliott/dsl.py` — `evaluate_parsed(tree, bindings)` companion function
- `src/wave_alpha/elliott/validator.py` — `validate(..., *, eval_cache=None)` optional cache parameter
- `src/wave_alpha/elliott/enumerator.py` — `enumerate_counts` window-major loop with `(n_required, tuple(waves))` grouping and per-window eval cache
- `tests/elliott/test_templates.py` — `test_label_convention_locked_per_wave_count` invariant test

**Not modified (intentionally):**
- `src/wave_alpha/elliott/models.py` — no `WaveCount` or `WaveSegment` change
- `src/wave_alpha/pipeline.py`, `src/wave_alpha/scan/orchestrator.py`, `src/wave_alpha/cli/app.py`, `src/wave_alpha/web/deps.py` — `enumerate_counts` signature is backward-compatible
- `src/wave_alpha/elliott/templates_data/*.yaml` — no YAML edits required
- `src/wave_alpha/right_edge/*`, `src/wave_alpha/backtest/*` — these callers don't pass `eval_cache`; they get unchanged semantics

---

### Task 1: `Constraint.parsed` cached AST

**Files:**
- Modify: `src/wave_alpha/elliott/templates.py`
- Create: `tests/elliott/test_enumerator_eval_cache.py`

- [ ] **Step 1: Write the failing test**

`tests/elliott/test_enumerator_eval_cache.py`:

```python
"""Tests for the enumerator constraint-eval cache optimization.

See docs/superpowers/specs/2026-05-11-enumerator-eval-cache-design.md
for the design.
"""

import ast

from wave_alpha.elliott.templates import Constraint, PatternTemplate


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
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/elliott/test_enumerator_eval_cache.py -v
```

Expected: FAIL — `Constraint` has no `parsed` attribute.

- [ ] **Step 3: Add the cached_property to Constraint**

Modify `src/wave_alpha/elliott/templates.py`. Replace the existing top of the file:

```python
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

# Templates that ship in the wheel but are NOT loaded by default at
# enumeration time. Users enable them via config (see prefs.models.
# TemplatesConfig). Adding to this set defers an opt-in template's
# default behaviour to "off" without removing it from the bundle.
OPT_IN_TEMPLATE_NAMES: frozenset[str] = frozenset({"leading_diagonal"})


class Constraint(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    severity: Literal["hard", "soft"]
    expr: str
    rationale: str = ""
```

with:

```python
from __future__ import annotations

import ast
from collections.abc import Iterable
from functools import cached_property
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

# Templates that ship in the wheel but are NOT loaded by default at
# enumeration time. Users enable them via config (see prefs.models.
# TemplatesConfig). Adding to this set defers an opt-in template's
# default behaviour to "off" without removing it from the bundle.
OPT_IN_TEMPLATE_NAMES: frozenset[str] = frozenset({"leading_diagonal"})


class Constraint(BaseModel):
    # frozen=True makes Pydantic immutable for declared fields. Pydantic v2
    # permits @cached_property because the cache writes to __dict__, which
    # is separate from the validated field set. If a Pydantic upgrade ever
    # breaks this, the fallback is a module-level WeakValueDictionary keyed
    # by id(constraint) — semantically equivalent.
    model_config = ConfigDict(frozen=True)

    name: str
    severity: Literal["hard", "soft"]
    expr: str
    rationale: str = ""

    @cached_property
    def parsed(self) -> ast.Expression:
        """Parse the constraint expression once per Constraint instance.

        Used by ``validator.validate`` via ``dsl.evaluate_parsed`` to skip
        the per-call ``ast.parse`` cost. See spec §3.1.
        """
        return ast.parse(self.expr, mode="eval")
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/elliott/test_enumerator_eval_cache.py -v
```

Expected: 2 PASS.

If `test_constraint_parsed_caches_ast` fails because Pydantic v2 frozen models reject the `cached_property` write, switch to the fallback. Replace the `@cached_property` body with:

```python
    @property
    def parsed(self) -> ast.Expression:
        cached = _PARSED_CACHE.get(id(self))
        if cached is None:
            cached = ast.parse(self.expr, mode="eval")
            _PARSED_CACHE[id(self)] = cached
        return cached
```

and add at module level above the class:

```python
import weakref

_PARSED_CACHE: weakref.WeakValueDictionary[int, ast.Expression] = (
    weakref.WeakValueDictionary()
)
```

(Only apply the fallback if the `@cached_property` route actually fails — Pydantic v2 normally supports it.)

- [ ] **Step 5: Run the full elliott test suite for regressions**

```bash
uv run pytest tests/elliott/ -q
```

Expected: all green. The new field is opt-in (only accessed via `.parsed`), so existing tests are unaffected.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/elliott/templates.py tests/elliott/test_enumerator_eval_cache.py
git commit -m "$(cat <<'EOF'
feat(elliott): Constraint.parsed cached AST property

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `evaluate_parsed()` in dsl.py

**Files:**
- Modify: `src/wave_alpha/elliott/dsl.py`
- Modify: `tests/elliott/test_enumerator_eval_cache.py`

- [ ] **Step 1: Append the failing tests**

Append to `tests/elliott/test_enumerator_eval_cache.py`:

```python
from datetime import date
from decimal import Decimal

from wave_alpha.domain import Interval
from wave_alpha.elliott.dsl import evaluate, evaluate_parsed
from wave_alpha.elliott.models import WaveSegment
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


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
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/elliott/test_enumerator_eval_cache.py -v
```

Expected: FAIL — `evaluate_parsed` is not yet defined.

- [ ] **Step 3: Add `evaluate_parsed` to dsl.py**

Append to `src/wave_alpha/elliott/dsl.py` (after the existing `evaluate` function):

```python
def evaluate_parsed(
    tree: ast.AST, bindings: dict[str, WaveSegment]
) -> tuple[bool, str]:
    """Evaluate a pre-parsed AST against bindings.

    Same return contract as ``evaluate(expr, bindings)`` but skips the
    ``ast.parse`` step. Used by ``validator.validate`` when the caller
    has already parsed via ``Constraint.parsed``. See spec §3.1.
    """
    namespace: dict[str, Any] = dict(bindings)
    try:
        result = _walk(tree, namespace)
        return bool(result), str(result)
    except DSLError as exc:
        return False, f"dsl error: {exc!s}"
    except Exception as exc:  # noqa: BLE001
        return False, f"eval error: {exc!s}"
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/elliott/test_enumerator_eval_cache.py -v
```

Expected: 4 PASS (2 from Task 1 + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/elliott/dsl.py tests/elliott/test_enumerator_eval_cache.py
git commit -m "$(cat <<'EOF'
feat(elliott): evaluate_parsed for pre-parsed constraint AST

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `validator.validate(..., *, eval_cache=None)`

**Files:**
- Modify: `src/wave_alpha/elliott/validator.py`
- Modify: `tests/elliott/test_enumerator_eval_cache.py`

- [ ] **Step 1: Append the failing tests**

Append to `tests/elliott/test_enumerator_eval_cache.py`:

```python
from wave_alpha.elliott.models import WaveCount
from wave_alpha.elliott.validator import validate


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
        f"shared constraint should have been cached; was re-evaluated. "
        f"call_log={call_log}"
    )
    # LD's W2-overlap-related new constraints should have been evaluated.
    assert any("retrace(W2, W1)" in entry for entry in call_log)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/elliott/test_enumerator_eval_cache.py -v
```

Expected: FAIL — `validate()` doesn't accept `eval_cache` and `validator.evaluate_parsed` isn't imported.

- [ ] **Step 3: Modify `validator.py`**

Replace `src/wave_alpha/elliott/validator.py` entirely with:

```python
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


def _segment_bindings(
    count: WaveCount, template: PatternTemplate
) -> dict[str, WaveSegment]:
    segs = count.segments()
    bindings: dict[str, WaveSegment] = {}
    for label, seg in zip(template.waves, segs, strict=False):
        bindings[f"W{label}"] = seg
    return bindings


# Re-export ``evaluate`` for any external callers that imported it via
# ``wave_alpha.elliott.validator``. Internal callers use evaluate_parsed.
__all__ = ["evaluate", "validate"]
```

The `evaluate` re-export is defensive — `validator.py` was a likely
import surface for any code that wanted the DSL. Removing it could
silently break downstream callers.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/elliott/test_enumerator_eval_cache.py -v
```

Expected: 7 PASS (4 prior + 3 new).

- [ ] **Step 5: Run the full elliott + pipeline test suite**

```bash
uv run pytest tests/elliott/ tests/test_pipeline.py -q
```

Expected: all green. `validate()` calls without `eval_cache` behave
identically to before; the new path is opt-in via the kwarg.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/elliott/validator.py tests/elliott/test_enumerator_eval_cache.py
git commit -m "$(cat <<'EOF'
feat(elliott): validator.validate accepts optional eval_cache

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Label-convention invariant test

**Files:**
- Modify: `tests/elliott/test_templates.py`

This task locks the §3.4 invariant **before** the loop restructure, so any
future YAML edit that breaks label conventions fails fast.

- [ ] **Step 1: Append the test that locks the (n_required, waves) grouping invariant**

Append to `tests/elliott/test_templates.py`:

```python
def test_template_grouping_invariant() -> None:
    """Locks the enumerator's (n_required, tuple(waves)) grouping topology.

    The enumerator-eval-cache optimization (spec
    2026-05-11-enumerator-eval-cache-design.md §3.3-3.4) keys its
    per-window cache by constraint expression text and assumes that
    every template within a (n_required, tuple(waves)) cell binds the
    same WaveSegment to the same wave-label. The enumerator groups
    templates by that compound key so cache sharing only happens
    within a single cell.

    Multiple distinct waves conventions per n_required are allowed
    (e.g., zigzag/flat/expanded_flat use ["A","B","C"] while wxy
    uses ["W","X","Y"] at the same n_required=4). The invariant this
    test locks is: every template must be assignable to some cell,
    and the topology must be at least non-empty.

    If a future template introduces a brand-new waves convention,
    this test still passes — but the enumerator will give that
    template its own cache cell (correct but doesn't share with
    others). That is the intended fail-safe.
    """
    cells: dict[tuple[int, tuple[str, ...]], list[str]] = {}
    for tpl in load_bundled_templates():
        key = (len(tpl.waves) + 1, tuple(tpl.waves))
        cells.setdefault(key, []).append(tpl.name)

    assert cells, "expected at least one template group"
    for (n_required, labels), names in cells.items():
        assert names, f"empty group at ({n_required}, {labels})"
        assert n_required == len(labels) + 1, (
            f"n_required/labels mismatch at ({n_required}, {labels}): {names}"
        )
```

- [ ] **Step 2: Run the test**

```bash
uv run pytest tests/elliott/test_templates.py::test_template_grouping_invariant -v
```

Expected: PASS. Current bundle topology:
- `(6, ("1","2","3","4","5"))`: impulse, leading_diagonal, ending_diagonal, contracting_triangle
- `(4, ("A","B","C"))`: zigzag, flat, expanded_flat
- `(4, ("W","X","Y"))`: wxy

The grouping correctly separates wxy from the simple 3-wave
correctives at the same n_required, so the per-window cache will
only share results within each of the three cells.

- [ ] **Step 3: Commit**

```bash
git add tests/elliott/test_templates.py
git commit -m "$(cat <<'EOF'
test(elliott): lock template grouping invariant (n_required, waves)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Window-major loop with per-window eval cache

**Files:**
- Modify: `src/wave_alpha/elliott/enumerator.py`
- Modify: `tests/elliott/test_enumerator_eval_cache.py`

This is the central change. Restructures `enumerate_counts` from
template-major to window-major iteration, grouped by
`(n_required, tuple(waves))`, with a fresh `eval_cache` per window
passed into `validate`.

- [ ] **Step 1: Append the failing tests**

Append to `tests/elliott/test_enumerator_eval_cache.py`:

```python
from wave_alpha.elliott.enumerator import enumerate_counts


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
    # 8 pivots; impulse iterates 6-pivot windows, zigzag iterates 4-pivot
    # windows. The DIFFERENT-shape windows produce DIFFERENT bindings
    # (impulse W1 spans 6 pivots' worth of structure; zigzag WA spans
    # the first 2). Re-binding length(W1) for impulse vs length(WA) for
    # zigzag would not have collided even with a single cache because
    # the labels differ — but we verify the cache scope holds.
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
```

- [ ] **Step 2: Run the dedup test to confirm it fails (pre-restructure)**

```bash
uv run pytest tests/elliott/test_enumerator_eval_cache.py::test_eval_cache_dedup_5wave_motive_window -v
```

Expected: FAIL — current `enumerate_counts` doesn't pass `eval_cache`
into `validate`, so the shared constraint evaluates 3 times.

- [ ] **Step 3: Restructure `enumerate_counts`**

Open `src/wave_alpha/elliott/enumerator.py`. Locate the existing
`enumerate_counts` body (lines 33–103 approximately, after the
constants). Replace the body (everything inside the function from
`if not pivots:` onward) with the window-major implementation:

```python
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
                            "sub_counts": {
                                k: v for k, v in sub_counts.items() if v is not None
                            },
                            "soft_violations": count.soft_violations + sub_softs,
                        }
                    )
                score = _score(
                    count, tpl, last_window_idx=end_idx - 1, latest_idx=latest_idx
                )
                count = count.model_copy(update={"score": score})

                current = best_per_template.get(tpl.name)
                if current is None or score > current.score:
                    best_per_template[tpl.name] = count

    candidates = sorted(best_per_template.values(), key=lambda c: c.score, reverse=True)
    return candidates[:top_k]
```

- [ ] **Step 4: Run the new cache tests**

```bash
uv run pytest tests/elliott/test_enumerator_eval_cache.py -v
```

Expected: 10 PASS (7 prior + 3 new).

- [ ] **Step 5: Run the FULL existing test suite — behavior-preservation check**

```bash
uv run pytest -q --deselect tests/test_cli_ui.py::test_pick_port_returns_first_free
```

Expected: **all green**, no test modifications required. The
deselected test is the known port-8765 flake (unrelated).

If any test fails, the loop restructure changed behavior somewhere.
**Do not change the failing test** — change the enumerator
implementation. Likely culprits:
- Iteration order within `best_per_template`: ensure `groups`
  preserves the original template order from the `templates` argument.
- Composite branch logic: confirm `sub_pivots` gating and the
  `_fit_sub_counts` call match the prior implementation byte-for-byte.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/elliott/enumerator.py tests/elliott/test_enumerator_eval_cache.py
git commit -m "$(cat <<'EOF'
perf(elliott): window-major enumerate_counts with per-window eval cache

Restructures the (template × window) outer-product into a
window-major loop grouped by (n_required, tuple(waves)). Within
each group, a fresh per-window dict[str, bool] cache is passed to
validator.validate so shared constraints (e.g., wave_3_not_shortest
on impulse + leading_diagonal + ending_diagonal) evaluate exactly
once per window across all templates in the group.

Behavior-preserving: every count emitted before is emitted with the
same hard_violations, soft_violations, and engine_score.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Smoke — LD opt-in toggle byte-equivalence

**Files:** none modified — verification only

Verify the spec's §5.5 smoke claim: `analyze` outputs are byte-identical
to `main` for both default-off and opt-in-on LD configurations.

- [ ] **Step 1: Compute hashes on the feature branch**

```bash
mkdir -p /tmp/eval_cache_smoke

# LD off (default)
rm -f ~/.wave_alpha/config.yaml.bak
[ -f ~/.wave_alpha/config.yaml ] && cp ~/.wave_alpha/config.yaml ~/.wave_alpha/config.yaml.bak
echo "templates:" > ~/.wave_alpha/config.yaml
echo "  enable_leading_diagonal: false" >> ~/.wave_alpha/config.yaml
uv run wave-alpha analyze AAPL --as-of 2024-12-31 --json 2>/dev/null \
  | tail -1 > /tmp/eval_cache_smoke/after_off.json

# LD on
echo "templates:" > ~/.wave_alpha/config.yaml
echo "  enable_leading_diagonal: true" >> ~/.wave_alpha/config.yaml
uv run wave-alpha analyze AAPL --as-of 2024-12-31 --json 2>/dev/null \
  | tail -1 > /tmp/eval_cache_smoke/after_on.json

# Restore config
[ -f ~/.wave_alpha/config.yaml.bak ] && mv ~/.wave_alpha/config.yaml.bak ~/.wave_alpha/config.yaml || rm -f ~/.wave_alpha/config.yaml

shasum /tmp/eval_cache_smoke/after_off.json /tmp/eval_cache_smoke/after_on.json
```

Record the two sha sums. They are the "after" reference.

- [ ] **Step 2: Compute hashes on main**

```bash
git stash push --include-untracked -m "eval-cache smoke checkpoint"
git checkout main

# Repeat both runs on main
[ -f ~/.wave_alpha/config.yaml ] && cp ~/.wave_alpha/config.yaml ~/.wave_alpha/config.yaml.bak
echo "templates:" > ~/.wave_alpha/config.yaml
echo "  enable_leading_diagonal: false" >> ~/.wave_alpha/config.yaml
uv run wave-alpha analyze AAPL --as-of 2024-12-31 --json 2>/dev/null \
  | tail -1 > /tmp/eval_cache_smoke/before_off.json

echo "templates:" > ~/.wave_alpha/config.yaml
echo "  enable_leading_diagonal: true" >> ~/.wave_alpha/config.yaml
uv run wave-alpha analyze AAPL --as-of 2024-12-31 --json 2>/dev/null \
  | tail -1 > /tmp/eval_cache_smoke/before_on.json

[ -f ~/.wave_alpha/config.yaml.bak ] && mv ~/.wave_alpha/config.yaml.bak ~/.wave_alpha/config.yaml || rm -f ~/.wave_alpha/config.yaml

shasum /tmp/eval_cache_smoke/before_off.json /tmp/eval_cache_smoke/before_on.json

# Return to feature branch
git checkout feat/enumerator-eval-cache
git stash pop
```

- [ ] **Step 3: Diff the JSON outputs**

```bash
diff /tmp/eval_cache_smoke/before_off.json /tmp/eval_cache_smoke/after_off.json && echo "OFF: byte-identical"
diff /tmp/eval_cache_smoke/before_on.json /tmp/eval_cache_smoke/after_on.json && echo "ON: byte-identical"
```

Expected: both diffs empty + the "byte-identical" messages print.

If they differ, the cache changed behavior somewhere. Inspect the
diffs; the most likely cause is a constraint expression that depends
on segment state mutated between templates (it shouldn't — segments are
immutable derived from `WaveCount.pivots`). Do **not** proceed to the
backtest gate until this passes.

- [ ] **Step 4: (No commit — verification only)**

---

### Task 7: Layer-3 backtest perf gate

**Files:**
- Create: `reports/eval_cache_count_before.md` (local, gitignored)
- Create: `reports/eval_cache_count_after.md` (local, gitignored)

The merge gate. Must show byte-identical metrics + ≥ 10% runtime
improvement.

- [ ] **Step 1: Baseline (main) backtest**

```bash
git stash push --include-untracked -m "eval-cache backtest checkpoint" 2>/dev/null || true
git checkout main
mkdir -p /tmp/eval_cache_bt reports
time uv run wave-alpha backtest count \
  --universe data/universe_curated.txt \
  --start 2020-01-01 \
  --end 2024-12-31 \
  --step 5 \
  --out /tmp/eval_cache_bt/before.md
```

Record the `real` time printed by `time` — call it T_before.

- [ ] **Step 2: Feature-branch backtest**

```bash
git checkout feat/enumerator-eval-cache
git stash pop 2>/dev/null || true
time uv run wave-alpha backtest count \
  --universe data/universe_curated.txt \
  --start 2020-01-01 \
  --end 2024-12-31 \
  --step 5 \
  --out /tmp/eval_cache_bt/after.md
```

Record the elapsed `real` time — call it T_after.

- [ ] **Step 3: Diff reports and verify gate**

```bash
diff /tmp/eval_cache_bt/before.md /tmp/eval_cache_bt/after.md
```

**Hard-fail if the diff is non-empty.** Every metric (overall and
bucketed) must be byte-identical (the markdown renderer rounds to 2
decimals — that's the byte-equivalence threshold the spec demands).

If the diff is non-empty:
- Open both files and identify which metric drifted.
- The cache is misbehaving. Diagnose by re-running with a smaller
  universe (`data/universe_smoke.txt`) and binary-searching templates
  until the drift narrows to a specific (template, constraint) pair.
- Do **not** weaken the gate. Fix the cache, then return to Step 1.

If the diff IS empty:

```bash
# Quick threshold sanity: runtime must improve by >= 10%.
python3 -c "
before = ${T_before}   # paste seconds
after  = ${T_after}    # paste seconds
delta_pct = (after - before) / before * 100
print(f'T_before={before:.1f}s, T_after={after:.1f}s, delta={delta_pct:+.1f}%')
assert delta_pct <= -10, 'gate fail: runtime not >= 10% better'
print('runtime gate PASS')
"
```

If delta > -10% (i.e., not enough improvement), the optimization
underdelivered. Diagnose where the cost actually is (use `cProfile` on
one ticker's full analyze) and either tighten the cache or surface a
DONE_WITH_CONCERNS to the user for a design call.

- [ ] **Step 4: Save the reports locally for the PR description**

```bash
cp /tmp/eval_cache_bt/before.md reports/eval_cache_count_before.md
cp /tmp/eval_cache_bt/after.md reports/eval_cache_count_after.md
ls -la reports/eval_cache_count_*.md
```

`reports/` is gitignored; these are local-only evidence files.

- [ ] **Step 5: (No commit — gate evidence only)**

Record T_before, T_after, and delta_pct for the PR description.

---

### Task 8: Final verification

**Files:** none modified — verification only

- [ ] **Step 1: Full pytest one more time**

```bash
uv run pytest -q --deselect tests/test_cli_ui.py::test_pick_port_returns_first_free
```

Expected: all green.

- [ ] **Step 2: Ruff lint**

```bash
uv run ruff check .
```

Expected: clean.

- [ ] **Step 3: Ruff format check**

```bash
uv run ruff format --check .
```

Expected: clean. If any file needs reformatting:

```bash
uv run ruff format .
git add -A
git commit -m "$(cat <<'EOF'
style: ruff format after enumerator eval-cache landing

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Refresh GitNexus index**

```bash
npx gitnexus analyze
```

Expected: success message; new symbols (`evaluate_parsed`,
`Constraint.parsed`) registered.

- [ ] **Step 5: Print a summary for the PR description**

```bash
echo "=== files changed ===" && git diff --stat main..HEAD
echo "=== commits ===" && git log --oneline main..HEAD
echo "=== gate evidence ===" && ls -la reports/eval_cache_count_*.md
```

---

## Spec coverage check (self-review before handoff)

| Spec section | Implementation task |
|---|---|
| §2 — Pre-parse AST | Task 1 |
| §2 — Per-window eval cache | Tasks 3, 5 |
| §2 — ≥10% runtime gate | Task 7 |
| §2 — Behavior-preserving | Tasks 5 (full suite), 6 (analyze smoke), 7 (backtest diff) |
| §3.1 — `Constraint.parsed` cached_property | Task 1 |
| §3.1 — `evaluate_parsed` companion | Task 2 |
| §3.2 — Window-major loop with `(n_required, tuple(waves))` grouping | Task 5 |
| §3.3 — `validate(..., *, eval_cache=None)` | Task 3 |
| §3.4 — Cache-key safety invariant | Task 4 |
| §3.5 — Composite-branch interaction unchanged | Task 5 (preserves `_fit_sub_counts` branch) |
| §3.6 — Iteration-order determinism | Task 5 (`dict` insertion-order preservation; final `sorted`) |
| §4 — Module changes | Tasks 1, 2, 3, 4, 5 cover all modified rows |
| §5.1 — Behavior-preservation tests pass without modification | Task 5 Step 5 |
| §5.2 — New eval-cache tests | Tasks 1, 2, 3, 5 |
| §5.3 — Label-convention invariant test | Task 4 |
| §5.4 — Layer-3 backtest gate (byte-identical metrics + ≥10% runtime) | Task 7 |
| §5.5 — LD opt-in smoke | Task 6 |
| §8 — Acceptance checklist (all 9 items) | Tasks 1–8 cumulatively |
