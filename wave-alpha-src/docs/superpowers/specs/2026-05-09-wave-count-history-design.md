# Wave-Count History — Design

**Status:** Draft
**Author:** alexchen
**Date:** 2026-05-09
**Related code:** `src/wave_alpha/web/routes/deepdive.py`, `src/wave_alpha/cli/app.py`, `src/wave_alpha/pipeline.py`

## 1. Motivation

Today, every visit to `/deepdive/<ticker>` re-runs `pipeline.run_analysis` from scratch and renders fresh wave counts. Nothing about the engine's prior view is preserved, so the user sees only "what the engine thinks right now" with no audit trail. This makes the analysis feel like speculation — even when the engine is in fact deterministic — because the user has no way to verify that today's count is consistent with yesterday's, or to see how the engine's view has evolved as new bars arrive.

The trust gap is "I cannot tell if the engine is consistent or chasing." The fix is not to hide the engine's evolution but to expose it: persist each analysis, surface the recent track record on the deepdive page, and let stability speak for itself.

## 2. Goals

- Persist a daily snapshot of the engine's view of every symbol the user actively analyzes.
- Make snapshots automatic — no extra user action required for trust to accumulate.
- Surface the recent timeline directly on the deepdive page where the doubt lives.
- Compute a "stability badge" so the user gets a one-line trust signal before drilling into the timeline.

## 3. Non-goals (v1)

- **Outcome tracking** ("did the predicted target hit?"). Deferred to v2; the schema stays additive so this can be layered on without migration churn.
- **Watchlist daily cron** to fill gaps for symbols the user did not visit. Auto-on-visit gives history for symbols the user touches; the cron is a v2 enhancement.
- **Engine-version handling beyond storing it.** No cross-version warnings, no filtering, no migration.
- **Chart overlays** of historical pivots, **diff views** between snapshots, or **clickable drill-down** from a timeline row. Read-only metadata strip in v1.
- **History writes from `scan` or `backtest`.** Those paths are high-volume / synthetic-as-of and would pollute the live track record.

## 4. User-visible behavior

When the user opens `/deepdive/AAPL`:

1. The existing chart, coherence, right-edge, counts, plan, and scenarios sections render exactly as today.
2. After the heavier sections start loading, a new **History** card appears at the bottom of the page (htmx-loaded with a 300ms delay so it never blocks the chart).
3. The card shows:
   - A stability badge: e.g. *"Top pick stable for 5 days (9 of last 14) — last changed 2026-04-28"*.
   - A vertical timeline of the last 14 snapshots, newest first, one line per snapshot: date · pattern · wave label · final score · RR.
   - The current (top) row has a left-border accent.
   - On a brand-new symbol: the message *"No snapshots yet. The first analysis will start the timeline."*
4. The user's current visit is itself recorded — by the time the History section lazy-loads, the row for today is already present.

When the user runs `wave-alpha analyze AAPL --as-of 2024-12-31` from the CLI: the same snapshot is recorded. CLI usage contributes to the trust loop.

When the user runs `wave-alpha scan ...` or any backtest: history is *not* touched.

## 5. Architecture

### 5.1 Module boundary

New package `src/wave_alpha/history/`:

| File | Purpose |
|---|---|
| `models.py` | `Snapshot`, `SnapshotCount`, `SnapshotPivot`, `SnapshotSignal`, `SnapshotCoherence`, `StabilityBadge`, `RecentHistory` — Pydantic v2 frozen models / frozen dataclasses |
| `identity.py` | `top_count_hash(WaveCount) -> str` — deterministic SHA-256 hash (first 16 hex chars) over `(pattern, degree, tuple((p.timestamp.isoformat(), str(p.price)) for p in c.pivots))` |
| `store.py` | SQLite persistence at `~/.wave_alpha/history.sqlite`. Pure CRUD: `upsert(snapshot)`, `recent(ticker, n)`. No knowledge of the pipeline. |
| `service.py` | Façade: `record_snapshot(result, ticker, as_of)`, `get_recent_with_badge(ticker, n=14)`. The only thing call sites import. |

The pipeline is untouched. `history` depends on `elliott.models` (`WaveCount`), `coherence.score` (`CoherenceResult`), and `trades.models` (`TradeSignal`) — all read-only.

### 5.2 Hook points

Two and only two:

- **`web/routes/deepdive.py:counts_fragment`** — after `result = deps.analyze(...)`, call `service.record_snapshot(result, ticker.upper(), resolved)`. This is the canonical "I rendered the counts" trigger; the other deepdive fragments fire on the same page load and would just create redundant write attempts (which would be idempotent but wasteful).
- **`cli/app.py` `analyze` command** — after `run_analysis(...)` returns, before output formatting, call `service.record_snapshot(...)`.

The hooks are 2 lines each. The history module is unaware of who is calling it.

### 5.3 Storage path

`deps._data_dir() / "history.sqlite"`, identical indirection to `cache.sqlite` and `llm_cache.sqlite`. Tests monkeypatch `_data_dir` to a `tmp_path`.

## 6. Data model

### 6.1 Pydantic models (`history/models.py`)

```python
class SnapshotPivot(BaseModel):
    model_config = ConfigDict(frozen=True)
    timestamp: date
    price: Decimal

class SnapshotCount(BaseModel):
    model_config = ConfigDict(frozen=True)
    candidate_id: str         # e.g. "C1", from RankedCount
    pattern: str              # "impulse", "zigzag", ...
    degree: int
    last_pivot_label: str     # e.g. "3" or "C"
    pivots: list[SnapshotPivot]
    engine_score: float
    llm_prob: float | None
    final_score: float

class SnapshotSignal(BaseModel):
    model_config = ConfigDict(frozen=True)
    direction: str            # "long" / "short"
    entry: Decimal
    stop: Decimal
    target: Decimal
    rr: float

class SnapshotCoherence(BaseModel):
    model_config = ConfigDict(frozen=True)
    multiplier: float
    weekly_label: str | None
    daily_label: str | None
    hourly4_label: str | None

class Snapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    ticker: str
    as_of: date
    engine_version: str
    top_3: list[SnapshotCount]      # ranked_daily[:3], length 1..3
    signal: SnapshotSignal | None
    coherence: SnapshotCoherence | None
    top_count_hash: str
    created_at: datetime

@dataclass(frozen=True)
class StabilityBadge:
    streak: int               # consecutive most-recent snapshots with the current top hash
    matches_in_window: int    # total count of current-hash matches across the window
    window: int               # actual number of snapshots considered (≤ requested n)
    last_changed_on: date | None  # as_of of the most recent snapshot whose hash differed

@dataclass(frozen=True)
class RecentHistory:
    snapshots: list[Snapshot]   # newest first
    badge: StabilityBadge | None  # None when len(snapshots) < 2
```

### 6.2 SQLite schema (`history/store.py`)

```sql
CREATE TABLE IF NOT EXISTS snapshots (
    ticker          TEXT NOT NULL,
    as_of           TEXT NOT NULL,        -- ISO date 'YYYY-MM-DD'
    engine_version  TEXT NOT NULL,
    top_count_hash  TEXT NOT NULL,
    payload_json    TEXT NOT NULL,        -- full Snapshot.model_dump_json()
    created_at      TEXT NOT NULL,        -- ISO datetime
    PRIMARY KEY (ticker, as_of)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_ticker_as_of
    ON snapshots(ticker, as_of DESC);
```

Upsert via `INSERT … ON CONFLICT(ticker, as_of) DO UPDATE SET …` — same-day re-runs overwrite. `created_at` is updated on overwrite.

`engine_version` and `top_count_hash` exist both as columns and inside `payload_json` — duplicated by design. The columns let the stability-badge query filter and read identity without parsing JSON. The payload remains the canonical, self-describing record of what the engine saw, which keeps `Snapshot.model_validate_json` round-trips lossless. Future additive fields (e.g. outcome tracking) live inside the payload only and require no ALTER TABLE.

### 6.3 `top_count_hash` definition

```python
def top_count_hash(c: WaveCount) -> str:
    key = (c.pattern, c.degree,
           tuple((p.timestamp.isoformat(), str(p.price)) for p in c.pivots))
    return hashlib.sha256(repr(key).encode()).hexdigest()[:16]
```

Hashing the full pivot sequence (timestamps + prices) is strict by design: when a pivot timestamp shifts because a new bar redrew the pivot, the hash changes and the count counts as "different" for stability purposes. That's correct semantics — a count whose pivots have moved is genuinely a different count, even if pattern and label match.

## 7. Write path

```python
# history/service.py
def record_snapshot(result: AnalyzeResult, ticker: str, as_of: date) -> None:
    if not result.ranked_daily:
        return
    try:
        snapshot = _build_snapshot(result, ticker, as_of)
        store.upsert(snapshot)
    except Exception as exc:
        logger.warning("history snapshot failed for %s @ %s: %s", ticker, as_of, exc)
```

Three deliberate behaviors:

1. **Empty results are skipped.** No null rows. Gaps in the timeline are signal, not noise.
2. **Errors are swallowed and logged.** History is a side-effect; a SQLite failure must not break the deepdive page or CLI output.
3. **Synchronous on the request thread.** Single-row SQLite upsert is sub-millisecond; no async machinery, no thread pool.

The hooks themselves are minimal:

```python
# In web/routes/deepdive.py:counts_fragment
from wave_alpha.history import service as history_service
history_service.record_snapshot(result, ticker.upper(), resolved)

# In cli/app.py analyze command
from wave_alpha.history import service as history_service
history_service.record_snapshot(result, ticker, as_of_resolved)
```

Both hooks normalize ticker case before calling.

**Backfill via CLI is allowed.** `wave-alpha analyze AAPL --as-of 2024-12-31` records a snapshot at that date. The history module makes no judgment about live-vs-historical; it just stores `(ticker, as_of, payload)`. The timeline strip orders by `as_of`, so backfilled snapshots appear where their date lands.

## 8. Read path

```python
# history/service.py
def get_recent_with_badge(ticker: str, n: int = 14) -> RecentHistory:
    rows = store.recent(ticker, n)
    snapshots = [Snapshot.model_validate_json(r.payload_json) for r in rows]
    badge = _compute_badge(rows, n) if len(rows) >= 2 else None
    return RecentHistory(snapshots=snapshots, badge=badge)
```

Underlying query — single index range scan on `(ticker, as_of DESC)`:

```sql
SELECT payload_json, top_count_hash, as_of
FROM snapshots
WHERE ticker = ?
ORDER BY as_of DESC
LIMIT ?
```

Stability computation is a pure function over the rows:

```python
def _compute_badge(rows, window: int) -> StabilityBadge | None:
    if len(rows) < 2:
        return None
    current = rows[0].top_count_hash
    streak = 1
    last_changed: date | None = None
    for r in rows[1:]:
        if r.top_count_hash == current:
            streak += 1
        else:
            last_changed = r.as_of
            break
    matches = sum(1 for r in rows if r.top_count_hash == current)
    return StabilityBadge(streak=streak, matches_in_window=matches,
                          window=len(rows), last_changed_on=last_changed)
```

Two metrics are exposed:

- `streak` — consecutive uninterrupted snapshots with the current top hash.
- `matches_in_window` — total occurrences of the current hash across the window. Captures dominance even when the count flip-flops.

The UI renders both, collapsing to just `streak` when they're equal. Note: `streak` counts *snapshots*, not *calendar days*. Weekends or skipped days do not break a streak. This is documented user-visible behavior; "stable for 5 days" means "the 5 most recent recorded snapshots."

## 9. UI surface

### 9.1 Route

```python
@router.get("/{ticker}/history", response_class=HTMLResponse)
def history_fragment(ticker: str, request: Request) -> HTMLResponse:
    from wave_alpha.history import service as history_service
    recent = history_service.get_recent_with_badge(ticker.upper(), n=14)
    return request.app.state.templates.TemplateResponse(
        request=request, name="_history.html",
        context={"snapshots": recent.snapshots, "badge": recent.badge},
    )
```

Deliberately no `as_of` parameter. The strip always shows the symbol's most recent track record regardless of which historical date the deepdive is rendered at — viewing AAPL at 2024-12-31 still benefits from seeing the engine's recent consistency on AAPL.

### 9.2 Template integration

A new `<section class="card">` appended to `web/templates/deepdive.html` after the Scenarios card:

```html
<section class="card"
         hx-get="/deepdive/{{ ticker }}/history"
         hx-trigger="load delay:300ms"
         hx-swap="innerHTML">
  <h2>History</h2>
  <span class="htmx-indicator">loading history…</span>
</section>
```

### 9.3 `_history.html` partial

Renders the stability badge (when present) above an ordered list of snapshot rows. Each row is a single line: date · pattern · `W{last_pivot_label}` · final score · `RR {rr}` (when a signal exists). The current row carries a `current` class for a left-border accent. Empty state shows "No snapshots yet."

### 9.4 CSS additions to `web/static/app.css`

- `.history-timeline` — flat ordered list, no markers, monospace columns.
- `.history-row` — single line with column alignment via gap.
- `.history-row.current` — left-border accent.
- `.stability-badge` — same visual weight as the existing coherence badge, sits above the list.

No JavaScript. No row-level interactivity in v1.

## 10. Error handling & edge cases

| Case | Behavior |
|---|---|
| `result.ranked_daily` empty | Skip write entirely. No null row. |
| SQLite write fails (lock, disk full, schema mismatch) | Caught at `service.record_snapshot`, logged WARNING, never propagates. |
| `~/.wave_alpha/` missing | `store.upsert` calls `mkdir(parents=True, exist_ok=True)` once on first write. |
| `history.sqlite` exists with old schema | `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS` on every connect. v1 has no migrations. |
| Ticker case mismatch | Hooks uppercase before calling. Read path uppercases too. Stored ticker is canonical. |
| `engine_version` differs across rows in a window | Stored faithfully on each row; v1 ignores when computing the badge. |
| Concurrent writes to the same `(ticker, as_of)` | SQLite upsert is atomic; last writer wins. |
| Snapshot serialization raises | Caught at the top-level `try/except` in `record_snapshot`. No partial write. |
| Reading payload from a future engine version with extra fields | Pydantic `extra='ignore'` (default) tolerates new fields; old code reading new payloads still works. |
| Brand-new symbol (no history) | `RecentHistory(snapshots=[], badge=None)`; template shows "No snapshots yet." |

## 11. Testing

**Unit (`tests/history/`):**

- `test_identity.py` — `top_count_hash` is deterministic; same `WaveCount` → same hash; perturbing a pivot price changes the hash; perturbing pattern changes the hash.
- `test_store.py` — fresh tempdir DB. `upsert` then `recent` round-trips a `Snapshot`. Same-key second `upsert` overwrites `created_at`. `recent` returns newest first.
- `test_service.py` — `record_snapshot` skips empty `AnalyzeResult`. `record_snapshot` swallows store exceptions (monkeypatch `store.upsert` to raise; assert no propagation, assert log emitted). `get_recent_with_badge` over a fixture DB returns correct `streak` and `matches_in_window` for hand-built sequences.
- `test_stability_badge.py` — table-driven cases for `_compute_badge`: 1 row → None; 5-of-5 same → streak=5, matches=5; 3-then-flip → streak=1, matches=3, `last_changed_on` set; flip-flop A,B,A,B,A → streak=1, matches=3.
- `test_idempotent_record.py` — calling `record_snapshot` twice with the same inputs leaves exactly one row; second `created_at` ≥ first.

**Integration (`tests/web/`):**

- `test_deepdive_history_route.py` — start FastAPI with a temp `~/.wave_alpha/`, hit `/deepdive/AAPL/counts` (triggers write), then `/deepdive/AAPL/history`, assert rendered fragment contains today's date and the top pattern. Monkeypatch `deps.analyze` to return a deterministic `AnalyzeResult`.

**CLI (`tests/cli/`):**

- `test_analyze_history_hook.py` — invoke `wave-alpha analyze AAPL --as-of 2024-12-31` with a stubbed provider; assert the snapshot row landed in the temp `history.sqlite`.

**Lookahead-leak invariant unaffected.** History reads/writes never touch bars; no new test in `tests/backtest/test_lookahead_guard.py`.

## 12. Open questions / v2 hooks

- **Outcome tracking.** Adding `outcome_json` to the payload (or a separate `outcomes` table keyed on `(ticker, as_of)`) is additive. v2 spec needed for evaluator rules ("when does an old count count as resolved?") and the UI surface.
- **Watchlist daily cron.** Adds a scheduled CLI entry point that calls `record_snapshot` for every watchlist ticker at market close. Requires platform-specific scheduling (launchd on macOS).
- **Engine-version warnings.** When the recent window spans multiple engine versions, the badge could downgrade or annotate. v1 ignores this entirely.
- **Drill-down.** Clicking a timeline row could navigate to `/deepdive/<ticker>?as_of=<that_date>`, which would re-render the engine's historical view at that date. Cheap to add later because both ends already exist.
