---
related:
  - docs/superpowers/specs/2026-05-08-multi-degree-enumeration-design.md  # S6 — unblocks this refit
  - docs/superpowers/specs/2026-05-07-ebt-empirical-fit-design.md  # degree-2 EBT methodology precedent
  - docs/superpowers/specs/2026-05-05-trade-sim-sanity-run.md  # §9 W1 deferred origin
---

# Empirical EBT refit for degrees 1 and 3 — design

## 1. Motivation

The 2026-05-08 multi-degree enumeration (S6, v0.5.10) ships
candidates at degrees 1, 2, and 3. `derive_signal` keys on
`count.degree` to look up `expected_bars_to_target` (median) and
`max_holding_bars` (p75) from `_EBT_MEDIAN_BY_DEGREE` and
`_EBT_P75_BY_DEGREE`. Today (v0.5.10):

- Degree 2 has empirical values (median=17, p75=29) from the
  2026-05-07 EBT spec, fit on n=148 winners from the sanity run.
- Degrees 1 and 3 use heuristic values (10/15 and 60/90
  respectively, the latter from `int(60 * 1.5)`).
- Degrees 0 and 4 use heuristic values (5/7 and 150/225) and
  are out of scope here (rare; no enumeration coverage).

The S6 spec deliberately deferred per-degree empirical fits (§9 W1)
because the data didn't exist yet — multi-degree enumeration had to
ship first to produce per-degree winner distributions.

This spec closes that gap. After v0.5.10, a multi-degree backtest
produces per-degree trade outcomes; this spec runs that backtest,
fits empirical median+p75 for degrees 1 and 3, and ships the refit
as v0.5.11.

The methodology mirrors the 2026-05-07 degree-2 fit: median →
expected_bars_to_target, p75 → max_holding_bars. Difference: the
degree-2 spec compared empirical-vs-empirical via paired bootstrap;
this spec compares empirical-vs-heuristic, which has no statistically
rigorous comparison — the criterion is simply "ship empirical iff
the per-degree winner count meets a threshold".

## 2. Goals and non-goals

**Goals**

- Produce a single trade-layer backtest under v0.5.10 defaults that
  surfaces per-degree winner distributions on the curated universe,
  2020-2024.
- Fit empirical median + p75 for degrees that meet the
  ≥100-winner threshold (per the brainstorm decision).
- Update `_EBT_MEDIAN_BY_DEGREE` and `_EBT_P75_BY_DEGREE` in
  `derive.py` for the qualifying degrees.
- Update `tests/trades/test_derive.py:test_derive_signal_per_degree_ebt`
  to reflect the new empirical values for refit degrees and keep
  heuristic assertions for degrees that didn't qualify.
- Ship as v0.5.11.

**Non-goals**

- Refitting degree 2. Its empirical values (17/29) were calibrated
  on the prior single-degree sanity run and remain the project's
  reference point. Re-fitting it on multi-degree data could shift
  the calibration baseline, breaking apples-to-apples comparisons
  with the v0.5.9 + earlier results. Out of scope.
- Refitting degrees 0 and 4. Neither is enumerated by the v0.5.10
  pipeline (`_ENUMERATED_DEGREES = (1, 2, 3)`); fitting them would
  require enumerating them first. Defer to a future S6+ extension.
- Statistical comparison of empirical vs. heuristic via bootstrap
  or hypothesis test. Heuristic values are guesses, not estimators —
  paired comparison is meaningless. The criterion is the threshold
  gate.
- Re-running prior backtests (target_R sweep, ablation grid Phase
  1/2) on the v0.5.11 refit. Those baselines are historical
  references; if they need re-validation, that is a separate spec.
- Adding tests for the inspect script's output. The script is a
  one-shot analysis tool (like `scripts/inspect_sanity_run.py`);
  manual verification is sufficient.
- Schema changes. `_EBT_MEDIAN_BY_DEGREE` and `_EBT_P75_BY_DEGREE`
  are simple dicts; no abstraction needed.

## 3. Architecture

### 3.1 Phase A — data collection

A single trade-layer backtest under v0.5.10 defaults. No grid axes
(this is one config, not a sweep).

**`configs/trade_ebt_refit_2026-05-08.yaml`** — copy of
`configs/sanity_run.yaml` with v0.5.10 defaults applied:

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

Note: `direction_modes: [long]` matches the Phase 1/2 verdict winner
(long-only); this is the v0.5.10 default behavior in spirit (the
schema default is `[long, short]` but every shipped backtest config
since Phase 1 has been long-only). Keeping it consistent with the
target_R sweep baseline and the recent multi-degree shipping
context.

Run command:

```bash
uv run wave-alpha backtest trade \
    --universe data/universe_curated.txt \
    --start 2020-01-01 --end 2024-12-31 \
    --rules configs/trade_ebt_refit_2026-05-08.yaml \
    --out reports/ebt-refit-2026-05-08/report.md \
    --trades-csv reports/ebt-refit-2026-05-08/trades.csv \
    --json reports/ebt-refit-2026-05-08/result.json
```

Wall time: ~1-2h on warm cache (single config, no axes; less work
than a 4-cell grid). The OHLCV cache is fully warm from the prior
target_R sweep.

### 3.2 Phase B — empirical fit

**`scripts/inspect_ebt_refit.py`** — one-shot Python script that
reads `reports/ebt-refit-2026-05-08/trades.csv`, filters winners,
groups by `degree`, and applies the threshold gate.

**Winner definition:** `exit_reason == "target_hit"`. Other exit
reasons (stop_out, time_stop, opposing_signal, open_at_end) are
*not* winners for EBT purposes — only target hits give a useful
bars-to-target sample. Exit reasons that are wins-on-PnL but
not target-hits (e.g., a positive realized_R from time_stop)
are excluded; they would skew the bars distribution toward the
time-stop cap rather than reflect actual target velocity.

**Per-degree fit:** for each degree d ∈ {1, 2, 3}:
1. Filter winners to that degree.
2. If `len(winners) >= 100`: compute `median(bars_held)` and
   `p75(bars_held)` (rounded to int). These become the new
   `_EBT_MEDIAN_BY_DEGREE[d]` and `_EBT_P75_BY_DEGREE[d]`.
3. If `len(winners) < 100`: skip — keep heuristic values for that
   degree. Document in verdict as "HOLD: n=X < 100".

**Output:** the script prints a summary table to stdout:

```
Per-degree EBT refit summary
============================

degree | n_winners | median_bars | p75_bars | verdict
-------|----------:|------------:|---------:|--------
   1   |    234    |       8     |    14    | SHIP
   2   |    410    |       16    |    27    | (skip — out of scope)
   3   |    127    |      52     |    78    | SHIP

Proposed dict updates:
  _EBT_MEDIAN_BY_DEGREE = {0: 5, 1: 8, 2: 17, 3: 52, 4: 150}
  _EBT_P75_BY_DEGREE = {1: 14, 2: 29, 3: 78}
```

(Values illustrative; actual numbers from the run.)

The script does NOT write the dicts back to `derive.py` — it just
prints proposed values. The human-author/agent updates `derive.py`
manually based on the script's output, ensuring the spec's verdict
file documents the decision.

### 3.3 Phase B continued — code update

For each refit degree, update `src/wave_alpha/trades/derive.py:17-22`:

```python
# Before:
_EBT_MEDIAN_BY_DEGREE: dict[int, int] = {0: 5, 1: 10, 2: 17, 3: 60, 4: 150}
_EBT_P75_BY_DEGREE: dict[int, int] = {2: 29}

# After (for degrees that passed threshold; preserve degrees 0, 2, 4 unchanged):
_EBT_MEDIAN_BY_DEGREE: dict[int, int] = {0: 5, 1: <fit>, 2: 17, 3: <fit>, 4: 150}
_EBT_P75_BY_DEGREE: dict[int, int] = {1: <fit>, 2: 29, 3: <fit>}
```

Update `tests/trades/test_derive.py:test_derive_signal_per_degree_ebt`
to assert the new values for refit degrees:

```python
# Replace the heuristic assertions for degrees that passed:
assert sig_d1.expected_bars_to_target == <new_fit>  # was 10 (heuristic)
assert sig_d1.max_holding_bars == <new_fit>          # was 15 (heuristic)
# (degree 2 unchanged — still 17/29)
assert sig_d3.expected_bars_to_target == <new_fit>  # was 60 (heuristic)
assert sig_d3.max_holding_bars == <new_fit>          # was 90 (heuristic)
```

For any degree that DID NOT pass the threshold, keep the heuristic
assertion and add an inline comment noting it.

### 3.4 Lookahead — no new surface

This spec touches data analysis (Phase A is a backtest, Phase B is
post-hoc fitting on the artifacts). No new fetch sites, no new
pipeline paths. The `PointInTimeView` boundary is unchanged. The
synthetic-leak guard at `tests/backtest/test_lookahead_guard.py`
remains green.

## 4. Threshold criterion

**SHIP threshold:** `n_winners >= 100` per degree.

**HOLD threshold:** `n_winners < 100` per degree → keep heuristic.

Per-degree independent: degree 1 may SHIP while degree 3 HOLDs (or
vice versa). The verdict file (§6) documents which degrees shipped
and which held, with the n_winners observed.

The 100-winner threshold matches the 2026-05-07 degree-2 EBT spec
precedent (n=148 for the original degree-2 fit, which shipped
MARGINAL). Degree-2's 148 was empirically OK; this threshold is a
slightly conservative floor below which median estimates start
becoming unstable.

If both degrees fail (very unlikely given the multi-degree pipeline
should produce enough trade volume across both), the spec ships
*nothing* — derive.py is unchanged, no version bump, the verdict
file documents the failure and recommends a longer-window or
broader-universe re-run as the follow-up.

## 5. File structure

**Created:**

- `configs/trade_ebt_refit_2026-05-08.yaml` — Phase A run config.
- `scripts/inspect_ebt_refit.py` — Phase B fit script.
- `reports/ebt-refit-2026-05-08/report.md` — backtest report (gitignored).
- `reports/ebt-refit-2026-05-08/trades.csv` — per-trade CSV (gitignored).
- `reports/ebt-refit-2026-05-08/result.json` — JSON sidecar (gitignored).
- `reports/ebt-refit-2026-05-08/verdict.md` — analyst commentary (gitignored).

**Modified (only if at least one degree passed threshold):**

- `src/wave_alpha/trades/derive.py:17-22` — `_EBT_MEDIAN_BY_DEGREE`
  and `_EBT_P75_BY_DEGREE` updates.
- `tests/trades/test_derive.py:test_derive_signal_per_degree_ebt` —
  assertions reflect new values.
- `pyproject.toml`, `src/wave_alpha/__init__.py`, `uv.lock` — bump
  to v0.5.11.
- `docs/superpowers/specs/2026-05-08-ebt-refit-1-and-3-design.md` —
  fill §6 verdict.

**Not modified:**

- `src/wave_alpha/pipeline.py` — no enumeration change.
- `src/wave_alpha/backtest/trade_layer.py` — no harness change.
- Any `tests/backtest/*` test — synthetic-leak guards stay green.

## 6. Verdict — degree 1 SHIP, degree 3 HOLD (2026-05-08)

Run config: curated 50-ticker universe × 2020-01-01..2024-12-31.
Wall time: 2h 57min. Total trades: 2,739. Target-hit winners: 198
(7.2% target-hit rate).

Per-degree breakdown:

| degree | n_winners | median | p75 | verdict |
|-------:|----------:|-------:|----:|---------|
|    1   |    119    |    8   |  11 | **SHIP** (was heuristic 10/15) |
|    2   |     48    |   16   |  22 | (out of scope; kept at 17/29) |
|    3   |     31    |   53   |  67 | **HOLD** (n < 100 threshold) |

**Applied:** `_EBT_MEDIAN_BY_DEGREE[1]: 10 → 8`;
`_EBT_P75_BY_DEGREE: + 1 → 11`. Degree-3 keeps heuristic 60/90.

**Censored-distribution caveat:** the empirical degree-1 p75=11 is
right-censored by the prior heuristic max_holding=15 cap (last 10
winners cluster at 14-15 bars). Future refits will produce
progressively tighter distributions due to this feedback loop. The
current methodology accepts the bias for empirical truthfulness;
explicit survival modeling (e.g., Kaplan-Meier) is deferred.

**Why degree 3 fell short:** 542 degree-3 trades produced only 31
target-hits (5.7% rate, vs 7.2% overall). Bigger targets are harder
to hit, and degree-3 patterns span ~12 weeks of consecutive moves
(rare on a 5-year window). Recorded for posterity: median=53, p75=67;
if a future broader-universe or longer-window backtest pushes
degree-3 winners past n=100, the empirical fit will likely settle
near these. Until then, heuristic 60/90 stays.

The companion verdict file at `reports/ebt-refit-2026-05-08/verdict.md`
(gitignored) contains the full breakdown and follow-up notes.

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Multi-degree backtest produces fewer per-degree winners than expected (< 100 for both degrees 1 and 3) | The verdict file documents the shortfall. Both-fail outcome ships nothing; spec is preserved as a record of the attempt. Future spec re-runs after engine improvements (e.g., broader universe, longer window) might reach the threshold. |
| Empirical median for degree 1 is unrealistically short (e.g., 3 bars) — signal lifetimes feel too tight | The fit is the data — heuristic values were guesses, not anchors. If the verdict file shows median=3, ship it (the user-facing reality is the truth). A future spec can revisit if the lived experience suggests a floor. |
| Empirical p75 for degree 3 is so long (e.g., 200+ bars) that time-stops never fire in 5-year backtests | Same as above — ship the data. Note the implication in verdict commentary. |
| Existing test pinning heuristic values (`test_derive_signal_per_degree_ebt`) breaks after dict update | Expected — update is part of the spec (§5). The test pre-existed S6 and was pinning values before this refit; updating it is the intended behavior. |
| The inspect script's median/p75 doesn't match what an analyst would compute by hand | Phase B includes a manual cross-check: the analyst computes median+p75 from the CSV using a few different tools (Python, pandas, spreadsheet) before applying the proposed dict updates. The verdict file documents the cross-check. |
| Phase A backtest takes longer than the 1-2h estimate (e.g., 4h on cold cache) | Cache is warm from prior target_R sweep, so 1-2h is realistic. If wall time exceeds 4h, abort and investigate (cache regression, harness bug, etc.). |
| Multi-degree trades' `expected_bars_to_target` field in the CSV reflects the *heuristic* values used at signal time (not the empirical fit) — using these to validate the fit would be circular | Correct caveat. The fit uses `bars_held` (actual time-to-target on winners), not `expected_bars_to_target`. The CSV's `expected_bars_to_target` field is informational only; it surfaces the heuristic that was in effect at the time of the trade. |

## 8. Open issues / deferred decisions

| # | Question | Defer to | Reason |
|---|---|---|---|
| W1 | Should degree 2 be re-fit on multi-degree data to check for regression? | Future spec | Would shift the calibration baseline. The current degree-2 values (17/29) were the project's reference point since 2026-05-07; changing them needs a paired-bootstrap comparison with the prior fit, not a fresh refit. |
| W2 | Should degrees 0 and 4 be enumerated and fit? | Future S6 extension | Requires adding them to `_ENUMERATED_DEGREES`; degree-0 is microstructure noise (likely too noisy to be useful) and degree-4 requires multi-year input (rarely produced on 5-year windows). |
| W3 | Should `expected_bars_to_target` and `max_holding_bars` be computed at signal time from the *current* dict (live values) or the *trade-time* dict (frozen)? | Out of scope | Current behavior is live values; freezing would require versioning the dicts. Not a problem this spec creates. |
| W4 | Should the inspect script auto-edit `derive.py` instead of just printing proposed dicts? | Out of scope | Auto-edit feels too magical for a one-shot analysis tool. Manual update is auditable. |
| W5 | Should winners include time-stops with positive realized_R (i.e., the trade exited profitably even though it didn't hit target)? | Out of scope; current spec says no | Including them would skew the bars distribution toward `max_holding_bars` rather than reflecting actual target velocity. Strict `target_hit`-only is the correct EBT signal. |

## 9. Roadmap impact

If at least one degree passes threshold: **v0.5.10 → v0.5.11** (minor;
behavior change for the affected degrees). Existing consumers see
slightly different EBT values for degree-1 and/or degree-3 signals.
The change is observable in trade-layer outcomes (different time-
stops) and in `analyze` output (different `expected_bars_to_target`
on signals).

If both degrees fail: no version bump. Verdict file documents the
shortfall. The spec ships as-is for record; no `derive.py` change.

After this ships, the next phase per the brainstormed sequence is
**W1 target_R follow-up sweep** (relax `max_holding_bars` for
high-R cells). The W1 sweep depends on per-degree EBT being stable;
this refit lands first.

## 10. Estimated runtime

- Spec: ~30 min (this document).
- Plan: ~30 min.
- Phase A backtest: ~1-2h compute (single config, full universe,
  warm cache).
- Phase B inspect script: ~1h (write + run + cross-check).
- `derive.py` + test update: ~30 min.
- Lint + commit + merge: ~30 min.

Total: ~3.5-4.5h active + 1-2h compute. Comparable to the target_R
sweep work (which was ~3h active + 7h compute).
