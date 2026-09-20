---
related:
  - docs/superpowers/specs/2026-05-03-wave-alpha-design.md  # §5–§6 — engine and pattern library
  - docs/superpowers/specs/2026-05-08-multi-degree-enumeration-design.md  # multi-degree pivot data flow this builds on
  - docs/superpowers/specs/2026-05-07-ebt-empirical-fit-design.md  # degree-2 baseline — composite counts ride the same scoring path
---

# Complex corrections (W-X-Y) — design

## 1. Motivation

The pattern library today (`src/wave_alpha/elliott/templates_data/`) has six
primitives: `impulse`, `zigzag`, `flat`, `expanded_flat`,
`contracting_triangle`, `ending_diagonal`. Real markets routinely produce
*combinations* — a zigzag joined to a flat by an X-wave, a triple-three
sideways grind — and the engine has no representation for these. When such
structures appear, the enumerator is forced to score them as stretched
zigzags or flats, polluting the top-K with low-fit primitives and
masking what is actually a recognizable corrective combination.

This spec ships the **double-three** family (W-X-Y) as a single new
template with sub-count enforcement. The goal is **count quality in
sideways markets** — the engine should classify a real W-X-Y as a W-X-Y,
not as an over-stretched zigzag. New trade-entry signal types,
right-edge feature changes, and triple-three (W-X-Y-X-Z) are all out of
scope here; this spec is engine-classification only.

A secondary motivation: the `WaveCount.sub_counts: dict[str, WaveCount]`
field and the `PatternTemplate.allowed_sub_patterns` field already exist
in the schema but are unused. This spec activates both **scoped to the
new composite template** — primitive templates remain flat, with no
recursive sub-count fitting. Project-wide hierarchical enforcement is a
separate, much larger future effort.

## 2. Goals and non-goals

**Goals**

- `enumerate_counts` recognizes W-X-Y at the degree it is enumerated at,
  populating `WaveCount.sub_counts` with primitive corrective sub-counts
  (zigzag / flat / expanded_flat / contracting_triangle) at degree N−1.
- W-X-Y candidates compete in the same top-K as primitives — no parallel
  field on `AnalyzeResult`, no fixed score discount.
- Sub-count fitting uses the **subset invariant** already enforced by
  `pivots/multi_degree.py:_intersect_with` — degree N pivots are a strict
  subset of degree N−1 pivots by `(timestamp, type)`. No tolerance is
  needed.
- A composite is rejected (not emitted) if its W or Y slot has a real
  sub-window (≥ 4 sub-pivots) that no primitive corrective validates
  cleanly.
- A degenerate slot (sub-window of < 4 pivots) is accepted with
  `sub_counts[label] = None` and a synthetic `<label>:no_subdivision`
  soft violation. This narrows the "strict" answer surgically to slot
  degeneracy; validation failures still reject.
- Sub-count `soft_violations` propagate into the parent's `soft_violations`
  with a `<label>:` prefix, so the existing scoring formula penalizes
  sloppy sub-counts without DSL changes.

**Non-goals**

- **Triple-three (W-X-Y-X-Z).** Real but rarer. Defer until v1 ships and
  produces a clean baseline.
- **New trade-entry signal types** for completed double-threes. The
  `derive_signal` path is unchanged. If a W-X-Y appears as the top
  candidate, downstream signal logic treats it the same as any other
  count — typically meaning no signal until trade rules add a composite
  case (own future spec).
- **Right-edge model retraining.** No new features added to
  `RightEdgeFeatures`. The composite count's `engine_score` flows into
  the existing model unchanged.
- **`allowed_sub_patterns` enforcement on primitive templates.** Today
  `zigzag.yaml`, `impulse.yaml`, etc. declare allowed sub-patterns but
  nothing enforces them. Activating that for primitives is a much
  larger refactor; out of scope.
- **DSL extensions.** No new constraint operators. Sub-count enforcement
  is purely the `allowed_sub_patterns` allowlist + recursion in the
  enumerator. Constraints in `wxy.yaml` use only the existing
  `length` / `retrace` / `overlaps` operators.
- **Time / duration constraints.** The DSL has no time primitive. Adding
  one is its own future spec.
- **Specialized double-three variants** (separate `double_zigzag.yaml`,
  `double_combination.yaml`, …). One template with a broad sub-pattern
  allowlist captures the family; specialization can come later if data
  shows variants behave differently in scoring.
- **Backtest neutrality gate** and **right-edge promotion-style gate**.
  Acceptance is unit fixtures + golden tests on hand-labeled real
  windows; backtest re-runs are encouraged but not gating.

## 3. Architecture

### 3.1 Data flow

The pipeline already builds `dict[degree, list[PivotEvent]]` via
`detect_multi_degree(bars, …)`, then calls `enumerate_counts(pivots,
degree=N)` once per degree in `_ENUMERATED_DEGREES = (1, 2, 3)`.

The change is to widen `enumerate_counts`'s signature with one optional
parameter and route composite templates through a sub-count branch:

```
pipeline._enumerate_for(bars, interval):
  by_degree = detect_multi_degree(bars, ...)
  for N in (1, 2, 3):
      enumerate_counts(
          pivots=by_degree[N],
          degree=N,
          sub_pivots=by_degree.get(N-1),   # NEW
          top_k=2,
      )

elliott.enumerator.enumerate_counts(pivots, degree, *, sub_pivots=None,
                                     templates=None, top_k=5):
  for tpl in templates:
    if tpl.name in _COMPOSITE_TEMPLATE_NAMES:    # explicit marker (see §4)
        if sub_pivots is None:
            continue                             # graceful degrade
        for window of len(tpl.waves)+1 pivots:
            count = WaveCount(pattern=tpl.name, ...)
            count = validate(count, tpl)         # parent constraints
            if count.hard_violations: continue
            sub_counts, sub_softs = _fit_sub_counts(window, tpl, sub_pivots, degree)
            if sub_counts is None: continue       # rejection
            count = count.model_copy(update={
                "sub_counts": sub_counts,
                "soft_violations": count.soft_violations + sub_softs,
            })
            score and dedupe per template as today
    else:
        existing flat path (unchanged)
```

`_fit_sub_counts(window, tpl, sub_pivots, degree)` returns
`(dict[label, WaveCount | None], list[str])` — the sub-count map and the
list of prefixed soft violations to merge into the parent. Returns
`(None, _)` to signal rejection.

The recursive call inside `_fit_sub_counts` is one level deep only:

```python
sub_candidates = enumerate_counts(
    sub_window,                                  # exact slice of sub_pivots
    degree=degree - 1,
    templates=PRIMITIVE_CORRECTIVES,             # no composites
    top_k=1,
)
```

`PRIMITIVE_CORRECTIVES` is a module-level constant in `enumerator.py`
listing the four corrective primitives (zigzag, flat, expanded_flat,
contracting_triangle). Restricting the recursive call to this list
guarantees:
1. No infinite recursion (composite cannot be its own sub-count).
2. No impulse / diagonal sub-counts in correctives (semantically wrong).

### 3.2 Sub-window slicing

A new helper:

```python
def _slice_sub_pivots(
    sub_pivots: list[PivotEvent],
    seg_start: PivotEvent,
    seg_end: PivotEvent,
) -> list[PivotEvent] | None:
    """Find seg_start and seg_end in sub_pivots by (timestamp, type) and
    return the inclusive slice. None if either anchor is missing."""
```

By the subset invariant in `multi_degree._intersect_with`, the lookup
should always succeed. A `None` return indicates a corrupted pipeline
state (e.g., caller passed `sub_pivots` from a different bar set). The
parent W-X-Y is rejected (composite skipped, primitives unaffected) and
the helper returns `None` rather than raising — the recommended response
is a debug log + counter increment so a caller mismatch surfaces in
diagnostics rather than crashing analyze. Surfacing-vs-raising is an
implementation choice; the spec requires that the parent is dropped and
the failure is observable, not silent.

### 3.3 Sub-count fitting policy

For each label in `tpl.waves` (W, X, Y):

1. `seg = parent_count.segment_by_label(label)` — the parent segment.
2. `sub_window = _slice_sub_pivots(sub_pivots, seg.start, seg.end)`.
   - If `sub_window is None` → reject parent.
3. **Degeneracy check.** If `len(sub_window) < 4` (fewer than three
   segments — no primitive corrective can fit):
   - `sub_counts[label] = None`
   - `soft_violations.append(f"{label}:no_subdivision")`
4. Otherwise, **strict fitting**:
   - `subs = enumerate_counts(sub_window, degree=N-1,
     templates=PRIMITIVE_CORRECTIVES, top_k=1)`
   - Filter to candidates whose first/last pivots match `seg.start` /
     `seg.end` by `(timestamp, type)` — ensures the sub-count spans
     the entire slot, not a sub-portion.
   - If no candidate survives the filter → reject parent.
   - If the surviving candidate's `pattern not in
     tpl.allowed_sub_patterns[label]` → reject parent.
   - Otherwise: `sub_counts[label] = candidate`, and propagate
     `f"{label}:{soft}"` for each `soft` in `candidate.soft_violations`.

### 3.4 Score propagation

Parent scoring is unchanged from the existing flat path:

```
score = 0.50 * (1 - 0.10 * len(soft_violations))
      + 0.20 * confluence
      + 0.30 * recency
```

Sub-count soft violations land in `soft_violations` via the prefix
mechanism in §3.3, so they reduce `base` automatically. No change to
`_score`, `_confluence_bonus`, or `_recency_factor`.

`_confluence_bonus` calls `fib_features(count)`, which inspects
`count.pattern`. The new `wxy` pattern is not curated, so it falls
through to the legacy `fib_confluence_score(W2, reference=W1)` path.
That gives a stable, low-magnitude confluence contribution for v1; a
follow-up can add `wxy` to the curated patterns if needed.

### 3.5 Top-K integration

The existing per-template dedupe + global top-K cut in `enumerate_counts`
applies unchanged. Composite candidates compete with primitives. No
score discount, no parallel field, no special ranking. Multi-degree
coherence (`three_way`) and the LLM reranker treat them as any other
`WaveCount`.

### 3.6 Degraded callers

Some callers of `enumerate_counts` do not have lower-degree pivots
available — most importantly the right-edge feature extractor and any
test that builds pivots by hand. Passing `sub_pivots=None` causes the
composite branch to be skipped entirely (no `wxy` candidates produced).
This preserves backward compatibility for every existing call site;
only `pipeline._enumerate_for` is updated to pass `sub_pivots`.

## 4. Module changes

| Module | Change |
|---|---|
| `src/wave_alpha/elliott/templates_data/wxy.yaml` | **NEW** — see §5 |
| `src/wave_alpha/elliott/enumerator.py` | Add `sub_pivots` parameter; add `PRIMITIVE_CORRECTIVES` constant; add `_fit_sub_counts` and `_slice_sub_pivots` helpers; route composite templates (those with non-empty `allowed_sub_patterns`) through the sub-count branch |
| `src/wave_alpha/pipeline.py:_enumerate_for` | Pass `sub_pivots=by_degree.get(N-1)` to each `enumerate_counts` call |
| `src/wave_alpha/elliott/templates.py` | No change — `allowed_sub_patterns` field already present |
| `src/wave_alpha/elliott/validator.py` | No change — sub-count enforcement happens in the enumerator |
| `src/wave_alpha/elliott/dsl.py` | No change — no new operators |
| `src/wave_alpha/elliott/models.py` | No change — `sub_counts` field already present |
| `src/wave_alpha/elliott/fib_features.py` | No change — `wxy` falls through to legacy single-ratio path |

The composite vs. primitive routing key inside the enumerator is an
explicit module-level set:

```python
_COMPOSITE_TEMPLATE_NAMES: frozenset[str] = frozenset({"wxy"})
```

Field presence is not viable as the discriminator: every existing
template declares `allowed_sub_patterns` (the field is populated even on
primitives like `contracting_triangle`), but no enforcement runs on
those declarations today. Using the name set keeps composite status
centralized and explicit; adding triple-three later means appending to
this set plus shipping `wxyxz.yaml`.

## 5. The `wxy.yaml` template

```yaml
name: wxy
direction_alternates: true
waves: ["W", "X", "Y"]
constraints:
  - name: x_shorter_than_w
    severity: hard
    expr: "length(WX) < length(WW)"
    rationale: "Connector X is shorter than the corrective W it joins."

  - name: x_shorter_than_y
    severity: hard
    expr: "length(WX) < length(WY)"
    rationale: "Connector X is shorter than the corrective Y it joins."

  - name: y_at_least_0618_w
    severity: soft
    expr: "length(WY) >= Decimal('0.618') * length(WW)"
    rationale: "Y typically reaches at least 0.618×W (size proportionality)."

  - name: y_at_most_1618_w
    severity: soft
    expr: "length(WY) <= Decimal('1.618') * length(WW)"
    rationale: "Y rarely extends more than 1.618×W; outliers are usually misclassified."

  - name: x_max_retrace_of_w
    severity: soft
    expr: "retrace(WX, WW) <= Decimal('0.786')"
    rationale: "X usually retraces W partially, not fully."

allowed_sub_patterns:
  "W": ["zigzag", "flat", "expanded_flat", "contracting_triangle"]
  "X": ["zigzag", "flat", "expanded_flat", "contracting_triangle"]
  "Y": ["zigzag", "flat", "expanded_flat", "contracting_triangle"]
```

Intentionally absent:
- No "W ≠ Y in pattern" constraint — double-zigzag is canonical.
- No time/duration constraint — DSL has no time primitive.
- No reference to sub-count internals from parent constraints — would
  require DSL extension.

## 6. Testing

Acceptance bar (chosen during brainstorming): synthetic fixtures +
golden tests on hand-labeled real charts. No backtest gate, no
right-edge calibration gate.

### 6.1 Layer 1 — synthetic unit tests (`tests/elliott/test_wxy_*.py`)

Each test hand-builds a degree-2 pivot list of length 4 (parent W-X-Y
boundaries) plus a degree-1 pivot list that subdivides each parent
segment. No bars, no `detect_multi_degree`, no provider. Tests:

- `test_wxy_recognized_when_w_zigzag_y_flat` — top-K contains a `wxy`
  count with `sub_counts["W"].pattern == "zigzag"` and
  `sub_counts["Y"].pattern == "flat"`.
- `test_wxy_recognized_when_w_and_y_both_zigzag` — double-zigzag
  flavor classified correctly.
- `test_wxy_rejected_when_w_subcount_invalid` — pivot configuration
  that fits no primitive in the W slot → no `wxy` emitted; existing
  primitive(s) still emitted unchanged.
- `test_wxy_silently_skipped_without_subpivots` — calling
  `enumerate_counts(pivots, degree=2, sub_pivots=None)` produces no
  `wxy` and does not raise.
- `test_wxy_subcount_violations_propagate` — a sub-zigzag with
  `soft_violations=["c_meets_or_exceeds_a"]` causes parent's
  `soft_violations` to contain `"W:c_meets_or_exceeds_a"`; parent's
  base score drops by exactly 0.10 vs. a clean equivalent.
- `test_wxy_pivot_alignment_strict` — for every label,
  `sub_counts[label].pivots[0]` and `[-1]` match the parent segment's
  start/end by `(timestamp, type)`.
- `test_wxy_no_self_recursion` — `wxy` does not appear in
  `sub_counts[label].pattern`. Verified by passing
  `templates=[wxy_template]` to the recursive call boundary; the
  recursion is restricted to `PRIMITIVE_CORRECTIVES`.
- `test_wxy_x_degenerate_no_subdivision` — when X's sub-window has
  3 pivots (one connector segment), parent is accepted with
  `sub_counts["X"] is None` and `"X:no_subdivision"` in
  `soft_violations`.
- `test_wxy_x_strict_when_subwindow_present` — when X has ≥ 4 sub-pivots
  but fits no primitive corrective, parent is rejected (degeneracy
  relaxation does not apply).
- `test_wxy_dedupes_per_template` — multiple windows yielding `wxy`
  candidates collapse to the highest-scoring one before global top-K,
  matching the existing `best_per_template` behavior.

### 6.2 Layer 2 — golden tests on hand-labeled real charts

Five to ten `(ticker, as_of)` pairs where the analyst has manually
labeled the count. Fixtures stored under
`tests/elliott/fixtures/wxy_golden/<name>.json`, each containing:
`bars`, `as_of`, `expected_top_pattern_at_degree_2`, optional
`expected_subcounts: {W: pattern, X: pattern, Y: pattern}`.

Two test classes:

- **W-X-Y positives** — assert `wxy` appears at #1 in degree-2 top-K
  with the expected sub-count patterns.
- **Primitive negatives** — chart sequences the analyst labels as a
  primitive zigzag or flat, asserting `wxy` does **not** appear at #1
  (regression guard against false positives).

Fixtures are added iteratively as the implementation lands. The spec
does not gate on a specific count; it gates on the presence of a
non-trivial set (≥ 5) before merge.

### 6.3 Layer 3 — pipeline integration test

`tests/test_pipeline.py::test_run_analysis_emits_wxy_when_present` —
fake `OHLCVProvider` returning synthetic bars engineered to produce a
`wxy` at degree 2 in `_ENUMERATED_DEGREES`. Asserts:
- `result.counts_daily` contains a `wxy` entry.
- `wxy.sub_counts["W"]` and `wxy.sub_counts["Y"]` are populated with
  primitive corrective `WaveCount`s.
- `result.coherence` is computed without raising on a composite top
  candidate.

## 7. Risks and open questions

- **R1: False-positive rate in sideways markets.** Adding W-X-Y may
  cause it to "win" the top-K in genuinely zigzag-only sideways action
  if the parent constraints accept too easily. Mitigated by the strict
  `x_shorter_than_w` and `x_shorter_than_y` hard constraints, by the
  spanning-pivot filter, and by golden-test "primitive negatives". If
  this surfaces post-merge, the next-cheapest tightening is adding a
  `length(WW) >= some_atr_floor` hard constraint to require W itself
  to be substantial.
- **R2: Confluence path is degraded.** `wxy` falls through to the
  legacy single-ratio confluence (W2-vs-W1 stand-in: WX-vs-WW).
  Functional but not curated. A follow-up can add `wxy`-aware
  confluence in `fib_features.py`. Out of scope here.
- **R3: Coherence with composite top candidate.** `three_way` was
  designed against primitive counts. The function accepts any
  `WaveCount`, but its scoring heuristics were tuned for primitives.
  Layer 3 includes a smoke test that coherence runs without raising on
  a composite top candidate; behavioral tuning of `three_way` for
  composites is a follow-up if anomalies surface.
- **Q1: Should triple-three be added in v1.1 or wait for empirical
  signal that it's needed?** Deferred; not blocking.
- **Q2: Should `wxy` be added to `allowed_sub_patterns` of impulse
  W2/W4?** Out of scope here — would activate sub-pattern enforcement
  on primitive templates, which we explicitly excluded.

## 8. Out of scope (deferred to future specs)

- Triple-three (`wxyxz.yaml`) and the corresponding 5-segment
  enumeration path.
- `allowed_sub_patterns` enforcement on impulse / zigzag / flat /
  diagonal / triangle templates.
- DSL extensions for sub-count constraint references (e.g., "Y is more
  complex than W" expressed in YAML).
- Time/duration constraint primitives in the DSL.
- New trade-entry signal types triggered by completed double-threes.
- Right-edge feature additions for composite counts.
- Backtest re-fit of EBT / scoring weights to account for composite
  candidates entering top-K.
- `wxy`-aware curated confluence in `fib_features.py`.
- LLM rerank prompt changes for composite candidates (`llm/prompts.py`,
  `llm/candidates.py:serialize_candidate_set` already accept arbitrary
  `WaveCount`; sub-count detail is implicitly serialized via pivots).

## 9. Acceptance checklist

- [ ] `wxy.yaml` ships in `templates_data/`; loaded by
      `load_bundled_templates()`.
- [ ] `enumerator.py` recognizes the composite branch by name; recursive
      call restricted to `PRIMITIVE_CORRECTIVES`; one level deep only.
- [ ] `pipeline._enumerate_for` passes `sub_pivots=by_degree.get(N-1)`.
- [ ] All Layer-1 unit tests pass.
- [ ] At least 5 Layer-2 golden fixtures land (mix of positives and
      primitive negatives).
- [ ] Layer-3 pipeline integration test passes.
- [ ] `uv run ruff check .` clean.
- [ ] `uv run pytest` full suite passes; no regression in existing
      enumerator / pipeline / right-edge tests.
- [ ] Spec's "Out of scope" section is honored — no `allowed_sub_patterns`
      enforcement on primitive templates leaks in.
