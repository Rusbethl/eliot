# EBT Refit for Degrees 1 and 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a v0.5.10 multi-degree backtest, fit empirical median+p75 EBT for degrees 1 and 3 (≥100-winner threshold), update `derive.py` dicts, ship as v0.5.11.

**Architecture:** Two-phase: Phase A is a single-config trade-layer backtest (~1-2h compute). Phase B is a one-shot Python inspect script that reads the trades CSV, filters to `target_hit` winners, computes per-degree median+p75, and prints proposed dict updates. Manual `derive.py` update applies passing degrees; degrees that don't meet the 100-winner threshold keep heuristic values.

**Tech Stack:** Python 3.11 / `uv`, `wave-alpha backtest trade` CLI, `pytest`, `ruff`.

---

## File Structure

**Created:**
- `configs/trade_ebt_refit_2026-05-08.yaml` — Phase A run config.
- `scripts/inspect_ebt_refit.py` — Phase B fit script.
- `reports/ebt-refit-2026-05-08/{report.md,trades.csv,result.json,verdict.md}` — Phase A outputs + verdict (gitignored).

**Modified (conditional on at least one degree passing threshold):**
- `src/wave_alpha/trades/derive.py:17-22` — `_EBT_MEDIAN_BY_DEGREE` and `_EBT_P75_BY_DEGREE` literals.
- `tests/trades/test_derive.py:test_derive_signal_per_degree_ebt` — assertions reflect new empirical values.
- `pyproject.toml`, `src/wave_alpha/__init__.py`, `uv.lock` — bump to v0.5.11.
- `docs/superpowers/specs/2026-05-08-ebt-refit-1-and-3-design.md` — fill §6 verdict.

**Branch:** `feat/ebt-refit-1-and-3` already created from `main` HEAD `031c785`. Spec is committed at `5a972e7` on this branch.

---

### Task 1: Create Phase A backtest config

**Files:**
- Create: `configs/trade_ebt_refit_2026-05-08.yaml`

- [ ] **Step 1: Write the yaml**

Create `configs/trade_ebt_refit_2026-05-08.yaml`:

```yaml
# Trade-layer config for the 2026-05-08 EBT refit backtest.
# Purpose: collect per-degree trade outcomes from the v0.5.10
# multi-degree pipeline so degrees 1 and 3 can be empirically fit.
# Spec: docs/superpowers/specs/2026-05-08-ebt-refit-1-and-3-design.md.
direction_modes: [long]
entry_trigger: immediate
stop_source: atr_multiple
target_source: fib_extension
target_R: null
time_stop_mode: degree_based
time_stop_bars: null
max_concurrent_signals_per_ticker: 1
slippage_bps: 5
commission_per_share: "0"
short_borrow_annual_pct: 0.005
min_confidence: 0.45
```

- [ ] **Step 2: Smoke-validate the yaml**

Run a 60-second smoke against the existing `data/universe_smoke.txt` (2 tickers, AAPL+MSFT) over a 6-month window:

```bash
mkdir -p /tmp/ebt-smoke
uv run wave-alpha backtest trade \
    --universe data/universe_smoke.txt \
    --start 2024-01-01 --end 2024-06-30 \
    --rules configs/trade_ebt_refit_2026-05-08.yaml \
    --out /tmp/ebt-smoke/report.md \
    --trades-csv /tmp/ebt-smoke/trades.csv \
    --json /tmp/ebt-smoke/result.json \
    --step 5
```

Expected: command exits 0; `/tmp/ebt-smoke/trades.csv` exists with rows; the CSV header includes `degree`, `bars_held`, `exit_reason`, `realized_R`. Confirm by:

```bash
head -1 /tmp/ebt-smoke/trades.csv
```

The header should contain those four columns (plus others). If the yaml fails to parse, fix it (common issues: `slippage_bps` must be int, `commission_per_share` must be quoted as a string, `direction_modes` is a YAML list).

- [ ] **Step 3: Commit**

```bash
git add configs/trade_ebt_refit_2026-05-08.yaml
git commit -m "feat(backtest): add EBT refit Phase A config"
```

Do NOT stage `AGENTS.md` or `CLAUDE.md` (gitnexus drift, unrelated).

---

### Task 2: Run Phase A backtest (long-running)

**Files:**
- Create: `reports/ebt-refit-2026-05-08/{report.md, trades.csv, result.json}`

- [ ] **Step 1: Ensure reports dir exists**

```bash
mkdir -p reports/ebt-refit-2026-05-08
```

- [ ] **Step 2: Launch Phase A backtest**

Long-running command (~1-2h on warm cache). Use `run_in_background: true` on the Bash tool; the controller will be notified when complete.

```bash
uv run wave-alpha backtest trade \
    --universe data/universe_curated.txt \
    --start 2020-01-01 --end 2024-12-31 \
    --rules configs/trade_ebt_refit_2026-05-08.yaml \
    --out reports/ebt-refit-2026-05-08/report.md \
    --trades-csv reports/ebt-refit-2026-05-08/trades.csv \
    --json reports/ebt-refit-2026-05-08/result.json
```

Expected: exits 0; all three files exist.

- [ ] **Step 3: Verify Phase A output**

```bash
ls -la reports/ebt-refit-2026-05-08/
wc -l reports/ebt-refit-2026-05-08/trades.csv
head -1 reports/ebt-refit-2026-05-08/trades.csv
```

Expected: `report.md`, `trades.csv`, `result.json` all present. The CSV should have several thousand rows (multi-degree should produce more than the v0.5.9 baseline of 2242). Header contains `degree`, `bars_held`, `exit_reason`, `realized_R`.

If the run produces < 1000 trades, something is wrong (cache issue, regression). Investigate before proceeding to Phase B.

- [ ] **Step 4: No commit**

`reports/` is gitignored. Proceed to T3.

---

### Task 3: Create Phase B inspect script

**Files:**
- Create: `scripts/inspect_ebt_refit.py`

- [ ] **Step 1: Write the script**

Create `scripts/inspect_ebt_refit.py`:

```python
"""Compute per-degree empirical EBT (median + p75) from the
2026-05-08 multi-degree backtest's trades.csv.

Filters to winners (exit_reason == "target_hit"), groups by degree,
applies the >=100-winner threshold gate, and prints proposed
_EBT_MEDIAN_BY_DEGREE and _EBT_P75_BY_DEGREE dict updates.

Run:
    uv run python scripts/inspect_ebt_refit.py

The script is read-only: it does NOT edit src/wave_alpha/trades/derive.py.
The verdict spec author applies the proposed dict updates manually
based on the script's output.

Spec: docs/superpowers/specs/2026-05-08-ebt-refit-1-and-3-design.md.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from statistics import median

TRADES_CSV = Path("reports/ebt-refit-2026-05-08/trades.csv")
THRESHOLD = 100  # min winners per degree to ship empirical fit
DEGREES_IN_SCOPE = (1, 3)  # degree 2 is out of scope (kept at 17/29)


def _percentile(sorted_values: list[int], p: float) -> int:
    """Inclusive linear interpolation; rounds to int (bars are integer-valued)."""
    if not sorted_values:
        raise ValueError("empty sequence")
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = k - lo
    return round(sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac)


def main() -> int:
    if not TRADES_CSV.exists():
        print(f"ERROR: {TRADES_CSV} does not exist. Run Phase A first.")
        return 1

    by_degree_winners: dict[int, list[int]] = defaultdict(list)
    total_trades = 0
    total_target_hits = 0

    with TRADES_CSV.open() as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            total_trades += 1
            if row["exit_reason"] != "target_hit":
                continue
            total_target_hits += 1
            degree = int(row["degree"])
            bars_held = int(row["bars_held"])
            by_degree_winners[degree].append(bars_held)

    print(f"Total trades: {total_trades}")
    print(f"Target hits (winners): {total_target_hits}")
    print()
    print(f"{'degree':>6} | {'n_winners':>9} | {'median':>6} | {'p75':>4} | verdict")
    print(f"{'-'*6}-+-{'-'*9}-+-{'-'*6}-+-{'-'*4}-+--------")

    proposed_median: dict[int, int] = {0: 5, 2: 17, 4: 150}
    proposed_p75: dict[int, int] = {2: 29}

    for degree in sorted(by_degree_winners.keys()):
        bars_list = sorted(by_degree_winners[degree])
        n = len(bars_list)
        med = round(median(bars_list)) if bars_list else 0
        p75 = _percentile(bars_list, 0.75) if bars_list else 0

        if degree not in DEGREES_IN_SCOPE:
            verdict = "(out of scope)"
        elif n >= THRESHOLD:
            verdict = "SHIP"
            proposed_median[degree] = med
            proposed_p75[degree] = p75
        else:
            verdict = f"HOLD: n={n} < {THRESHOLD}"

        print(f"{degree:>6} | {n:>9} | {med:>6} | {p75:>4} | {verdict}")

    print()
    print("Proposed dict updates (apply manually to src/wave_alpha/trades/derive.py):")
    print(f"  _EBT_MEDIAN_BY_DEGREE: dict[int, int] = {dict(sorted(proposed_median.items()))}")
    print(f"  _EBT_P75_BY_DEGREE: dict[int, int] = {dict(sorted(proposed_p75.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

The script is read-only; it does not write to `derive.py` (per spec §3.2).

- [ ] **Step 2: Lint the script**

```bash
uv run ruff check scripts/inspect_ebt_refit.py
uv run ruff format --check scripts/inspect_ebt_refit.py
```

Expected: clean. If not, fix and re-run.

- [ ] **Step 3: Commit**

```bash
git add scripts/inspect_ebt_refit.py
git commit -m "feat(backtest): add EBT refit Phase B inspect script"
```

---

### Task 4: Run inspect script + record results

**Files:** none (analysis only)

- [ ] **Step 1: Run the script**

```bash
uv run python scripts/inspect_ebt_refit.py
```

Expected output: per-degree summary table with `n_winners`, `median`, `p75`, and `SHIP|HOLD|(out of scope)` verdict per degree. Final lines show proposed `_EBT_MEDIAN_BY_DEGREE` and `_EBT_P75_BY_DEGREE` dicts.

Capture the output verbatim — it's needed for the verdict file (T6) and the dict update (T5).

- [ ] **Step 2: Cross-check with a quick second method**

Sanity-check one degree's median+p75 by hand. Pick the degree with the most winners (likely degree 2). Compute the median and p75 from the CSV using a different tool:

```bash
python3 -c "
import csv
from statistics import median
with open('reports/ebt-refit-2026-05-08/trades.csv') as fh:
    rows = [int(r['bars_held']) for r in csv.DictReader(fh)
            if r['exit_reason'] == 'target_hit' and int(r['degree']) == 2]
rows.sort()
print(f'n={len(rows)}, median={median(rows)}, p75={rows[int(len(rows)*0.75)]}')
"
```

The values should roughly match the script's degree-2 output (small discrepancy in p75 OK due to interpolation method differences). If they're wildly off, the script has a bug — investigate before applying T5.

- [ ] **Step 3: Decide ship/hold per degree**

For each of degrees 1 and 3:

- If `n_winners >= 100` and the median+p75 values look reasonable (median ≥ 1, p75 > median, neither is absurd like > 500): **SHIP**.
- If `n_winners < 100`: **HOLD** (keep heuristic values for that degree).
- If values look anomalous (e.g., median = 0 from rounding, or p75 > 500 bars): **HOLD with note** in verdict; investigate the CSV manually.

Record the decision per degree. T5 applies only the SHIP-marked degrees.

- [ ] **Step 4: No commit yet**

T5 staged with the dict update.

---

### Task 5: Update `derive.py` and tests (conditional)

**Skip this task entirely if both degrees 1 and 3 are HOLD.** Jump to T6.

**Files:**
- Modify: `src/wave_alpha/trades/derive.py:17` (and possibly :22)
- Modify: `tests/trades/test_derive.py:test_derive_signal_per_degree_ebt`

- [ ] **Step 1: Update `_EBT_MEDIAN_BY_DEGREE`**

Edit `src/wave_alpha/trades/derive.py:17`. Apply only the SHIP-marked degrees from T4.

Example for degree-1 SHIP (median=8) and degree-3 SHIP (median=52); preserve degrees 0, 2, 4 unchanged:

```python
_EBT_MEDIAN_BY_DEGREE: dict[int, int] = {0: 5, 1: 8, 2: 17, 3: 52, 4: 150}
```

If only degree 1 ships (degree 3 holds), keep `3: 60` heuristic. If only degree 3 ships, keep `1: 10` heuristic. Document the rationale by referencing T4's verdict.

- [ ] **Step 2: Update `_EBT_P75_BY_DEGREE`**

Edit `src/wave_alpha/trades/derive.py:22`.

Example for both degrees SHIP (degree-1 p75=14, degree-3 p75=78):

```python
_EBT_P75_BY_DEGREE: dict[int, int] = {1: 14, 2: 29, 3: 78}
```

Degree 2's `29` is preserved unchanged (out of scope). For HOLD degrees, omit them from this dict — `derive.py` line 53's fallback `int(expected * 1.5)` handles the missing-degree case.

- [ ] **Step 3: Update the per-degree EBT test**

Edit `tests/trades/test_derive.py:test_derive_signal_per_degree_ebt`. For each SHIP degree, replace the heuristic value with the empirical fit. For HOLD degrees, keep the heuristic assertion and add a comment.

Example (both degrees SHIP, values 8/14 and 52/78):

```python
def test_derive_signal_per_degree_ebt() -> None:
    """The C5 invariant from the sanity run: per-degree EBT diversity
    is non-trivial.  Degree-2 signals use empirical EBT (17/29);
    degree-1 use empirical 8/14 (refit 2026-05-08); degree-3 use
    empirical 52/78 (refit 2026-05-08).  Pins the wiring through
    derive_signal at the unit level so multi-degree enumeration
    produces signals with degree-appropriate time-stops."""
    base = _bullish_w5_setup()  # degree=2 by default

    sig_d1 = derive_signal(
        count=base.model_copy(update={"degree": 1}),
        ticker="TEST",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(stop_source="count_invalidation"),
    )
    sig_d2 = derive_signal(
        count=base,
        ticker="TEST",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(stop_source="count_invalidation"),
    )
    sig_d3 = derive_signal(
        count=base.model_copy(update={"degree": 3}),
        ticker="TEST",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(stop_source="count_invalidation"),
    )

    assert sig_d1 is not None
    assert sig_d1.expected_bars_to_target == 8
    assert sig_d1.max_holding_bars == 14

    assert sig_d2 is not None
    assert sig_d2.expected_bars_to_target == 17
    assert sig_d2.max_holding_bars == 29

    assert sig_d3 is not None
    assert sig_d3.expected_bars_to_target == 52
    assert sig_d3.max_holding_bars == 78
```

If one degree HOLDs and the other SHIPs, update only the SHIP degree's assertions and add an inline comment to the HOLD's heuristic assertion (e.g., `# heuristic — held per 2026-05-08 verdict, n=X < 100`).

The other existing tests (`test_derive_signal_uses_empirical_ebt_for_degree_2`, `test_derive_signal_falls_back_to_heuristic_for_unfit_degrees`) may need updates:

- `test_derive_signal_uses_empirical_ebt_for_degree_2` — unchanged (degree 2 is out of scope).
- `test_derive_signal_falls_back_to_heuristic_for_unfit_degrees` — currently tests degree 3 with heuristic 60/90. If degree 3 SHIPs, this test breaks. Either:
  - Repurpose it to test a *truly* unfit degree (e.g., degree 4, which keeps heuristic 150/225), OR
  - Delete it (the C5 test now covers all 3 enumerated degrees).

Choose based on whether degree 4 testing has ongoing value. Recommend: repurpose to degree 4 if heuristic-fallback coverage matters; delete if redundant.

- [ ] **Step 4: Run the modified tests**

```bash
uv run pytest tests/trades/test_derive.py -v
```

Expected: all green. If `test_derive_signal_falls_back_to_heuristic_for_unfit_degrees` fails (because degree 3 now has empirical), apply the fix from step 3.

- [ ] **Step 5: Run full suite**

```bash
uv run pytest 2>&1 | tail -3
```

Expected: 509 passed (or 508 if one test was deleted; or 509 if repurposed).

- [ ] **Step 6: Lint**

```bash
uv run ruff check src/wave_alpha/trades/derive.py tests/trades/test_derive.py
uv run ruff format --check src/wave_alpha/trades/derive.py tests/trades/test_derive.py
```

Expected: clean. If format wants reformat, run `uv run ruff format <file>`.

- [ ] **Step 7: No commit yet**

T7 bundles the derive.py + test + version-bump commit.

---

### Task 6: Author verdict file

**Files:**
- Create: `reports/ebt-refit-2026-05-08/verdict.md`

- [ ] **Step 1: Compose the verdict**

Write `reports/ebt-refit-2026-05-08/verdict.md` using this template (fill in actual numbers from T4):

```markdown
# EBT refit (degrees 1 and 3) verdict — 2026-05-08

Spec: `docs/superpowers/specs/2026-05-08-ebt-refit-1-and-3-design.md`
Plan: `docs/superpowers/plans/2026-05-08-ebt-refit-1-and-3.md`
Branch: `feat/ebt-refit-1-and-3`
Engine: v0.5.10 → v0.5.11 (if any degree shipped)

## Phase A backtest

Universe: `data/universe_curated.txt` (50 tickers).
Window: 2020-01-01 → 2024-12-31.
Wall time: <fill in>.
Total trades: <N>.
Target-hit winners: <N>.

## Per-degree empirical fit

| degree | n_winners | median | p75 | verdict |
|-------:|----------:|-------:|----:|---------|
|    1   |      ...  |    ... | ... | SHIP|HOLD |
|    2   |      ...  |    ... | ... | (out of scope) |
|    3   |      ...  |    ... | ... | SHIP|HOLD |

Cross-check: the script's degree-2 median+p75 was independently
verified against a Python one-liner on the same CSV; values matched
within ±1 bar.

## Verdict per degree

### Degree 1: [SHIP|HOLD]

[2-3 sentences. If SHIP: state new median+p75 and the prior heuristic
values (10/15) for comparison. If HOLD: state n_winners observed and
why it fell short.]

### Degree 3: [SHIP|HOLD]

[2-3 sentences. If SHIP: state new median+p75 and the prior heuristic
values (60/90). If HOLD: state n_winners observed.]

## Applied changes

[List the dict updates applied to derive.py. If both degrees HOLD,
state "no code changes; spec preserved as a record".]

## Open follow-ups

[List relevant W1-W5 from spec §8 that this run made more or less
interesting. Examples:
- "W1 (refit degree 2 on multi-degree data) — still deferred; this
  run's degree-2 numbers (n=X, median=Y, p75=Z) are recorded for
  future comparison if a degree-2 refit spec is opened."
- "W5 (winner definition) — current target_hit-only filter produced
  N% of trades as winners; alternative definitions could expand the
  sample but would skew the bars distribution."]
```

- [ ] **Step 2: No commit (gitignored)**

`reports/` is gitignored. Proceed to T7.

---

### Task 7: Bump version + commit (conditional)

**Skip this task entirely if both degrees HOLD (no code change).** In that case, jump to T8.

**Files:**
- Modify: `pyproject.toml:7`
- Modify: `src/wave_alpha/__init__.py:1`
- Modify: `docs/superpowers/specs/2026-05-08-ebt-refit-1-and-3-design.md` (fill §6)

- [ ] **Step 1: Bump pyproject.toml**

Edit `pyproject.toml` line 7. Replace:

```toml
version = "0.5.10"
```

With:

```toml
version = "0.5.11"
```

- [ ] **Step 2: Bump `__init__.py`**

Edit `src/wave_alpha/__init__.py` line 1. Replace:

```python
__version__ = "0.5.10"
```

With:

```python
__version__ = "0.5.11"
```

- [ ] **Step 3: Sync uv.lock**

```bash
uv sync --extra dev --extra ui
```

The `--extra ui` is required to keep `fastapi` etc. installed (mirror of the issue caught during multi-degree merge — `--extra dev` alone removes ui deps).

Expected: `uv.lock` updates the wave-alpha entry to 0.5.11.

- [ ] **Step 4: Fill spec §6 verdict**

Edit `docs/superpowers/specs/2026-05-08-ebt-refit-1-and-3-design.md` §6 (currently the "To be filled" placeholder). Replace with a condensed inline version of the verdict file's content (per-degree table, ship/hold decisions, dict updates, follow-ups).

- [ ] **Step 5: Final test pass**

```bash
uv run pytest 2>&1 | tail -3
```

Expected: 509 passed (or 508 if a test was deleted in T5 step 3).

- [ ] **Step 6: Stage and commit**

Stage only the project files:

```bash
git add src/wave_alpha/trades/derive.py \
    tests/trades/test_derive.py \
    pyproject.toml \
    src/wave_alpha/__init__.py \
    uv.lock \
    docs/superpowers/specs/2026-05-08-ebt-refit-1-and-3-design.md
```

Verify staging:

```bash
git status --short
```

Files staged (`M `) should be only those above. Files unstaged (` M`) should be the gitnexus drift on AGENTS.md / CLAUDE.md.

- [ ] **Step 7: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(trades): empirical EBT refit for degrees 1 and 3 — closes §9 W1

Per the 2026-05-08 EBT refit verdict (§6 of spec):
- degree 1: <ship status with median/p75 numbers>
- degree 3: <ship status with median/p75 numbers>

Methodology mirrors 2026-05-07 degree-2 EBT spec: median →
expected_bars_to_target, p75 → max_holding_bars; ≥100-winner
threshold gate per degree.  Heuristic→empirical comparison; no
paired bootstrap (heuristic values aren't statistical estimators).

v0.5.10 → v0.5.11.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Replace `<ship status with median/p75 numbers>` placeholders with actual results from T4 (e.g., "SHIP empirical 8/14 (was heuristic 10/15)" or "HOLD heuristic 60/90 — n=87 < 100 winners").

---

### Task 8: Merge to main + push

**Files:** none (git-only)

- [ ] **Step 1: Discard gitnexus drift on AGENTS.md / CLAUDE.md**

```bash
git checkout -- AGENTS.md CLAUDE.md
```

(The drift will regenerate after the merge from the post-commit hook.)

- [ ] **Step 2: Switch to main and merge**

```bash
git checkout main
git merge --no-ff feat/ebt-refit-1-and-3 -m "Merge feat/ebt-refit-1-and-3 — EBT refit + v0.5.11"
```

If both degrees were HOLD (no code change), use this merge message instead:
```bash
git merge --no-ff feat/ebt-refit-1-and-3 -m "Merge feat/ebt-refit-1-and-3 — EBT refit verdict (HOLD both degrees)"
```

- [ ] **Step 3: Verify tests on merged main**

```bash
uv run pytest 2>&1 | tail -3
```

Expected: same green result as T7 step 5.

- [ ] **Step 4: Push to origin**

```bash
git push origin main
```

- [ ] **Step 5: Delete feature branch**

```bash
git branch -d feat/ebt-refit-1-and-3
```

- [ ] **Step 6: Confirm origin sync**

```bash
git log --oneline -5
git status
```

Expected: HEAD shows merge commit + verdict commit + spec commit; working tree shows only gitnexus drift.

---

## Self-review checklist (run before declaring plan ready)

- [x] Spec coverage: §3.1 Phase A → T1, T2 ; §3.2 Phase B → T3, T4 ; §3.3 code update → T5 ; §4 threshold → T4 step 3 + T5 ; §5 file structure → T1, T3, T5, T6, T7 ; §6 verdict → T6, T7 step 4 ; §10 v0.5.11 bump → T7
- [x] Spec §7 risks (cache slowness, fewer winners, anomalous values) → T2 step 3 (volume check), T4 step 3 (decision matrix)
- [x] Spec §8 W1-W5 deferred → none touched (correctly out of scope)
- [x] No "TBD" / "TODO" placeholders in steps (the `<fit>` and `<ship status>` are templated values to be filled at execution time, not unresolved design questions)
- [x] Every step that runs a command shows the exact command + expected result
- [x] Every step that writes code shows the actual code

## Notes for the executor

- T2 is wall-clock-long (~1-2h). If executing inline, use Bash's `run_in_background: true` and wait for the bash-completion notification. Don't dispatch a subagent for T2 — controller-direct background bash is the proven pattern (the multi-degree backtest used the same approach).
- T4 step 2 (cross-check) is non-negotiable — catches script bugs before applying derive.py changes.
- T5 is conditional. If both degrees HOLD, skip directly to T6 (verdict file documents the failure) and then T8 (merge with the spec-only commit). T7 (version bump) is also skipped in that case.
- The verdict file at `reports/ebt-refit-2026-05-08/verdict.md` is gitignored; the spec §6 fill in T7 step 4 is the only verdict text that ends up in version control.
- After merging, the immediate next phase per the brainstormed roadmap is **W1 target_R follow-up sweep** (relax `max_holding_bars` for high-R cells). It depends on per-degree EBT being stable; this refit lands first.
