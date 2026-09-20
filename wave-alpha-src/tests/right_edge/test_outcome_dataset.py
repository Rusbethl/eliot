from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from wave_alpha.domain import Bar, Interval
from wave_alpha.history import store
from wave_alpha.history.models import (
    Snapshot,
    SnapshotCoherence,
    SnapshotCount,
    SnapshotOutcome,
    SnapshotPivot,
    SnapshotSignal,
)
from wave_alpha.right_edge.outcome_dataset import (
    OutcomeDataset,
    WeightSchedule,
    build_outcome_dataset,
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
        out.append(
            Bar(
                timestamp=start + timedelta(days=i),
                open=price,
                high=price + Decimal("1"),
                low=price - Decimal("1"),
                close=price,
                volume=1_000_000,
            )
        )
    return out


def _resolved_snap(ticker: str, as_of: date, *, status: str, count_id: str) -> Snapshot:
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
            count_id=count_id,
            count_pattern="impulse",
            wave_label="3",
            degree=1,
            confidence=0.6,
            expected_bars_to_target=10,
            max_holding_bars=20,
        ),
        coherence=SnapshotCoherence(score=0.7, multiplier=1.0, pairwise={}, fallback_used=None),
        top_count_hash="h",
        created_at=datetime(as_of.year, as_of.month, as_of.day, 16, 0, tzinfo=UTC),
        outcome=SnapshotOutcome(
            status=status,  # type: ignore[arg-type]
            exit_date=as_of + timedelta(days=10) if status != "pending" else None,
            exit_price=(
                Decimal("115")
                if status == "target"
                else Decimal("95")
                if status == "stop"
                else Decimal("100")
            ),
            bars_held=10 if status != "pending" else None,
            realized_R=(2.0 if status == "target" else -1.0 if status == "stop" else 0.0),
            last_observed_date=as_of + timedelta(days=10),
            bars_elapsed=10,
            unrealized_R=0.0,
            mfe_R=0.0,
            mae_R=0.0,
            resolved_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )


def test_build_outcome_dataset_filters_to_resolved_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "h.sqlite"
    # 1 target + 1 stop — only resolved rows should be considered
    store.upsert(
        _resolved_snap("AAPL", date(2025, 1, 1), status="target", count_id="C1"), db_path=db_path
    )
    store.upsert(
        _resolved_snap("AAPL", date(2025, 2, 1), status="stop", count_id="C2"), db_path=db_path
    )

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
    # Two resolved rows; total accounted for across rows + skip buckets.
    assert len(ds.rows) + ds.skipped_drift + ds.skipped_no_bars == 2


def test_build_outcome_dataset_skips_no_bars(tmp_path: Path) -> None:
    db_path = tmp_path / "h.sqlite"
    store.upsert(
        _resolved_snap("AAPL", date(2025, 1, 1), status="target", count_id="C1"), db_path=db_path
    )
    provider = _FakeProvider({"AAPL": []})  # NO bars

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
    store.upsert(
        _resolved_snap("AAPL", date(2025, 1, 1), status="time_stop", count_id="C1"), db_path=db_path
    )
    bars = _make_bars(date(2024, 1, 1), 450)
    provider = _FakeProvider({"AAPL": bars})

    ds = build_outcome_dataset(
        db_path=db_path,
        bar_provider=provider,
        degree=1,
        schedule=WeightSchedule(include_time_stops=False),
        n_synth_per_degree={1: 500},
    )
    # time_stop dropped before any feature work; not counted in either skip bucket.
    assert ds.rows == []
    assert ds.skipped_drift == 0
    assert ds.skipped_no_bars == 0


def test_build_outcome_dataset_empty_db_returns_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "h.sqlite"
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


def test_build_outcome_dataset_counts_wrong_degree_skips(tmp_path: Path) -> None:
    db_path = tmp_path / "h.sqlite"
    # Seed a degree-1 snapshot (the _resolved_snap helper makes degree=1 snapshots)
    # but request degree=2 from the builder — should count as skipped_wrong_degree.
    snap = _resolved_snap("AAPL", date(2025, 1, 1), status="target", count_id="C1")
    store.upsert(snap, db_path=db_path)
    bars = _make_bars(date(2024, 1, 1), 450)
    provider = _FakeProvider({"AAPL": bars})

    ds = build_outcome_dataset(
        db_path=db_path,
        bar_provider=provider,
        degree=2,  # mismatch with snapshot.signal.degree=1
        schedule=WeightSchedule(),
        n_synth_per_degree={2: 500},
    )
    assert ds.rows == []
    assert ds.skipped_wrong_degree == 1
    assert ds.skipped_drift == 0
    assert ds.skipped_no_bars == 0


def test_build_outcome_dataset_populates_exit_date(tmp_path: Path) -> None:
    """build_outcome_dataset must surface snapshot.outcome.exit_date on each
    constructed OutcomeRow. The temporal split logic in outcome_eval needs it."""
    from datetime import date as _date

    from wave_alpha.right_edge.outcome_dataset import WeightSchedule, build_outcome_dataset

    db_path = tmp_path / "h.sqlite"
    store.upsert(
        _resolved_snap("AAPL", date(2025, 1, 1), status="target", count_id="C1"), db_path=db_path
    )
    store.upsert(
        _resolved_snap("AAPL", date(2025, 2, 1), status="stop", count_id="C2"), db_path=db_path
    )
    bars = _make_bars(date(2024, 1, 1), 450)
    provider = _FakeProvider({"AAPL": bars})

    ds = build_outcome_dataset(
        db_path=db_path,
        bar_provider=provider,
        degree=1,
        schedule=WeightSchedule(),
        n_synth_per_degree={1: 50},
    )
    for r in ds.rows:
        assert isinstance(r.exit_date, _date)
