---
related:
  - docs/superpowers/specs/2026-05-03-wave-alpha-design.md  # §5–§6 — engine and pattern library
  - docs/superpowers/specs/2026-05-10-complex-corrections-design.md  # template-addition pattern this follows
  - docs/superpowers/specs/2026-05-08-multi-degree-enumeration-design.md  # multi-degree enumeration context
  - docs/superpowers/specs/2026-05-07-ebt-empirical-fit-design.md  # degree-2 score baseline LD competes against
---

# Leading diagonal — design

## 1. Motivation

The bundled pattern library ships an `ending_diagonal` template but no
`leading_diagonal`. The schema is ready for it — `impulse.yaml` already
lists `leading_diagonal` in `allowed_sub_patterns["1"]` (currently
informational, since primitive sub-pattern enforcement is out of scope).
When a real LD appears in market data — typically at the opening wave-1
of an impulse, or the wave-A of a zigzag — the enumerator has to score
it as a stretched impulse (which fails the hard `wave_4_no_overlap`
constraint and is rejected) or as an ending diagonal out of position.
The latter pollutes the top-K and undermines downstream count quality.

This spec adds `leading_diagonal` as a peer primitive motive template.
LD and ED are structurally near-identical at the pivot level; classical
EW disambiguates them by position in the larger structure, which the
engine has no facility to track. The chosen disambiguator is therefore
**soft-constraint scoring**: LD biases toward a deeper W2 retrace and
a less-extended W3, ED keeps its existing shallow-W2 bias implicitly
(by lack of contrary softs). Both templates compete in the same top-K;
the score sorts them.

## 2. Goals and non-goals

**Goals**

- Ship `leading_diagonal.yaml` as a primitive motive template recognized
  by `enumerate_counts` via the existing flat (non-composite) path.
- No enumerator, validator, DSL, or pipeline code changes — every
  required operator (`length`, `retrace`, `overlaps`, `min`) is in the DSL.
- LD and ED both match diagonal-shaped windows; soft-constraint scoring
  discriminates by W2 retrace depth and W3 extension.
- Count-layer backtest on the curated 50-ticker universe shows no
  significant regression vs. main. This is the merge gate.

**Non-goals**

- **Position-aware enumeration.** The engine does not know whether a
  window sits at wave-1 of a larger motive; emitting LD only at
  start-of-motive positions would require multi-degree context plumbing
  that does not exist. Out of scope; explicitly deferred.
- **Primitive `allowed_sub_patterns` enforcement.** Same exclusion as
  the complex-corrections spec — informational only on primitives.
- **New trade-entry signal types.** `derive_signal` is unchanged. If
  LD becomes the top candidate, downstream logic treats it the same as
  any other count.
- **Curated `fib_features` path for LD.** Falls through to the legacy
  single-ratio path, same as `ending_diagonal` today.
- **Expanding vs. contracting LD split.** One template covers both;
  soft constraints describe central tendency. A future spec can split
  if backtest data shows the families behave distinctly.
- **LD-specific right-edge features.** No additions to `RightEdgeFeatures`.
- **LLM prompt changes.** `llm/candidates.py:serialize_candidate_set` is
  pattern-agnostic.

## 3. The `leading_diagonal.yaml` template

```yaml
name: leading_diagonal
direction_alternates: true
waves: ["1", "2", "3", "4", "5"]
constraints:
  - name: wave_3_not_shortest
    severity: hard
    expr: "length(W3) >= min(length(W1), length(W5))"
    rationale: "Motive rule — W3 cannot be the shortest of 1, 3, 5."

  - name: wave_2_max_retrace
    severity: hard
    expr: "retrace(W2, W1) <= Decimal('0.99')"
    rationale: "W2 cannot fully retrace W1; full retrace means there is no W1."

  - name: wave_2_min_retrace
    severity: hard
    expr: "retrace(W2, W1) >= Decimal('0.3')"
    rationale: "LDs need a real W2 retrace. A W2 below 0.3 of W1 is too shallow to be a diagonal corrective; the window is structurally an impulse with a tiny W2, not an LD. Fail-fast filter to keep LD out of the top-K on non-diagonal windows. **Added during v1 iteration after backtest gate failure.**"

  - name: wave_4_overlaps_wave_1
    severity: hard
    expr: "overlaps(W4, W1)"
    rationale: "Classical EW: diagonals MUST show W4 overlap with W1. Without overlap, the window is either an impulse (already validates) or not a 5-wave motive. **Promoted to hard during v1 iteration** to keep LD from competing with impulse on the same windows."

  - name: wave_2_deep_retrace
    severity: soft
    expr: "retrace(W2, W1) >= Decimal('0.5')"
    rationale: "LD W2 typically retraces 50%+ of W1 (ED tends shallow, 30–60%)."

  - name: wave_2_not_excessive
    severity: soft
    expr: "retrace(W2, W1) <= Decimal('0.9')"
    rationale: "LD W2 rarely exceeds 90% of W1; deeper is usually a corrective misclassification."

  - name: wave_3_not_extended
    severity: soft
    expr: "length(W3) <= Decimal('1.618') * length(W1)"
    rationale: "LD W3 is compressed vs. impulse W3; heavy extension suggests impulse, not LD."

allowed_sub_patterns:
  "1": ["zigzag", "impulse"]
  "2": ["zigzag", "flat"]
  "3": ["zigzag", "impulse"]
  "4": ["zigzag", "flat"]
  "5": ["zigzag", "impulse"]
```

**Design notes**

- `wave_2_max_retrace` mirrors `impulse.yaml` and goes slightly beyond
  what `ending_diagonal.yaml` declares (ED lacks this hard constraint
  today, an inconsistency this spec does not touch). An LD that fully
  retraces W1 is geometrically impossible to label.
- `wave_4_overlaps_wave_1` is hard (asymmetric vs. ED, which keeps it
  soft). Classical EW treats the overlap as definitional for both LD
  and ED, but ED's existing soft is left untouched to avoid breaking
  prior ED tuning; for LD we adopt the stricter classical reading
  because it doubles as a cheap fail-fast filter.
- Odd-numbered slots in `allowed_sub_patterns` accept both `zigzag`
  (canonical 3-3-3-3-3 LDs) and `impulse` (rarer 5-3-5-3-5 LDs).
  Informational only, since primitives do not enforce sub-patterns.

**v1 iteration history.** The originally-shipped template had
`wave_4_overlaps_wave_1` as a soft and lacked `wave_2_min_retrace`.
The Layer-3 count-layer backtest (§5.3) failed: top-3 hit rate
−6 pp (0.78 → 0.72) and runtime +23% (48m → 60m). Diagnosis: LD
displaced corrective candidates (zigzag/flat/wxy) in the top-3,
concentrating direction predictions on the motive side; the +23%
runtime came from LD's full constraint suite evaluated on every
5-pivot window. The fix promotes `wave_4_overlaps_wave_1` to hard
and adds the new `wave_2_min_retrace` hard floor; both are early-
exit filters that make LD enumeration cheap on non-diagonal
windows and limit LD's top-K presence to windows where it has a
real structural claim.

**Required companion change to `ending_diagonal.yaml`.** The engine's
`_score` formula only *penalizes* soft violations; it does not reward
passes. LD's `wave_2_deep_retrace` therefore cannot, by itself, make
LD outscore ED on a deep-W2 window — both templates accrue zero
violations on the same window and tie. To produce the
"deep W2 → LD wins / shallow W2 → ED wins" ordering the spec claims,
ED must carry a symmetric soft that *penalizes* ED on deep-W2
windows. The companion change adds:

```yaml
- name: wave_2_shallow_retrace_preferred
  severity: soft
  expr: "retrace(W2, W1) < Decimal('0.6')"
  rationale: "ED W2 typically retraces 30-60% of W1; deeper retraces are more characteristic of a leading diagonal."
```

The 0.6 threshold leaves a 0.5–0.6 "gray zone" where neither template
penalizes the W2 retrace — windows in that zone tie on this axis and
are decided by other softs (`wave_4_overlaps_wave_1`, `wave_3_not_extended`).

## 4. Module changes

| Module | Change |
|---|---|
| `src/wave_alpha/elliott/templates_data/leading_diagonal.yaml` | **NEW** — see §3 |
| `src/wave_alpha/elliott/templates_data/ending_diagonal.yaml` | **MODIFIED** — add `wave_2_shallow_retrace_preferred` soft (see §3 design notes); required for the LD-vs-ED score-disambiguation contract to hold under the engine's penalty-only `_score` formula |
| `src/wave_alpha/elliott/enumerator.py` | **MODIFIED** — `enumerate_counts` gains an `enabled_opt_in_templates: frozenset[str]` parameter that forwards to `load_default_templates`; default filter drops opt-in templates so production callers exclude LD by default |
| `src/wave_alpha/elliott/templates.py` | **MODIFIED** — new `OPT_IN_TEMPLATE_NAMES` frozenset and `load_default_templates(enabled_opt_in=...)` helper that filters the bundled set against the opt-in registry |
| `src/wave_alpha/prefs/models.py` | **MODIFIED** — new `TemplatesConfig` Pydantic model with `enable_leading_diagonal: bool = False` and `enabled_opt_in_names() -> frozenset[str]`; `AppConfig` gains a `templates: TemplatesConfig` field |
| `src/wave_alpha/pipeline.py` | **MODIFIED** — `run_analysis` and `_enumerate_for` thread `enabled_opt_in_templates` through to the enumerator |
| `src/wave_alpha/scan/orchestrator.py` | **MODIFIED** — `run_scan` and `_scan_one` thread the opt-in set |
| `src/wave_alpha/cli/app.py` | **MODIFIED** — `analyze` and `scan` CLI commands pass `cfg.templates.enabled_opt_in_names()` into the pipeline |
| `src/wave_alpha/web/deps.py` | **MODIFIED** — web `_run_analysis_uncached` passes `cfg.templates.enabled_opt_in_names()` into the pipeline |
| `src/wave_alpha/elliott/validator.py` | No change — existing operators (`length`, `retrace`, `overlaps`, `min`) cover all constraints |
| `src/wave_alpha/elliott/dsl.py` | No change — no new operators |
| `src/wave_alpha/elliott/fib_features.py` | No change — LD falls through to legacy single-ratio path |
| `tests/test_lean_install.py:66` | Bump `>= 6` template-count assertion to `>= 8` (wxy + LD = 8 bundled templates; LD is still bundled even when opt-in is off) |

## 5. Testing

### 5.1 Layer 1 — synthetic unit tests (`tests/elliott/test_leading_diagonal.py`)

Hand-built 5-wave pivot lists; assert template loading, hard-constraint
rejection, and soft-constraint score effects.

- `test_leading_diagonal_template_loads` — template appears in
  `load_bundled_templates()`; name/waves/direction_alternates correct;
  full constraint-name set matches §3.
- `test_ld_hard_rejects_w3_shortest` — pivot list with W3 strictly
  shorter than `min(W1, W5)` → no LD count emitted.
- `test_ld_hard_rejects_w2_full_retrace` — W2 retrace ≥ 1.0 → rejected.
- `test_ld_hard_rejects_w2_too_shallow` — W2 retrace < 0.3 → rejected
  by the new `wave_2_min_retrace` hard constraint (v1 iteration).
- `test_ld_hard_rejects_when_w4_does_not_overlap_w1` — W4 outside W1's
  range → rejected by the hard-promoted `wave_4_overlaps_wave_1`
  (v1 iteration).
- `test_ld_accepts_w4_overlap` — diagonal-shape window with W4
  overlapping W1 → LD count emitted (alongside ED for the same window).
- `test_ld_outscores_ed_on_deep_w2` — window with W2 retrace ≈ 0.7 →
  LD score strictly greater than ED score in the top-K.
- `test_ed_outscores_ld_on_shallow_w2` — window with W2 retrace ≈ 0.38
  → ED score strictly greater than LD score.
- `test_ld_survives_where_impulse_dies` — window with W4 overlapping W1
  → impulse is rejected on its hard `wave_4_no_overlap`; LD remains in
  top-K.
- `test_ld_dedupes_per_template` — multiple windows yielding LD
  candidates collapse to the highest-scoring instance before the
  global top-K cut, matching `best_per_template` behaviour.

### 5.2 Layer 2 — golden fixtures (`tests/elliott/fixtures/leading_diagonal_golden/`)

Loader pattern mirrors `tests/elliott/test_wxy_golden.py`. Each fixture
is a JSON file:

```json
{
  "name": "human-readable label",
  "ticker": "TICKER",
  "as_of": "YYYY-MM-DD",
  "bars": [
    {"timestamp": "...", "open": "...", "high": "...", "low": "...", "close": "...", "volume": 1234, "interval": "1d"}
  ],
  "expected_top_pattern_at_degree_2": "leading_diagonal | ending_diagonal | impulse | primitive",
  "expected_runner_up_in_topk": "ending_diagonal"
}
```

`expected_top_pattern_at_degree_2` values:
- `"leading_diagonal"` — positive: LD must be #1 at degree 2.
- `"ending_diagonal"` / `"impulse"` — negative: LD must NOT be #1.
- `"primitive"` — any non-LD pattern; regression guard.

**Required before merge:** ≥ 5 fixtures total — at least 3 positives
and 2 negatives. Synthetic seed fixtures (built from a hand-pivot
helper similar to wxy) are acceptable for the loader test; real
analyst-labelled charts arrive incrementally.

### 5.3 Layer 3 — count-layer backtest sanity gate

Run `wave-alpha backtest count --universe data/universe_curated.txt
--start 2020-01-01 --end 2024-12-31 --out <path>` on `main` and on the
LD branch. The count-layer backtest reports `top1_hit_rate`,
`top3_hit_rate`, and `sample_size` (overall and per
coherence-bucket / per-year breakdowns from `render_count_markdown`).

**Hard-fail thresholds (any one trips the gate):**
- Overall `top1_hit_rate` drops by > 2 percentage points (absolute).
- Overall `top3_hit_rate` drops by > 2 percentage points (absolute).
- `sample_size` drops by > 5% (pipeline rejecting samples it
  previously accepted).
- Runtime regresses by > 10%.

**Soft watches (investigate, do not gate):**
- `top1_hit_rate` drops in the `full`-coherence bucket — LD is winning
  on windows where the engine was previously confident; that
  confidence is now wrong.
- Any per-year bucket drops `top1_hit_rate` by > 4 pp — a regime where
  LD systematically misclassifies.

Outputs committed under `reports/leading_diagonal_count_before.md`
and `reports/leading_diagonal_count_after.md` for the PR description.

### 5.4 Layer 4 — pipeline integration test (`tests/test_pipeline.py`)

`test_run_analysis_emits_leading_diagonal_when_present` — fake
`OHLCVProvider` returns synthetic bars engineered (linear interpolation
between hand-chosen pivot prices, as in
`_build_bars_for_wxy_at_degree_2`) to produce an LD at degree 2.
Asserts:
- `result.counts_daily` contains a `leading_diagonal` entry.
- `result.coherence` is computed without raising on an LD top
  candidate.

## 6. Risks and open questions

- **R1: Top-K crowding by LD+ED duplicates.** Both templates match the
  same diagonal windows. The differentiating softs in §3 nudge scores
  apart, the existing per-template dedupe prevents intra-template
  multiplication, and the §5.3 backtest gate catches systemic
  crowding. If the soft watch on LD top-1 share fires, the next-cheapest
  tightening is converting `wave_2_deep_retrace` to a hard floor
  (`retrace(W2, W1) >= Decimal('0.382')`) or splitting LD into
  contracting/expanding variants.
- **R2: Soft-constraint weights are heuristic, not empirically fit.**
  The retrace bands (0.5 / 0.9) and W3 cap (1.618×W1) are classical EW
  values. Empirical refit via an EBT-style sweep is a separate spec
  if the v1 ranking proves wrong.
- **R3: `direction_alternates: true` assumption.** Diagonals can be
  contracting or expanding; both must satisfy `_alternates_ok`.
  Implementation must verify the existing alternation check does not
  reject expanding diagonals by construction (a unit test in §5.1
  covers this implicitly via the deep-W2 fixture).
- **Q1: Curated `fib_features` path for LD?** Out of scope per scoping
  decision. If backtest shows LD ranking is confluence-driven and
  wrong, add a curated path in a follow-up.
- **Q2: §5.3 thresholds (−2 pp hit-rate, −5% sample size, +10% runtime).**
  Chosen against actual `CountLayerResult` metrics. Open to
  tightening if reviewers prefer.

## 7. Out of scope (deferred to future specs)

- Expanding-leading-diagonal as a separate template.
- Position-aware enumeration (emit LD only when sitting at wave-1 of a
  higher-degree motive).
- `allowed_sub_patterns` enforcement on primitive templates.
- Curated `fib_features` path for LD.
- LD-specific right-edge features.
- LD-specific trade-entry signal types in `derive_signal`.
- LLM prompt changes for diagonal candidates.
- Empirical refit of LD soft-constraint weights via EBT-style sweep.

## 8. Acceptance checklist

- [ ] `leading_diagonal.yaml` ships in `templates_data/`; loaded by
      `load_bundled_templates()` but **excluded by default** via
      `load_default_templates()` (opt-in registry, see §10).
- [ ] `tests/test_lean_install.py` template-count assertion bumped to
      `>= 8` (LD remains bundled even when opt-in is off).
- [ ] All Layer-1 unit tests pass.
- [ ] ≥ 5 Layer-2 golden fixtures land (≥ 3 positives + ≥ 2 negatives).
      *Golden tests pass `enabled_opt_in_templates={"leading_diagonal"}`
      to run_analysis* — these fixtures exercise the opt-in path.
- [ ] Layer-3 backtest before/after stored locally under `reports/`
      (gitignored); default-config run identical to baseline (LD off).
- [ ] Layer-4 pipeline integration tests pass (both the opt-in path
      and the default-off path).
- [ ] `uv run ruff check .` clean.
- [ ] `uv run pytest` full suite passes; no regression in existing
      enumerator / pipeline / right-edge / template-count tests.
- [ ] Spec's "Out of scope" section honoured — no curated
      `fib_features` path, no primitive sub-pattern enforcement, no
      `derive_signal` changes leak into the PR.

## 10. Iteration history and the opt-in pivot

**Originally-shipped design.** v0 of this spec proposed adding LD as
a peer primitive (always-on) with soft-constraint disambiguation from
ED. The Layer-3 count-layer backtest gate (§5.3) was the merge gate.

**First gate run.** Failed on two metrics:
- top-3 hit rate dropped 6 percentage points (0.78 → 0.72).
- runtime regressed 23% (48m → 60m).

**Diagnosis.** LD displaced corrective candidates (zigzag/flat/wxy)
in the top-3 list. Correctives predict the opposite direction from
motives; replacing a corrective with another motive concentrated
direction predictions on the motive side, costing top-3 hit rate on
corrective moves. The runtime regression was structural — adding any
new template to the inner enumeration loop carries a dispatch cost.

**v1 iteration — tightened hard constraints.** Promoted
`wave_4_overlaps_wave_1` from soft to hard and added a new hard
`wave_2_min_retrace >= 0.3`. Goal: fail-fast on non-diagonal windows
so LD almost never reaches the scoring path. Re-ran the backtest gate.

**Second gate run.** Still failed:
- top-3 hit rate: 0.72 → 0.74 (recovered 2 pp but still −4 pp from
  baseline, twice the threshold).
- runtime: 60m → 61m (no improvement — confirmed runtime cost is
  per-template dispatch, not per-window constraint cost).

**v1 opt-in pivot.** Conclusion: adding LD as an always-on peer
primitive is not net-positive on the curated 50-ticker universe. The
spec's central premise does not hold without architectural work
(position-aware enumeration that defers LD to wave-1 positions, or
per-template short-circuit when impulse already validates the
window — both explicitly out of scope here).

Resolution: **ship LD bundled but opt-in via config**, default off.

- `AppConfig.templates.enable_leading_diagonal: bool = False` (new
  `TemplatesConfig` Pydantic model).
- `OPT_IN_TEMPLATE_NAMES = frozenset({"leading_diagonal"})` in
  `elliott.templates`.
- `load_default_templates(enabled_opt_in=...)` filters bundled
  templates against the opt-in registry.
- `enumerate_counts(..., enabled_opt_in_templates=...)` takes a
  frozenset and forwards to `load_default_templates` when `templates`
  is not explicit.
- `run_analysis` and `_enumerate_for` plumb the parameter through.
- `cli.app` (analyze, scan) and `web.deps` read
  `cfg.templates.enabled_opt_in_names()` from `AppConfig` and pass it
  in.

**Effect on the gate.** With LD opt-in off (default), the production
configuration is byte-identical to baseline `9ad7f07` on the engine
path. The Layer-3 backtest with default config passes trivially
because nothing about the production path changed. The opt-in path
remains available for users who specifically expect LDs on their
charts and accept the trade-off.

**Future work (out of scope here).** A follow-up spec could try
position-aware enumeration: only consider LD when the window sits at
the wave-1 position of a degree+1 motive. That would let LD ship
on-by-default once it has a real positional signal beyond raw pivot
geometry. Tracked separately; not gating.
