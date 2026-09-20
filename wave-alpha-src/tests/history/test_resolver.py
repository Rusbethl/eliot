"""End-to-end tests for history.resolver.resolve_pending_for."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from wave_alpha.domain import Bar, Interval
from wave_alpha.history.models import (
    Snapshot,
    SnapshotCount,
    SnapshotPivot,
    SnapshotSignal,
)
from wave_alpha.history.resolver import resolve_pending_for
from wave_alpha.history.store import _INITIALIZED, recent, upsert


def _bar(day: int, *, o: str, h: str, low: str, c: str) -> Bar:
    return Bar(
        timestamp=date(2026, 1, day),
        interval=Interval.DAILY,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=1_000_000,
    )


def _build_snapshot(*, as_of_day: int, with_signal: bool = True) -> Snapshot:
    signal = (
        SnapshotSignal(
            direction="long",
            entry_price=Decimal("100"),
            stop_price=Decimal("95"),
            target_price=Decimal("110"),
            rr=2.0,
            count_id="C1",
            count_pattern="impulse",
            wave_label="3",
            degree=2,
            confidence=0.7,
            expected_bars_to_target=20,
            max_holding_bars=60,
        )
        if with_signal
        else None
    )
    return Snapshot(
        ticker="AAPL",
        as_of=date(2026, 1, as_of_day),
        engine_version="0.7.0",
        top_3=[
            SnapshotCount(
                candidate_id="C1",
                pattern="impulse",
                degree=2,
                last_pivot_label="3",
                pivots=[SnapshotPivot(timestamp=date(2026, 1, as_of_day), price=Decimal("100"))],
                engine_score=0.7,
                llm_prob=None,
                final_score=0.7,
            ),
        ],
        signal=signal,
        coherence=None,
        top_count_hash=f"hash-{as_of_day}",
        created_at=datetime(2026, 1, as_of_day, tzinfo=UTC),
    )


def test_resolve_pending_for_marks_resolved_rows_with_target(tmp_path):
    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)
    upsert(_build_snapshot(as_of_day=1), db_path=db)
    bars = [
        _bar(2, o="101", h="105", low="99", c="103"),
        _bar(3, o="103", h="112", low="102", c="111"),  # target hit
    ]
    updated = resolve_pending_for("AAPL", bars, db_path=db, today=date(2026, 1, 3))
    assert updated == 1
    rows = recent("AAPL", n=10, db_path=db)
    assert len(rows) == 1
    snap = Snapshot.model_validate_json(rows[0].payload_json)
    assert snap.outcome is not None
    assert snap.outcome.status == "target"
    assert snap.outcome.realized_R == 2.0


def test_resolve_pending_for_skips_signal_none(tmp_path):
    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)
    upsert(_build_snapshot(as_of_day=1, with_signal=False), db_path=db)
    bars = [_bar(2, o="101", h="105", low="99", c="103")]
    updated = resolve_pending_for("AAPL", bars, db_path=db, today=date(2026, 1, 2))
    assert updated == 0
    rows = recent("AAPL", n=10, db_path=db)
    snap = Snapshot.model_validate_json(rows[0].payload_json)
    assert snap.outcome is None


def test_resolve_pending_for_skips_v1_signals_missing_max_holding(tmp_path):
    """A snapshot whose SnapshotSignal lacks max_holding_bars (v1 row) is
    skipped — outcome stays None."""
    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)
    snap = _build_snapshot(as_of_day=1)
    # Strip the new fields to simulate a v1-shaped signal.
    v1_signal = SnapshotSignal(
        direction="long",
        entry_price=Decimal("100"),
        stop_price=Decimal("95"),
        target_price=Decimal("110"),
        rr=2.0,
    )
    v1_snap = snap.model_copy(update={"signal": v1_signal})
    upsert(v1_snap, db_path=db)
    bars = [
        _bar(2, o="103", h="112", low="102", c="111"),
    ]
    updated = resolve_pending_for("AAPL", bars, db_path=db, today=date(2026, 1, 2))
    assert updated == 0
    rows = recent("AAPL", n=10, db_path=db)
    snap_after = Snapshot.model_validate_json(rows[0].payload_json)
    assert snap_after.outcome is None


def test_resolve_pending_for_writes_pending_state_when_in_window(tmp_path):
    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)
    upsert(_build_snapshot(as_of_day=1), db_path=db)
    bars = [
        _bar(2, o="101", h="105", low="99", c="103"),
        _bar(3, o="103", h="106", low="102", c="105"),
    ]
    updated = resolve_pending_for("AAPL", bars, db_path=db, today=date(2026, 1, 3))
    assert updated == 1
    rows = recent("AAPL", n=10, db_path=db)
    snap = Snapshot.model_validate_json(rows[0].payload_json)
    assert snap.outcome is not None
    assert snap.outcome.status == "pending"
    assert snap.outcome.bars_elapsed == 2
    assert snap.outcome.unrealized_R == 1.0  # (105 - 100) / 5


def test_resolve_pending_for_only_touches_target_ticker(tmp_path):
    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)
    upsert(_build_snapshot(as_of_day=1), db_path=db)
    msft = _build_snapshot(as_of_day=1).model_copy(update={"ticker": "MSFT"})
    upsert(msft, db_path=db)
    bars = [
        _bar(2, o="103", h="112", low="102", c="111"),  # target
    ]
    updated = resolve_pending_for("AAPL", bars, db_path=db, today=date(2026, 1, 2))
    assert updated == 1
    msft_rows = recent("MSFT", n=10, db_path=db)
    msft_snap = Snapshot.model_validate_json(msft_rows[0].payload_json)
    assert msft_snap.outcome is None


def test_resolve_pending_for_isolates_per_row_failures(tmp_path, monkeypatch):
    """A failure on one row must not abort the batch — subsequent rows
    for the same ticker still get processed."""
    from wave_alpha.history import resolver as resolver_mod
    from wave_alpha.history.store import _INITIALIZED, recent, upsert

    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)
    # Seed two unresolved snapshots, days 1 and 2.
    upsert(_build_snapshot(as_of_day=1), db_path=db)
    upsert(_build_snapshot(as_of_day=2), db_path=db)

    # Bars that would resolve both as target (day 3 high=112).
    bars = [
        _bar(3, o="103", h="112", low="102", c="111"),
    ]

    # Monkeypatch resolve_outcome to raise on the FIRST row (as_of=Jan 1)
    # and succeed on subsequent rows (as_of=Jan 2).
    real_resolve = resolver_mod.resolve_outcome
    calls = {"n": 0}

    def flaky_resolve(signal, bars_arg, *, as_of_close):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("synthetic per-row failure")
        return real_resolve(signal, bars_arg, as_of_close=as_of_close)

    monkeypatch.setattr(resolver_mod, "resolve_outcome", flaky_resolve)

    updated = resolver_mod.resolve_pending_for("AAPL", bars, db_path=db, today=date(2026, 1, 3))
    # Second row still resolved, batch did not abort.
    assert updated == 1
    rows = recent("AAPL", n=10, db_path=db)
    # Day-1 row stayed unresolved; day-2 row resolved as target.
    as_of_to_outcome = {r.as_of: Snapshot.model_validate_json(r.payload_json).outcome for r in rows}
    assert as_of_to_outcome[date(2026, 1, 1)] is None
    assert as_of_to_outcome[date(2026, 1, 2)] is not None
    assert as_of_to_outcome[date(2026, 1, 2)].status == "target"
