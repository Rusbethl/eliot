# EBT Empirical Fit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the degree-2 entry of the heuristic `_DEFAULT_BARS_PER_DEGREE` lookup with the empirical median (17 bars) from the 2026-05-05 sanity run, and decouple `max_holding_bars` from the hardcoded `× 1.5` multiplier by adding a separate per-degree p75 lookup (degree-2 = 29). Validate against the ablation grid's Phase 2 winner cell.

**Architecture:** Two small constants and a one-line dispatch change in `src/wave_alpha/trades/derive.py`. No schema changes, no artifact files, no `TradeRules` knobs. Validation is a single-cell re-run of the grid's Phase 2 winner with the refit applied; pass criterion is `|expectancy delta| ≤ 0.05R`. Sequenced post-grid-merge to avoid invalidating Phase 2 metrics mid-run.

**Tech Stack:** Python 3.11, `uv`, `ruff`, `pytest`, `pydantic` v2 (no schema change). No new external dependencies.

**Spec:** `docs/superpowers/specs/2026-05-07-ebt-empirical-fit-design.md`

**Branch:** `feat/ebt-empirical-fit` (already created at the spec commit `7c2a88d`).

---

## File structure

**Modified:**
- `src/wave_alpha/trades/derive.py` — rename `_DEFAULT_BARS_PER_DEGREE` → `_EBT_MEDIAN_BY_DEGREE`, update degree-2 entry from `25` to `17`, add new `_EBT_P75_BY_DEGREE: dict[int, int] = {2: 29}` constant, change `max_hold` derivation from `int(expected * 1.5)` to `_EBT_P75_BY_DEGREE.get(count.degree, int(expected * 1.5))`. ~10 lines including comments.
- `tests/trades/test_derive.py` — add 2 new tests pinning the empirical fit + the heuristic fallback for unfit degrees.
- `docs/superpowers/specs/2026-05-05-trade-sim-sanity-run.md` — close §9 S2 with a reference to this spec.
- `docs/superpowers/specs/2026-05-06-ablation-grid-design.md` — close §9 G4 with a reference to this spec.
- `pyproject.toml`, `src/wave_alpha/__init__.py`, `uv.lock` — version bump to v0.5.7 at merge time.
- `docs/superpowers/specs/2026-05-07-ebt-empirical-fit-design.md` — fill §6 (validation results) at merge time.

**New (transient, gitignored):**
- `reports/ebt-empirical-fit-2026-05-XX/{report.md,result.json,trades.csv,comparison.md}` — validation artifacts.

**Not modified:**
- `src/wave_alpha/trades/models.py` — no schema change.
- `src/wave_alpha/backtest/trade_layer.py` — engine numerics unchanged.
- `src/wave_alpha/pipeline.py` — no change.
- `tests/backtest/_synthetic.py` — `_record(expected_bars_to_target=10)` is a fixture default unrelated to the runtime fit; stays as-is.

**Sequencing constraint:** Tasks 1–2 (code refit + sibling-spec doc updates) can land any time. Tasks 3–5 (validation pass + final review + bump) are **chronologically gated on the ablation grid spec merging to `main`** — the validation reads the grid's Phase 2 winner cell. Until grid v0.5.6 lands, the branch parks at the end of Task 2.

---

## Task 1: Refit `derive.py` with empirical lookups

**Files:**
- Modify: `src/wave_alpha/trades/derive.py`
- Modify: `tests/trades/test_derive.py`

- [ ] **Step 1: Write failing test for the degree-2 empirical fit**

Append to `tests/trades/test_derive.py`:

```python
def test_derive_signal_uses_empirical_ebt_for_degree_2() -> None:
    """Degree-2 setups produce signals with empirical EBT (median=17, p75=29)
    from the sanity run, not the prior heuristic (25, 38)."""
    count = _bullish_w5_setup()  # degree=2 by default
    sig = derive_signal(
        count=count,
        ticker="AAPL",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(),
    )
    assert sig is not None
    assert sig.degree == 2
    assert sig.expected_bars_to_target == 17
    assert sig.max_holding_bars == 29
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/trades/test_derive.py::test_derive_signal_uses_empirical_ebt_for_degree_2 -xvs
```

Expected: `assert sig.expected_bars_to_target == 17` fails (current value is 25).

- [ ] **Step 3: Apply the empirical refit in `derive.py`**

In `src/wave_alpha/trades/derive.py`, replace the existing `_DEFAULT_BARS_PER_DEGREE` block (around line 17) and the `max_hold` line (around line 48) with the empirical lookups:

```python
# Median bars-to-target, per degree.  Degree 2 is empirical (sanity run
# 2026-05-05, n=148 winners on the curated 50-ticker universe over
# 2020-2024, median=17 bars).  Other degrees retain the pre-empirical
# heuristic until multi-degree enumeration ships and produces data.
_EBT_MEDIAN_BY_DEGREE: dict[int, int] = {0: 5, 1: 10, 2: 17, 3: 60, 4: 150}

# p75 bars-to-target, per degree, used as the time-stop upper bound.
# Degree 2 is empirical (p75=29 of winners).  Other degrees fall back
# to median * 1.5 inside derive_signal (the existing heuristic ratio).
_EBT_P75_BY_DEGREE: dict[int, int] = {2: 29}
```

In `derive_signal`, replace these two lines:

```python
    expected = _DEFAULT_BARS_PER_DEGREE.get(count.degree, 25)
    max_hold = int(expected * 1.5)
```

with:

```python
    expected = _EBT_MEDIAN_BY_DEGREE.get(count.degree, 25)
    max_hold = _EBT_P75_BY_DEGREE.get(count.degree, int(expected * 1.5))
```

The `_DEFAULT_BARS_PER_DEGREE` symbol is renamed to `_EBT_MEDIAN_BY_DEGREE` (no other call sites — confirm via `grep -rn "_DEFAULT_BARS_PER_DEGREE" src/ tests/` returns nothing after the change).

- [ ] **Step 4: Run the failing test to verify it passes**

```bash
uv run pytest tests/trades/test_derive.py::test_derive_signal_uses_empirical_ebt_for_degree_2 -xvs
```

Expected: PASS.

- [ ] **Step 5: Add the heuristic-fallback test for unfit degrees**

Append to `tests/trades/test_derive.py`:

```python
def test_derive_signal_falls_back_to_heuristic_for_unfit_degrees() -> None:
    """Degrees other than 2 retain the pre-empirical heuristic
    (count.degree=3 → expected=60, max_holding=60*1.5=90).  Pinned so
    that adding empirical fits for new degrees later doesn't silently
    flip degree-3 behavior without a deliberate test update."""
    count = _bullish_w5_setup().model_copy(update={"degree": 3})
    sig = derive_signal(
        count=count,
        ticker="AAPL",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(),
    )
    assert sig is not None
    assert sig.degree == 3
    assert sig.expected_bars_to_target == 60
    assert sig.max_holding_bars == 90
```

- [ ] **Step 6: Run both new tests + the full test suite**

```bash
uv run pytest tests/trades/test_derive.py -k "empirical_ebt or unfit_degrees" -v
```

Expected: 2 passed.

```bash
uv run pytest -x
```

Expected: full suite green.  No existing tests assert on `expected_bars_to_target` or `max_holding_bars` values from `derive_signal` (verified by `grep -rn "expected_bars_to_target\s*==\|max_holding_bars\s*==" tests/ src/` returning only the synthetic-record test in `tests/backtest/test_trade_layer.py:754`, which constructs a `TradeRecord` directly with `expected_bars_to_target=20` and is unrelated to the runtime fit).

- [ ] **Step 7: Confirm no stale `_DEFAULT_BARS_PER_DEGREE` references remain**

```bash
grep -rn "_DEFAULT_BARS_PER_DEGREE" src/ tests/
```

Expected: empty output.

- [ ] **Step 8: Lint**

```bash
uv run ruff check src/wave_alpha/trades/derive.py tests/trades/test_derive.py
```

Expected: `All checks passed!`

- [ ] **Step 9: Commit**

```bash
git add src/wave_alpha/trades/derive.py tests/trades/test_derive.py
git commit -m "$(cat <<'EOF'
feat(derive): EBT empirical fit — refit degree-2 from sanity run

Replaces _DEFAULT_BARS_PER_DEGREE[2]=25 with the empirical median
(17 bars, n=148 winners from the 2026-05-05 sanity run on the curated
50-ticker universe over 2020-2024).  Decouples max_holding_bars from
the hardcoded × 1.5 multiplier by adding a separate
_EBT_P75_BY_DEGREE lookup (degree 2 = 29, the empirical p75).  Other
degrees fall back to median * 1.5 (preserving today's behavior) until
multi-degree enumeration ships and produces empirical data for them.

Spec: docs/superpowers/specs/2026-05-07-ebt-empirical-fit-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Close sibling-spec deferred decisions

**Files:**
- Modify: `docs/superpowers/specs/2026-05-05-trade-sim-sanity-run.md`
- Modify: `docs/superpowers/specs/2026-05-06-ablation-grid-design.md`

This task records the resolution of two deferred decisions. The original entries say "Separate follow-up spec — fitting changes engine behavior, which would invalidate this run's metrics."  The follow-up is now this spec.

- [ ] **Step 1: Resolve §9 S2 in the sanity-run spec**

Find this row in `docs/superpowers/specs/2026-05-05-trade-sim-sanity-run.md` §9:

```markdown
| S2 | Should `expected_bars_to_target` re-fitting happen in this branch? | Separate follow-up spec | Fitting changes engine behavior, which would invalidate this run's metrics. The fit lands as its own spec, with its own A/B comparison against the heuristic. |
```

Replace with:

```markdown
| S2 | Should `expected_bars_to_target` re-fitting happen in this branch? | **Resolved by `2026-05-07-ebt-empirical-fit-design.md`** | The follow-up spec ships the degree-2 empirical fit (median=17, p75=29) with a single-cell validation pass against the grid's Phase 2 winner. |
```

- [ ] **Step 2: Resolve §9 G4 in the grid spec**

Find this row in `docs/superpowers/specs/2026-05-06-ablation-grid-design.md` §9:

```markdown
| G4 | Should `expected_bars_to_target` be empirically refit before Phase 2? | T7 spec | T7 is its own spec keyed off the sanity-run distribution. Refitting changes engine behavior, which would invalidate Phase 1's metrics. Order: Phase 1 → Phase 2 → T7 fit (if motivated). |
```

Replace with:

```markdown
| G4 | Should `expected_bars_to_target` be empirically refit before Phase 2? | **Resolved by `2026-05-07-ebt-empirical-fit-design.md`** | The follow-up spec ships post-grid-merge with a single-cell validation pass against the Phase 2 winner.  Order is preserved: Phase 1 → Phase 2 → EBT fit. |
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-05-05-trade-sim-sanity-run.md docs/superpowers/specs/2026-05-06-ablation-grid-design.md
git commit -m "$(cat <<'EOF'
docs(specs): close S2 + G4 deferrals — EBT refit ships separately

Both prior specs deferred the EBT empirical refit to a separate
follow-up.  The follow-up landed at
docs/superpowers/specs/2026-05-07-ebt-empirical-fit-design.md.
Update the §9 deferred-decisions tables to reference the resolution
spec.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Validation pass — re-run the grid's Phase 2 winner cell post-refit

**Files:**
- Read-only on the grid Phase 2 artifacts at `reports/grid-2026-05-06/phase2/`.
- Create: `configs/ebt_validation_winner.yaml` — one-cell `TradeRules` matching the Phase 2 winner.
- Create: `reports/ebt-empirical-fit-2026-05-XX/` directory with run artifacts (gitignored).

**SEQUENCING:** This task runs **only after the ablation grid spec has merged to `main`** (which lands the Phase 2 winner cell + the §6.4 verdict).  Confirm the grid is merged (`git log main --oneline | grep "Merge feat/ablation-grid-spec"`) before starting.

- [ ] **Step 1: Identify the Phase 2 winner cell**

```bash
grep -A 8 "Best cell" reports/grid-2026-05-06/phase2/grid.md
```

Note the cell's:
- `cell_id`
- All axis values (`entry_trigger`, `stop_source`, `target_source`, `min_confidence`)
- The `fixed` block: `direction_modes`, `target_R` (if applicable), `slippage_bps`, `time_stop_mode`

- [ ] **Step 2: Pull the cell's pre-refit headline metrics**

```bash
uv run python -c "
import json
cell_id = 'PASTE_FROM_STEP_1'  # e.g., 'entry-immediate_stop-count-invalidation_target-fib-extension_minconf-0.55'
p = json.load(open(f'reports/grid-2026-05-06/phase2/cells/{cell_id}/result.json'))
h = p['headline']
print(f'pre-refit headline:')
print(f'  closed_trades: {h[\"closed_trades\"]}')
print(f'  hit_rate:      {h[\"hit_rate\"]:.4f}')
print(f'  avg_R:         {h[\"avg_R\"]:+.4f}')
print(f'  expectancy:    {h[\"expectancy\"]:+.4f}')
print(f'  profit_factor: {h[\"profit_factor\"]}')
print(f'  max_drawdown:  {h[\"max_drawdown_R\"]:+.4f}')
"
```

Capture this output — it's the pre-refit baseline for the comparison table.

- [ ] **Step 3: Build the validation YAML config**

Create `configs/ebt_validation_winner.yaml` with the Phase 2 winner's `TradeRules` (a flat YAML, NOT a grid YAML — this feeds `wave-alpha backtest trade --rules`, not `backtest grid`):

```yaml
# Phase 2 winner cell (replicated for the EBT-refit validation pass).
# Spec: docs/superpowers/specs/2026-05-07-ebt-empirical-fit-design.md §3.3
# Source: reports/grid-2026-05-06/phase2/grid.md "Best cell"
direction_modes: <FILL FROM STEP 1>
entry_trigger: <FILL FROM STEP 1>
stop_source: <FILL FROM STEP 1>
target_source: <FILL FROM STEP 1>
target_R: <FILL — null if target_source != fixed_R, else 2.0>
slippage_bps: 5
time_stop_mode: degree_based
min_confidence: <FILL FROM STEP 1>
```

Verify it round-trips:

```bash
uv run python -c "
import yaml
from wave_alpha.trades.models import TradeRules
r = TradeRules.model_validate(yaml.safe_load(open('configs/ebt_validation_winner.yaml')))
print(r)
"
```

Expected: prints the populated TradeRules with no validation errors.

- [ ] **Step 4: Capture run metadata**

```bash
mkdir -p reports/ebt-empirical-fit-$(date -u +%Y-%m-%d)
RUN_DIR=$(ls -d reports/ebt-empirical-fit-* | tail -1)
git rev-parse HEAD > $RUN_DIR/code_sha.txt
date -u +%Y-%m-%dT%H:%M:%SZ > $RUN_DIR/run_started_at.txt
cat $RUN_DIR/code_sha.txt $RUN_DIR/run_started_at.txt
```

- [ ] **Step 5: Run the post-refit single-cell backtest**

```bash
uv run wave-alpha backtest trade \
  --universe data/universe_curated.txt \
  --start 2020-01-01 --end 2024-12-31 \
  --step 5 \
  --rules configs/ebt_validation_winner.yaml \
  --out $RUN_DIR/report.md \
  --json $RUN_DIR/result.json \
  --trades-csv $RUN_DIR/trades.csv \
  > $RUN_DIR/run.log 2>&1; echo "EXIT=$?" >> $RUN_DIR/run.log
date -u +%Y-%m-%dT%H:%M:%SZ > $RUN_DIR/run_finished_at.txt
```

Expected: ~22 min wall time on a warm cache (matches the sanity-run / per-cell grid timing).  Exit code 0.  Artifacts written.

- [ ] **Step 6: Verify artifacts**

```bash
test -s $RUN_DIR/report.md && \
  test -s $RUN_DIR/result.json && \
  test -s $RUN_DIR/trades.csv && \
  echo "all three artifacts non-empty"
```

Expected: `all three artifacts non-empty`.

- [ ] **Step 7: Compute the comparison table**

```bash
uv run python <<'PY'
import json
import os
from pathlib import Path

RUN_DIR = sorted(Path("reports").glob("ebt-empirical-fit-*"))[-1]
PRE_CELL_ID = "PASTE_FROM_TASK3_STEP_1"

post = json.load(open(RUN_DIR / "result.json"))["headline"]
pre = json.load(open(f"reports/grid-2026-05-06/phase2/cells/{PRE_CELL_ID}/result.json"))["headline"]

def fmt(v):
    if v is None:
        return "∞"
    return f"{v:+.4f}" if isinstance(v, float) else str(v)

print("| metric | pre-refit (Phase 2) | post-refit | delta |")
print("|---|---:|---:|---:|")
for key in ("closed_trades", "hit_rate", "avg_R", "expectancy", "profit_factor", "max_drawdown_R"):
    pre_v = pre.get(key)
    post_v = post.get(key)
    if isinstance(pre_v, (int, float)) and isinstance(post_v, (int, float)):
        delta = post_v - pre_v
        print(f"| {key} | {fmt(pre_v)} | {fmt(post_v)} | {fmt(delta)} |")
    else:
        print(f"| {key} | {fmt(pre_v)} | {fmt(post_v)} | — |")

exp_delta = abs(post["expectancy"] - pre["expectancy"])
print(f"\nExpectancy delta magnitude: {exp_delta:.4f} R")
if exp_delta <= 0.05:
    print("VERDICT: PASS — refit ships (|delta| ≤ 0.05 R).")
elif exp_delta <= 0.10:
    print("VERDICT: MARGINAL — ship with note (0.05 < |delta| ≤ 0.10 R).")
else:
    print("VERDICT: FAIL — do not ship (|delta| > 0.10 R).  Document and revert.")
PY
```

Save the output to `$RUN_DIR/comparison.md`:

```bash
# Re-run the same script with output redirection
uv run python -c "<paste the script>" > $RUN_DIR/comparison.md
```

Or paste the printed output manually into a markdown file.

- [ ] **Step 8: Decide PASS / MARGINAL / FAIL based on Step 7 output**

- **PASS** (`|delta| ≤ 0.05 R`): proceed to Task 4 to fill the spec.
- **MARGINAL** (`0.05 < |delta| ≤ 0.10 R`): proceed to Task 4, but the spec §6 verdict is "ship with note." Document the regression source explicitly (most likely: the tighter time-stop is closing winners that previously held longer).
- **FAIL** (`|delta| > 0.10 R`): proceed to Task 4 to fill §6 with the FAIL verdict, then **revert this branch unmerged**. Likely root cause: the time-stop was implicitly carrying the engine; tightening it removes hidden edge.  A NO-SHIP outcome is documented work, not failed work.

- [ ] **Step 9: Commit the validation config**

```bash
git add configs/ebt_validation_winner.yaml
git commit -m "$(cat <<'EOF'
feat(configs): EBT validation cell — Phase 2 winner config

Replicates the ablation grid Phase 2 winner cell as a flat TradeRules
YAML for the EBT-empirical-fit single-cell validation pass.  Source:
reports/grid-2026-05-06/phase2/grid.md "Best cell".

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

(Run artifacts under `reports/ebt-empirical-fit-*/` are gitignored — no commit.)

---

## Task 4: Fill spec §6 with validation results

**Files:**
- Modify: `docs/superpowers/specs/2026-05-07-ebt-empirical-fit-design.md`

- [ ] **Step 1: Locate §6 of the spec**

Open `docs/superpowers/specs/2026-05-07-ebt-empirical-fit-design.md` and find the §6 "Validation results (filled at merge time)" section.

- [ ] **Step 2: Fill the metadata block + comparison table + verdict**

Replace the placeholder block with the actual results:

```markdown
> **Pre-refit cell:** `<PASTE Phase 2 winner cell_id>` — expectancy `<pre>` R, n=`<pre_n>`
> **Post-refit run code SHA:** `<PASTE from Task 3 Step 4>`
> **Run date:** `<PASTE from run_started_at.txt>`

| metric | pre-refit (Phase 2) | post-refit | delta |
|---|---:|---:|---:|
| closed_trades | <pre> | <post> | <delta> |
| hit_rate | <pre> | <post> | <delta> |
| avg_R | <pre> | <post> | <delta> |
| expectancy | <pre> | <post> | <delta> |
| profit_factor | <pre> | <post> | <delta> |
| max_drawdown_R | <pre> | <post> | <delta> |
```

Tick the appropriate verdict checkbox in §6:

- `[x] **PASS**` — refit ships, `|expectancy delta| ≤ 0.05 R`.
- `[x] **MARGINAL**` — ship with note, `0.05 < |delta| ≤ 0.10 R`.
- `[x] **FAIL**` — do not ship, `|delta| > 0.10 R`. Branch reverts unmerged.

Add a 1–3 sentence reasoning paragraph explaining the delta's likely source:

- For PASS: typically "tighter `max_holding_bars` (29 vs 38) closes a small fraction of slow winners; the empirical median is closer to typical exit timing so the EBT field is now more honest."
- For MARGINAL: "tighter time-stop closed `<N>` winners that previously held to target. Net expectancy degraded by `<delta>`R but PF improved/degraded; the trade-off is that the EBT signal field is now empirically truthful at the cost of `<X>%` of historical winners."
- For FAIL: "the Phase 2 winner's edge depended on holding stalled trades past the empirical p75. Tightening `max_holding_bars` to 29 removes that hidden edge, knocking expectancy by `<delta>`R. Refit is invalid for this winner cell."

- [ ] **Step 3: Commit the §6 fill**

```bash
git add docs/superpowers/specs/2026-05-07-ebt-empirical-fit-design.md
git commit -m "$(cat <<'EOF'
docs(spec): EBT empirical fit §6 validation results — <PASS|MARGINAL|FAIL>

Single-cell validation against the ablation grid Phase 2 winner
(<cell_id>) shows post-refit expectancy = <X.XXX>R vs pre-refit
<X.XXX>R, delta <X.XXX>R.  Verdict: <PASS|MARGINAL|FAIL>.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

(Adjust the placeholder values in the message to match the actual numbers.)

---

## Task 5: Final review + bump to v0.5.7

Run regardless of validation verdict iff the verdict is PASS or MARGINAL.  If the verdict is FAIL, **skip Task 5 entirely** and revert the branch unmerged (the spec records the negative finding as documented work, but no version is published).

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/wave_alpha/__init__.py`
- Modify: `uv.lock`

- [ ] **Step 1: Final test pass**

```bash
uv run pytest
```

Expected: all green (existing 499+ tests + 2 new EBT tests).

- [ ] **Step 2: Lint**

```bash
uv run ruff check .
```

Expected: `All checks passed!`

- [ ] **Step 3: Bump `pyproject.toml`**

Edit `pyproject.toml`. Change `version = "0.5.6"` (the version after the grid lands) to:

```toml
version = "0.5.7"
```

- [ ] **Step 4: Bump runtime `__version__`**

Edit `src/wave_alpha/__init__.py`. Change `__version__ = "0.5.6"` to `__version__ = "0.5.7"`.

- [ ] **Step 5: Refresh the lock**

```bash
uv lock
```

Expected: `Updated wave-alpha v0.5.6 -> v0.5.7`.

- [ ] **Step 6: Verify the bump**

```bash
uv run wave-alpha version
```

Expected: prints `0.5.7`.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/wave_alpha/__init__.py
git commit -m "$(cat <<'EOF'
chore(release): bump to v0.5.7

EBT empirical fit ships: degree-2 _DEFAULT_BARS_PER_DEGREE entry
replaced with the empirical median (17 bars) from the 2026-05-05
sanity run.  max_holding_bars decoupled from the × 1.5 multiplier;
degree-2 now uses the empirical p75 (29).  Validated against the
ablation grid Phase 2 winner cell — see
docs/superpowers/specs/2026-05-07-ebt-empirical-fit-design.md §6.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 8: Print branch summary**

```bash
git log --oneline main..HEAD
```

Expected: 5 commits — spec, refit (T1), sibling-spec close (T2), validation config (T3), spec §6 fill (T4), version bump (T5).

---

## Final Review Boundary

```bash
uv run pytest
uv run ruff check .
git log --oneline main..HEAD
```

Expected: full suite green, lint clean, branch summary ready for merge to `main` (merge-commit per project convention).

---

## Self-Review Checklist (controller)

- [ ] **Spec coverage:** every spec section maps to a task — §3.1 code change → T1; §3.2 no other consumer changes → confirmed in T1 Step 6's full-suite check; §3.3 validation pass → T3 + T4; §3.4 sequencing → noted in plan header + T3 prerequisite; §4 tests → T1 Steps 1+5; §5 file structure → plan File Structure section; §6 validation results → T4; §7 risks (single-cell narrow; informational EBT field change; etc.) → no implementation tasks (documented in spec); §8 deferred → no implementation tasks; §9 roadmap → T5 version bump.
- [ ] **Placeholder scan:** every step has actual code or commands. Task 3 explicitly threads through the user filling axis values from the Phase 2 winner — that's a parameterized step, not a placeholder. Task 4's §6 fill uses templated paragraphs with explicit "Adjust to match the actual numbers" notes.
- [ ] **Type consistency:** `_EBT_MEDIAN_BY_DEGREE: dict[int, int]` and `_EBT_P75_BY_DEGREE: dict[int, int]` are consistent across T1 Step 3. The `expected` and `max_hold` local variables in `derive_signal` keep their existing types (int).
- [ ] **No phantom symbols:** `_EBT_MEDIAN_BY_DEGREE`, `_EBT_P75_BY_DEGREE`, `_DEFAULT_BARS_PER_DEGREE` (the renamed-from constant) are all defined or removed in T1. No external module references — verified by `grep -rn "_DEFAULT_BARS_PER_DEGREE" src/ tests/` returning empty after T1 (Step 7 enforces).
- [ ] **TDD:** T1 is failing-test-first. T2 is doc-only. T3, T4, T5 are operational/documentation/metadata respectively, no test discipline because there's no behavior change in those phases.
- [ ] **Frequent commits:** 5 commits across 5 tasks. Each is self-contained and revertible. The validation config commit (T3 Step 9) lands separately from the spec fill (T4) so the gitignored run artifacts stay out of the commit graph.
