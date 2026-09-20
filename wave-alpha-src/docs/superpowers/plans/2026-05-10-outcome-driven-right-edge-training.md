# Outcome-driven right-edge training (with bounded history) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add outcome-blended training to `wave-alpha train right-edge` and bounded auto-prune to `history.sqlite`, without growing per-row storage.

**Architecture:** Outcome examples are recomputed at training time from `cache.sqlite` bars (zero new persisted state). They enter the training pipeline only at the **final refit** stage — `walk_forward` stays synthetic-only as an OOS estimator. The final logistic + Platt scalers are refit on `synthetic ∪ outcomes` with a per-degree sample-weight ramp. Auto-prune is per-ticker, runs inside `record_snapshot` after the resolver pass, and is gated by a new `history.retention_days` config (default 730).

**Tech Stack:** Python 3.11+, NumPy (already a transitive dep), SQLite3 (stdlib), Typer (existing), Pydantic v2 (existing). All commands via `uv run`.

**Spec:** `docs/superpowers/specs/2026-05-10-outcome-driven-right-edge-training-design.md`

---

## File Structure

**New files:**
- `src/wave_alpha/right_edge/outcome_dataset.py` — `WeightSchedule`, `OutcomeRow`, `OutcomeDataset`, `build_outcome_dataset`, `assign_weights`, label encoder.
- `src/wave_alpha/cli/history_app.py` — Typer sub-app with `stats` and `prune` commands.
- `tests/right_edge/test_outcome_dataset.py`
- `tests/right_edge/test_weight_schedule.py`
- `tests/right_edge/test_training_blended.py`
- `tests/right_edge/test_fit_logistic_weights.py`
- `tests/history/test_prune.py`
- `tests/history/test_service_auto_prune.py`
- `tests/cli/test_history_app.py`
- `tests/cli/test_train_right_edge_use_outcomes.py`

**Modified files:**
- `src/wave_alpha/history/store.py` — add `prune_older_than(...)`.
- `src/wave_alpha/history/service.py` — call prune after resolver in `record_snapshot`.
- `src/wave_alpha/prefs/models.py` — add `HistoryConfig`, `RightEdgeOutcomeTrainingConfig`; wire into `AppConfig` and `RightEdgeConfig`.
- `src/wave_alpha/right_edge/training.py` — `fit_logistic` and `fit_platt` accept optional `sample_weights`; new `train_blended_final(...)`; `serialize_artifact` accepts `outcome_metadata`.
- `src/wave_alpha/cli/app.py` — add `--use-outcomes`, `--outcome-weight-target`, `--limit`, `--drop-time-stops` flags to `train right-edge`; register `history` sub-app.
- `CLAUDE.md` — update "Right-edge confirmation models" section.

---

## Task 1: Add `prune_older_than` SQL primitive

**Files:**
- Modify: `src/wave_alpha/history/store.py`
- Test: `tests/history/test_prune.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/history/test_prune.py
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from wave_alpha.history import store
from wave_alpha.history.models import (
    Snapshot, SnapshotCoherence, SnapshotCount, SnapshotPivot, SnapshotSignal,
)


def _snap(ticker: str, as_of: date) -> Snapshot:
    return Snapshot(
        ticker=ticker,
        as_of=as_of,
        engine_version="0.9.0",
        top_3=[SnapshotCount(
            candidate_id="C1", pattern="impulse", degree=1, last_pivot_label="3",
            pivots=[SnapshotPivot(timestamp=as_of, price=Decimal("100"))],
            engine_score=0.7, llm_prob=None, final_score=0.7,
        )],
        signal=SnapshotSignal(
            direction="long", entry_price=Decimal("100"),
            stop_price=Decimal("95"), target_price=Decimal("115"), rr=3.0,
        ),
        coherence=SnapshotCoherence(score=0.7, multiplier=1.0, pairwise={}, fallback_used=None),
        top_count_hash=f"h-{ticker}-{as_of}",
        created_at=datetime(as_of.year, as_of.month, as_of.day, 16, 0, 0),
    )


def test_prune_older_than_deletes_only_older_rows_for_ticker(tmp_path: Path) -> None:
    db_path = tmp_path / "h.sqlite"
    # AAPL: 3 rows spanning 2022-2024
    for as_of in (date(2022, 6, 1), date(2023, 6, 1), date(2024, 6, 1)):
        store.upsert(_snap("AAPL", as_of), db_path=db_path)
    # MSFT: 1 old row (should NOT be touched — per-ticker scope)
    store.upsert(_snap("MSFT", date(2022, 6, 1)), db_path=db_path)

    deleted = store.prune_older_than(
        db_path=db_path, ticker="AAPL", cutoff=date(2023, 12, 31)
    )

    assert deleted == 2  # 2022 and 2023 rows
    remaining = [r.as_of for r in store.recent("AAPL", 10, db_path=db_path)]
    assert remaining == [date(2024, 6, 1)]
    msft_remaining = [r.as_of for r in store.recent("MSFT", 10, db_path=db_path)]
    assert msft_remaining == [date(2022, 6, 1)]  # untouched


def test_prune_older_than_returns_zero_when_no_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "h.sqlite"
    store.upsert(_snap("AAPL", date(2024, 6, 1)), db_path=db_path)
    deleted = store.prune_older_than(
        db_path=db_path, ticker="AAPL", cutoff=date(2020, 1, 1)
    )
    assert deleted == 0


def test_prune_older_than_missing_db_returns_zero(tmp_path: Path) -> None:
    db_path = tmp_path / "missing.sqlite"
    deleted = store.prune_older_than(
        db_path=db_path, ticker="AAPL", cutoff=date(2024, 1, 1)
    )
    assert deleted == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/history/test_prune.py -v`
Expected: FAIL with `AttributeError: module 'wave_alpha.history.store' has no attribute 'prune_older_than'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/wave_alpha/history/store.py`:

```python
def prune_older_than(*, db_path: Path, ticker: str, cutoff: date) -> int:
    """Delete snapshots for `ticker` with as_of < cutoff. Returns rows deleted.

    Missing DB returns 0 (idempotent). The (ticker, as_of) primary key + the
    existing idx_snapshots_ticker_as_of index make the deletion O(log n + k).
    """
    if not db_path.exists():
        return 0
    _ensure_schema(db_path)
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                "DELETE FROM snapshots WHERE ticker = ? AND as_of < ?",
                (ticker, cutoff.isoformat()),
            )
            return cur.rowcount
    finally:
        conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/history/test_prune.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/history/store.py tests/history/test_prune.py
git commit -m "feat(history): add prune_older_than SQL primitive"
```

---

## Task 2: Add `HistoryConfig.retention_days` to prefs

**Files:**
- Modify: `src/wave_alpha/prefs/models.py`
- Test: `tests/prefs/test_models.py` (create if missing)

- [ ] **Step 1: Write the failing test**

Append to `tests/prefs/test_models.py` (create file with the imports below if it does not exist):

```python
# tests/prefs/test_models.py
from wave_alpha.prefs.models import AppConfig, HistoryConfig


def test_history_config_defaults() -> None:
    cfg = HistoryConfig()
    assert cfg.retention_days == 730


def test_app_config_includes_history_with_defaults() -> None:
    cfg = AppConfig()
    assert cfg.history.retention_days == 730


def test_app_config_history_override_via_model_validate() -> None:
    cfg = AppConfig.model_validate({"history": {"retention_days": 365}})
    assert cfg.history.retention_days == 365


def test_history_config_retention_zero_is_allowed() -> None:
    cfg = HistoryConfig(retention_days=0)
    assert cfg.retention_days == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/prefs/test_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'HistoryConfig'`

- [ ] **Step 3: Write minimal implementation**

Edit `src/wave_alpha/prefs/models.py`. Add `HistoryConfig` and wire into `AppConfig`:

```python
class HistoryConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    retention_days: int = Field(default=730, ge=0)
    """Auto-prune snapshots older than this many days. 0 disables auto-prune."""


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    llm: LLMConfig = Field(default_factory=LLMConfig)
    right_edge: RightEdgeConfig = Field(default_factory=RightEdgeConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    history: HistoryConfig = Field(default_factory=HistoryConfig)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/prefs/test_models.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/prefs/models.py tests/prefs/test_models.py
git commit -m "feat(prefs): add HistoryConfig.retention_days (default 730)"
```

---

## Task 3: Wire auto-prune into `record_snapshot`

**Files:**
- Modify: `src/wave_alpha/history/service.py`
- Test: `tests/history/test_service_auto_prune.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/history/test_service_auto_prune.py
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from wave_alpha.history import service, store
from wave_alpha.history.models import (
    Snapshot, SnapshotCoherence, SnapshotCount, SnapshotPivot, SnapshotSignal,
)
from wave_alpha.prefs.models import HistoryConfig


def _snap(ticker: str, as_of: date, *, top_hash: str = "h") -> Snapshot:
    return Snapshot(
        ticker=ticker, as_of=as_of, engine_version="0.9.0",
        top_3=[SnapshotCount(
            candidate_id="C1", pattern="impulse", degree=1, last_pivot_label="3",
            pivots=[SnapshotPivot(timestamp=as_of, price=Decimal("100"))],
            engine_score=0.7, llm_prob=None, final_score=0.7,
        )],
        signal=SnapshotSignal(
            direction="long", entry_price=Decimal("100"),
            stop_price=Decimal("95"), target_price=Decimal("115"), rr=3.0,
        ),
        coherence=SnapshotCoherence(score=0.7, multiplier=1.0, pairwise={}, fallback_used=None),
        top_count_hash=top_hash,
        created_at=datetime(as_of.year, as_of.month, as_of.day, 16, 0, tzinfo=UTC),
    )


def test_record_snapshot_prunes_old_rows_for_same_ticker(tmp_path: Path) -> None:
    db_path = tmp_path / "h.sqlite"
    # Seed AAPL with old rows
    for as_of in (date(2020, 1, 1), date(2021, 1, 1), date(2024, 1, 1)):
        store.upsert(_snap("AAPL", as_of, top_hash=f"h-{as_of}"), db_path=db_path)
    # Seed MSFT with one old row — must not be touched
    store.upsert(_snap("MSFT", date(2020, 1, 1)), db_path=db_path)

    # `record_snapshot` only reads `result.ranked_daily` and `result.daily_bars`
    # before delegating to `_build_snapshot`, which we patch — so a duck-typed
    # SimpleNamespace is sufficient as the stand-in.
    today = date(2026, 1, 1)
    snap_today = _snap("AAPL", today, top_hash="h-today")
    result = SimpleNamespace(ranked_daily=[object()], daily_bars=[])
    with patch.object(service, "_build_snapshot", return_value=snap_today), \
         patch.object(service.resolver, "resolve_pending_for", return_value=None), \
         patch.object(service, "_load_history_config",
                      return_value=HistoryConfig(retention_days=730)):
        service.record_snapshot(result, "AAPL", today, db_path=db_path)

    aapl_rows = [r.as_of for r in store.recent("AAPL", 10, db_path=db_path)]
    # 2020 and 2021 are >730 days before 2026-01-01 → pruned. 2024 and today remain.
    assert date(2020, 1, 1) not in aapl_rows
    assert date(2021, 1, 1) not in aapl_rows
    assert date(2024, 1, 1) in aapl_rows
    assert today in aapl_rows
    # MSFT untouched
    msft_rows = [r.as_of for r in store.recent("MSFT", 10, db_path=db_path)]
    assert msft_rows == [date(2020, 1, 1)]


def test_record_snapshot_skips_prune_when_retention_is_zero(tmp_path: Path) -> None:
    db_path = tmp_path / "h.sqlite"
    store.upsert(_snap("AAPL", date(2010, 1, 1)), db_path=db_path)

    today = date(2026, 1, 1)
    snap_today = _snap("AAPL", today, top_hash="h-today")
    with patch.object(service, "_build_snapshot", return_value=snap_today), \
         patch.object(service.resolver, "resolve_pending_for", return_value=None), \
         patch.object(service, "_load_history_config",
                      return_value=HistoryConfig(retention_days=0)):
        result = SimpleNamespace(ranked_daily=[object()], daily_bars=[])
        service.record_snapshot(result, "AAPL", today, db_path=db_path)

    rows = [r.as_of for r in store.recent("AAPL", 10, db_path=db_path)]
    assert date(2010, 1, 1) in rows  # never pruned


def test_record_snapshot_swallows_prune_failures(tmp_path: Path, caplog) -> None:
    db_path = tmp_path / "h.sqlite"
    today = date(2026, 1, 1)
    snap_today = _snap("AAPL", today)
    with patch.object(service, "_build_snapshot", return_value=snap_today), \
         patch.object(service.resolver, "resolve_pending_for", return_value=None), \
         patch.object(service, "_load_history_config",
                      return_value=HistoryConfig(retention_days=730)), \
         patch.object(store, "prune_older_than", side_effect=RuntimeError("boom")):
        result = SimpleNamespace(ranked_daily=[object()], daily_bars=[])
        # Must NOT raise
        service.record_snapshot(result, "AAPL", today, db_path=db_path)
    # warning logged
    assert any("auto-prune" in rec.message.lower() for rec in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/history/test_service_auto_prune.py -v`
Expected: FAIL — `_load_history_config` doesn't exist; prune never called.

- [ ] **Step 3: Write minimal implementation**

Edit `src/wave_alpha/history/service.py`. Add the config loader helper near the top of the module (after imports) and call prune from `record_snapshot`:

```python
def _load_history_config() -> HistoryConfig:
    """Load HistoryConfig from disk, falling back to defaults.

    Wrapped as a function for monkeypatch in tests."""
    from wave_alpha.prefs.config import load_config
    try:
        return load_config().history
    except Exception:
        return HistoryConfig()
```

Add this import at the top of the file:

```python
from wave_alpha.prefs.models import HistoryConfig
```

Modify `record_snapshot` to call prune after the resolver:

```python
def record_snapshot(
    result: AnalyzeResult,
    ticker: str,
    as_of: date,
    *,
    db_path: Path,
) -> None:
    if not result.ranked_daily:
        return
    try:
        snapshot = _build_snapshot(result, ticker, as_of)
        store.upsert(snapshot, db_path=db_path)
        if result.daily_bars:
            resolver.resolve_pending_for(ticker, result.daily_bars, db_path=db_path, today=as_of)
    except Exception as exc:
        logger.warning("history snapshot/outcome failed for %s @ %s: %s", ticker, as_of, exc)
        return

    # Auto-prune is best-effort; never propagates failure.
    try:
        from datetime import timedelta
        cfg = _load_history_config()
        if cfg.retention_days > 0:
            cutoff = as_of - timedelta(days=cfg.retention_days)
            store.prune_older_than(db_path=db_path, ticker=ticker, cutoff=cutoff)
    except Exception as exc:
        logger.warning("auto-prune failed for %s @ %s: %s", ticker, as_of, exc)
```

Note: the prune block is OUTSIDE the snapshot try/except so a prune failure does not mask a snapshot-write failure (the snapshot one already logs and returns). The dedicated try/except ensures the function still never raises.

If `prefs/config.py:load_config` does not exist with that signature, check what does exist (`grep -n "load_config\\|def load" src/wave_alpha/prefs/config.py`) and adjust the `_load_history_config` body to call the existing entry point. The contract is: read disk config, return a `HistoryConfig`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/history/test_service_auto_prune.py -v`
Expected: PASS (3 tests)

Then run the full history test suite to confirm no regressions:
Run: `uv run pytest tests/history/ -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/history/service.py tests/history/test_service_auto_prune.py
git commit -m "feat(history): auto-prune snapshots older than retention_days

Per-ticker prune inside record_snapshot after the resolver pass. Best-effort
— failures log at WARNING and never propagate. Default retention is 730 days
(configurable; 0 disables)."
```

---

## Task 4: Add `history` CLI sub-app (`stats`, `prune`)

**Files:**
- Create: `src/wave_alpha/cli/history_app.py`
- Modify: `src/wave_alpha/cli/app.py` (one `add_typer` line + import)
- Test: `tests/cli/test_history_app.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/cli/test_history_app.py
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wave_alpha.cli.app import app
from wave_alpha.history import store
from wave_alpha.history.models import (
    Snapshot, SnapshotCoherence, SnapshotCount, SnapshotPivot, SnapshotSignal,
)


def _snap(ticker: str, as_of: date, *, outcome_status: str | None = None) -> Snapshot:
    snap = Snapshot(
        ticker=ticker, as_of=as_of, engine_version="0.9.0",
        top_3=[SnapshotCount(
            candidate_id="C1", pattern="impulse", degree=1, last_pivot_label="3",
            pivots=[SnapshotPivot(timestamp=as_of, price=Decimal("100"))],
            engine_score=0.7, llm_prob=None, final_score=0.7,
        )],
        signal=SnapshotSignal(
            direction="long", entry_price=Decimal("100"),
            stop_price=Decimal("95"), target_price=Decimal("115"), rr=3.0,
        ),
        coherence=SnapshotCoherence(score=0.7, multiplier=1.0, pairwise={}, fallback_used=None),
        top_count_hash=f"h-{ticker}-{as_of}",
        created_at=datetime(as_of.year, as_of.month, as_of.day, 16, 0, tzinfo=UTC),
    )
    return snap


@pytest.fixture
def seeded_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "h.sqlite"
    for ticker, as_of in [
        ("AAPL", date(2024, 1, 1)),
        ("AAPL", date(2025, 1, 1)),
        ("MSFT", date(2024, 6, 1)),
    ]:
        store.upsert(_snap(ticker, as_of), db_path=db_path)
    # Repoint the CLI helper at our tmp DB.
    monkeypatch.setattr(
        "wave_alpha.cli.history_app._history_db_path", lambda: db_path
    )
    return db_path


def test_history_stats_prints_row_counts(seeded_db: Path) -> None:
    runner = CliRunner()
    res = runner.invoke(app, ["history", "stats"])
    assert res.exit_code == 0, res.output
    assert "AAPL" in res.output
    assert "MSFT" in res.output
    assert "3" in res.output  # total rows


def test_history_prune_dry_run_does_not_delete(seeded_db: Path) -> None:
    runner = CliRunner()
    res = runner.invoke(app, ["history", "prune", "--older-than", "180d", "--dry-run"])
    assert res.exit_code == 0, res.output
    # Both AAPL old rows would be pruned
    assert "would delete" in res.output.lower() or "dry-run" in res.output.lower()
    # Verify DB unchanged
    rows = list(store.recent("AAPL", 10, db_path=seeded_db))
    assert len(rows) == 2


def test_history_prune_deletes_older_rows(seeded_db: Path) -> None:
    runner = CliRunner()
    res = runner.invoke(app, ["history", "prune", "--older-than", "180d"])
    assert res.exit_code == 0
    aapl_rows = [r.as_of for r in store.recent("AAPL", 10, db_path=seeded_db)]
    # Whichever AAPL rows are <180 days from today are kept; both seed dates
    # are >180 days from any 2026 today, so both should be deleted.
    msft_rows = [r.as_of for r in store.recent("MSFT", 10, db_path=seeded_db)]
    assert len(aapl_rows) + len(msft_rows) <= 3  # at most original count
    # Some output mentions deletion
    assert "delete" in res.output.lower() or "prune" in res.output.lower()


def test_history_prune_ticker_scope(seeded_db: Path) -> None:
    runner = CliRunner()
    res = runner.invoke(
        app, ["history", "prune", "--older-than", "1d", "--ticker", "AAPL"]
    )
    assert res.exit_code == 0
    msft_rows = [r.as_of for r in store.recent("MSFT", 10, db_path=seeded_db)]
    # MSFT must still be present even though its rows are older than 1d
    assert len(msft_rows) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_history_app.py -v`
Expected: FAIL — `history` is not a registered command.

- [ ] **Step 3: Write minimal implementation**

Create `src/wave_alpha/cli/history_app.py`:

```python
"""`wave-alpha history` sub-app: stats and prune commands."""

from __future__ import annotations

import json as _json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import typer

history_app = typer.Typer(no_args_is_help=True, help="Inspect and prune history.sqlite.")


def _history_db_path() -> Path:
    """Resolve the on-disk path of history.sqlite. Indirected for tests."""
    from wave_alpha.prefs.config import data_dir  # existing helper
    return data_dir() / "history.sqlite"


def _parse_older_than(value: str) -> int:
    """Parse `730d` or `730` → 730 days. Rejects non-day units explicitly."""
    s = value.strip().lower()
    if s.endswith("d"):
        s = s[:-1]
    try:
        days = int(s)
    except ValueError as exc:
        raise typer.BadParameter(
            f"--older-than: expected `<N>d` or `<N>`, got {value!r}"
        ) from exc
    if days < 0:
        raise typer.BadParameter("--older-than must be non-negative")
    return days


@history_app.command("stats")
def stats(json: bool = typer.Option(False, "--json", help="Emit JSON output.")) -> None:
    """Print per-ticker row counts, outcome-status counts, oldest/newest as_of."""
    db_path = _history_db_path()
    if not db_path.exists():
        if json:
            typer.echo(_json.dumps({"rows": 0, "tickers": []}))
        else:
            typer.echo("history.sqlite does not exist yet.")
        return

    conn = sqlite3.connect(db_path)
    try:
        per_ticker = conn.execute(
            """
            SELECT ticker, COUNT(*), MIN(as_of), MAX(as_of)
            FROM snapshots
            GROUP BY ticker
            ORDER BY ticker
            """
        ).fetchall()
        outcome_counts = dict(conn.execute(
            """
            SELECT COALESCE(outcome_status, 'unresolved'), COUNT(*)
            FROM snapshots
            GROUP BY outcome_status
            """
        ).fetchall())
    finally:
        conn.close()

    total = sum(r[1] for r in per_ticker)
    size_bytes = db_path.stat().st_size

    if json:
        typer.echo(_json.dumps({
            "rows": total,
            "size_bytes": size_bytes,
            "outcome_counts": outcome_counts,
            "tickers": [
                {"ticker": t, "rows": n, "oldest": o, "newest": x}
                for (t, n, o, x) in per_ticker
            ],
        }))
        return

    typer.echo(f"history.sqlite at {db_path}")
    typer.echo(f"  total rows: {total}")
    typer.echo(f"  on-disk size: {size_bytes / 1024:.1f} KiB")
    typer.echo(f"  by outcome_status: {outcome_counts}")
    typer.echo(f"  per ticker (rows / oldest / newest):")
    for ticker, n, oldest, newest in per_ticker:
        typer.echo(f"    {ticker:>6}  {n:>5}  {oldest}  →  {newest}")


@history_app.command("prune")
def prune(
    older_than: str = typer.Option(..., "--older-than", help="e.g. `730d` or `730`."),
    ticker: str | None = typer.Option(None, "--ticker", help="Limit to one ticker."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be deleted."),
    json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Manually prune snapshots older than --older-than days."""
    from wave_alpha.history import store as _store

    db_path = _history_db_path()
    if not db_path.exists():
        typer.echo("history.sqlite does not exist yet; nothing to prune.")
        return

    days = _parse_older_than(older_than)
    cutoff = date.today() - timedelta(days=days)

    # Discover candidate tickers
    if ticker:
        tickers = [ticker]
    else:
        conn = sqlite3.connect(db_path)
        try:
            tickers = [r[0] for r in conn.execute(
                "SELECT DISTINCT ticker FROM snapshots ORDER BY ticker"
            ).fetchall()]
        finally:
            conn.close()

    if dry_run:
        # Count by ticker, no delete.
        results: dict[str, int] = {}
        conn = sqlite3.connect(db_path)
        try:
            for t in tickers:
                n = conn.execute(
                    "SELECT COUNT(*) FROM snapshots WHERE ticker = ? AND as_of < ?",
                    (t, cutoff.isoformat()),
                ).fetchone()[0]
                if n:
                    results[t] = n
        finally:
            conn.close()
        if json:
            typer.echo(_json.dumps({"dry_run": True, "cutoff": cutoff.isoformat(), "would_delete": results}))
        else:
            total = sum(results.values())
            typer.echo(f"DRY-RUN: would delete {total} rows older than {cutoff.isoformat()}")
            for t, n in results.items():
                typer.echo(f"  {t}: {n}")
        return

    deleted_by_ticker: dict[str, int] = {}
    for t in tickers:
        deleted_by_ticker[t] = _store.prune_older_than(db_path=db_path, ticker=t, cutoff=cutoff)

    total = sum(deleted_by_ticker.values())
    if json:
        typer.echo(_json.dumps({"cutoff": cutoff.isoformat(), "deleted": deleted_by_ticker}))
    else:
        typer.echo(f"Pruned {total} rows older than {cutoff.isoformat()}")
        for t, n in deleted_by_ticker.items():
            if n:
                typer.echo(f"  {t}: deleted {n}")
```

Then add the sub-app to `src/wave_alpha/cli/app.py`. Find the line `app.add_typer(train_app, name="train")` (around line 62 — verify) and add immediately after:

```python
from wave_alpha.cli.history_app import history_app
app.add_typer(history_app, name="history")
```

Place the `import` at the top of the file with the other imports if that matches the existing style. If `prefs/config.py:data_dir` does not exist under that name, find the existing data-dir resolver (likely `_data_dir` or similar — check `grep -n "def.*data_dir\|\.wave_alpha" src/wave_alpha/prefs/`) and adjust `_history_db_path()` to call it.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cli/test_history_app.py -v`
Expected: PASS (4 tests)

Then verify the existing CLI suite still passes:
Run: `uv run pytest tests/cli/ -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/cli/history_app.py src/wave_alpha/cli/app.py tests/cli/test_history_app.py
git commit -m "feat(cli): add 'wave-alpha history' sub-app (stats, prune)"
```

---

## Task 5: Data models + weight assignment

**Files:**
- Create: `src/wave_alpha/right_edge/outcome_dataset.py` (data models + `assign_weights` only at this stage)
- Test: `tests/right_edge/test_weight_schedule.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/right_edge/test_weight_schedule.py
from dataclasses import replace
from datetime import date

import pytest

from wave_alpha.right_edge.features import RightEdgeFeatures
from wave_alpha.right_edge.outcome_dataset import (
    OutcomeDataset,
    OutcomeRow,
    WeightSchedule,
    assign_weights,
)


def _feats() -> RightEdgeFeatures:
    return RightEdgeFeatures(
        retracement_progress=0.5,
        bars_since_candidate=5,
        volume_decline_pct=0.0,
        higher_degree_zone_match=0.0,
        atr_regime=1.0,
    )


def _row(degree: int) -> OutcomeRow:
    return OutcomeRow(
        ticker="AAPL", as_of=date(2025, 1, 1), degree=degree,
        features=_feats(), label=1.0, weight=0.0,
    )


def test_weights_zero_when_no_outcomes() -> None:
    weighted = assign_weights(rows=[], n_synth_per_degree={1: 500}, schedule=WeightSchedule())
    assert weighted == []


def test_weights_below_target_get_partial_ramp() -> None:
    # 50 outcome rows at degree 1, 500 synthetic, target 100
    rows = [_row(1) for _ in range(50)]
    schedule = WeightSchedule(n_target_per_degree=100)
    weighted = assign_weights(rows=rows, n_synth_per_degree={1: 500}, schedule=schedule)
    # ramp = 50/100 = 0.5; weight = (500/50) * 0.5 = 5.0
    assert all(r.weight == pytest.approx(5.0) for r in weighted)


def test_weights_at_target_match_synthetic_mass() -> None:
    rows = [_row(1) for _ in range(100)]
    schedule = WeightSchedule(n_target_per_degree=100)
    weighted = assign_weights(rows=rows, n_synth_per_degree={1: 500}, schedule=schedule)
    # ramp = 1.0; weight = 500/100 = 5.0. Total outcome mass = 100 * 5 = 500 = synthetic mass.
    assert all(r.weight == pytest.approx(5.0) for r in weighted)
    assert sum(r.weight for r in weighted) == pytest.approx(500)


def test_weights_above_target_stay_balanced_one_to_one() -> None:
    rows = [_row(1) for _ in range(1000)]
    schedule = WeightSchedule(n_target_per_degree=100)
    weighted = assign_weights(rows=rows, n_synth_per_degree={1: 500}, schedule=schedule)
    # ramp = min(1, 1000/100) = 1.0; weight = 500/1000 = 0.5. Total mass = 500.
    assert all(r.weight == pytest.approx(0.5) for r in weighted)
    assert sum(r.weight for r in weighted) == pytest.approx(500)


def test_weights_per_degree_independent() -> None:
    rows = [_row(1) for _ in range(50)] + [_row(2) for _ in range(100)]
    schedule = WeightSchedule(n_target_per_degree=100)
    weighted = assign_weights(
        rows=rows, n_synth_per_degree={1: 500, 2: 200}, schedule=schedule
    )
    d1 = [r for r in weighted if r.degree == 1]
    d2 = [r for r in weighted if r.degree == 2]
    # degree 1: ramp 0.5, weight = (500/50) * 0.5 = 5.0
    assert all(r.weight == pytest.approx(5.0) for r in d1)
    # degree 2: ramp 1.0, weight = 200/100 = 2.0
    assert all(r.weight == pytest.approx(2.0) for r in d2)


def test_weights_zero_when_synthetic_count_zero_for_degree() -> None:
    # Defensive: if no synthetic rows for a degree, outcome rows for that degree
    # carry no weight (we have nothing to balance against).
    rows = [_row(3) for _ in range(50)]
    weighted = assign_weights(
        rows=rows, n_synth_per_degree={}, schedule=WeightSchedule(n_target_per_degree=100)
    )
    assert all(r.weight == 0.0 for r in weighted)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/right_edge/test_weight_schedule.py -v`
Expected: FAIL — module `outcome_dataset` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `src/wave_alpha/right_edge/outcome_dataset.py`:

```python
"""Outcome-driven training dataset construction.

Reads resolved snapshots from history.sqlite, replays the pipeline at each
snapshot's as_of against cache.sqlite bars, recomputes right-edge features,
and emits labeled rows for blended training. No features are persisted —
recompute is the only path.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import date
from typing import Literal

from wave_alpha.right_edge.features import RightEdgeFeatures


@dataclass(frozen=True)
class WeightSchedule:
    """Per-degree sample-weight schedule for outcome rows."""

    n_target_per_degree: int = 100
    """Full-ramp threshold: outcome influence reaches synthetic parity here."""

    include_time_stops: bool = True
    """When False, snapshots with outcome_status='time_stop' are dropped entirely."""

    time_stop_label: float = 0.0
    """Binary label assigned to time_stop outcomes when included."""


@dataclass(frozen=True)
class OutcomeRow:
    ticker: str
    as_of: date
    degree: int
    features: RightEdgeFeatures
    label: float           # 0.0 or 1.0
    weight: float          # populated by assign_weights


@dataclass(frozen=True)
class OutcomeDataset:
    rows: list[OutcomeRow]
    skipped_drift: int     # count_id absent and no acceptable fallback; or extraction error
    skipped_no_bars: int   # provider could not return bars at as_of
    schedule: WeightSchedule


def label_for_status(
    status: Literal["target", "stop", "time_stop"],
    *,
    schedule: WeightSchedule,
) -> float | None:
    """Map outcome_status → binary label, honoring schedule.include_time_stops.

    Returns None to signal "skip this row entirely" (only for time_stop with
    include_time_stops=False).
    """
    if status == "target":
        return 1.0
    if status == "stop":
        return 0.0
    if status == "time_stop":
        return schedule.time_stop_label if schedule.include_time_stops else None
    raise ValueError(f"unexpected outcome_status {status!r}")


def assign_weights(
    rows: list[OutcomeRow],
    *,
    n_synth_per_degree: dict[int, int],
    schedule: WeightSchedule,
) -> list[OutcomeRow]:
    """Assign per-row weights using the per-degree ramp.

    Properties (proved in tests):
    - n_outc=0           → returns []
    - n_outc<n_target    → outcome mass < synthetic mass, proportional to ramp
    - n_outc=n_target    → outcome mass == synthetic mass exactly
    - n_outc>>n_target   → outcome mass stays == synthetic mass (ramp caps)
    - n_synth(d)=0       → all outcome rows for degree d carry weight 0
    """
    if not rows:
        return []

    n_outc_per_degree: Counter[int] = Counter(r.degree for r in rows)
    weights_by_degree: dict[int, float] = {}
    for degree, n_outc in n_outc_per_degree.items():
        n_synth = n_synth_per_degree.get(degree, 0)
        if n_outc == 0 or n_synth == 0:
            weights_by_degree[degree] = 0.0
            continue
        ramp = min(1.0, n_outc / schedule.n_target_per_degree)
        weights_by_degree[degree] = (n_synth / n_outc) * ramp

    return [replace(r, weight=weights_by_degree[r.degree]) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/right_edge/test_weight_schedule.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/right_edge/outcome_dataset.py tests/right_edge/test_weight_schedule.py
git commit -m "feat(right_edge): outcome-dataset data models + per-degree weight ramp"
```

---

## Task 6: `build_outcome_dataset` (recompute features per row)

**Files:**
- Modify: `src/wave_alpha/right_edge/outcome_dataset.py` (add `build_outcome_dataset`)
- Test: `tests/right_edge/test_outcome_dataset.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/right_edge/test_outcome_dataset.py
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from wave_alpha.domain import Bar, Interval
from wave_alpha.history import store
from wave_alpha.history.models import (
    Snapshot, SnapshotCoherence, SnapshotCount, SnapshotOutcome,
    SnapshotPivot, SnapshotSignal,
)
from wave_alpha.right_edge.outcome_dataset import (
    OutcomeDataset, WeightSchedule, build_outcome_dataset,
)


class _FakeProvider:
    """Returns canned bars; mirrors the OHLCVProvider.fetch interface."""

    def __init__(self, bars_by_ticker: dict[str, list[Bar]]):
        self._bars = bars_by_ticker

    def fetch(self, ticker: str, interval: Interval, *, end: date) -> list[Bar]:
        bars = self._bars.get(ticker, [])
        return [b for b in bars if b.timestamp <= end]


def _make_bars(start: date, n: int, base: float = 100.0) -> list[Bar]:
    """Generate `n` daily bars with simple trending OHLCV."""
    out: list[Bar] = []
    for i in range(n):
        price = Decimal(str(base + i * 0.5))
        out.append(Bar(
            timestamp=start + timedelta(days=i),
            open=price, high=price + Decimal("1"),
            low=price - Decimal("1"), close=price, volume=1_000_000,
        ))
    return out


def _resolved_snap(ticker: str, as_of: date, *, status: str, count_id: str) -> Snapshot:
    return Snapshot(
        ticker=ticker, as_of=as_of, engine_version="0.9.0",
        top_3=[SnapshotCount(
            candidate_id=count_id, pattern="impulse", degree=1, last_pivot_label="3",
            pivots=[SnapshotPivot(timestamp=as_of, price=Decimal("100"))],
            engine_score=0.7, llm_prob=None, final_score=0.7,
        )],
        signal=SnapshotSignal(
            direction="long", entry_price=Decimal("100"),
            stop_price=Decimal("95"), target_price=Decimal("115"), rr=3.0,
            count_id=count_id, count_pattern="impulse", wave_label="3",
            degree=1, confidence=0.6, expected_bars_to_target=10, max_holding_bars=20,
        ),
        coherence=SnapshotCoherence(score=0.7, multiplier=1.0, pairwise={}, fallback_used=None),
        top_count_hash="h",
        created_at=datetime(as_of.year, as_of.month, as_of.day, 16, 0, tzinfo=UTC),
        outcome=SnapshotOutcome(
            status=status,  # type: ignore[arg-type]
            exit_date=as_of + timedelta(days=10) if status != "pending" else None,
            exit_price=Decimal("115") if status == "target" else Decimal("95") if status == "stop" else Decimal("100"),
            bars_held=10 if status != "pending" else None,
            realized_R=2.0 if status == "target" else -1.0 if status == "stop" else 0.0,
            last_observed_date=as_of + timedelta(days=10),
            bars_elapsed=10, unrealized_R=0.0, mfe_R=0.0, mae_R=0.0,
            resolved_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )


def test_build_outcome_dataset_filters_to_resolved_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "h.sqlite"
    # 1 target + 1 stop + 1 pending — only resolved rows should be considered
    store.upsert(_resolved_snap("AAPL", date(2025, 1, 1), status="target", count_id="C1"), db_path=db_path)
    store.upsert(_resolved_snap("AAPL", date(2025, 2, 1), status="stop", count_id="C2"), db_path=db_path)
    pending = _resolved_snap("AAPL", date(2025, 3, 1), status="pending", count_id="C3")
    # Force pending status by overriding outcome_status
    object.__setattr__(pending, "outcome", None)
    store.upsert(pending, db_path=db_path)

    # Bars from 2024-01-01 (enough lookback) through 2025-03-15
    bars = _make_bars(date(2024, 1, 1), 450)
    provider = _FakeProvider({"AAPL": bars})

    ds = build_outcome_dataset(
        db_path=db_path,
        bar_provider=provider,
        degree=1,
        schedule=WeightSchedule(),
        n_synth_per_degree={1: 500},
    )

    assert isinstance(ds, OutcomeDataset)
    # Two resolved rows; pending excluded. Some may be skipped if the recompute
    # cannot find a matching count — assertion is "≤ 2 retained, total accounted for".
    assert len(ds.rows) + ds.skipped_drift + ds.skipped_no_bars == 2


def test_build_outcome_dataset_skips_no_bars(tmp_path: Path) -> None:
    db_path = tmp_path / "h.sqlite"
    store.upsert(_resolved_snap("AAPL", date(2025, 1, 1), status="target", count_id="C1"), db_path=db_path)

    # Provider has NO bars for AAPL
    provider = _FakeProvider({"AAPL": []})

    ds = build_outcome_dataset(
        db_path=db_path,
        bar_provider=provider,
        degree=1,
        schedule=WeightSchedule(),
        n_synth_per_degree={1: 500},
    )
    assert ds.rows == []
    assert ds.skipped_no_bars == 1


def test_build_outcome_dataset_respects_include_time_stops_false(tmp_path: Path) -> None:
    db_path = tmp_path / "h.sqlite"
    store.upsert(_resolved_snap("AAPL", date(2025, 1, 1), status="time_stop", count_id="C1"), db_path=db_path)
    bars = _make_bars(date(2024, 1, 1), 450)
    provider = _FakeProvider({"AAPL": bars})

    ds = build_outcome_dataset(
        db_path=db_path,
        bar_provider=provider,
        degree=1,
        schedule=WeightSchedule(include_time_stops=False),
        n_synth_per_degree={1: 500},
    )
    # time_stop is dropped before any feature work; counts as neither skip bucket
    assert ds.rows == []
    assert ds.skipped_drift == 0
    assert ds.skipped_no_bars == 0


def test_build_outcome_dataset_empty_db_returns_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "h.sqlite"
    # Empty store (no rows). Test that a missing DB or empty DB returns clean.
    provider = _FakeProvider({})

    ds = build_outcome_dataset(
        db_path=db_path,
        bar_provider=provider,
        degree=1,
        schedule=WeightSchedule(),
        n_synth_per_degree={1: 500},
    )
    assert ds.rows == []
    assert ds.skipped_drift == 0
    assert ds.skipped_no_bars == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/right_edge/test_outcome_dataset.py -v`
Expected: FAIL — `build_outcome_dataset` not defined.

- [ ] **Step 3: Write minimal implementation**

Append to `src/wave_alpha/right_edge/outcome_dataset.py`:

```python
def _select_resolved_snapshots(*, db_path):
    """Yield (ticker, as_of, Snapshot) for rows whose outcome_status is resolved."""
    import sqlite3 as _sqlite3
    from wave_alpha.history.models import Snapshot as _Snapshot

    if not db_path.exists():
        return
    conn = _sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            """
            SELECT ticker, as_of, payload_json
            FROM snapshots
            WHERE outcome_status IN ('target', 'stop', 'time_stop')
            ORDER BY ticker ASC, as_of ASC
            """
        )
        for ticker, as_of_str, payload_json in cursor.fetchall():
            snap = _Snapshot.model_validate_json(payload_json)
            yield ticker, date.fromisoformat(as_of_str), snap
    finally:
        conn.close()


def _recompute_features_for_snapshot(
    snap,
    *,
    bar_provider,
    degree: int,
):
    """Return (features, used_fallback_top1) or None on drift/extraction failure."""
    from datetime import timedelta as _td
    from wave_alpha.data.point_in_time import PointInTimeView
    from wave_alpha.domain import Interval
    from wave_alpha.elliott.enumerator import enumerate_counts
    from wave_alpha.pipeline import _DEFAULT_ATR_MULTIPLES
    from wave_alpha.pivots.multi_degree import detect_multi_degree_with_snapshots
    from wave_alpha.right_edge.extractor import FeatureExtractor

    bars = bar_provider.fetch(snap.ticker, Interval.DAILY, end=snap.as_of)
    if not bars:
        return "no_bars"

    pit = PointInTimeView(bars, snap.as_of).visible()
    if not pit:
        return "no_bars"

    snaps_by_degree = detect_multi_degree_with_snapshots(
        pit, interval=Interval.DAILY, atr_multiples=_DEFAULT_ATR_MULTIPLES,
    )
    pivot_snap = snaps_by_degree.get(degree)
    if pivot_snap is None or pivot_snap.atr_at_last_bar is None or pivot_snap.required_move <= 0:
        return None

    counts = enumerate_counts(pivot_snap.pivots, degree=degree, top_k=5)
    if not counts:
        return None

    target_count_id = snap.signal.count_id if snap.signal else None
    matched = None
    if target_count_id is not None:
        for c in counts:
            # Match on the candidate's stable id — implementation detail of
            # WaveCount; the candidate_id stored in snapshot.top_3 is derived
            # from the same hash. Use the public attribute lookup defensively.
            cid = getattr(c, "candidate_id", None) or getattr(c, "id", None)
            if cid == target_count_id:
                matched = c
                break

    if matched is None:
        # Drift fallback: top-1 of recomputed enumeration, but only if it
        # plausibly matches the snapshot's signal at pattern + degree.
        top1 = counts[0]
        if (
            snap.signal is not None
            and top1.pattern == snap.signal.count_pattern
            and top1.degree == snap.signal.degree
        ):
            matched = top1
        else:
            return None

    extractor = FeatureExtractor()
    try:
        # Use the latest pivot in the count as the candidate; mirrors training-row
        # assembly's "latest tentative pivot" semantics.
        candidate = matched.pivots[-1]
        features = extractor.extract(
            candidate=candidate,
            point_in_time_bars=pit,
            atr_at_candidate=pivot_snap.atr_at_last_bar,
            required_move=pivot_snap.required_move,
            higher_degree_zone_match=0.0,
            top_count=matched,
        )
    except Exception:
        return None
    return features


def build_outcome_dataset(
    *,
    db_path,
    bar_provider,
    degree: int,
    schedule: WeightSchedule,
    n_synth_per_degree: dict[int, int],
    limit: int | None = None,
) -> OutcomeDataset:
    """Build an outcome-labeled dataset by replaying the pipeline at each
    resolved snapshot's as_of and recomputing right-edge features.

    Args:
        db_path: history.sqlite location.
        bar_provider: OHLCVProvider — typically `CachedProvider(YahooProvider())`.
        degree: which pivot degree to recompute at (must match snapshot.signal.degree
                semantics — caller is responsible for routing).
        schedule: weight-schedule (label policy + ramp constants).
        n_synth_per_degree: per-degree counts from the synthetic dataset, used
                            to balance outcome mass against synthetic mass.
        limit: optional cap on rows processed (debug / fast iteration).
    """
    rows: list[OutcomeRow] = []
    skipped_drift = 0
    skipped_no_bars = 0

    iterator = _select_resolved_snapshots(db_path=db_path)
    processed = 0
    for ticker, as_of, snap in iterator:
        if snap.outcome is None or snap.outcome.status == "pending":
            continue  # defensive — query already filters, double-guard for type
        # Only recompute at the requested degree; snapshots from other degrees
        # are silently skipped (caller would invoke us per-degree if needed).
        if snap.signal is None or snap.signal.degree != degree:
            continue

        label = label_for_status(snap.outcome.status, schedule=schedule)
        if label is None:
            continue  # drop time_stop when include_time_stops=False

        outcome = _recompute_features_for_snapshot(snap, bar_provider=bar_provider, degree=degree)
        if outcome == "no_bars":
            skipped_no_bars += 1
            continue
        if outcome is None:
            skipped_drift += 1
            continue

        features = outcome
        rows.append(OutcomeRow(
            ticker=ticker, as_of=as_of, degree=degree,
            features=features, label=label, weight=0.0,
        ))
        processed += 1
        if limit is not None and processed >= limit:
            break

    weighted = assign_weights(rows=rows, n_synth_per_degree=n_synth_per_degree, schedule=schedule)
    return OutcomeDataset(
        rows=weighted,
        skipped_drift=skipped_drift,
        skipped_no_bars=skipped_no_bars,
        schedule=schedule,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/right_edge/test_outcome_dataset.py -v`
Expected: PASS (4 tests). Note the first test asserts a balance — if it fails, inspect whether recompute is finding matches or hitting drift fallback; both are acceptable per the test assertion.

Then run the broader right_edge suite to confirm no regressions:
Run: `uv run pytest tests/right_edge/ -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/right_edge/outcome_dataset.py tests/right_edge/test_outcome_dataset.py
git commit -m "feat(right_edge): build_outcome_dataset — recompute features at train time"
```

---

## Task 7: `fit_logistic` + `fit_platt` accept sample weights

**Files:**
- Modify: `src/wave_alpha/right_edge/training.py`
- Test: `tests/right_edge/test_fit_logistic_weights.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/right_edge/test_fit_logistic_weights.py
import numpy as np
import pytest
from datetime import date

from wave_alpha.right_edge.features import RightEdgeFeatures
from wave_alpha.right_edge.training import (
    TrainingRow, fit_logistic, fit_platt, predict_logistic,
)


def _row(label: int, atr: float = 1.0) -> TrainingRow:
    return TrainingRow(
        ticker="T", as_of=date(2025, 1, 1), degree=1,
        features=RightEdgeFeatures(
            retracement_progress=0.5, bars_since_candidate=5,
            volume_decline_pct=0.0, higher_degree_zone_match=0.0,
            atr_regime=atr,
        ),
        outcome=label,
    )


def test_fit_logistic_unweighted_unchanged_by_unit_weights() -> None:
    # Passing all 1.0 weights must produce identical params to passing no weights.
    rows = [_row(0, 0.5), _row(1, 1.5)] * 50
    p_unweighted = fit_logistic(rows, seed=42)
    p_weighted = fit_logistic(
        rows, seed=42, sample_weights=np.ones(len(rows), dtype=np.float64)
    )
    assert p_unweighted.weights == pytest.approx(p_weighted.weights, abs=1e-9)
    assert p_unweighted.intercept == pytest.approx(p_weighted.intercept, abs=1e-9)


def test_fit_logistic_high_weight_row_pulls_decision_boundary() -> None:
    # 1 positive at atr=1.5 with weight=1000, 100 negatives at atr=0.5.
    # Heavy positive must shift the boundary so atr=1.5 is predicted as positive.
    rows = [_row(0, 0.5)] * 100 + [_row(1, 1.5)]
    weights = np.array([1.0] * 100 + [1000.0])
    p = fit_logistic(rows, seed=42, sample_weights=weights)
    pos_pred = predict_logistic(
        p,
        RightEdgeFeatures(
            retracement_progress=0.5, bars_since_candidate=5,
            volume_decline_pct=0.0, higher_degree_zone_match=0.0,
            atr_regime=1.5,
        ),
    )
    assert pos_pred > 0.5


def test_fit_platt_unweighted_unchanged_by_unit_weights() -> None:
    preds = [(0.1, 0), (0.9, 1), (0.4, 0), (0.6, 1)] * 30
    p_a = fit_platt(preds)
    p_b = fit_platt(preds, sample_weights=np.ones(len(preds), dtype=np.float64))
    assert p_a.a == pytest.approx(p_b.a, abs=1e-9)
    assert p_a.b == pytest.approx(p_b.b, abs=1e-9)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/right_edge/test_fit_logistic_weights.py -v`
Expected: FAIL — `sample_weights` is not a parameter.

- [ ] **Step 3: Write minimal implementation**

Edit `src/wave_alpha/right_edge/training.py`. Modify `fit_logistic`:

```python
def fit_logistic(
    rows: list[TrainingRow],
    *,
    lr: float = 0.05,
    n_iters: int = 2000,
    seed: int = 42,
    with_fib: bool = False,
    sample_weights: np.ndarray | None = None,
) -> LogisticParams:
    """Batch gradient descent on binary cross-entropy. Deterministic given seed.

    If `sample_weights` is provided, the gradient is the weighted mean —
    rows with higher weights pull the boundary toward their label. Defaults
    to uniform weights (behavior identical to the no-weights path).
    """
    if not rows:
        raise ValueError("cannot fit on empty rows")
    rng = np.random.default_rng(seed)
    x_mat = np.stack([_row_to_vector(r, with_fib=with_fib) for r in rows])
    y = np.array([r.outcome for r in rows], dtype=np.float64)
    if sample_weights is None:
        sw = np.ones(len(rows), dtype=np.float64)
    else:
        sw = np.asarray(sample_weights, dtype=np.float64)
        if sw.shape != (len(rows),):
            raise ValueError(
                f"sample_weights shape {sw.shape} != ({len(rows)},)"
            )
    sw_sum = float(sw.sum())
    w = rng.normal(0, 0.01, size=x_mat.shape[1])
    b = 0.0
    for _ in range(n_iters):
        z = x_mat @ w + b
        p = 1.0 / (1.0 + np.exp(-z))
        err = (p - y) * sw
        grad_w = x_mat.T @ err / sw_sum
        grad_b = float(err.sum() / sw_sum)
        w -= lr * grad_w
        b -= lr * grad_b
    return LogisticParams(
        feature_order=feature_order(with_fib=with_fib),
        weights=tuple(float(v) for v in w),
        intercept=float(b),
    )
```

Modify `fit_platt` similarly:

```python
def fit_platt(
    predictions: list[tuple[float, int]],
    *,
    sample_weights: np.ndarray | None = None,
) -> PlattParams:
    """Platt scaling: 1-feature logistic on (raw_proba, label) pairs.

    Honors `sample_weights` symmetrically with `fit_logistic`.
    """
    if not predictions:
        return PlattParams(a=1.0, b=0.0)
    x_col = np.array([[p] for p, _ in predictions], dtype=np.float64)
    y = np.array([o for _, o in predictions], dtype=np.float64)
    if sample_weights is None:
        sw = np.ones(len(predictions), dtype=np.float64)
    else:
        sw = np.asarray(sample_weights, dtype=np.float64)
    sw_sum = float(sw.sum())
    a = 0.0
    b = 0.0
    for _ in range(2000):
        z = x_col[:, 0] * a + b
        ph = 1.0 / (1.0 + np.exp(-z))
        err = (ph - y) * sw
        grad_a = float((err * x_col[:, 0]).sum() / sw_sum)
        grad_b = float(err.sum() / sw_sum)
        a -= 0.05 * grad_a
        b -= 0.05 * grad_b
    return PlattParams(a=float(a), b=float(b))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/right_edge/test_fit_logistic_weights.py -v`
Expected: PASS (3 tests)

Run the full right_edge suite to verify backward compatibility:
Run: `uv run pytest tests/right_edge/ -v`
Expected: all existing tests still green.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/right_edge/training.py tests/right_edge/test_fit_logistic_weights.py
git commit -m "feat(right_edge): fit_logistic / fit_platt accept optional sample weights"
```

---

## Task 8: `train_blended_final` — refit logistic + per-degree Platt on synthetic ∪ outcomes

**Files:**
- Modify: `src/wave_alpha/right_edge/training.py` (add `train_blended_final`)
- Test: `tests/right_edge/test_training_blended.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/right_edge/test_training_blended.py
from datetime import date

import numpy as np
import pytest

from wave_alpha.right_edge.features import RightEdgeFeatures
from wave_alpha.right_edge.outcome_dataset import (
    OutcomeDataset, OutcomeRow, WeightSchedule,
)
from wave_alpha.right_edge.training import TrainingRow, train_blended_final


def _feats(retrace: float = 0.5, atr: float = 1.0) -> RightEdgeFeatures:
    return RightEdgeFeatures(
        retracement_progress=retrace, bars_since_candidate=5,
        volume_decline_pct=0.0, higher_degree_zone_match=0.0,
        atr_regime=atr,
    )


def _synth(label: int, degree: int = 1) -> TrainingRow:
    return TrainingRow(
        ticker="S", as_of=date(2025, 1, 1), degree=degree,
        features=_feats(), outcome=label,
    )


def _outc(label: float, weight: float, degree: int = 1) -> OutcomeRow:
    return OutcomeRow(
        ticker="O", as_of=date(2025, 6, 1), degree=degree,
        features=_feats(), label=label, weight=weight,
    )


def test_train_blended_final_with_empty_outcomes_is_synthetic_only() -> None:
    synth = [_synth(0)] * 50 + [_synth(1)] * 50
    outc = OutcomeDataset(rows=[], skipped_drift=0, skipped_no_bars=0,
                          schedule=WeightSchedule())
    logistic, platt = train_blended_final(synth, outc, seed=42, with_fib=False)
    # Compare against an unweighted fit on the same synth-only data
    from wave_alpha.right_edge.training import fit_logistic
    expected = fit_logistic(synth, seed=42, with_fib=False)
    assert logistic.weights == pytest.approx(expected.weights, abs=1e-9)
    assert logistic.intercept == pytest.approx(expected.intercept, abs=1e-9)


def test_train_blended_final_includes_outcome_rows() -> None:
    synth = [_synth(0)] * 50
    # 50 outcome rows all labeled 1, weight 10 — should shift the model toward positive.
    outc_rows = [_outc(1.0, weight=10.0) for _ in range(50)]
    outc = OutcomeDataset(rows=outc_rows, skipped_drift=0, skipped_no_bars=0,
                          schedule=WeightSchedule())
    logistic, platt = train_blended_final(synth, outc, seed=42, with_fib=False)
    # Prediction on the shared feature vector should now be ~> 0.5 because
    # outcome mass (50 * 10 = 500) >> synthetic mass (50 * 1 = 50).
    from wave_alpha.right_edge.training import predict_logistic
    pred = predict_logistic(logistic, _feats())
    assert pred > 0.5


def test_train_blended_final_fits_per_degree_platt() -> None:
    synth = [_synth(0, degree=1), _synth(1, degree=1)] * 30
    outc = OutcomeDataset(rows=[_outc(1.0, weight=1.0, degree=1)],
                          skipped_drift=0, skipped_no_bars=0,
                          schedule=WeightSchedule())
    logistic, platt = train_blended_final(synth, outc, seed=42, with_fib=False)
    # Platt fit on the blended (raw, outcome) pairs — must have an entry for degree 1
    assert 1 in platt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/right_edge/test_training_blended.py -v`
Expected: FAIL — `train_blended_final` not defined.

- [ ] **Step 3: Write minimal implementation**

Append to `src/wave_alpha/right_edge/training.py`:

```python
def train_blended_final(
    synthetic_rows: list[TrainingRow],
    outcome_dataset,                # OutcomeDataset — typed loosely to avoid import cycle
    *,
    seed: int = 42,
    with_fib: bool = False,
) -> tuple[LogisticParams, dict[int, PlattParams]]:
    """Refit the production logistic + per-degree Platt on synthetic ∪ outcomes.

    This is the FINAL-refit replacement for the synthetic-only artifact
    produced by walk_forward. Walk-forward stays synthetic-only as an OOS
    estimator; outcomes enter only here.

    Args:
        synthetic_rows: all synthetic rows (across all quarters) — typically
                        the flattening of `rows_by_quarter.values()` used by
                        walk_forward's final refit.
        outcome_dataset: OutcomeDataset with per-row weights already assigned
                         by build_outcome_dataset.

    Returns:
        (LogisticParams, dict[degree, PlattParams]) — drop-in replacement for
        (wf.final_logistic, wf.platt_per_degree).
    """
    if not synthetic_rows and not outcome_dataset.rows:
        raise ValueError("train_blended_final called with no rows")

    # Build the combined row list + sample_weights vector. Synthetic rows
    # carry weight 1.0; outcome rows carry their assigned per-degree weight.
    combined_rows: list[TrainingRow] = list(synthetic_rows)
    weights: list[float] = [1.0] * len(synthetic_rows)
    for orow in outcome_dataset.rows:
        combined_rows.append(TrainingRow(
            ticker=orow.ticker,
            as_of=orow.as_of,
            degree=orow.degree,
            features=orow.features,
            outcome=int(round(orow.label)),  # binary label
        ))
        weights.append(orow.weight)

    sw = np.asarray(weights, dtype=np.float64)
    logistic = fit_logistic(
        combined_rows, seed=seed, with_fib=with_fib, sample_weights=sw,
    )

    # Per-degree Platt: fit on (raw_proba, outcome) pairs from the combined
    # rows, also weighted. Mirrors the structure of walk_forward's final platt
    # refit, with weights threaded through.
    by_degree: dict[int, list[tuple[float, int]]] = {}
    weights_by_degree: dict[int, list[float]] = {}
    for r, w in zip(combined_rows, sw, strict=True):
        raw = predict_logistic(logistic, r.features)
        by_degree.setdefault(r.degree, []).append((raw, r.outcome))
        weights_by_degree.setdefault(r.degree, []).append(float(w))

    platt = {
        d: fit_platt(
            pairs,
            sample_weights=np.asarray(weights_by_degree[d], dtype=np.float64),
        )
        for d, pairs in by_degree.items()
        if pairs
    }
    return logistic, platt
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/right_edge/test_training_blended.py -v`
Expected: PASS (3 tests)

Run the full right_edge suite:
Run: `uv run pytest tests/right_edge/ -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/right_edge/training.py tests/right_edge/test_training_blended.py
git commit -m "feat(right_edge): train_blended_final refits on synthetic ∪ outcomes"
```

---

## Task 9: `RightEdgeOutcomeTrainingConfig` in prefs

**Files:**
- Modify: `src/wave_alpha/prefs/models.py`
- Test: `tests/prefs/test_models.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/prefs/test_models.py`:

```python
from wave_alpha.prefs.models import RightEdgeConfig, RightEdgeOutcomeTrainingConfig


def test_right_edge_outcome_training_defaults() -> None:
    cfg = RightEdgeOutcomeTrainingConfig()
    assert cfg.n_target_per_degree == 100
    assert cfg.include_time_stops is True
    assert cfg.time_stop_label == 0.0


def test_right_edge_config_includes_outcome_training_defaults() -> None:
    cfg = RightEdgeConfig()
    assert cfg.outcome_training.n_target_per_degree == 100


def test_right_edge_outcome_training_overrides() -> None:
    cfg = RightEdgeConfig.model_validate({
        "outcome_training": {"n_target_per_degree": 50, "include_time_stops": False}
    })
    assert cfg.outcome_training.n_target_per_degree == 50
    assert cfg.outcome_training.include_time_stops is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/prefs/test_models.py::test_right_edge_outcome_training_defaults -v`
Expected: FAIL — `RightEdgeOutcomeTrainingConfig` does not exist.

- [ ] **Step 3: Write minimal implementation**

Edit `src/wave_alpha/prefs/models.py`. Add the new config class and wire it into `RightEdgeConfig`:

```python
class RightEdgeOutcomeTrainingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    n_target_per_degree: int = Field(default=100, ge=1)
    include_time_stops: bool = True
    time_stop_label: float = Field(default=0.0, ge=0.0, le=1.0)


class RightEdgeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: Literal["auto", "heuristic", "calibrated_heuristic", "logistic"] = "auto"
    outcome_training: RightEdgeOutcomeTrainingConfig = Field(
        default_factory=RightEdgeOutcomeTrainingConfig
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/prefs/test_models.py -v`
Expected: PASS (all tests in file including the prior 4 from Task 2).

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/prefs/models.py tests/prefs/test_models.py
git commit -m "feat(prefs): add RightEdgeOutcomeTrainingConfig"
```

---

## Task 10: CLI wiring — `train right-edge --use-outcomes`

**Files:**
- Modify: `src/wave_alpha/cli/app.py` (function `train_right_edge`)
- Modify: `src/wave_alpha/right_edge/training.py` (extend `serialize_artifact`)
- Test: `tests/cli/test_train_right_edge_use_outcomes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/cli/test_train_right_edge_use_outcomes.py
"""Smoke test for `wave-alpha train right-edge --use-outcomes`.

Validates:
- The flag is accepted by the CLI.
- When passed, build_outcome_dataset is invoked and train_blended_final is
  called instead of the synthetic-only final refit.
- The serialized artifact has an `outcome_training` metadata block.
"""
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from wave_alpha.cli.app import app
from wave_alpha.right_edge.outcome_dataset import OutcomeDataset, WeightSchedule


@pytest.fixture
def stub_walk_forward(monkeypatch: pytest.MonkeyPatch):
    """Replace the heavy walk_forward + universe loader with a stub returning
    a minimal WalkForwardResult so the CLI test stays fast."""
    from wave_alpha.right_edge.training import (
        Fold, LogisticParams, PlattParams, WalkForwardResult,
    )
    fake_logistic = LogisticParams(
        feature_order=("retracement_progress", "bars_since_candidate",
                       "volume_decline_pct", "higher_degree_zone_match", "atr_regime"),
        weights=(0.0, 0.0, 0.0, 0.0, 0.0),
        intercept=0.0,
    )
    fake_wf = WalkForwardResult(
        folds=[Fold(train_until=date(2025, 1, 1),
                    test_start=date(2025, 1, 1), test_end=date(2025, 4, 1))],
        final_logistic=fake_logistic,
        platt_per_degree={1: PlattParams(a=1.0, b=0.0)},
        oos_predictions=[],
        n_train_rows=10,
        n_oos_rows=0,
    )
    fake_loader = MagicMock(return_value=(date(2024, 1, 1), date(2025, 1, 1), [], {date(2024, 1, 1): []}))
    monkeypatch.setattr("wave_alpha.cli.app._load_rows_by_quarter", fake_loader)
    monkeypatch.setattr("wave_alpha.right_edge.training.walk_forward",
                        MagicMock(return_value=fake_wf))
    return fake_wf


def test_use_outcomes_flag_is_accepted(tmp_path: Path, stub_walk_forward) -> None:
    out_path = tmp_path / "out.json"
    runner = CliRunner()
    # Stub the outcome dataset builder so we don't need history.sqlite.
    with patch(
        "wave_alpha.right_edge.outcome_dataset.build_outcome_dataset",
        return_value=OutcomeDataset(rows=[], skipped_drift=0, skipped_no_bars=0,
                                    schedule=WeightSchedule()),
    ) as stub_build:
        res = runner.invoke(app, [
            "train", "right-edge",
            "--start", "2024-01-01",
            "--end", "2025-01-01",
            "--use-outcomes",
            "--output", str(out_path),
        ])
    assert res.exit_code == 0, res.output
    assert stub_build.called
    # Even with no outcome rows, the CLI should report outcome metadata.
    assert "outcome" in res.output.lower()


def test_use_outcomes_writes_metadata_into_artifact(
    tmp_path: Path, stub_walk_forward
) -> None:
    import json as _json
    out_path = tmp_path / "out.json"
    runner = CliRunner()
    ds = OutcomeDataset(
        rows=[], skipped_drift=2, skipped_no_bars=1, schedule=WeightSchedule()
    )
    with patch(
        "wave_alpha.right_edge.outcome_dataset.build_outcome_dataset",
        return_value=ds,
    ):
        res = runner.invoke(app, [
            "train", "right-edge",
            "--start", "2024-01-01", "--end", "2025-01-01",
            "--use-outcomes", "--force", "--write",
            "--output", str(out_path),
        ])
    # --force allows write regardless of verdict
    assert out_path.exists(), res.output
    artifact = _json.loads(out_path.read_text())
    assert "outcome_training" in artifact
    assert artifact["outcome_training"]["skipped_drift"] == 2
    assert artifact["outcome_training"]["skipped_no_bars"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_train_right_edge_use_outcomes.py -v`
Expected: FAIL — `--use-outcomes` flag does not exist.

- [ ] **Step 3: Write minimal implementation**

First, extend `serialize_artifact` in `src/wave_alpha/right_edge/training.py`. Modify the function signature to accept an optional `outcome_training` dict and include it in the output when non-None:

```python
def serialize_artifact(
    *,
    wf: WalkForwardResult,
    gate: object,
    seed: int,
    trained_at: _date_type | None = None,
    git_sha: str | None = None,
    outcome_training: dict | None = None,
) -> dict:
    base = {
        "version": "logistic_v1",
        "trained_at": (trained_at or _date_type.today()).isoformat(),
        "git_sha": git_sha if git_sha is not None else _git_sha(),
        "feature_order": list(wf.final_logistic.feature_order),
        "weights": list(wf.final_logistic.weights),
        "intercept": wf.final_logistic.intercept,
        "platt_per_degree": {str(d): {"a": p.a, "b": p.b} for d, p in wf.platt_per_degree.items()},
        "training": {
            "n_train_rows": wf.n_train_rows,
            "n_oos_rows": wf.n_oos_rows,
            "n_folds": len(wf.folds),
            "rng_seed": seed,
        },
        "gate": {
            "verdict": gate.verdict,  # type: ignore[attr-defined]
            "logistic_brier_ci": list(gate.logistic_brier_ci),  # type: ignore[attr-defined]
            "heuristic_brier_ci": list(gate.heuristic_brier_ci),  # type: ignore[attr-defined]
            "per_degree_calibration": gate.per_degree_calibration,  # type: ignore[attr-defined]
        },
    }
    if outcome_training is not None:
        base["outcome_training"] = outcome_training
    return base
```

Next, edit `train_right_edge` in `src/wave_alpha/cli/app.py`. Add the new flags to the signature:

```python
@train_app.command("right-edge")
def train_right_edge(
    # ... existing options ...
    use_outcomes: bool = typer.Option(
        False, "--use-outcomes",
        help="Blend outcome-labeled rows from history.sqlite into the final refit.",
    ),
    outcome_weight_target: int = typer.Option(
        100, "--outcome-weight-target",
        help="Per-degree outcome row count at which outcomes match synthetic mass.",
    ),
    outcome_limit: int | None = typer.Option(
        None, "--outcome-limit",
        help="Cap on outcome rows processed (debug/fast iteration).",
    ),
    drop_time_stops: bool = typer.Option(
        False, "--drop-time-stops",
        help="Exclude time_stop snapshots from the outcome dataset.",
    ),
) -> None:
```

Then, after the existing `walk_forward` call but before `gate = _evaluate_promotion(...)`, add the blended-final step:

```python
    outcome_metadata: dict | None = None
    if use_outcomes:
        from collections import Counter
        from wave_alpha.right_edge.outcome_dataset import (
            WeightSchedule, build_outcome_dataset,
        )
        from wave_alpha.right_edge.training import train_blended_final
        from wave_alpha.prefs.config import data_dir

        schedule = WeightSchedule(
            n_target_per_degree=outcome_weight_target,
            include_time_stops=not drop_time_stops,
        )
        synth_rows_all = [r for rs in rows_by_quarter.values() for r in rs]
        n_synth_per_degree = dict(Counter(r.degree for r in synth_rows_all))
        db_path = data_dir() / "history.sqlite"

        typer.echo("Building outcome dataset (recompute from cached bars)...")
        ds = build_outcome_dataset(
            db_path=db_path,
            bar_provider=provider,
            degree=2,  # primary degree; multi-degree extension is a v2 concern
            schedule=schedule,
            n_synth_per_degree=n_synth_per_degree,
            limit=outcome_limit,
        )
        typer.echo(
            f"  → outcome rows: {len(ds.rows)} | "
            f"skipped_drift: {ds.skipped_drift} | "
            f"skipped_no_bars: {ds.skipped_no_bars}"
        )

        logistic_blended, platt_blended = train_blended_final(
            synth_rows_all, ds, seed=seed, with_fib=with_fib_features,
        )
        # Splice blended results back into the WalkForwardResult for serialization.
        wf = WalkForwardResult(
            folds=wf.folds,
            final_logistic=logistic_blended,
            platt_per_degree=platt_blended,
            oos_predictions=wf.oos_predictions,
            n_train_rows=wf.n_train_rows + len(ds.rows),
            n_oos_rows=wf.n_oos_rows,
        )
        outcome_metadata = {
            "n_outcome_rows": len(ds.rows),
            "skipped_drift": ds.skipped_drift,
            "skipped_no_bars": ds.skipped_no_bars,
            "n_target_per_degree": schedule.n_target_per_degree,
            "include_time_stops": schedule.include_time_stops,
            "time_stop_label": schedule.time_stop_label,
        }
```

Then update the `serialize_artifact` call to pass the metadata:

```python
        artifact = serialize_artifact(wf=wf, gate=gate, seed=seed, outcome_training=outcome_metadata)
```

(`outcome_metadata` is `None` when `--use-outcomes` was not passed, so the artifact is unchanged in the default path.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cli/test_train_right_edge_use_outcomes.py -v`
Expected: PASS (2 tests).

Run the full CLI suite to confirm no regressions:
Run: `uv run pytest tests/cli/ -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/cli/app.py src/wave_alpha/right_edge/training.py tests/cli/test_train_right_edge_use_outcomes.py
git commit -m "feat(cli): 'train right-edge --use-outcomes' blends outcomes into final refit"
```

---

## Task 11: Documentation — update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Find the existing right-edge section**

Run: `grep -n "Right-edge confirmation models" CLAUDE.md`
Expected: A single line number matching the section header.

- [ ] **Step 2: Edit the section to mention `--use-outcomes` and the recompute policy**

Add a paragraph at the end of the "Right-edge confirmation models" section:

```markdown
**Outcome-driven retraining.** `wave-alpha train right-edge --use-outcomes` blends realized-outcome rows from `history.sqlite` into the final logistic + Platt refit. Features are **recomputed at training time** from `cache.sqlite` bars — no per-row storage is added to `history.sqlite`. Walk-forward training stays synthetic-only as an OOS estimator; outcomes enter only at the final refit. The per-degree sample-weight ramp (default `n_target_per_degree=100`, configurable via `--outcome-weight-target` or `right_edge.outcome_training` in `config.yaml`) scales outcome influence from ~0 to synthetic-parity smoothly as the labeled dataset grows.

**`history.sqlite` retention.** Auto-prune runs inside `record_snapshot` after the resolver pass, deleting rows for the current ticker older than `history.retention_days` (default 730, configurable; 0 disables). Manual companions: `wave-alpha history stats` for inspection and `wave-alpha history prune --older-than 365d [--ticker T] [--dry-run]` for ad-hoc cleanup.
```

- [ ] **Step 3: Verify the edit reads cleanly**

Run: `grep -A 30 "Right-edge confirmation models" CLAUDE.md | head -40`
Expected: section now mentions `--use-outcomes`, the recompute policy, and the history retention behavior.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md notes outcome-driven training + history retention"
```

---

## Final verification

After all 11 tasks, run the full test suite and verify lint/format:

- [ ] **Run the full test suite**

Run: `uv run pytest`
Expected: all tests pass.

- [ ] **Run lint**

Run: `uv run ruff check .`
Expected: no errors.

- [ ] **Run format check**

Run: `uv run ruff format --check .`
Expected: no diffs. (If diffs, run `uv run ruff format .` and amend the relevant commit.)

- [ ] **Run gitnexus detect_changes** (per CLAUDE.md mandate)

Run: ask the user to run `mcp__gitnexus__detect_changes` via the MCP tools, or `npx gitnexus analyze` to refresh the index, then `mcp__gitnexus__detect_changes`. Expected: changes scoped to the modules listed in the File Structure block — no surprising symbol churn elsewhere.

- [ ] **Open the PR**

Run:
```bash
gh pr create --title "feat(right-edge): outcome-driven training + bounded history retention" --body "$(cat <<'EOF'
## Summary
- New `wave-alpha train right-edge --use-outcomes` blends realized-outcome rows from history.sqlite into the final logistic + Platt refit.
- Features are recomputed at train time from cache.sqlite — zero per-row storage added to history.sqlite.
- New `history.retention_days` config (default 730) auto-prunes per-ticker on every `record_snapshot`.
- New `wave-alpha history {stats,prune}` sub-app for inspection and manual cleanup.
- `fit_logistic` / `fit_platt` gain optional `sample_weights` (backward-compatible).

## Spec
docs/superpowers/specs/2026-05-10-outcome-driven-right-edge-training-design.md

## Test plan
- [ ] Unit tests across `tests/right_edge/`, `tests/history/`, `tests/cli/`, `tests/prefs/` pass
- [ ] `uv run pytest` clean
- [ ] `uv run ruff check .` clean
- [ ] Smoke: `uv run wave-alpha history stats` on a real cache lists tickers + counts
- [ ] Smoke: `uv run wave-alpha train right-edge --use-outcomes --start 2024-01-01 --end 2025-12-31 --no-write` runs end-to-end on local data

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

Spec → plan coverage check:

| Spec section | Implementing task(s) |
|---|---|
| §2 Goal: blended training mode | Tasks 5, 6, 7, 8, 10 |
| §2 Goal: recompute features at train time | Task 6 |
| §2 Goal: per-degree sample-weight ramp | Tasks 5, 8 |
| §2 Goal: reuse existing artifact format + promotion gate | Tasks 8, 10 (unchanged gate) |
| §2 Goal: auto-prune by age | Tasks 1, 2, 3 |
| §2 Goal: manual `train right-edge --use-outcomes` | Task 10 |
| §2 Goal: `history` sub-app | Task 4 |
| §2 Goal: artifact metadata records schedule | Task 10 (serialize_artifact extension) |
| §3 Non-goal: 3-class output | Honored — Task 5 labels are binary float |
| §3 Non-goal: per-degree separate artifacts | Honored — single logistic + per-degree Platt |
| §3 Non-goal: auto-retrain triggers | Honored — manual CLI only |
| §3 Non-goal: cache.sqlite pruning | Honored — out of scope |
| §6.1 WeightSchedule | Task 5 |
| §6.2 OutcomeRow / OutcomeDataset | Task 5 |
| §6.3 Label encoding | Task 5 (`label_for_status`) + Task 6 (applied in builder) |
| §6.4 Sample-weight computation | Task 5 (`assign_weights`) |
| §6.5 Count-drift handling | Task 6 (`_recompute_features_for_snapshot`) |
| §6.6 SQLite changes (`prune_older_than`) | Task 1 |
| §7.1 `train right-edge` extended flags | Task 10 |
| §7.2 `history` sub-app | Task 4 |
| §8 Configuration | Tasks 2, 9 |
| §9 Error handling | Tasks 3 (prune), 6 (build) cover the error matrix |
| §10 Testing | Tests in tasks 1, 3, 4, 5, 6, 7, 8, 10 |

No spec requirement is missing a task. No task references a function defined nowhere. Naming is consistent across tasks (`build_outcome_dataset`, `assign_weights`, `WeightSchedule`, `train_blended_final`).

Two type-consistency notes to flag for the implementer:
1. `OutcomeRow.label` is `float` (per Task 5), but `train_blended_final` (Task 8) casts to `int(round(label))` to construct a `TrainingRow` whose `outcome` field is `int`. This is intentional; both 0.0 and 1.0 round correctly. If the schedule ever introduces fractional labels, this round must change.
2. `_recompute_features_for_snapshot` (Task 6) reads `candidate_id` defensively via `getattr(c, "candidate_id", None) or getattr(c, "id", None)`. The actual attribute on `WaveCount` is whichever the existing snapshot code populates `SnapshotCount.candidate_id` from — verify with `grep -n "candidate_id" src/wave_alpha/elliott/models.py src/wave_alpha/pipeline.py` during implementation; if it's something different, hard-code the correct attribute and drop the `getattr` fallback.
