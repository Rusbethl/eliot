# target_R Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the target_R sweep per spec `2026-05-08-target-r-sweep-design.md` — produce baseline + sweep grid reports, author the verdict, and conditionally apply it (GO → ship v0.6.0 default flip; MARGINAL/NO-GO → verdict file only).

**Architecture:** Two-yaml split (baseline 1-cell + sweep 4-cell) reuses the existing `wave-alpha backtest grid` harness without modification. Smoke tests on a 2-ticker mini-universe validate yaml schema before launching the long-running full-universe runs (~3-4h total wall time).

**Tech Stack:** Python 3.11 / `uv`, existing `backtest/grid.py:run_grid` + `backtest/grid_report.py:render_grid_markdown`, `wave-alpha backtest grid` CLI, `pytest` for any test updates on GO verdict.

---

## File Structure

**New (always):**
- `configs/grid_target_r_baseline.yaml` — 1-cell baseline run config
- `configs/grid_target_r_sweep.yaml` — 4-cell fixed_R sweep config
- `data/universe_smoke.txt` — 2-ticker mini-universe for yaml smoke validation
- `reports/target-r-2026-05-08/baseline.md` — baseline harness output (gitignored)
- `reports/target-r-2026-05-08/sweep.md` — sweep harness output (gitignored)
- `reports/target-r-2026-05-08/verdict.md` — analyst commentary (gitignored)

**Modified (only on GO verdict):**
- `src/wave_alpha/trades/models.py` — flip `TradeRules.target_source` and `target_R` defaults
- `tests/trades/test_models.py:test_trade_rules_defaults` — assertions + docstring
- `tests/trades/test_derive.py` — audit any test relying on old default
- `docs/superpowers/specs/2026-05-08-target-r-sweep-design.md` — fill §6 verdict
- `pyproject.toml` + `src/wave_alpha/__init__.py` — bump to 0.6.0

---

## Branching

Working branch: `feat/target-r-sweep`. Create at the start of T1 from current `main` (HEAD `beab93f`).

```bash
git checkout main
git checkout -b feat/target-r-sweep
```

---

### Task 1: Create baseline yaml + smoke universe

**Files:**
- Create: `configs/grid_target_r_baseline.yaml`
- Create: `data/universe_smoke.txt`

- [ ] **Step 1: Create the smoke universe (2 tickers)**

Write `data/universe_smoke.txt`:

```
AAPL
MSFT
```

The mini-universe is reused across all smoke tests in this plan; it is *not* part of the actual sweep (which uses `data/universe_curated.txt`). Two tickers is small enough for a smoke run to finish in <60 seconds while still exercising the full pipeline (count enumeration, signal derivation, grid aggregation).

- [ ] **Step 2: Create the baseline yaml**

Write `configs/grid_target_r_baseline.yaml`:

```yaml
# target_R sweep baseline — fib_extension on the v0.5.9 default
# rules. Produces a single cell to compare against the 4-cell
# fixed_R sweep.  Spec: docs/superpowers/specs/2026-05-08-target-r-sweep-design.md.
axes:
  - name: target_source
    levels: [fib_extension]

fixed:
  direction_modes: [long]
  entry_trigger: immediate
  stop_source: atr_multiple
  min_confidence: 0.45
  time_stop_mode: degree_based
  slippage_bps: 5
```

- [ ] **Step 3: Smoke-validate the baseline yaml**

Run:

```bash
uv run wave-alpha backtest grid \
    --universe data/universe_smoke.txt \
    --start 2024-01-01 --end 2024-06-30 \
    --grid configs/grid_target_r_baseline.yaml \
    --out /tmp/smoke-baseline.md \
    --phase-name "smoke baseline" \
    --step 5
```

Expected: command exits 0; `/tmp/smoke-baseline.md` exists and contains a markdown table with 1 cell row, headed "smoke baseline". `--step 5` (every 5 business days) keeps the smoke run brief.

If the command fails with a yaml-parse or schema error, fix the yaml and re-run. Common issues:
- `axes` and `fixed` must not name the same field (validated by `GridConfig.__post_init__`).
- `slippage_bps` must be an int.
- `stop_source` literal must match `Literal["count_invalidation", "atr_multiple"]`.

- [ ] **Step 4: Commit**

```bash
git add data/universe_smoke.txt configs/grid_target_r_baseline.yaml
git commit -m "feat(backtest): add target_R baseline grid config + smoke universe"
```

---

### Task 2: Create sweep yaml + smoke validation

**Files:**
- Create: `configs/grid_target_r_sweep.yaml`

- [ ] **Step 1: Create the sweep yaml**

Write `configs/grid_target_r_sweep.yaml`:

```yaml
# target_R sweep — 4 fixed_R cells across {1.5, 2.0, 2.5, 3.0}.
# Baseline runs from configs/grid_target_r_baseline.yaml.
# Spec: docs/superpowers/specs/2026-05-08-target-r-sweep-design.md.
axes:
  - name: target_R
    levels: [1.5, 2.0, 2.5, 3.0]

fixed:
  target_source: fixed_R
  direction_modes: [long]
  entry_trigger: immediate
  stop_source: atr_multiple
  min_confidence: 0.45
  time_stop_mode: degree_based
  slippage_bps: 5
```

- [ ] **Step 2: Smoke-validate the sweep yaml**

Run:

```bash
uv run wave-alpha backtest grid \
    --universe data/universe_smoke.txt \
    --start 2024-01-01 --end 2024-06-30 \
    --grid configs/grid_target_r_sweep.yaml \
    --out /tmp/smoke-sweep.md \
    --phase-name "smoke sweep" \
    --step 5
```

Expected: command exits 0; `/tmp/smoke-sweep.md` exists and contains a markdown table with 4 cell rows (one per `target_R` level), headed "smoke sweep".

- [ ] **Step 3: Sanity-check the smoke output**

Open `/tmp/smoke-sweep.md`. Confirm:
- 4 rows present, with `target_R-1.5`, `target_R-2.0`, `target_R-2.5`, `target_R-3.0` cell IDs.
- Each row has finite expectancy (could be negative — smoke universe is tiny).
- The 4 cells produce **different** expectancy values (proving the dispatch actually varies behavior; if all 4 are identical, G7 wiring regressed and this plan must abort).

If all 4 are identical, stop and investigate `_target_price` dispatch. The unit test `test_target_source_fixed_R_long` should still pass (`uv run pytest tests/trades/test_derive.py::test_target_source_fixed_R_long`); if it does, the regression is in the grid harness's TradeRules construction or the trade-layer. Bisect and fix before proceeding.

- [ ] **Step 4: Commit**

```bash
git add configs/grid_target_r_sweep.yaml
git commit -m "feat(backtest): add target_R fixed_R sweep grid config"
```

---

### Task 3: Run baseline grid (full universe, long-running)

**Files:**
- Create: `reports/target-r-2026-05-08/baseline.md`

- [ ] **Step 1: Ensure reports dir exists**

```bash
mkdir -p reports/target-r-2026-05-08
```

- [ ] **Step 2: Launch baseline grid (~30-50 min)**

Run:

```bash
uv run wave-alpha backtest grid \
    --universe data/universe_curated.txt \
    --start 2020-01-01 --end 2024-12-31 \
    --grid configs/grid_target_r_baseline.yaml \
    --out reports/target-r-2026-05-08/baseline.md \
    --phase-name "target_R baseline"
```

This is a long-running command. If executing inline, use `run_in_background: true` and poll for completion. If executing via subagent-driven-development, the dispatched subagent should run this synchronously and report back.

Expected: command exits 0; `reports/target-r-2026-05-08/baseline.md` exists.

- [ ] **Step 3: Verify baseline output**

Open `reports/target-r-2026-05-08/baseline.md`. Confirm:
- 1 cell row with id `target-fib-extension`.
- `n_trades` is non-zero (expected ~1,000 trades for the curated universe over 5y at long-only, min_confidence=0.45).
- `expectancy_R` is finite (expected near +0.218R per the v0.5.9 sanity baseline; deviations >0.05R warrant investigation before proceeding to T4).
- `profit_factor`, `hit_rate`, `avg_winner_R`, `avg_loser_R` all finite.

If any of these fail, stop and investigate. Likely causes: cache regression, EBT regression, dispatch regression. Do not run T4 until baseline matches the v0.5.9 sanity baseline within 0.05R tolerance.

- [ ] **Step 4: Commit (no — reports/ is gitignored)**

`reports/` is in `.gitignore`. No commit needed for the report file. Proceed to T4.

---

### Task 4: Run sweep grid (full universe, long-running)

**Files:**
- Create: `reports/target-r-2026-05-08/sweep.md`

- [ ] **Step 1: Launch sweep grid (~2-4h)**

Run:

```bash
uv run wave-alpha backtest grid \
    --universe data/universe_curated.txt \
    --start 2020-01-01 --end 2024-12-31 \
    --grid configs/grid_target_r_sweep.yaml \
    --out reports/target-r-2026-05-08/sweep.md \
    --phase-name "target_R sweep"
```

Long-running. Cache is fully warm from T3, so per-cell time should be roughly equal (~30-50 min/cell). If executing inline, use `run_in_background: true`.

Expected: command exits 0; `reports/target-r-2026-05-08/sweep.md` exists.

- [ ] **Step 2: Verify sweep output**

Open `reports/target-r-2026-05-08/sweep.md`. Confirm:
- 4 cell rows with ids `target_R-1.5`, `target_R-2.0`, `target_R-2.5`, `target_R-3.0`.
- All cells have `n_trades` > 0 (expected ~1,000 each — should be roughly equal across cells since the count layer is identical, only target placement varies).
- `expectancy_R` finite for all 4 cells.
- `hit_rate` decreases monotonically as `target_R` increases (basic sanity — bigger targets are harder to hit).
- `avg_winner_R` increases monotonically with `target_R` (winners pay R-multiples).

If any of the monotonic sanity checks fails, stop and investigate. The dispatch may be misrouting some cells. Cross-check by re-running just the failing cell on the smoke universe.

If the cell with the lowest `n_trades` has fewer than 100 trades (per spec §8), flag in the verdict as "low confidence" and treat verdict as MARGINAL even if the headline delta is large.

- [ ] **Step 3: No commit (reports/ gitignored)**

Proceed to T5.

---

### Task 5: Author verdict file

**Files:**
- Create: `reports/target-r-2026-05-08/verdict.md`

- [ ] **Step 1: Compute the delta**

From the two report files:
- `T0_expectancy` = baseline.md `expectancy_R` for `target-fib-extension`
- `Tbest_expectancy` = max over sweep.md `expectancy_R` for the 4 fixed_R cells
- `Tbest_target_R` = the `target_R` value of the winning cell
- `Δ = Tbest_expectancy - T0_expectancy`

Apply the §4 tie-breaker if any two cells are within 0.01R: prefer the lower `target_R`.

- [ ] **Step 2: Determine the verdict tier**

Per spec §4:
- `Δ ≥ +0.05R` → **GO**: ship as v0.6.0 default
- `Δ ∈ (-0.05R, +0.05R)` → **MARGINAL**: keep fib_extension default; document fixed_R as opt-in
- `Δ ≤ -0.05R` → **NO-GO**: keep fib_extension default; flag fixed_R as discouraged

Override to MARGINAL if any cell had `n_trades < 100` (per T4 step 2).

- [ ] **Step 3: Write the verdict file**

Write `reports/target-r-2026-05-08/verdict.md` following this template (fill in numbers from steps 1-2):

```markdown
# target_R sweep verdict — 2026-05-08

Spec: `docs/superpowers/specs/2026-05-08-target-r-sweep-design.md`
Plan: `docs/superpowers/plans/2026-05-08-target-r-sweep.md`
Branch: `feat/target-r-sweep`
Engine: v0.5.9

## Cells (combined from baseline + sweep reports)

| Cell                  | n_trades | hit_rate | expectancy_R | profit_factor | avg_winner_R | avg_loser_R |
|-----------------------|----------|----------|--------------|---------------|--------------|-------------|
| target-fib-extension  | ...      | ...      | ...          | ...           | ...          | ...         |
| target_R-1.5          | ...      | ...      | ...          | ...           | ...          | ...         |
| target_R-2.0          | ...      | ...      | ...          | ...           | ...          | ...         |
| target_R-2.5          | ...      | ...      | ...          | ...           | ...          | ...         |
| target_R-3.0          | ...      | ...      | ...          | ...           | ...          | ...         |

## Winner

- Best fixed_R cell: `target_R-X.X` (expectancy_R=...)
- Baseline: `target-fib-extension` (expectancy_R=...)
- Δ = ...R
- Tie-breaker applied: yes/no — `target_R=X.X` over `target_R=Y.Y` (within 0.01R)

## Verdict: [GO | MARGINAL | NO-GO]

[2-3 sentences justifying the tier classification, referencing the
delta and the §4 thresholds. If MARGINAL or NO-GO, note any sample-
size caveats from T4 step 2.]

## Curve shape

[2-3 sentences on whether expectancy is monotonic/peaked/flat across
target_R values. Note any cell with anomalously low n_trades.]

## Recommendation

[Per the verdict tier, the action to take. Reference spec §10 roadmap
impact for the corresponding code/doc changes.]

## Open follow-ups

[List any §9 deferred decisions (W1-W5) that this run's results have
made more or less interesting. E.g., "MARGINAL → W1 (relax max_holding)
becomes the natural next experiment."]
```

- [ ] **Step 4: No commit (reports/ gitignored)**

Proceed to T6 if GO; else jump to T8.

---

### Task 6 (GO only): Apply default flip + version bump

Skip this task entirely if verdict is MARGINAL or NO-GO. Jump to T8.

**Files:**
- Modify: `src/wave_alpha/trades/models.py:17-19`
- Modify: `tests/trades/test_models.py:9-21`
- Modify: `pyproject.toml:7`
- Modify: `src/wave_alpha/__init__.py:1`

- [ ] **Step 1: Write the failing test for the new default**

Replace `test_trade_rules_defaults` body in `tests/trades/test_models.py:9-21` with assertions reflecting the GO verdict winner. Example for `target_R=2.0` winner:

```python
def test_trade_rules_defaults() -> None:
    """The default target_source is fixed_R with target_R=2.0 (post-
    target_R-sweep verdict 2026-05-08): the sweep established a
    +X.XXXR sensitivity gap over fib_extension on the curated
    universe.  See docs/superpowers/specs/2026-05-08-target-r-sweep-design.md §6."""
    r = TradeRules()
    assert "long" in r.direction_modes and "short" in r.direction_modes
    assert r.entry_trigger == "immediate"
    assert r.stop_source == "atr_multiple"
    assert r.target_source == "fixed_R"
    assert r.target_R == 2.0
    assert r.min_confidence == 0.45
```

Replace `target_R == 2.0` with the actual winner. Replace `+X.XXXR` with the actual delta.

- [ ] **Step 2: Run the test to verify it fails (RED)**

```bash
uv run pytest tests/trades/test_models.py::test_trade_rules_defaults -v
```

Expected: FAIL — current default is `target_source="count_target"`.

- [ ] **Step 3: Flip the defaults in models.py**

Edit `src/wave_alpha/trades/models.py:18-19`:

```python
    target_source: Literal["count_target", "fib_extension", "fixed_R"] = "fixed_R"
    target_R: float | None = 2.0  # noqa: N815
```

Replace `2.0` with the actual winner.

- [ ] **Step 4: Run the test to verify it passes (GREEN)**

```bash
uv run pytest tests/trades/test_models.py::test_trade_rules_defaults -v
```

Expected: PASS.

- [ ] **Step 5: Run full suite to find broken downstream tests**

```bash
uv run pytest 2>&1 | tail -40
```

Expected: some tests fail because they implicitly assumed the old default. Most likely candidates:
- `tests/trades/test_derive.py` — tests using `TradeRules()` without `target_source` will now exercise fixed_R; some may need explicit `target_source="fib_extension"` or `target_source="count_target"` to preserve the original test intent.
- `tests/trades/test_derive_fixed_r_target.py` — likely already explicit; verify.
- `tests/test_pipeline.py` — same pattern as the atr_multiple migration (commit `a092e0e`).

For each failure, decide: was the test intentionally pinning the old default (add explicit `target_source="fib_extension"` to keep behavior), or pinning generic behavior (let it use the new default)? Mirror the discipline used in the v0.5.8 atr_multiple flip migration.

- [ ] **Step 6: Update each broken test with explicit target_source**

For each test that fails because it relied on the old default, add `target_source="fib_extension"` to the `TradeRules()` call site. Pattern:

```python
# Before
rules=TradeRules(stop_source="count_invalidation"),
# After
rules=TradeRules(stop_source="count_invalidation", target_source="fib_extension"),
```

Re-run `uv run pytest` after each batch until clean.

- [ ] **Step 7: Bump version**

Edit `pyproject.toml:7`:

```toml
version = "0.6.0"
```

Edit `src/wave_alpha/__init__.py:1`:

```python
__version__ = "0.6.0"
```

- [ ] **Step 8: Sync uv.lock**

```bash
uv sync --extra dev
```

Expected: uv.lock updates the wave-alpha entry to 0.6.0.

- [ ] **Step 9: Run lint + final test pass**

```bash
uv run ruff check src/wave_alpha/trades/models.py tests/trades/test_models.py tests/trades/test_derive.py
uv run ruff format --check src/wave_alpha/trades/models.py tests/trades/test_models.py tests/trades/test_derive.py
uv run pytest 2>&1 | tail -5
```

Expected: all clean, all green.

- [ ] **Step 10: Commit**

```bash
git add src/wave_alpha/trades/models.py tests/trades/test_models.py \
    tests/trades/test_derive.py tests/trades/test_derive_fixed_r_target.py \
    tests/test_pipeline.py pyproject.toml src/wave_alpha/__init__.py uv.lock
git commit -m "$(cat <<'EOF'
feat(trades): default target_source flips to fixed_R (target_R=X.X)

Per the 2026-05-08 target_R sweep verdict (§6 of spec), fixed_R with
target_R=X.X beat fib_extension by +X.XXXR expectancy on the curated
50-ticker universe over 2020-2024.

Tests pinning the prior default add explicit target_source=fib_extension.

v0.5.9 → v0.6.0.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Replace `X.X` and `X.XXXR` with actual values from the verdict.

---

### Task 7 (GO only): Fill spec §6 and commit

Skip if verdict is MARGINAL or NO-GO.

**Files:**
- Modify: `docs/superpowers/specs/2026-05-08-target-r-sweep-design.md:215-219`

- [ ] **Step 1: Replace §6 placeholder with verdict body**

Edit the §6 section, replacing:

```markdown
## 6. Verdict

To be filled after the run completes. Format mirrors the Phase 2
§6.4 verdict block: cell table, winner delta, tier classification,
recommendation, and any open issues.
```

With a body matching the structure of `reports/target-r-2026-05-08/verdict.md` (the gitignored verdict can be referenced; the spec §6 inlines the key facts so the spec is self-contained).

- [ ] **Step 2: Commit spec update**

```bash
git add docs/superpowers/specs/2026-05-08-target-r-sweep-design.md
git commit -m "docs(spec): target_R sweep §6 verdict — GO @ target_R=X.X (+X.XXXR)"
```

---

### Task 8 (MARGINAL or NO-GO only): Document opt-in / discouraged status

Skip if verdict is GO. Jump to T9.

**Files:**
- Modify: `docs/superpowers/specs/2026-05-08-target-r-sweep-design.md:215-219`
- Modify (MARGINAL only): `CLAUDE.md` (add fixed_R opt-in note in trade-rules section)

- [ ] **Step 1: Fill spec §6 with the negative verdict**

Same as T7 step 1 but reflecting the MARGINAL or NO-GO outcome.

- [ ] **Step 2 (MARGINAL only): Add an opt-in note to CLAUDE.md**

Find the section in CLAUDE.md about trade rules / signal derivation. Add a sentence noting that `target_source="fixed_R"` with `target_R` is implemented and unit-tested but did not beat the fib_extension default on the curated universe. Reference the verdict file location.

For NO-GO, the same note but with stronger language: "implemented for completeness but underperforms the default; do not use in production configs without revalidation."

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-05-08-target-r-sweep-design.md
# add CLAUDE.md if MARGINAL
git commit -m "docs(spec): target_R sweep §6 verdict — [MARGINAL|NO-GO]"
```

---

### Task 9: Merge to main

**Files:** none (git-only)

- [ ] **Step 1: Verify final test pass on the branch**

```bash
uv run pytest 2>&1 | tail -3
```

Expected: 506+ passed (more if T6 added tests).

- [ ] **Step 2: Switch to main and merge with --no-ff**

```bash
git checkout main
git pull origin main
git merge --no-ff feat/target-r-sweep -m "Merge feat/target-r-sweep — target_R sweep verdict + [v0.6.0|spec-only]"
```

Adjust the merge message: "v0.6.0" if GO, "spec-only" if MARGINAL/NO-GO.

- [ ] **Step 3: Verify tests on merged main**

```bash
uv run pytest 2>&1 | tail -3
```

Expected: same green result as step 1.

- [ ] **Step 4: Push to origin**

```bash
git push origin main
```

- [ ] **Step 5: Delete the feature branch**

```bash
git branch -d feat/target-r-sweep
```

- [ ] **Step 6: Confirm origin is in sync**

```bash
git log --oneline -3
git status
```

Expected: HEAD shows the merge commit + verdict commit; working tree clean.

---

## Self-review checklist (run before declaring plan ready)

- [x] Spec coverage: every §3 component (config, harness, verdict file) → T1, T2, T3, T4, T5
- [x] Spec §4 verdict tiers → T5 step 2 + branching to T6/T7/T8
- [x] Spec §5 conditional file changes → T6 (GO only)
- [x] Spec §10 roadmap impact (v0.6.0 bump on GO) → T6 step 7
- [x] Spec §11 run commands → T3 step 2 + T4 step 1 (matches verbatim)
- [x] Spec §12 baseline-first run discipline → T3 before T4
- [x] No "TBD" / "TODO" placeholders in steps
- [x] Every step that runs a command shows the exact command + expected result
- [x] Every step that writes code shows the actual code

## Notes for the executor

- T3 and T4 are wall-clock-long (~3-4h combined). If executing via subagent, dispatch each as its own subagent so the parent can review intermediate results.
- The verdict tier in T5 determines which subsequent tasks run. Do not run T6/T7 if verdict is MARGINAL or NO-GO; do not run T8 if verdict is GO.
- The smoke tests in T1/T2 are non-negotiable — they catch yaml schema errors in <60 seconds and prevent burning ~4h of compute on a misconfigured run.
- After merging, the immediate next phase per the brainstormed roadmap is **S6 multi-degree enumeration** — that work is independent of this verdict.
