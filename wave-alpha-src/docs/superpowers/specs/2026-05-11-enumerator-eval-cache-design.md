---
related:
  - docs/superpowers/specs/2026-05-10-leading-diagonal-design.md  # §10 documented the +25% per-template runtime cost this spec addresses
  - docs/superpowers/specs/2026-05-08-multi-degree-enumeration-design.md  # enumerator architecture
  - docs/superpowers/specs/2026-05-10-complex-corrections-design.md  # composite branch interaction
---

# Enumerator constraint-eval cache — design

## 1. Motivation

`elliott.enumerator.enumerate_counts` iterates the outer product
`(template × window)` and runs `dsl.evaluate(expr, bindings)` for every
constraint on every (template, window) pair. Two redundancies dominate
the cost:

- **`ast.parse(expr)` per call.** Constraint expressions are static text
  on the template, parsed fresh on every evaluation. The string-to-AST
  conversion is the largest fixed cost per `evaluate` call.
- **Shared constraints, repeated evaluation.** Templates that share a
  wave-count + label-convention bind identical `WaveSegment`s for the
  same window. The predicate
  `length(W3) >= min(length(W1), length(W5))` is declared on `impulse`,
  `leading_diagonal`, AND `ending_diagonal`. For one 5-pivot window
  it's currently evaluated three times with the same inputs.

The 2026-05-10 leading-diagonal landing exposed the per-template
dispatch cost empirically: adding one template added ~23–26% wall-clock
to the count-layer backtest on the curated 50-ticker universe (recorded
in `2026-05-10-leading-diagonal-design.md` §10 and in project memory).
Every future template will pay the same overhead until this is fixed.

## 2. Goals and non-goals

**Goals**

- Behavior-preserving: every count emitted before is emitted with the
  same `engine_score` and the same `hard_violations` / `soft_violations`
  after. This is *the* central claim — the gate is byte-identical
  metrics on the count-layer backtest.
- Pre-parse constraint AST at template load (eliminates per-call
  `ast.parse`).
- Window-scoped evaluation cache so shared constraints are evaluated
  once per window across templates that share wave-count and label
  conventions.
- ≥ 10% runtime improvement on
  `wave-alpha backtest count --universe data/universe_curated.txt
  --start 2020-01-01 --end 2024-12-31 --step 5`.
- All existing tests pass without modification.

**Non-goals**

- Behavioral hierarchy / family score-threshold short-circuit (defers
  emission of less-specific templates when a more-specific one
  validates). Separate spec; would require its own behavior gate.
- Position-aware enumeration (defer LD only to wave-1 positions).
- DSL extensions or new operators.
- Cross-call caching (caching that persists across
  `enumerate_counts` invocations). Per-call freshness is simpler and
  safe; cross-call adds invalidation complexity for marginal win in the
  typical pipeline workload.
- Replacing the AST walker with compiled Python bytecode or JIT
  codegen. Premature; this spec's two-strategy combo addresses the
  dominant cost.

## 3. Architecture

### 3.1 Pre-parse constraint AST at template load

`Constraint` (in `elliott.templates`) gains a `cached_property` for the
parsed AST so each constraint's `ast.parse` runs at most once per
process per `Constraint` instance.

```python
import ast
from functools import cached_property

class Constraint(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    severity: Literal["hard", "soft"]
    expr: str
    rationale: str = ""

    @cached_property
    def parsed(self) -> ast.Expression:
        return ast.parse(self.expr, mode="eval")
```

Pydantic v2 frozen models support `@cached_property` (the cache lives
on the instance `__dict__`, distinct from validated fields).
Implementation contingency: if Pydantic's frozen-model immutability
blocks `cached_property` writes, fall back to a module-level cache
keyed by `id(constraint)` in `validator.py` — semantically equivalent.

A companion helper in `dsl.py`:

```python
def evaluate_parsed(
    tree: ast.AST, bindings: dict[str, WaveSegment]
) -> tuple[bool, str]:
    """Evaluate a pre-parsed AST against bindings. Same return contract
    as ``evaluate(expr, bindings)`` but skips parsing."""
    namespace: dict[str, Any] = dict(bindings)
    try:
        result = _walk(tree, namespace)
        return bool(result), str(result)
    except DSLError as exc:
        return False, f"dsl error: {exc!s}"
    except Exception as exc:  # noqa: BLE001
        return False, f"eval error: {exc!s}"
```

`evaluate(expr, bindings)` is retained for external callers (none in
the current codebase, but the function is public API).

### 3.2 Window-major loop restructure

`enumerate_counts` today is template-major:

```python
for tpl in templates:                       # outer
    for end_idx in range(...):              # inner
        window = pivots[end_idx - n : end_idx]
        ...
```

Templates that share `n_required = len(waves) + 1` iterate identical
window slices. A window-major loop groups them so a single per-window
cache shares constraint results among them:

```python
# Group templates by their window size.
groups: dict[int, list[PatternTemplate]] = {}
for tpl in templates:
    if tpl.name in _COMPOSITE_TEMPLATE_NAMES and sub_pivots is None:
        continue  # graceful-degrade gate stays
    n_required = len(tpl.waves) + 1
    groups.setdefault(n_required, []).append(tpl)

best_per_template: dict[str, WaveCount] = {}
latest_idx = len(pivots) - 1

for n_required, group in groups.items():
    if len(pivots) < n_required:
        continue
    for end_idx in range(len(pivots), n_required - 1, -1):
        window = pivots[end_idx - n_required : end_idx]
        if not _alternates_ok(window):
            continue
        eval_cache: dict[str, bool] = {}  # fresh per window
        for tpl in group:
            count = WaveCount(pattern=tpl.name, degree=degree, pivots=window)
            count = validate(count, tpl, eval_cache=eval_cache)
            if count.hard_violations:
                continue
            if tpl.name in _COMPOSITE_TEMPLATE_NAMES:
                # composite branch unchanged from current logic
                ...
            score = _score(count, tpl, last_window_idx=end_idx - 1,
                           latest_idx=latest_idx)
            count = count.model_copy(update={"score": score})
            current = best_per_template.get(tpl.name)
            if current is None or score > current.score:
                best_per_template[tpl.name] = count

candidates = sorted(best_per_template.values(), key=lambda c: c.score,
                    reverse=True)
return candidates[:top_k]
```

The composite-branch sub-count fitting (`_fit_sub_counts`) is
unchanged.

### 3.3 Validator cache wiring

```python
def validate(
    count: WaveCount,
    template: PatternTemplate,
    *,
    eval_cache: dict[str, bool] | None = None,
) -> WaveCount:
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
    return count.model_copy(update={"hard_violations": hard,
                                     "soft_violations": soft})
```

Callers that don't pass `eval_cache` get the existing semantics
(every constraint evaluated freshly per call). This keeps the
`validate` function safe for all external use sites.

### 3.4 Cache-key safety

Cache key is the constraint expression text `c.expr`. Sharing is safe
when bindings are identical for the same key across templates within a
loop group. Identity holds when:

1. Templates iterate the same window slice (same `n_required` —
   guaranteed by the loop grouping).
2. Templates declare the same `waves` labels (so `W3` binds to the
   same segment under `_segment_bindings`).

Current bundle:
- 5-wave motive group (impulse, leading_diagonal, ending_diagonal,
  contracting_triangle): all use `waves: ["1","2","3","4","5"]`.
- 3-wave corrective group (zigzag, flat, expanded_flat): all use
  `waves: ["A","B","C"]`.
- wxy uses `waves: ["W","X","Y"]` (alone in its label convention but
  the only 3-wave composite, so it forms its own group when grouped
  by `(n_required, tuple(waves))` — see invariant below).

**Locked invariant.** All bundled templates that share `n_required`
must declare identical `waves` labels. A unit test asserts this and
fails if a future template breaks the convention. The implementation
also keys groups by `(n_required, tuple(waves))` rather than
`n_required` alone — defensive separation so an accidental new label
convention causes a fresh cache (correct but slow) rather than a
collision (incorrect).

### 3.5 Composite-branch interaction

Composite templates (currently only `wxy`) recursively call
`enumerate_counts` to fit primitive corrective sub-counts. The
recursive call gets a fresh `eval_cache` (it's a fresh function
invocation) and operates on a different (smaller) pivot slice. No
state crosses the recursion boundary. Behavior unchanged.

### 3.6 Iteration-order determinism

Python `dict` preserves insertion order. The groups are built by
iterating `templates` once, so within a group, templates retain their
original relative order. The window loop direction (newest-first via
`range(len(pivots), n_required - 1, -1)`) is unchanged.
`best_per_template` is keyed by template name, and the final
`sorted(..., key=lambda c: c.score, reverse=True)` is stable on score
ties.

Determinism of output ranks is preserved.

## 4. Module changes

| Module | Change |
|---|---|
| `src/wave_alpha/elliott/templates.py` | Add `@cached_property parsed` on `Constraint`; import `ast` + `functools.cached_property`. Fallback path (module-level cache by `id(constraint)`) documented in code comments. |
| `src/wave_alpha/elliott/dsl.py` | Add `evaluate_parsed(tree, bindings)` next to existing `evaluate(expr, bindings)`. The latter is retained for external/diagnostic use. |
| `src/wave_alpha/elliott/validator.py` | `validate(count, template, *, eval_cache=None)` accepts the per-window cache and routes through `evaluate_parsed(c.parsed, bindings)`. Default `eval_cache=None` matches existing semantics. |
| `src/wave_alpha/elliott/enumerator.py` | Restructure `enumerate_counts` body to window-major loop with per-window eval cache. Composite branch logic, dedupe, score, top-K cut unchanged. |
| `tests/elliott/test_enumerator_eval_cache.py` | **NEW** — dedup tests (count `evaluate_parsed` invocations on shared windows), behavior-preservation tests (compare top-K against pre-cache reference values built into the fixture). |
| `tests/elliott/test_templates.py` | New test asserting label-convention invariant per wave count. |

**Not modified (intentionally):**
- `src/wave_alpha/pipeline.py`, `src/wave_alpha/scan/orchestrator.py`,
  `src/wave_alpha/cli/app.py`, `src/wave_alpha/web/deps.py` — `enumerate_counts`
  signature is backward-compatible.
- `src/wave_alpha/elliott/templates_data/*.yaml` — no template change required.
- `src/wave_alpha/elliott/fib_features.py`, `src/wave_alpha/elliott/models.py` — no change.
- `src/wave_alpha/right_edge/*` and `src/wave_alpha/backtest/*` — `enumerate_counts`
  callers continue to pass no `eval_cache`; they get the existing semantics
  (the cache is internal to a single `enumerate_counts` call).

## 5. Testing

### 5.1 Behavior-preservation unit tests

Every test in
`tests/elliott/test_leading_diagonal.py`,
`tests/elliott/test_wxy_*.py`,
`tests/elliott/test_enumeration.py`,
`tests/elliott/test_templates.py`,
`tests/test_pipeline.py`, and
`tests/elliott/test_leading_diagonal_golden.py`
must continue to pass with **zero modifications**. These tests
exercise the engine's output shape and score values; their continued
passage is the strongest signal that behavior is preserved.

### 5.2 New tests — `tests/elliott/test_enumerator_eval_cache.py`

- `test_eval_cache_dedup_5wave_motive_window` — monkey-patch
  `evaluate_parsed` to count invocations. Run `enumerate_counts` on a
  6-pivot window with templates `[impulse, leading_diagonal,
  ending_diagonal]`. The shared constraint
  `wave_3_not_shortest` (declared on all three) must be evaluated
  exactly once, not three times.
- `test_eval_cache_isolated_per_window` — multiple windows in one
  `enumerate_counts` call. Assert that the cache resets between
  windows (no false hits from a prior window's bindings).
- `test_eval_cache_isolated_per_wave_count_group` — templates with
  different `n_required` do not share a cache (a 3-wave corrective
  evaluating `length(WA)` does not collide with a 5-wave motive's
  `length(W1)`).
- `test_validate_without_eval_cache_unchanged` — call `validate(...)`
  without `eval_cache`; the violation lists match the pre-cache
  baseline (use a hardcoded reference for one (window, template)
  pair).
- `test_constraint_parsed_caches_ast` — call `c.parsed` twice on the
  same `Constraint` instance, assert `is` identity (no re-parsing).

### 5.3 Invariant test — `tests/elliott/test_templates.py`

`test_label_convention_locked_per_wave_count` — load all bundled
templates, group by `len(waves) + 1`, assert all templates in each
group declare identical `waves` lists. Fails if a future template
breaks the §3.4 invariant.

### 5.4 Layer 3 — count-layer backtest performance gate

Run `wave-alpha backtest count --universe data/universe_curated.txt
--start 2020-01-01 --end 2024-12-31 --step 5 --out <path>` on `main`
baseline (pre-cache) and on the feature branch (post-cache).

**Hard-fail thresholds:**
- Overall `top1_hit_rate`, `top3_hit_rate`, `sample_size` **byte-identical
  to baseline** (rounded to 2 decimals as the report renders them).
  Behavior-preservation is the central claim; any drift indicates a
  cache bug.
- Per-bucket (`full`, `partial`, `disagreement`) and per-year breakdowns
  also byte-identical.
- Runtime **must improve by ≥ 10%**. (If it doesn't, the optimization
  isn't paying for its complexity; the spec is wrong about where the
  cost was.)

**Soft watch:**
- Runtime improvement < 5% — reconsider the architectural call (maybe
  the cost isn't where the spec predicted).

### 5.5 Smoke — LD opt-in toggle

Run `wave-alpha analyze AAPL --as-of 2024-12-31 --json` with both
`templates.enable_leading_diagonal: false` (default) and `: true`. The
JSON outputs on the feature branch must match outputs from the same
runs on `main` byte-identically.

## 6. Risks and open questions

- **R1: Cache-key collision** if a future template adopts non-standard
  labels at an existing `n_required`. Mitigated by §3.4 invariant
  test + defensive `(n_required, tuple(waves))` group key. A label
  divergence would cause a fresh cache (slower but correct), not a
  silent wrong result.
- **R2: `cached_property` on a frozen Pydantic v2 model.** Pydantic v2
  permits `@cached_property` on frozen models (the cache writes to
  `__dict__`, not validated fields). Implementation contingency:
  fallback module-level cache keyed by `id(constraint)` if frozen
  semantics block the write.
- **R3: Loop restructure changing iteration order.** §3.6 argues
  determinism is preserved. The behavior-preservation gate in §5.4
  catches it definitively if not.
- **R4: AST allowlist constants.** `_BIN_OPS` / `_CMP_OPS` /
  `_CALLABLES` in `dsl.py` are module-level singletons reused across
  invocations — no per-call setup cost. The walker itself is stateless.
- **Q1: Should `evaluate_parsed` also accept a cache argument
  internally?** No — the cache is per-`validate`-call scope; passing
  it deeper just shifts the same logic.
- **Q2: Memoize the diagnostic string too?** The current implementation
  discards it (`validate` ignores the second return value). Skip —
  YAGNI.

## 7. Out of scope (deferred)

- Behavioral family short-circuit (score-threshold suppression of
  less-specific templates within a family).
- Position-aware enumeration (LD only emits at wave-1 positions of a
  degree+1 motive).
- Cross-call caching (across multiple `enumerate_counts` invocations).
- Compiling constraint ASTs to Python bytecode or function objects for
  faster `_walk` traversal.
- Expanding-triangle template addition (separate template-expansion
  spec; tracked but not gating).

## 8. Acceptance checklist

- [ ] `Constraint.parsed` cached property ships; AST parsed once per
      `Constraint` instance.
- [ ] `evaluate_parsed(tree, bindings)` exported from
      `elliott.dsl`; `evaluate(expr, bindings)` retained for external
      callers.
- [ ] `validator.validate` accepts `eval_cache: dict[str, bool] | None`;
      defaults preserve existing semantics for non-cache callers.
- [ ] `enumerator.enumerate_counts` body restructured to window-major
      loop grouped by `(n_required, tuple(waves))`.
- [ ] §3.4 label-convention invariant test passes; all bundled
      templates conform.
- [ ] All Layer-1 new tests pass (eval-cache dedup, isolation,
      `Constraint.parsed` identity, validate-without-cache fallback).
- [ ] Existing test suite passes with **zero test-code changes**.
- [ ] Layer-3 backtest: per-metric byte-identical to baseline, runtime
      ≥ 10% better.
- [ ] LD opt-in smoke (both directions) byte-identical to `main`.
- [ ] `uv run ruff check .` clean.
