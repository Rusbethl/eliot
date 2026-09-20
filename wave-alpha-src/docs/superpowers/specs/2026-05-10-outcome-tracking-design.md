---
related:
  - docs/superpowers/specs/2026-05-09-wave-count-history-design.md  # v1 — snapshot persistence; this spec layers outcomes on top
  - src/wave_alpha/history/  # existing snapshot module
  - src/wave_alpha/backtest/trade_layer.py  # _walk_to_exit, source of the shared resolver
  - src/wave_alpha/trades/models.py  # TradeSignal: entry / stop / target / max_holding_bars
---

# Outcome tracking on history snapshots — design

**Status:** Draft
**Author:** alexchen
**Date:** 2026-05-10
**Related code:** `src/wave_alpha/history/`, `src/wave_alpha/backtest/trade_layer.py`, `src/wave_alpha/trades/`, `src/wave_alpha/pipeline.py`, `src/wave_alpha/web/routes/deepdive.py`, `src/wave_alpha/web/templates/_history.html`

## 1. Motivation

The 2026-05-09 wave-count history spec persists daily snapshots of the engine's view and exposes a "stability badge" on the deepdive page — the user can see whether the engine's top pick has been stable. That answers *"is the engine consistent?"* but explicitly defers *"is the engine right?"* to v2. The trust gap closes only when both questions have an audit trail in the same place.

This spec lifts the gap: every snapshot whose `signal` is non-null gets walked forward against subsequent daily bars and produces an `Outcome` — `target` / `stop` / `time_stop` / `pending`, with realized R, MFE, MAE, and bars-held. The deepdive timeline grows an outcome chip on every resolved row and a headline outcome badge alongside the existing stability badge.

Beyond user-facing trust, the resolved outcomes form the queryable ground-truth dataset that several deferred features depend on:

- Right-edge model retraining on realized outcomes (currently trained on synthetic / pivot-confirmation labels).
- LLM blend (α) evaluation: does the rerank improve expectancy on live signals, not just on backtest replay?
- Cross-version engine comparison once `engine_version` snapshots accumulate.

The v1 history spec foresaw this exact follow-up — section 12 explicitly notes "Adding `outcome_json` to the payload (or a separate `outcomes` table keyed on `(ticker, as_of)`) is additive." This spec executes that plan.

## 2. Goals

- Every snapshot with a `signal` gets an `outcome` field populated as bars elapse: `pending` while in window, then one of `target` / `stop` / `time_stop` at exit.
- Outcomes are **raw engine accuracy** — no slippage, commission, or short-borrow. R is `(exit_price − entry_price) × dir / risk` using the engine's published prices.
- The single resolver function is shared with `backtest/trade_layer.py` so simulation and live-history outcomes can't diverge.
- The deepdive timeline shows a per-row outcome chip and a headline outcome badge ("X of Y resolved hit target · avg ±R · N in flight").
- A `wave-alpha resolve` CLI command runs the resolver in batch for cron / backfill use.
- Outcomes are queryable in SQL via an `outcome_status` column for downstream retraining / evaluation workflows.

## 3. Non-goals (v2)

- **Slippage- or commission-adjusted R** in outcomes. The backtest applies these; the deepdive does not. (Decided in brainstorm Q1.)
- **Chart overlays** annotating entries and exits on the price chart. Cleanly layers on the per-row data later; deferred to a separate spec.
- **Right-edge retraining using the new outcome dataset.** Data becomes queryable here; the retraining pipeline is its own spec.
- **LLM-blend A/B evaluation** using outcome data. Same — separate spec.
- **Cross-ticker outcome aggregation** ("the engine hit 64% globally"). Per-ticker badge is the v2 surface.
- **Outcome-driven push notifications** (e.g., "AAPL signal hit target today"). Notification layer is separate.
- **Resolving outcomes against historical signal mutations.** Snapshot upsert today already overwrites same-day signals; the resolver evaluates against the latest-stored signal for each `as_of`. That is the unit of record.
- **Watchlist cron** triggering bulk resolves. The CLI primitive is shipped; the cron itself remains a v1-deferred follow-on.

## 4. User-visible behavior

When the user opens `/deepdive/AAPL`:

1. The chart, coherence, right-edge, counts, plan, scenarios, and existing stability-badge sections render unchanged.
2. The History card grows:
   - The stability badge is unchanged.
   - A new **Outcomes** badge appears immediately under it: e.g. *"Outcomes: 3 of 5 resolved (60%) hit target · avg +0.7R · 2 still in flight."* Hidden when no rows have resolved.
   - Each timeline row grows a right-edge **outcome chip** (see §7.1 for glyphs and colors).
3. The card lazy-loads exactly as v1 — outcome chips render server-side from snapshot payload, no extra fetches.

When the user runs `wave-alpha analyze AAPL`: the same outcome resolution piggybacks on the snapshot write — every unresolved or pending AAPL snapshot whose window has fresh bars to walk gets re-resolved.

When the user runs `wave-alpha resolve --ticker AAPL` or `--all`: bulk resolution over the SQLite store. Reports `{rows_resolved, rows_still_pending, errors}` per ticker.

When the user runs `wave-alpha scan` or any backtest: history (and therefore outcome resolution) is *not* touched. The v1 carve-out remains.

## 5. Architecture

### 5.1 Module split

| Module | Purpose |
|---|---|
| `src/wave_alpha/trades/outcome.py` (new) | Pure resolver: `resolve_outcome(signal, bars, *, as_of_close) -> Outcome \| None`. No slippage/commission/borrow. No knowledge of history or backtest. The single source of bar-walk truth. |
| `src/wave_alpha/history/models.py` | Add `SnapshotOutcome` Pydantic model; extend `Snapshot` with `outcome: SnapshotOutcome \| None = None`. |
| `src/wave_alpha/history/resolver.py` (new) | `resolve_pending_for(ticker, daily_bars, *, db_path, today)`. Loads unresolved rows, calls `resolve_outcome`, upserts updated payloads. |
| `src/wave_alpha/history/store.py` | Add `unresolved_for(ticker)`; extend `_ensure_schema` to add `outcome_status` column and index (idempotent). |
| `src/wave_alpha/history/service.py` | Extend `record_snapshot` to call the resolver after upserting today's row; extract `compute_outcome_badge` pure function. |
| `src/wave_alpha/backtest/trade_layer.py` | Replace inline bar-walk logic in `_walk_to_exit` with a call to `trades.outcome.resolve_outcome()` plus an adapter that layers slippage / commission / borrow. Behavior unchanged. |
| `src/wave_alpha/pipeline.py` | Add `AnalyzeResult.daily_bars: list[Bar] = field(default_factory=list)`. Strictly additive. |
| `src/wave_alpha/cli/app.py` | New top-level command `wave-alpha resolve [--ticker T] [--all] [--json]`. Lazy-imports `wave_alpha.history`. |
| `src/wave_alpha/web/templates/_history.html` | Render per-row outcome chip and the outcome badge in the card header. |

### 5.2 Data flow on `record_snapshot`

```
analyze runs                      → AnalyzeResult (with daily_bars)
  ↓
record_snapshot(result, T, as_of) → store.upsert(today's snapshot)
  ↓
resolve_pending_for(T, daily_bars) → for each unresolved/pending row for T:
                                       resolve_outcome(snapshot.signal, post-as_of bars)
                                       → upsert snapshot with .outcome populated
```

The resolver runs synchronously on the request thread. Single-row SQLite upserts; ~14 unresolved rows in the typical case; sub-10ms in aggregate even on a cold ticker. Errors are caught at `record_snapshot`'s top-level `except Exception` — outcome resolution is best-effort and must never break the deepdive render or CLI output.

### 5.3 Storage path

Unchanged from v1: `~/.wave_alpha/history.sqlite` via `deps._data_dir()`.

## 6. Data model

### 6.1 `Outcome` (pure dataclass, `trades/outcome.py`)

```python
@dataclass(frozen=True)
class Outcome:
    status: Literal["target", "stop", "time_stop", "pending"]
    # Final fields — None while pending:
    exit_date: date | None
    exit_price: Decimal | None
    bars_held: int | None
    realized_R: float | None  # noqa: N815
    # Always populated:
    last_observed_date: date  # walked[-1].timestamp
    bars_elapsed: int         # len(walked); 1-indexed count of bars strictly after signal.as_of (≤ as_of_close)
    unrealized_R: float       # noqa: N815 — (walked[-1].close - entry) × dir / risk while pending; equals realized_R when finalized
    mfe_R: float              # noqa: N815 — max favorable excursion over walked window (intraday high/low based)
    mae_R: float              # noqa: N815 — max adverse excursion over walked window
```

`Outcome` is the resolver's pure output. Not persisted directly — it is wrapped by `SnapshotOutcome` before storage.

### 6.2 `SnapshotOutcome` (Pydantic, `history/models.py`)

```python
class SnapshotOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["target", "stop", "time_stop", "pending"]
    exit_date: date | None
    exit_price: Decimal | None
    bars_held: int | None
    realized_R: float | None  # noqa: N815
    last_observed_date: date
    bars_elapsed: int
    unrealized_R: float  # noqa: N815
    mfe_R: float  # noqa: N815
    mae_R: float  # noqa: N815
    resolved_at: datetime  # when the resolver last wrote this outcome
```

Mirrors `Outcome` exactly, plus `resolved_at` (when the resolver last wrote). Frozen Pydantic v2 model — mutations create a new instance via `model_copy(update=...)`.

### 6.3 Extension of `Snapshot`

```python
class Snapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    # ... existing fields unchanged ...
    outcome: SnapshotOutcome | None = None
```

`outcome` is `None` for: snapshots whose `signal is None`, snapshots whose `as_of == today` (no forward bars yet), and any v1 snapshot until its next analyze pass triggers resolution. The field default makes pre-v2 payloads deserialize cleanly without migration.

### 6.4 SQLite schema (`history/store.py`)

```sql
ALTER TABLE snapshots ADD COLUMN outcome_status TEXT;   -- NULL | 'pending' | 'target' | 'stop' | 'time_stop'
CREATE INDEX IF NOT EXISTS idx_snapshots_outcome_status
    ON snapshots(ticker, outcome_status);
```

`_ensure_schema` becomes:

```python
_OUTCOME_INDEX = """
CREATE INDEX IF NOT EXISTS idx_snapshots_outcome_status
    ON snapshots(ticker, outcome_status);
"""

def _ensure_schema(db_path: Path) -> None:
    if db_path in _INITIALIZED:
        return
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute(_SCHEMA)       # CREATE TABLE IF NOT EXISTS — v1 statement
            conn.execute(_INDEX)        # v1 (ticker, as_of) index
            cols = {r[1] for r in conn.execute("PRAGMA table_info(snapshots)").fetchall()}
            if "outcome_status" not in cols:
                conn.execute("ALTER TABLE snapshots ADD COLUMN outcome_status TEXT")
            conn.execute(_OUTCOME_INDEX)  # idempotent; safe to run when index exists
    finally:
        conn.close()
    _INITIALIZED.add(db_path)
```

`store.upsert` must additionally write the column. Update the INSERT to include `outcome_status` (set to `snapshot.outcome.status if snapshot.outcome else None`); the ON CONFLICT clause likewise updates the column from `excluded.outcome_status`. Code change is local to `store.py`.

The column is a denormalized projection of `payload.outcome.status`. The payload JSON remains the canonical, self-describing record; the column exists purely for index-accelerated queries (`unresolved_for`, future retraining bulk extracts). Idempotent: column-presence check via `PRAGMA table_info` makes the function safe on fresh installs and on existing v1 DBs.

## 7. Resolver

### 7.1 `resolve_outcome` (pure function)

```python
def resolve_outcome(
    signal: TradeSignal,
    bars: list[Bar],          # bars asc, including or excluding signal.as_of (filtered internally to strictly after as_of)
    *,
    as_of_close: date,        # latest bar date to walk to (resolver's "today")
) -> Outcome | None:
```

Algorithm:

1. Filter `bars` to `b.timestamp > signal.as_of and b.timestamp <= as_of_close`. If empty, return `None`. Call the resulting sequence `walked`. Bars are filtered by date, not business-day index — weekend gaps are skipped naturally.
2. Compute `risk = abs(signal.entry_price - signal.stop_price)` and `dir_sign = 1 if direction == "long" else -1`.
3. Define `bars_held` (and post-loop `bars_elapsed`) as the 1-indexed count of walked bars, i.e. the first walked bar is `bars_held = 1`. Compare against `signal.max_holding_bars` directly: `time_stop` fires when `bars_held == max_holding_bars` with no prior exit. (This matches `_walk_to_exit`'s `offset` semantics shifted by one: the backtest counts the fill bar as `offset=0`; here the entry is the published price on `signal.as_of` and the first *post-as_of* bar is `bars_held=1`.)
4. Walk bars in order. For each bar at position `i` (1-indexed):
   - **MFE / MAE update** (intraday extremes):
     - long: `excursion_high = (bar.high - entry) / risk`; `excursion_low = (bar.low - entry) / risk`; `mfe_R = max(mfe_R, excursion_high)`; `mae_R = min(mae_R, excursion_low)`.
     - short: `mfe_R = max(mfe_R, -(bar.low - entry) / risk)`; `mae_R = min(mae_R, -(bar.high - entry) / risk)`.
   - **Stop check** (wins ties on same bar):
     - long: `bar.low <= signal.stop_price` → `status="stop"`, `exit_price=signal.stop_price`, `bars_held=i`, exit loop.
     - short: `bar.high >= signal.stop_price` → same.
   - **Target check** (after stop check failed):
     - long: `bar.high >= signal.target_price` → `status="target"`, `exit_price=signal.target_price`, `bars_held=i`, exit loop.
     - short: `bar.low <= signal.target_price` → same.
   - **Time-stop check** (only if both above failed): if `i >= signal.max_holding_bars` → `status="time_stop"`, `exit_price=bar.close`, `bars_held=i`, exit loop.
5. After loop: if no exit fired, `status="pending"`, `exit_date=None`, `exit_price=None`, `bars_held=None`, `realized_R=None`, `unrealized_R=(walked[-1].close - entry) × dir_sign / risk`.
6. For finalized statuses (`target`, `stop`, `time_stop`): `realized_R = (exit_price - entry) × dir_sign / risk`; `unrealized_R = realized_R` (mirrors final value for downstream consumers that don't branch on status); `exit_date = walked[i-1].timestamp`.
7. `bars_elapsed = len(walked)` regardless of status. `last_observed_date = walked[-1].timestamp` regardless of status.

**Engine-signal vs. user-fill semantics.** The resolver does not validate that `signal.entry_price` was actually reachable on any bar. If a long signal published entry=$100 and the next bar gapped to a low of $105, the engine never had a chance to enter — but the resolver still computes outcomes as if entry occurred at $100. This is deliberate: we are evaluating the engine's *published* signal, not a simulated trade fill. "Could the user have actually entered?" is a backtest concern, not engine-accuracy.

**Three deliberate differences from `_walk_to_exit`**:

- No slippage, no commission, no short-borrow. R is computed at published prices.
- Entry is `signal.entry_price`, not next-bar open. We are evaluating the engine's *signal*, not a simulated user fill.
- Time-stop bound is `signal.max_holding_bars` verbatim, never overridden by `TradeRules.time_stop_bars`. Outcomes evaluate the engine's published holding window.

`signal.entry_price` is the natural anchor for "did the engine's published target / stop hit?" — and it removes the need for any provider call beyond the daily bars we already have.

**Stop-on-fill-bar edge case**: if `bar.low <= stop` on the first walked bar and the signal was long, we still record `status="stop"`. The backtest layer has a "wins same-bar ties on fill bar" rule using the effective fill price; here the entry is the published `signal.entry_price`, not a fill, so the simpler check applies. Documented in test cases.

### 7.2 `backtest/trade_layer.py` refactor

`_walk_to_exit` shrinks to a thin adapter:

```python
def _walk_to_exit(self, *, signal, fill_date, fill_price, bar_dates, bars_by_date, ...):
    bars_post = [bars_by_date[d] for d in bar_dates if d > signal.as_of and d <= end]
    base = resolve_outcome(signal, bars_post, as_of_close=end)
    if base is None or base.status == "pending":
        return self._make_open_at_end_record(...)
    # Layer slippage / commission / borrow on top using fill_price (not signal.entry_price)
    # and base.exit_price (recomputed with slippage_bps).
    return self._make_record_from_base(base, fill_price=fill_price, fill_date=fill_date, ...)
```

The slippage-and-commissions math stays in `trade_layer.py` because it's a backtest concern. A golden-parity test pins existing `tests/backtest/test_trade_layer.py` outputs unchanged across the refactor.

### 7.3 `history/resolver.py`

```python
def resolve_pending_for(
    ticker: str,
    daily_bars: list[Bar],
    *,
    db_path: Path,
    today: date,
) -> int:
    """Walk every (ticker, as_of) row with outcome_status IN (NULL, 'pending')
    where the snapshot has a non-null signal. Compute outcome, upsert. Returns
    count of rows updated."""
    rows = store.unresolved_for(ticker, db_path=db_path)
    bars_sorted = sorted(daily_bars, key=lambda b: b.timestamp)
    updated = 0
    for row in rows:
        snapshot = Snapshot.model_validate_json(row.payload_json)
        if snapshot.signal is None:
            continue
        signal = _to_trade_signal(snapshot)        # SnapshotSignal -> TradeSignal
        outcome = resolve_outcome(signal, bars_sorted, as_of_close=today)
        if outcome is None:
            continue
        new_snapshot = snapshot.model_copy(
            update={"outcome": _to_snapshot_outcome(outcome)}
        )
        store.upsert(new_snapshot, db_path=db_path)
        updated += 1
    return updated
```

`_to_trade_signal` reconstructs a `TradeSignal` from `SnapshotSignal` plus snapshot fields. `SnapshotSignal` currently lacks `max_holding_bars`, `expected_bars_to_target`, `count_id`, `count_pattern`, `wave_label`, `degree`, `confidence` — these need to be added to `SnapshotSignal` so the resolver can reconstruct the signal completely. **This is a forward-compat-breaking change to `SnapshotSignal`** for any v1 snapshots that were already written. We handle this by making the new fields optional with `None` defaults; resolver skips snapshots where `max_holding_bars is None` (treat them as unresolvable). New snapshots written from this version forward populate all fields.

### 7.4 `service.record_snapshot` extension

```python
def record_snapshot(result, ticker, as_of, *, db_path):
    if not result.ranked_daily:
        return
    try:
        snapshot = _build_snapshot(result, ticker, as_of)
        store.upsert(snapshot, db_path=db_path)
        if result.daily_bars:
            resolver.resolve_pending_for(
                ticker, result.daily_bars, db_path=db_path, today=as_of
            )
    except Exception as exc:
        logger.warning("history snapshot/outcome failed for %s @ %s: %s", ticker, as_of, exc)
```

Errors in the resolver are caught by the same top-level `except Exception` — outcome resolution is best-effort. Today's snapshot has already been written before resolution runs, so a resolver failure does not lose today's record.

### 7.5 Resolution scope per call

When today's AAPL snapshot is written, the resolver walks **only AAPL's** prior unresolved rows. It does *not* sweep other tickers' rows opportunistically. Two reasons: (a) the bars in hand are AAPL-specific; (b) cross-ticker sweeps would couple deepdive latency to history depth and defeat the lazy model. The CLI `wave-alpha resolve --all` handles bulk sweeps when the user explicitly asks.

## 8. AnalyzeResult bars plumbing

`pipeline.py:run_analysis` already fetches daily bars internally. Expose them additively:

```python
@dataclass
class AnalyzeResult:
    ticker: str
    as_of: date
    counts_weekly: list[WaveCount] = field(default_factory=list)
    counts_daily: list[WaveCount] = field(default_factory=list)
    counts_4h: list[WaveCount] = field(default_factory=list)
    coherence: CoherenceResult | None = None
    ranked_daily: list[RankedCount] = field(default_factory=list)
    llm_scan: ScanResponse | None = None
    signals: list[TradeSignal] = field(default_factory=list)
    n_atr_skipped: int = 0
    daily_bars: list[Bar] = field(default_factory=list)   # NEW — for outcome resolution
```

`run_analysis` populates `daily_bars` from the same fetch it already performs. Consumers that don't read the field aren't impacted. Other callers (scan, backtest) continue to work; they may set `daily_bars` to empty and the resolver simply finds no bars to walk.

The field also unlocks future cleanups where consumers re-fetch bars they already had.

## 9. CLI: `wave-alpha resolve`

```
wave-alpha resolve --ticker AAPL          # one ticker
wave-alpha resolve --all                  # every ticker with unresolved rows
wave-alpha resolve --ticker AAPL --json   # machine-readable summary
```

Implementation:

- Lazy-imports `wave_alpha.history` inside the command body (consistent with `cli/app.py` style).
- Uses the standard `CachedProvider` chain that `analyze` uses.
- For each target ticker: query unresolved rows; if none, report and continue. Otherwise fetch daily bars from the earliest unresolved `as_of` through `today` (via the cached provider — fetches that overlap existing cache are free). Call `resolve_pending_for`.
- Reports per-ticker: `{ticker, total_unresolved, rows_resolved, rows_still_pending, errors}`.
- Errors per ticker are caught and logged; the command continues to the next ticker. Exit code 0 even when some tickers error; only structural failures (DB unreadable) raise.

JSON output schema (when `--json` flag set):

```json
{
  "resolved_at": "2026-05-10T15:30:00Z",
  "tickers": [
    {"ticker": "AAPL", "total_unresolved": 12, "rows_resolved": 8, "rows_still_pending": 4, "errors": []},
    {"ticker": "MSFT", "total_unresolved": 5, "rows_resolved": 5, "rows_still_pending": 0, "errors": []}
  ]
}
```

## 10. UI surface

### 10.1 Per-row outcome chip (in `_history.html`)

Right edge of each timeline row:

```
2026-04-28 · impulse · wave 3 · score 0.78 · RR 2.4   ✓ +2.4R (12 bars)
2026-04-21 · zigzag  · wave C · score 0.62 · RR 1.8   ✗ −1.0R (5 bars)
2026-04-14 · impulse · wave 3 · score 0.71 · RR 2.1   ⊘ +0.1R (60 bars)
2026-04-07 · impulse · wave 3 · score 0.71 · RR 2.1   ⊙ +0.4R · day 9/60 · peak +1.1R
2026-03-31 · zigzag  · wave A · score 0.55 · RR 1.5   — (no signal)
```

Glyphs and palette:

| Status | Glyph | Color token |
|---|---|---|
| `target` | ✓ | success (green) |
| `stop` | ✗ | danger (red) |
| `time_stop` | ⊘ | warning (amber) |
| `pending` | ⊙ | info (blue) |
| no signal | — | muted |

Status text uses the existing palette tokens — no neon (per the candles-classic-colors memory; the chart-overlay UI should not introduce neon-adjacent color).

### 10.2 Outcome badge in card header

Appears under the existing stability badge, computed by `service.compute_outcome_badge(rows)`:

> Outcomes: **3 of 5 resolved (60%) hit target · avg +0.7R · 2 still in flight**

```python
@dataclass(frozen=True)
class OutcomeBadge:
    resolved_count: int       # target + stop + time_stop
    target_count: int
    hit_rate: float           # target_count / resolved_count when resolved_count > 0
    avg_R: float              # mean of realized_R across resolved
    in_flight_count: int      # pending

def compute_outcome_badge(snapshots: list[Snapshot]) -> OutcomeBadge | None:
    resolved = [s.outcome for s in snapshots if s.outcome and s.outcome.status != "pending"]
    pending = [s.outcome for s in snapshots if s.outcome and s.outcome.status == "pending"]
    if not resolved and not pending:
        return None
    # ...
```

Returns `None` (and the UI hides the badge) when no signal has produced any outcome — distinct from `resolved_count == 0 and in_flight_count > 0`, which renders as *"0 resolved · 2 in flight."*

### 10.3 No new endpoints

The existing `/deepdive/<ticker>/history` htmx fragment already renders the History card. The outcome data is in the snapshot payload that fragment already loads — no new request paths, no additional latency.

## 11. Testing strategy

| Layer | Test |
|---|---|
| `tests/trades/test_outcome_resolver.py` (new) | Pure-function tests for `resolve_outcome`. Synthetic bar series: target-first, stop-first, target-and-stop-same-bar (stop wins per existing convention), time-stop, pending mid-window, MFE/MAE on intraday extremes, stop on first walked bar, long and short symmetry, zero forward bars returns `None`. ~12 cases. |
| `tests/backtest/test_trade_layer.py` (extend) | Golden parity: existing trade-layer outputs unchanged after the `_walk_to_exit` extraction. Diff before/after on the curated universe smoke run. |
| `tests/history/test_resolver.py` (new) | End-to-end: write a snapshot at T−10, advance 30 bars, call `resolve_pending_for`, assert outcome populates and `outcome_status` column updates. Plus: `signal is None` skip path; v1-row round-trip via `Snapshot.model_validate_json` (no `outcome` field present); resolver-error swallowed in service. |
| `tests/history/test_service.py` (extend) | Verify `record_snapshot` triggers resolution; verify resolver errors don't break the write path (inject a malformed prior row, ensure today's snapshot still writes). |
| `tests/history/test_store_migration.py` (new) | `_ensure_schema` adds `outcome_status` column when missing; is a no-op when column already exists; index creation is idempotent. |
| `tests/cli/test_resolve_command.py` (new) | `wave-alpha resolve --ticker AAPL` smoke test with fake provider; `--all` sweeps multiple tickers; `--json` output shape. |

## 12. Migration & rollout

- `_ensure_schema` runs `PRAGMA table_info(snapshots)`, adds `outcome_status` column + index if missing. Idempotent across fresh installs and existing v1 DBs.
- v1 snapshots remain valid: `Snapshot.model_validate_json` parses them (new `outcome` field defaults to `None`); `outcome_status` column is `NULL`. They get resolved opportunistically next time their ticker is analyzed, or in bulk via `wave-alpha resolve`.
- `SnapshotSignal` gains optional fields (`max_holding_bars`, `expected_bars_to_target`, etc.); v1 rows where these are absent deserialize with `None` and the resolver skips them. New rows populate all fields.
- No engine_version gating: the resolver doesn't care which engine version produced the signal — it walks bars against published entry/stop/target/max_holding_bars. A signal from v0.7 resolves cleanly under v0.8.
- Single-process SQLite, no concurrency concern with the ALTER TABLE.

## 13. Risks and open questions

- **`SnapshotSignal` forward-compat**: existing v1 snapshots were written without `max_holding_bars`. The mitigation (optional fields, resolver skips when absent) means v1 rows never get outcome-resolved. Acceptable because the user-facing trust value compounds going forward; we don't lose anything by skipping pre-v2 rows. If we *did* want to retro-resolve v1 rows, we'd need to re-derive `max_holding_bars` from the snapshot's count via `derive_signal` — out of scope for v2.
- **MFE/MAE basis choice**: we use intraday `high`/`low` for excursion, but realized R uses `bar.close` (time_stop) or the signal's published `target_price`/`stop_price` (target/stop). The metric basis is mixed by design — excursion is most useful as "how far did this go *intra-bar*?" which is the daily high/low question.
- **Resolver runtime on a hot ticker**: a ticker with 200 unresolved rows costs ~200 bar-walks × O(window_length) per analyze call. At 60-bar windows that's ~12k bar comparisons — sub-10ms. Doesn't need to be async.
- **`AnalyzeResult.daily_bars` memory**: a 5-year daily bar series is ~1300 Pydantic-Bar objects, ~150KB. Per request, in-memory, freed when the request ends. Negligible. Backtest paths produce thousands of `AnalyzeResult`s during a sweep; we currently don't store them, so this is not a memory accumulation risk.
- **Same-bar tie semantics**: stop wins target ties on same bar. This matches `_walk_to_exit` and is the conservative choice. Documented in test cases and in §7.1.

## 14. v3 hooks (post-launch wishlist)

These are not part of this spec but the architecture preserves the door:

- **Chart-overlay annotations** of resolved entries/exits — the per-snapshot outcome rows already carry `exit_date`, `exit_price`, status; rendering on the chart is a UI-only follow-on.
- **Right-edge retraining on realized outcomes** — `outcome_status = 'target' OR 'stop'` is the labeled set; `realized_R` is the regression target.
- **LLM blend (α) live-evaluation** — group outcomes by `engine_version` (and a future `llm_alpha` snapshot field) to A/B realized expectancy.
- **Watchlist cron** triggering `wave-alpha resolve --all` nightly to keep the dataset current without requiring user visits.
- **Per-pattern / per-degree outcome breakdowns** — once the dataset is large enough, surface "impulse-wave-3 signals hit 71%, zigzag-wave-C signals hit 48%" on a separate analytics page.
