from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from wave_alpha.history import service, store
from wave_alpha.history.models import (
    Snapshot,
    SnapshotCoherence,
    SnapshotCount,
    SnapshotPivot,
    SnapshotSignal,
)


def _snap(*, as_of: date, top_hash: str, ticker: str = "AAPL") -> Snapshot:
    return Snapshot(
        ticker=ticker,
        as_of=as_of,
        engine_version="0.5.11",
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
        created_at=datetime(as_of.year, as_of.month, as_of.day, 16, 0, 0),
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "history.sqlite"


def test_empty_history(db_path: Path) -> None:
    rh = service.get_recent_with_badge("AAPL", db_path=db_path)
    assert rh.snapshots == []
    assert rh.badge is None


def test_single_snapshot_no_badge(db_path: Path) -> None:
    store.upsert(_snap(as_of=date(2024, 12, 31), top_hash="h-A"), db_path=db_path)
    rh = service.get_recent_with_badge("AAPL", db_path=db_path)
    assert len(rh.snapshots) == 1
    assert rh.badge is None


def test_stable_top_pick_produces_full_streak_badge(db_path: Path) -> None:
    for i in range(5, 0, -1):
        store.upsert(_snap(as_of=date(2024, 12, i), top_hash="h-A"), db_path=db_path)
    rh = service.get_recent_with_badge("AAPL", db_path=db_path)
    assert len(rh.snapshots) == 5
    assert rh.snapshots[0].as_of == date(2024, 12, 5)
    assert rh.badge is not None
    assert rh.badge.streak == 5
    assert rh.badge.matches_in_window == 5
    assert rh.badge.last_changed_on is None


def test_default_window_is_14(db_path: Path) -> None:
    for i in range(20):
        store.upsert(
            _snap(as_of=date(2024, 1, 1).replace(day=i + 1), top_hash="h-A"), db_path=db_path
        )
    rh = service.get_recent_with_badge("AAPL", db_path=db_path)
    assert len(rh.snapshots) == 14
