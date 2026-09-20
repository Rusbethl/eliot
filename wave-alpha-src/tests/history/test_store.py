from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from wave_alpha.history import store
from wave_alpha.history.models import (
    Snapshot,
    SnapshotCoherence,
    SnapshotCount,
    SnapshotPivot,
    SnapshotSignal,
)


def _snap(
    ticker: str = "AAPL",
    as_of: date = date(2024, 12, 31),
    *,
    top_hash: str = "h-default",
    created_at: datetime | None = None,
    engine_score: float = 0.7,
) -> Snapshot:
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
                engine_score=engine_score,
                llm_prob=None,
                final_score=engine_score,
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
        created_at=created_at or datetime(2024, 12, 31, 16, 0, 0),
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "history.sqlite"


def test_upsert_then_recent_round_trips(db_path: Path) -> None:
    snap = _snap()
    store.upsert(snap, db_path=db_path)
    rows = store.recent("AAPL", 10, db_path=db_path)
    assert len(rows) == 1
    assert rows[0].ticker == "AAPL"
    assert rows[0].as_of == date(2024, 12, 31)
    assert rows[0].top_count_hash == "h-default"
    # payload round-trip via Snapshot.model_validate_json works
    restored = Snapshot.model_validate_json(rows[0].payload_json)
    assert restored == snap


def test_upsert_overwrites_same_key(db_path: Path) -> None:
    first = _snap(engine_score=0.6, created_at=datetime(2024, 12, 31, 12, 0, 0))
    second = _snap(engine_score=0.9, created_at=datetime(2024, 12, 31, 18, 0, 0))
    store.upsert(first, db_path=db_path)
    store.upsert(second, db_path=db_path)
    rows = store.recent("AAPL", 10, db_path=db_path)
    assert len(rows) == 1
    restored = Snapshot.model_validate_json(rows[0].payload_json)
    assert restored.top_3[0].final_score == pytest.approx(0.9)


def test_recent_returns_newest_first(db_path: Path) -> None:
    store.upsert(_snap(as_of=date(2024, 12, 1), top_hash="h-old"), db_path=db_path)
    store.upsert(_snap(as_of=date(2024, 12, 31), top_hash="h-new"), db_path=db_path)
    store.upsert(_snap(as_of=date(2024, 12, 15), top_hash="h-mid"), db_path=db_path)
    rows = store.recent("AAPL", 10, db_path=db_path)
    assert [r.as_of for r in rows] == [date(2024, 12, 31), date(2024, 12, 15), date(2024, 12, 1)]


def test_recent_filters_by_ticker(db_path: Path) -> None:
    store.upsert(_snap(ticker="AAPL"), db_path=db_path)
    store.upsert(_snap(ticker="MSFT"), db_path=db_path)
    rows = store.recent("AAPL", 10, db_path=db_path)
    assert len(rows) == 1
    assert rows[0].ticker == "AAPL"


def test_recent_respects_limit(db_path: Path) -> None:
    for i in range(1, 8):
        store.upsert(_snap(as_of=date(2024, 12, i), top_hash=f"h-{i}"), db_path=db_path)
    rows = store.recent("AAPL", 3, db_path=db_path)
    assert len(rows) == 3
    assert [r.as_of for r in rows] == [date(2024, 12, 7), date(2024, 12, 6), date(2024, 12, 5)]


def test_recent_empty_for_unknown_ticker(db_path: Path) -> None:
    store.upsert(_snap(ticker="AAPL"), db_path=db_path)
    rows = store.recent("NVDA", 10, db_path=db_path)
    assert rows == []


def test_upsert_creates_parent_dir(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "more" / "history.sqlite"
    store.upsert(_snap(), db_path=nested)
    assert nested.exists()


def test_recent_returns_empty_when_db_does_not_exist(tmp_path: Path) -> None:
    """Cold-start: recent() must not create the file just to read from it."""
    nonexistent = tmp_path / "never_written.sqlite"
    rows = store.recent("AAPL", 10, db_path=nonexistent)
    assert rows == []
    assert not nonexistent.exists()


def test_recent_across_all_returns_one_row_per_ticker_newest_first(db_path: Path) -> None:
    # AAPL has multiple snapshots; the newest should win and only that row appears.
    store.upsert(_snap(ticker="AAPL", as_of=date(2024, 12, 1), top_hash="a-old"), db_path=db_path)
    store.upsert(_snap(ticker="AAPL", as_of=date(2024, 12, 31), top_hash="a-new"), db_path=db_path)
    store.upsert(_snap(ticker="MSFT", as_of=date(2024, 12, 15), top_hash="m"), db_path=db_path)
    store.upsert(_snap(ticker="NVDA", as_of=date(2024, 12, 20), top_hash="n"), db_path=db_path)
    rows = store.recent_across_all(db_path=db_path, limit=10)
    assert [(r.ticker, r.as_of) for r in rows] == [
        ("AAPL", date(2024, 12, 31)),
        ("NVDA", date(2024, 12, 20)),
        ("MSFT", date(2024, 12, 15)),
    ]


def test_recent_across_all_respects_limit(db_path: Path) -> None:
    for i, t in enumerate(["AAPL", "MSFT", "NVDA", "GOOG", "TSLA"]):
        store.upsert(
            _snap(ticker=t, as_of=date(2024, 12, i + 1), top_hash=f"h-{t}"),
            db_path=db_path,
        )
    rows = store.recent_across_all(db_path=db_path, limit=2)
    assert len(rows) == 2


def test_recent_across_all_empty_when_db_does_not_exist(tmp_path: Path) -> None:
    nonexistent = tmp_path / "never_written.sqlite"
    rows = store.recent_across_all(db_path=nonexistent, limit=5)
    assert rows == []
    assert not nonexistent.exists()


def test_unresolved_for_returns_null_and_pending_rows(tmp_path):
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from wave_alpha.history.models import (
        Snapshot,
        SnapshotCount,
        SnapshotOutcome,
        SnapshotPivot,
    )
    from wave_alpha.history.store import _INITIALIZED, unresolved_for, upsert

    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)

    def _snap(day: int, outcome: SnapshotOutcome | None) -> Snapshot:
        return Snapshot(
            ticker="AAPL",
            as_of=date(2026, 1, day),
            engine_version="0.7.0",
            top_3=[
                SnapshotCount(
                    candidate_id="C1",
                    pattern="impulse",
                    degree=2,
                    last_pivot_label="3",
                    pivots=[SnapshotPivot(timestamp=date(2026, 1, day), price=Decimal("100"))],
                    engine_score=0.7,
                    llm_prob=None,
                    final_score=0.7,
                ),
            ],
            signal=None,
            coherence=None,
            top_count_hash=f"h{day}",
            created_at=datetime(2026, 1, day, tzinfo=UTC),
            outcome=outcome,
        )

    pending = SnapshotOutcome(
        status="pending",
        exit_date=None,
        exit_price=None,
        bars_held=None,
        realized_R=None,
        last_observed_date=date(2026, 1, 2),
        bars_elapsed=1,
        unrealized_R=0.0,
        mfe_R=0.0,
        mae_R=0.0,
        resolved_at=datetime(2026, 1, 3, tzinfo=UTC),
    )
    target = SnapshotOutcome(
        status="target",
        exit_date=date(2026, 1, 5),
        exit_price=Decimal("110"),
        bars_held=4,
        realized_R=2.0,
        last_observed_date=date(2026, 1, 5),
        bars_elapsed=4,
        unrealized_R=2.0,
        mfe_R=2.4,
        mae_R=-0.2,
        resolved_at=datetime(2026, 1, 6, tzinfo=UTC),
    )

    upsert(_snap(1, None), db_path=db)
    upsert(_snap(2, pending), db_path=db)
    upsert(_snap(3, target), db_path=db)

    rows = unresolved_for("AAPL", db_path=db)
    as_ofs = [r.as_of for r in rows]
    assert as_ofs == [date(2026, 1, 1), date(2026, 1, 2)]  # NULL + pending, asc
