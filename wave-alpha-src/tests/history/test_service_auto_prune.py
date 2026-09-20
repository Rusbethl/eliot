import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from wave_alpha.history import service, store
from wave_alpha.history.models import (
    Snapshot,
    SnapshotCoherence,
    SnapshotCount,
    SnapshotPivot,
    SnapshotSignal,
)
from wave_alpha.prefs.models import HistoryConfig


def _snap(ticker: str, as_of: date, *, top_hash: str = "h") -> Snapshot:
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
        top_count_hash=top_hash,
        created_at=datetime(as_of.year, as_of.month, as_of.day, 16, 0, tzinfo=UTC),
    )


def test_record_snapshot_prunes_old_rows_for_same_ticker(tmp_path: Path) -> None:
    db_path = tmp_path / "h.sqlite"
    # Seed AAPL with old rows; 2024-01-02 is exactly 730 days before 2026-01-01
    # (2024 is a leap year: 366 + 364 = 730)
    for as_of in (date(2020, 1, 1), date(2021, 1, 1), date(2024, 1, 2)):
        store.upsert(_snap("AAPL", as_of, top_hash=f"h-{as_of}"), db_path=db_path)
    # Seed MSFT with one old row — must not be touched
    store.upsert(_snap("MSFT", date(2020, 1, 1)), db_path=db_path)

    today = date(2026, 1, 1)
    snap_today = _snap("AAPL", today, top_hash="h-today")
    # `record_snapshot` only reads `result.ranked_daily` and `result.daily_bars`
    # before delegating to `_build_snapshot`, which we patch — so a duck-typed
    # SimpleNamespace is sufficient as the stand-in.
    result = SimpleNamespace(ranked_daily=[object()], daily_bars=[])
    with (
        patch.object(service, "_build_snapshot", return_value=snap_today),
        patch.object(service.resolver, "resolve_pending_for", return_value=None),
        patch.object(
            service, "_load_history_config", return_value=HistoryConfig(retention_days=730)
        ),
    ):
        service.record_snapshot(result, "AAPL", today, db_path=db_path)

    aapl_rows = [r.as_of for r in store.recent("AAPL", 10, db_path=db_path)]
    # 2020 and 2021 are >730 days before 2026-01-01 → pruned.
    # 2024-01-02 is exactly 730 days before 2026-01-01 → retained (boundary is inclusive).
    # today is retained.
    assert date(2020, 1, 1) not in aapl_rows
    assert date(2021, 1, 1) not in aapl_rows
    assert date(2024, 1, 2) in aapl_rows
    assert today in aapl_rows
    # MSFT untouched
    msft_rows = [r.as_of for r in store.recent("MSFT", 10, db_path=db_path)]
    assert msft_rows == [date(2020, 1, 1)]


def test_record_snapshot_skips_prune_when_retention_is_zero(tmp_path: Path) -> None:
    db_path = tmp_path / "h.sqlite"
    store.upsert(_snap("AAPL", date(2010, 1, 1)), db_path=db_path)

    today = date(2026, 1, 1)
    snap_today = _snap("AAPL", today, top_hash="h-today")
    with (
        patch.object(service, "_build_snapshot", return_value=snap_today),
        patch.object(service.resolver, "resolve_pending_for", return_value=None),
        patch.object(service, "_load_history_config", return_value=HistoryConfig(retention_days=0)),
    ):
        result = SimpleNamespace(ranked_daily=[object()], daily_bars=[])
        service.record_snapshot(result, "AAPL", today, db_path=db_path)

    rows = [r.as_of for r in store.recent("AAPL", 10, db_path=db_path)]
    assert date(2010, 1, 1) in rows  # never pruned


def test_record_snapshot_swallows_prune_failures(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db_path = tmp_path / "h.sqlite"
    today = date(2026, 1, 1)
    snap_today = _snap("AAPL", today)
    with (
        caplog.at_level(logging.WARNING, logger="wave_alpha.history.service"),
        patch.object(service, "_build_snapshot", return_value=snap_today),
        patch.object(service.resolver, "resolve_pending_for", return_value=None),
        patch.object(
            service, "_load_history_config", return_value=HistoryConfig(retention_days=730)
        ),
        patch.object(store, "prune_older_than", side_effect=RuntimeError("boom")),
    ):
        result = SimpleNamespace(ranked_daily=[object()], daily_bars=[])
        service.record_snapshot(result, "AAPL", today, db_path=db_path)
    assert any("auto-prune" in rec.message.lower() for rec in caplog.records)
