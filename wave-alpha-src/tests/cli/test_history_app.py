from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wave_alpha.cli.app import app
from wave_alpha.history import store
from wave_alpha.history.models import (
    Snapshot,
    SnapshotCoherence,
    SnapshotCount,
    SnapshotPivot,
    SnapshotSignal,
)


def _snap(ticker: str, as_of: date) -> Snapshot:
    return Snapshot(
        ticker=ticker,
        as_of=as_of,
        engine_version="0.9.0",
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
            target_price=Decimal("115"),
            rr=3.0,
        ),
        coherence=SnapshotCoherence(score=0.7, multiplier=1.0, pairwise={}, fallback_used=None),
        top_count_hash=f"h-{ticker}-{as_of}",
        created_at=datetime(as_of.year, as_of.month, as_of.day, 16, 0, tzinfo=UTC),
    )


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
    monkeypatch.setattr("wave_alpha.cli.history_app._history_db_path", lambda: db_path)
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
    assert "would delete" in res.output.lower() or "dry-run" in res.output.lower()
    rows = list(store.recent("AAPL", 10, db_path=seeded_db))
    assert len(rows) == 2


def test_history_prune_deletes_older_rows(seeded_db: Path) -> None:
    runner = CliRunner()
    res = runner.invoke(app, ["history", "prune", "--older-than", "180d"])
    assert res.exit_code == 0
    # All three seeded rows (AAPL 2024-01-01, AAPL 2025-01-01, MSFT 2024-06-01)
    # are >180 days before any 2026+ run date, so all three must be deleted.
    msft_rows = [r.as_of for r in store.recent("MSFT", 10, db_path=seeded_db)]
    aapl_rows = [r.as_of for r in store.recent("AAPL", 10, db_path=seeded_db)]
    assert len(aapl_rows) == 0
    assert len(msft_rows) == 0
    assert "delete" in res.output.lower() or "prune" in res.output.lower()


def test_history_prune_ticker_scope(seeded_db: Path) -> None:
    runner = CliRunner()
    res = runner.invoke(app, ["history", "prune", "--older-than", "1d", "--ticker", "AAPL"])
    assert res.exit_code == 0
    msft_rows = [r.as_of for r in store.recent("MSFT", 10, db_path=seeded_db)]
    # MSFT must still be present even though its rows are older than 1d
    assert len(msft_rows) == 1
