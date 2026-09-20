from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

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
        created_at=datetime(as_of.year, as_of.month, as_of.day, 16, 0, 0),
    )


def test_prune_older_than_deletes_only_older_rows_for_ticker(tmp_path: Path) -> None:
    db_path = tmp_path / "h.sqlite"
    # AAPL: 3 rows spanning 2022-2024
    for as_of in (date(2022, 6, 1), date(2023, 6, 1), date(2024, 6, 1)):
        store.upsert(_snap("AAPL", as_of), db_path=db_path)
    # MSFT: 1 old row (should NOT be touched — per-ticker scope)
    store.upsert(_snap("MSFT", date(2022, 6, 1)), db_path=db_path)

    deleted = store.prune_older_than(db_path=db_path, ticker="AAPL", cutoff=date(2023, 12, 31))

    assert deleted == 2  # 2022 and 2023 rows
    remaining = [r.as_of for r in store.recent("AAPL", 10, db_path=db_path)]
    assert remaining == [date(2024, 6, 1)]
    msft_remaining = [r.as_of for r in store.recent("MSFT", 10, db_path=db_path)]
    assert msft_remaining == [date(2022, 6, 1)]  # untouched


def test_prune_older_than_returns_zero_when_no_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "h.sqlite"
    store.upsert(_snap("AAPL", date(2024, 6, 1)), db_path=db_path)
    deleted = store.prune_older_than(db_path=db_path, ticker="AAPL", cutoff=date(2020, 1, 1))
    assert deleted == 0


def test_prune_older_than_missing_db_returns_zero(tmp_path: Path) -> None:
    db_path = tmp_path / "missing.sqlite"
    deleted = store.prune_older_than(db_path=db_path, ticker="AAPL", cutoff=date(2024, 1, 1))
    assert deleted == 0


def test_prune_older_than_cutoff_row_is_retained(tmp_path: Path) -> None:
    """Cutoff is exclusive — a row whose as_of equals cutoff is retained.

    Locks in `<` (not `<=`) semantics so callers in Task 3 that compute
    `cutoff = today - timedelta(days=retention_days)` get predictable behavior.
    """
    db_path = tmp_path / "h.sqlite"
    cutoff = date(2023, 6, 1)
    store.upsert(_snap("AAPL", cutoff), db_path=db_path)  # on the boundary
    store.upsert(_snap("AAPL", date(2023, 5, 31)), db_path=db_path)  # one day before

    deleted = store.prune_older_than(db_path=db_path, ticker="AAPL", cutoff=cutoff)

    assert deleted == 1  # only 2023-05-31 removed
    remaining = [r.as_of for r in store.recent("AAPL", 10, db_path=db_path)]
    assert remaining == [cutoff]
