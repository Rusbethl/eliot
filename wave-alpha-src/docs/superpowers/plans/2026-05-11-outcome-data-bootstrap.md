# Outcome-Data Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make outcome-driven right-edge training viable by (a) regenerating the existing 70 unresolvable history snapshots under the current signal schema and (b) adding a CLI command that densifies `history.sqlite` over a curated universe × quarterly as-of grid, so a subsequent `wave-alpha train right-edge --use-outcomes` run has enough labeled outcomes to actually shift the model.

**Architecture:** A single new helper `replay_snapshot` in `history/service.py` re-runs `pipeline.run_analysis` at a given `(ticker, as_of)` against cached bars and upserts the result via `store.upsert` + `resolver.resolve_pending_for` — deliberately bypassing `record_snapshot`'s auto-prune so backfilling old as-of dates does not delete legitimate sibling rows. Two new sub-commands on the existing `wave-alpha history` Typer app drive it: `backfill-signals` walks rows already in the DB; `densify` walks (universe × quarterly cadence) creating new rows.

**Tech Stack:** Python 3.11, Typer, Pydantic v2, SQLite (via existing `wave_alpha.history.store`), pytest. No new runtime deps. LLM is forced to `LLMMode.DISABLED` for both commands — bootstrap data must be deterministic and offline.

**Decisions baked in (recommended defaults; redirect before execution if you want to change):**
1. **Cadence:** quarterly (~28 dates × 50 tickers ≈ 1400 snapshots; densest payoff per overnight run)
2. **Universe:** `data/universe_curated.txt` (the 50-ticker set used elsewhere)
3. **Missing-bars handling:** silent-skip with summary count (universes drift; e.g. COIN didn't exist in 2018)
4. **Resolver pass:** every `replay_snapshot` call invokes the resolver; auto-prune is *bypassed*

---

## File Structure

| File | Responsibility | Status |
|---|---|---|
| `src/wave_alpha/history/service.py` | Add `replay_snapshot()` helper. Calls `run_analysis` (LLM disabled), upserts, resolves. No prune. | Modify |
| `src/wave_alpha/cli/history_app.py` | Add `backfill-signals` and `densify` Typer commands. | Modify |
| `tests/history/test_replay_snapshot.py` | Unit tests for the new helper, with `_FakeProvider`. | Create |
| `tests/cli/test_history_backfill_signals.py` | CLI tests using `CliRunner` + monkeypatched provider/db. | Create |
| `tests/cli/test_history_densify.py` | CLI tests for date enumeration + dry-run + universe parsing. | Create |
| `docs/superpowers/plans/2026-05-11-outcome-data-bootstrap.md` | This document. | Create |

No engine, no training, no artifact files are touched. The PR is purely additive.

---

## Task 1: `replay_snapshot` helper

**Files:**
- Modify: `src/wave_alpha/history/service.py` (add new function below existing `record_snapshot` near line 209)
- Create: `tests/history/test_replay_snapshot.py`

- [ ] **Step 1: Write the failing test**

Create `tests/history/test_replay_snapshot.py`:

```python
"""Unit tests for history.service.replay_snapshot."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from wave_alpha.domain import Bar, Interval
from wave_alpha.history import service, store


class _FakeProvider:
    """Returns canned bars; mirrors the OHLCVProvider.fetch interface."""

    def __init__(self, bars_by_key: dict[tuple[str, Interval], list[Bar]]):
        self._bars = bars_by_key

    def fetch(self, ticker: str, interval: Interval, *, end: date) -> list[Bar]:
        bars = self._bars.get((ticker, interval), [])
        return [b for b in bars if b.timestamp <= end]


def _make_bars(start: date, n: int, interval: Interval, base: float = 100.0) -> list[Bar]:
    out: list[Bar] = []
    for i in range(n):
        price = Decimal(str(base + i * 0.5))
        out.append(
            Bar(
                timestamp=start + timedelta(days=i if interval == Interval.DAILY else i * 7),
                interval=interval,
                open=price,
                high=price + Decimal("1"),
                low=price - Decimal("1"),
                close=price,
                volume=1_000_000,
            )
        )
    return out


def test_replay_snapshot_writes_row(tmp_path: Path) -> None:
    db_path = tmp_path / "history.sqlite"
    as_of = date(2024, 6, 30)
    daily = _make_bars(date(2022, 1, 1), 700, Interval.DAILY)
    weekly = _make_bars(date(2022, 1, 1), 100, Interval.WEEKLY)
    h4 = _make_bars(date(2022, 1, 1), 700, Interval.HOURLY_4)
    provider = _FakeProvider(
        {
            ("AAPL", Interval.DAILY): daily,
            ("AAPL", Interval.WEEKLY): weekly,
            ("AAPL", Interval.HOURLY_4): h4,
        }
    )

    written = service.replay_snapshot(
        ticker="AAPL", as_of=as_of, provider=provider, db_path=db_path
    )

    assert written is True
    rows = store.recent("AAPL", n=10, db_path=db_path)
    assert len(rows) == 1
    assert rows[0].as_of == as_of


def test_replay_snapshot_does_not_prune_old_siblings(tmp_path: Path) -> None:
    """auto-prune is bypassed: replaying at a recent as_of must not delete
    older snapshots for the same ticker (backfill safety)."""
    db_path = tmp_path / "history.sqlite"
    # Seed an ancient row directly via the existing resolver test helper pattern.
    from datetime import UTC, datetime

    from wave_alpha.history.models import Snapshot, SnapshotCount, SnapshotPivot

    ancient = Snapshot(
        ticker="AAPL",
        as_of=date(2015, 12, 31),
        engine_version="0.1.0",
        top_3=[
            SnapshotCount(
                candidate_id="C1",
                pattern="impulse",
                degree=1,
                last_pivot_label="3",
                pivots=[SnapshotPivot(timestamp=date(2015, 12, 31), price=Decimal("50"))],
                engine_score=0.5,
                llm_prob=None,
                final_score=0.5,
            )
        ],
        signal=None,
        coherence=None,
        top_count_hash="h0",
        created_at=datetime(2015, 12, 31, tzinfo=UTC),
    )
    store.upsert(ancient, db_path=db_path)
    assert len(store.recent("AAPL", n=10, db_path=db_path)) == 1

    # Replay at a much-newer as_of. record_snapshot would prune the ancient row
    # at default retention=730d; replay_snapshot must NOT.
    daily = _make_bars(date(2022, 1, 1), 700, Interval.DAILY)
    weekly = _make_bars(date(2022, 1, 1), 100, Interval.WEEKLY)
    h4 = _make_bars(date(2022, 1, 1), 700, Interval.HOURLY_4)
    provider = _FakeProvider(
        {
            ("AAPL", Interval.DAILY): daily,
            ("AAPL", Interval.WEEKLY): weekly,
            ("AAPL", Interval.HOURLY_4): h4,
        }
    )

    service.replay_snapshot(
        ticker="AAPL", as_of=date(2024, 6, 30), provider=provider, db_path=db_path
    )

    rows = store.recent("AAPL", n=10, db_path=db_path)
    as_ofs = {r.as_of for r in rows}
    assert date(2015, 12, 31) in as_ofs, "ancient row was pruned (auto-prune leaked into replay)"


def test_replay_snapshot_returns_false_when_no_ranked_counts(tmp_path: Path) -> None:
    """If run_analysis produces no ranked_daily, no row is written and we
    return False — caller can count skips."""
    db_path = tmp_path / "history.sqlite"
    provider = _FakeProvider({})  # no bars for anything → empty AnalyzeResult
    written = service.replay_snapshot(
        ticker="ZZZZ", as_of=date(2024, 6, 30), provider=provider, db_path=db_path
    )
    assert written is False
    assert store.recent("ZZZZ", n=10, db_path=db_path) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/history/test_replay_snapshot.py -v`
Expected: FAIL — `AttributeError: module 'wave_alpha.history.service' has no attribute 'replay_snapshot'`

- [ ] **Step 3: Implement `replay_snapshot` in `src/wave_alpha/history/service.py`**

Add immediately after `record_snapshot` (around line 209). Mirror its imports — `pipeline.run_analysis`, `AnalyzeRequest`, `LLMMode` are not yet imported in service.py.

Add to existing imports near top of file (alongside `from wave_alpha.history import resolver, store`):

```python
from wave_alpha.llm.modes import LLMMode
from wave_alpha.pipeline import AnalyzeRequest, run_analysis
```

Add the new function after `record_snapshot`:

```python
def replay_snapshot(
    *,
    ticker: str,
    as_of: date,
    provider,
    db_path: Path,
) -> bool:
    """Re-run the analysis pipeline at (ticker, as_of) and upsert the snapshot.

    Used by `wave-alpha history backfill-signals` and `wave-alpha history
    densify` to (re)generate snapshots under the current signal schema —
    notably populating `signal.max_holding_bars` so the resolver can act on
    them. LLM is forced to DISABLED so bootstrap data is deterministic.

    Unlike `record_snapshot`, this DOES NOT run the auto-prune pass — backfill
    of old as_of values must not delete legitimate sibling rows.

    Returns True if a row was upserted, False if the pipeline produced no
    ranked candidates (e.g. cache miss, insufficient bars).
    """
    req = AnalyzeRequest(ticker=ticker, as_of=as_of)
    try:
        result = run_analysis(req, provider=provider, llm_mode=LLMMode.DISABLED)
    except Exception as exc:
        logger.warning("replay_snapshot pipeline failed for %s @ %s: %s", ticker, as_of, exc)
        return False

    if not result.ranked_daily:
        return False

    try:
        snapshot = _build_snapshot(result, ticker, as_of)
        store.upsert(snapshot, db_path=db_path)
        if result.daily_bars:
            resolver.resolve_pending_for(ticker, result.daily_bars, db_path=db_path, today=as_of)
    except Exception as exc:
        logger.warning("replay_snapshot upsert/resolve failed for %s @ %s: %s", ticker, as_of, exc)
        return False

    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/history/test_replay_snapshot.py -v`
Expected: PASS for all three tests.

- [ ] **Step 5: Lint + format**

Run: `uv run ruff check src/wave_alpha/history/service.py tests/history/test_replay_snapshot.py && uv run ruff format src/wave_alpha/history/service.py tests/history/test_replay_snapshot.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/history/service.py tests/history/test_replay_snapshot.py
git commit -m "feat(history): add replay_snapshot helper for backfill/densify

Re-runs run_analysis at a given (ticker, as_of) against the supplied
provider, upserts the resulting snapshot, and runs the resolver — but
deliberately bypasses record_snapshot's auto-prune so backfill of old
as_of dates does not delete sibling rows."
```

---

## Task 2: `wave-alpha history backfill-signals` command

**Files:**
- Modify: `src/wave_alpha/cli/history_app.py` (append new command)
- Create: `tests/cli/test_history_backfill_signals.py`

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_history_backfill_signals.py`:

```python
"""Tests for the `wave-alpha history backfill-signals` command."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wave_alpha.cli.app import app
from wave_alpha.cli import history_app as history_cli
from wave_alpha.domain import Bar, Interval
from wave_alpha.history import store
from wave_alpha.history.models import (
    Snapshot,
    SnapshotCount,
    SnapshotPivot,
    SnapshotSignal,
)


class _FakeProvider:
    def __init__(self, bars_by_key):
        self._bars = bars_by_key

    def fetch(self, ticker, interval, *, end):
        bars = self._bars.get((ticker, interval), [])
        return [b for b in bars if b.timestamp <= end]


def _make_bars(start, n, interval):
    out = []
    for i in range(n):
        price = Decimal(str(100 + i * 0.5))
        step = timedelta(days=i if interval == Interval.DAILY else i * 7)
        out.append(
            Bar(
                timestamp=start + step,
                interval=interval,
                open=price,
                high=price + Decimal("1"),
                low=price - Decimal("1"),
                close=price,
                volume=1_000_000,
            )
        )
    return out


def _seed_legacy_snapshot(db_path: Path, ticker: str, as_of: date) -> None:
    """Seed a v1-style snapshot WITHOUT max_holding_bars — the unresolvable case."""
    snap = Snapshot(
        ticker=ticker,
        as_of=as_of,
        engine_version="0.7.0",
        top_3=[
            SnapshotCount(
                candidate_id="C1",
                pattern="impulse",
                degree=1,
                last_pivot_label="3",
                pivots=[SnapshotPivot(timestamp=as_of, price=Decimal("100"))],
                engine_score=0.7,
                llm_prob=None,
                final_score=0.7,
            )
        ],
        signal=SnapshotSignal(
            direction="long",
            entry_price=Decimal("100"),
            stop_price=Decimal("95"),
            target_price=Decimal("110"),
            rr=2.0,
            count_id="C1",
            count_pattern="impulse",
            wave_label="3",
            degree=1,
            confidence=0.7,
            expected_bars_to_target=20,
            max_holding_bars=None,  # the bug we're backfilling
        ),
        coherence=None,
        top_count_hash="h_legacy",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    store.upsert(snap, db_path=db_path)


@pytest.fixture
def fake_provider():
    daily = _make_bars(date(2022, 1, 1), 700, Interval.DAILY)
    weekly = _make_bars(date(2022, 1, 1), 100, Interval.WEEKLY)
    h4 = _make_bars(date(2022, 1, 1), 700, Interval.HOURLY_4)
    return _FakeProvider(
        {
            ("AAPL", Interval.DAILY): daily,
            ("AAPL", Interval.WEEKLY): weekly,
            ("AAPL", Interval.HOURLY_4): h4,
            ("MSFT", Interval.DAILY): daily,
            ("MSFT", Interval.WEEKLY): weekly,
            ("MSFT", Interval.HOURLY_4): h4,
        }
    )


def test_backfill_signals_replays_unresolvable_rows(
    tmp_path, monkeypatch, fake_provider
) -> None:
    db_path = tmp_path / "history.sqlite"
    _seed_legacy_snapshot(db_path, "AAPL", date(2024, 6, 30))

    monkeypatch.setattr(history_cli, "_history_db_path", lambda: db_path)
    monkeypatch.setattr(history_cli, "_default_provider_for_backfill", lambda: fake_provider)

    runner = CliRunner()
    result = runner.invoke(app, ["history", "backfill-signals", "--ticker", "AAPL"])
    assert result.exit_code == 0, result.output
    assert "AAPL" in result.output

    # The row was upserted with the new schema → max_holding_bars now populated.
    rows = store.recent("AAPL", n=10, db_path=db_path)
    assert len(rows) == 1
    snap = Snapshot.model_validate_json(rows[0].payload_json)
    assert snap.signal is not None
    assert snap.signal.max_holding_bars is not None


def test_backfill_signals_all_iterates_every_ticker(
    tmp_path, monkeypatch, fake_provider
) -> None:
    db_path = tmp_path / "history.sqlite"
    _seed_legacy_snapshot(db_path, "AAPL", date(2024, 6, 30))
    _seed_legacy_snapshot(db_path, "MSFT", date(2024, 6, 30))

    monkeypatch.setattr(history_cli, "_history_db_path", lambda: db_path)
    monkeypatch.setattr(history_cli, "_default_provider_for_backfill", lambda: fake_provider)

    runner = CliRunner()
    result = runner.invoke(app, ["history", "backfill-signals", "--all", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    tickers_seen = {t["ticker"] for t in payload["tickers"]}
    assert tickers_seen == {"AAPL", "MSFT"}
    assert all(t["replayed"] >= 1 for t in payload["tickers"])


def test_backfill_signals_dry_run_does_not_write(
    tmp_path, monkeypatch, fake_provider
) -> None:
    db_path = tmp_path / "history.sqlite"
    _seed_legacy_snapshot(db_path, "AAPL", date(2024, 6, 30))
    original = Snapshot.model_validate_json(
        store.recent("AAPL", n=1, db_path=db_path)[0].payload_json
    )

    monkeypatch.setattr(history_cli, "_history_db_path", lambda: db_path)
    monkeypatch.setattr(history_cli, "_default_provider_for_backfill", lambda: fake_provider)

    runner = CliRunner()
    result = runner.invoke(
        app, ["history", "backfill-signals", "--ticker", "AAPL", "--dry-run"]
    )
    assert result.exit_code == 0
    after = Snapshot.model_validate_json(
        store.recent("AAPL", n=1, db_path=db_path)[0].payload_json
    )
    assert after.signal == original.signal  # unchanged
    assert after.signal.max_holding_bars is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_history_backfill_signals.py -v`
Expected: FAIL — command does not exist (`No such command 'backfill-signals'`).

- [ ] **Step 3: Implement the command**

Append to `src/wave_alpha/cli/history_app.py`:

```python
def _default_provider_for_backfill():
    """Indirected for tests. In production: CachedProvider over Yahoo, no
    network at the daily-resolution end since we only re-read cached bars
    for as-of dates already in cache.sqlite."""
    from wave_alpha.cli.app import _default_provider

    return _default_provider()


@history_app.command("backfill-signals")
def backfill_signals(
    ticker: str | None = typer.Option(None, "--ticker", "-t", help="One ticker to backfill."),
    all_tickers: bool = typer.Option(
        False, "--all", help="Backfill every ticker present in history.sqlite."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print plan; do not write."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON summary."),
) -> None:
    """Replay run_analysis at every existing (ticker, as_of) and upsert the
    snapshot under the current schema. The historical use case is upgrading
    pre-`max_holding_bars` rows so the outcome resolver can act on them."""
    if not ticker and not all_tickers:
        typer.echo("Specify --ticker T or --all", err=True)
        raise typer.Exit(code=1)

    from wave_alpha.history import service as history_service
    from wave_alpha.history import store as history_store

    db_path = _history_db_path()
    if not db_path.exists():
        typer.echo("history.sqlite does not exist yet; nothing to backfill.")
        raise typer.Exit(code=0)

    if all_tickers:
        all_rows = history_store.recent_across_all(db_path=db_path, limit=10_000)
        tickers = sorted({r.ticker for r in all_rows})
    else:
        tickers = [ticker.upper()]

    provider = _default_provider_for_backfill()
    summaries = []
    for t in tickers:
        rows = history_store.recent(t, n=10_000, db_path=db_path)
        as_ofs = sorted({r.as_of for r in rows})
        replayed = 0
        skipped = 0
        if not dry_run:
            for as_of in as_ofs:
                ok = history_service.replay_snapshot(
                    ticker=t, as_of=as_of, provider=provider, db_path=db_path
                )
                if ok:
                    replayed += 1
                else:
                    skipped += 1
        summaries.append(
            {
                "ticker": t,
                "candidates": len(as_ofs),
                "replayed": replayed,
                "skipped": skipped,
            }
        )

    if json_output:
        typer.echo(_json.dumps({"dry_run": dry_run, "tickers": summaries}))
    else:
        for s in summaries:
            typer.echo(
                f"{s['ticker']}: {s['replayed']}/{s['candidates']} replayed"
                f" ({s['skipped']} skipped){' [dry-run]' if dry_run else ''}"
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cli/test_history_backfill_signals.py -v`
Expected: PASS for all three tests.

- [ ] **Step 5: Smoke-check the existing history-app tests still pass**

Run: `uv run pytest tests/cli/test_history_app.py -v`
Expected: PASS.

- [ ] **Step 6: Lint + format**

Run: `uv run ruff check src/wave_alpha/cli/history_app.py tests/cli/test_history_backfill_signals.py && uv run ruff format src/wave_alpha/cli/history_app.py tests/cli/test_history_backfill_signals.py`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/wave_alpha/cli/history_app.py tests/cli/test_history_backfill_signals.py
git commit -m "feat(cli): wave-alpha history backfill-signals

Replays run_analysis at every existing (ticker, as_of) and upserts the
snapshot under the current schema. Concretely, this upgrades pre-
max_holding_bars rows so the outcome resolver can act on them. LLM is
forced disabled and auto-prune is bypassed via replay_snapshot."
```

---

## Task 3: `wave-alpha history densify` command

**Files:**
- Modify: `src/wave_alpha/cli/history_app.py` (append new command + helpers)
- Create: `tests/cli/test_history_densify.py`

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_history_densify.py`:

```python
"""Tests for the `wave-alpha history densify` command."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wave_alpha.cli.app import app
from wave_alpha.cli import history_app as history_cli
from wave_alpha.domain import Bar, Interval
from wave_alpha.history import store


class _FakeProvider:
    def __init__(self, bars_by_key):
        self._bars = bars_by_key

    def fetch(self, ticker, interval, *, end):
        bars = self._bars.get((ticker, interval), [])
        return [b for b in bars if b.timestamp <= end]


def _make_bars(start, n, interval):
    out = []
    for i in range(n):
        price = Decimal(str(100 + i * 0.5))
        step = timedelta(days=i if interval == Interval.DAILY else i * 7)
        out.append(
            Bar(
                timestamp=start + step,
                interval=interval,
                open=price,
                high=price + Decimal("1"),
                low=price - Decimal("1"),
                close=price,
                volume=1_000_000,
            )
        )
    return out


def test_quarterly_dates_endpoints_inclusive() -> None:
    """Pure unit: 2018-Q1 through 2018-Q4 → 4 quarter-end dates."""
    out = history_cli._quarterly_dates(date(2018, 1, 1), date(2018, 12, 31))
    assert out == [date(2018, 3, 31), date(2018, 6, 30), date(2018, 9, 30), date(2018, 12, 31)]


def test_densify_dry_run_prints_plan(tmp_path, monkeypatch) -> None:
    universe = tmp_path / "u.txt"
    universe.write_text("AAPL\nMSFT\n")
    monkeypatch.setattr(history_cli, "_history_db_path", lambda: tmp_path / "history.sqlite")
    monkeypatch.setattr(
        history_cli, "_default_provider_for_backfill", lambda: _FakeProvider({})
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "history",
            "densify",
            "--universe",
            str(universe),
            "--start",
            "2018-01-01",
            "--end",
            "2018-12-31",
            "--cadence",
            "quarterly",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    # 2 tickers × 4 quarters = 8 attempts
    assert "8" in result.output
    # Nothing written
    assert not (tmp_path / "history.sqlite").exists()


def test_densify_writes_and_skips_missing_bars(tmp_path, monkeypatch) -> None:
    universe = tmp_path / "u.txt"
    universe.write_text("AAPL\nGHOST\n")
    db_path = tmp_path / "history.sqlite"

    daily = _make_bars(date(2017, 1, 1), 800, Interval.DAILY)
    weekly = _make_bars(date(2017, 1, 1), 120, Interval.WEEKLY)
    h4 = _make_bars(date(2017, 1, 1), 800, Interval.HOURLY_4)
    provider = _FakeProvider(
        {
            ("AAPL", Interval.DAILY): daily,
            ("AAPL", Interval.WEEKLY): weekly,
            ("AAPL", Interval.HOURLY_4): h4,
            # GHOST has no bars at any interval → silent skip
        }
    )

    monkeypatch.setattr(history_cli, "_history_db_path", lambda: db_path)
    monkeypatch.setattr(history_cli, "_default_provider_for_backfill", lambda: provider)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "history",
            "densify",
            "--universe",
            str(universe),
            "--start",
            "2018-01-01",
            "--end",
            "2018-12-31",
            "--cadence",
            "quarterly",
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    by_ticker = {t["ticker"]: t for t in payload["tickers"]}
    assert by_ticker["AAPL"]["written"] >= 1
    assert by_ticker["GHOST"]["written"] == 0
    assert by_ticker["GHOST"]["skipped"] == 4

    # AAPL rows actually present in DB
    rows = store.recent("AAPL", n=10, db_path=db_path)
    assert len(rows) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/cli/test_history_densify.py -v`
Expected: FAIL — `_quarterly_dates` does not exist; `densify` command does not exist.

- [ ] **Step 3: Implement quarterly date enumeration + command**

Append to `src/wave_alpha/cli/history_app.py`:

```python
def _quarterly_dates(start: date, end: date) -> list[date]:
    """Return quarter-end dates (Mar-31, Jun-30, Sep-30, Dec-31) within [start, end]."""
    quarter_ends_per_year = [(3, 31), (6, 30), (9, 30), (12, 31)]
    out: list[date] = []
    for year in range(start.year, end.year + 1):
        for month, day in quarter_ends_per_year:
            d = date(year, month, day)
            if start <= d <= end:
                out.append(d)
    return out


def _read_universe(path: Path) -> list[str]:
    """Read newline-delimited tickers; skip blanks and `#` comments. Uppercase."""
    tickers: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tickers.append(line.upper())
    return tickers


@history_app.command("densify")
def densify(
    universe: Path = typer.Option(..., "--universe", help="Newline-delimited ticker file."),
    start: str = typer.Option(..., "--start", help="ISO start date inclusive."),
    end: str = typer.Option(..., "--end", help="ISO end date inclusive."),
    cadence: str = typer.Option(
        "quarterly", "--cadence", help="Currently supports `quarterly` only."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan; do not write."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON summary."),
) -> None:
    """Generate snapshots over (universe × cadence) using cached bars. LLM is
    forced disabled. Tickers missing bars at the requested as_of are silently
    skipped (universe drift is normal — e.g. COIN didn't exist in 2018)."""
    if cadence != "quarterly":
        typer.echo(f"Only --cadence quarterly is supported (got {cadence!r}).", err=True)
        raise typer.Exit(code=2)

    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    if end_d < start_d:
        typer.echo("--end must be >= --start", err=True)
        raise typer.Exit(code=2)

    tickers = _read_universe(universe)
    as_ofs = _quarterly_dates(start_d, end_d)
    total_attempts = len(tickers) * len(as_ofs)

    if dry_run:
        typer.echo(
            f"Plan: {len(tickers)} tickers × {len(as_ofs)} dates = {total_attempts} attempts"
        )
        typer.echo(f"  date range: {as_ofs[0] if as_ofs else '-'} → {as_ofs[-1] if as_ofs else '-'}")
        return

    from wave_alpha.history import service as history_service

    db_path = _history_db_path()
    provider = _default_provider_for_backfill()
    summaries = []
    for t in tickers:
        written = 0
        skipped = 0
        for as_of in as_ofs:
            ok = history_service.replay_snapshot(
                ticker=t, as_of=as_of, provider=provider, db_path=db_path
            )
            if ok:
                written += 1
            else:
                skipped += 1
        summaries.append({"ticker": t, "written": written, "skipped": skipped})

    if json_output:
        typer.echo(
            _json.dumps(
                {
                    "tickers": summaries,
                    "total_attempts": total_attempts,
                    "total_written": sum(s["written"] for s in summaries),
                }
            )
        )
    else:
        total_written = sum(s["written"] for s in summaries)
        typer.echo(f"densify: {total_written}/{total_attempts} snapshots written")
        for s in summaries:
            typer.echo(f"  {s['ticker']:>6}  written={s['written']}  skipped={s['skipped']}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/cli/test_history_densify.py -v`
Expected: PASS.

- [ ] **Step 5: Run full history + cli test suites**

Run: `uv run pytest tests/history tests/cli -v`
Expected: PASS.

- [ ] **Step 6: Lint + format**

Run: `uv run ruff check . && uv run ruff format src/wave_alpha/cli/history_app.py tests/cli/test_history_densify.py`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add src/wave_alpha/cli/history_app.py tests/cli/test_history_densify.py
git commit -m "feat(cli): wave-alpha history densify

Generates snapshots over (universe × quarterly cadence) using cached
bars. LLM forced disabled, auto-prune bypassed. Silently skips tickers
without cached bars at the requested as_of (universe drift is normal)."
```

---

## Task 4: Operational dry-run + plan doc commit

This task does NOT execute the real backfill — it commits the plan document and runs a small dry-run to validate the CLI surface end-to-end. The actual backfill is an out-of-band overnight run after the PR merges.

**Files:**
- Add: this plan document (already created at `docs/superpowers/plans/2026-05-11-outcome-data-bootstrap.md`).

- [ ] **Step 1: Verify the dry-run path works against the real DB**

Run: `uv run wave-alpha history backfill-signals --all --dry-run --json`
Expected: JSON listing the 9-12 tickers currently in `~/.wave_alpha/history.sqlite` with `replayed: 0` and `skipped: 0` (dry-run does no work).

- [ ] **Step 2: Verify the densify dry-run works**

Run: `uv run wave-alpha history densify --universe data/universe_curated.txt --start 2018-01-01 --end 2024-12-31 --cadence quarterly --dry-run`
Expected: a single line of the form `Plan: 50 tickers × 28 dates = 1400 attempts`.

- [ ] **Step 3: Commit the plan document**

```bash
git add docs/superpowers/plans/2026-05-11-outcome-data-bootstrap.md
git commit -m "docs(plans): outcome-data bootstrap plan

Captures decisions (quarterly cadence, curated universe, silent-skip
for missing bars, replay_snapshot bypasses auto-prune) and the staged
follow-up work after PR merge: backfill, densify, train, evaluate."
```

---

## Out of scope for this PR (post-merge follow-ups)

These are operational, not code changes — track separately rather than blocking the PR:

1. **Run `wave-alpha history backfill-signals --all`** against the real DB. Expected outcome: 60-70 of 70 rows replayed; ~50 of those become `target`/`stop`/`time_stop` once the resolver picks them up.
2. **Run `wave-alpha history densify --universe data/universe_curated.txt --start 2018-01-01 --end 2024-12-31 --cadence quarterly`** as an overnight job. Expected wall-clock: ~30-90 min depending on cache warmth. Expected yield: ~1000-1300 resolved snapshots.
3. **Inspect outcome distribution** via `wave-alpha history stats --json` and confirm per-degree counts approach `n_target_per_degree=100`.
4. **Train + promote**: `uv run wave-alpha train right-edge --use-outcomes --outcome-weight-target 100 --write` followed by `evaluate_promotion`. Document the verdict (PROMOTE / PROMOTE_CALIBRATION / HOLD) regardless of outcome — the bootstrap is *information*, not necessarily a model release.

---

## Self-review notes

- **Spec coverage:** every requirement from the scoping turn maps to a task — `replay_snapshot` (T1), `backfill-signals` (T2), `densify` (T3), plan doc + dry-run validation (T4). Decision points 1-4 from the scope are baked in as defaults at the top.
- **Placeholder scan:** every code block is complete; no TODO/TBD; no "similar to Task N" — code repeated where needed (test fixtures appear in both T2 and T3 because subagents may execute tasks out of order).
- **Type/name consistency:** `replay_snapshot(*, ticker, as_of, provider, db_path) -> bool` is the single signature called from both CLI commands. `_history_db_path` and `_default_provider_for_backfill` are the two indirections used for monkeypatching in all CLI tests.
- **Footgun handled:** auto-prune bypass is enforced inside `replay_snapshot` and explicitly tested (`test_replay_snapshot_does_not_prune_old_siblings`). No need for a CLI flag.
