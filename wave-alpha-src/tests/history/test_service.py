import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from wave_alpha.coherence.score import CoherenceResult
from wave_alpha.domain import Interval
from wave_alpha.elliott.models import WaveCount
from wave_alpha.history import service, store
from wave_alpha.history.models import Snapshot
from wave_alpha.pipeline import AnalyzeResult, RankedCount
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
from wave_alpha.trades.models import TradeSignal


def _pivots() -> list[PivotEvent]:
    return [
        PivotEvent(
            degree=1,
            timestamp=date(2024, 1, 1),
            price=Decimal("100"),
            interval=Interval.DAILY,
            type=PivotType.LOW,
            state=PivotState.CONFIRMED,
        ),
        PivotEvent(
            degree=1,
            timestamp=date(2024, 2, 1),
            price=Decimal("110"),
            interval=Interval.DAILY,
            type=PivotType.HIGH,
            state=PivotState.CONFIRMED,
        ),
        PivotEvent(
            degree=1,
            timestamp=date(2024, 3, 1),
            price=Decimal("105"),
            interval=Interval.DAILY,
            type=PivotType.LOW,
            state=PivotState.CONFIRMED,
        ),
        PivotEvent(
            degree=1,
            timestamp=date(2024, 6, 1),
            price=Decimal("130"),
            interval=Interval.DAILY,
            type=PivotType.HIGH,
            state=PivotState.CONFIRMED,
        ),
    ]


def _wc(pattern: str = "impulse", score: float = 0.7) -> WaveCount:
    return WaveCount(pattern=pattern, degree=1, pivots=_pivots(), score=score)


def _ranked(score: float = 0.7) -> RankedCount:
    return RankedCount(
        candidate_id="C1",
        count=_wc(score=score),
        engine_score=score,
        llm_prob=None,
        final_score=score,
    )


def _signal() -> TradeSignal:
    return TradeSignal(
        ticker="AAPL",
        as_of=date(2024, 6, 1),
        direction="long",
        entry_price=Decimal("130.00"),
        stop_price=Decimal("125.00"),
        target_price=Decimal("145.00"),
        count_id="C1",
        count_pattern="impulse",
        wave_label="3",
        degree=1,
        confidence=0.7,
        expected_bars_to_target=20,
        max_holding_bars=60,
    )


def _coh(score: float = 0.8) -> CoherenceResult:
    return CoherenceResult(
        score=score, multiplier=1.05, pairwise={"weekly_daily": score}, fallback_used=None
    )


def _result(*, ranked: list[RankedCount] | None = None) -> AnalyzeResult:
    if ranked is None:
        ranked = [_ranked()]
    return AnalyzeResult(
        ticker="AAPL",
        as_of=date(2024, 6, 1),
        ranked_daily=ranked,
        signals=[_signal()] if ranked else [],
        coherence=_coh() if ranked else None,
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "history.sqlite"


def test_record_snapshot_writes_a_row(db_path: Path) -> None:
    service.record_snapshot(_result(), "AAPL", date(2024, 6, 1), db_path=db_path)
    rows = store.recent("AAPL", 10, db_path=db_path)
    assert len(rows) == 1
    snap = Snapshot.model_validate_json(rows[0].payload_json)
    assert snap.ticker == "AAPL"
    assert snap.as_of == date(2024, 6, 1)
    assert snap.top_3[0].pattern == "impulse"
    assert snap.signal is not None
    assert snap.signal.direction == "long"
    assert snap.coherence is not None
    assert snap.coherence.score == pytest.approx(0.8)
    assert len(snap.top_count_hash) == 16
    # created_at is stamped during the call (within 10 seconds)
    from datetime import UTC, datetime

    delta = abs((datetime.now(tz=UTC) - snap.created_at).total_seconds())
    assert delta < 10, f"created_at not recent: delta={delta}s"


def test_record_snapshot_skips_when_ranked_daily_empty(db_path: Path) -> None:
    empty = AnalyzeResult(ticker="AAPL", as_of=date(2024, 6, 1))
    service.record_snapshot(empty, "AAPL", date(2024, 6, 1), db_path=db_path)
    assert store.recent("AAPL", 10, db_path=db_path) == []


def test_record_snapshot_swallows_store_errors(
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(service.store, "upsert", boom)
    with caplog.at_level(logging.WARNING, logger="wave_alpha.history.service"):
        service.record_snapshot(_result(), "AAPL", date(2024, 6, 1), db_path=db_path)
    assert any("history snapshot/outcome failed" in rec.message for rec in caplog.records)


def test_record_snapshot_caps_top_3_at_three(db_path: Path) -> None:
    five = [_ranked(score=0.9 - 0.1 * i) for i in range(5)]
    service.record_snapshot(_result(ranked=five), "AAPL", date(2024, 6, 1), db_path=db_path)
    rows = store.recent("AAPL", 10, db_path=db_path)
    snap = Snapshot.model_validate_json(rows[0].payload_json)
    assert len(snap.top_3) == 3


def test_record_snapshot_idempotent_on_repeat(db_path: Path) -> None:
    service.record_snapshot(_result(), "AAPL", date(2024, 6, 1), db_path=db_path)
    service.record_snapshot(_result(), "AAPL", date(2024, 6, 1), db_path=db_path)
    rows = store.recent("AAPL", 10, db_path=db_path)
    assert len(rows) == 1


def test_record_snapshot_triggers_resolution_of_prior_pending_rows(tmp_path: Path) -> None:
    """Writing today's snapshot for AAPL should also resolve any prior
    AAPL snapshot whose outcome is unresolved."""
    from datetime import UTC, datetime
    from decimal import Decimal

    from wave_alpha.domain import Bar, Interval
    from wave_alpha.elliott.models import WaveCount
    from wave_alpha.history.models import (
        Snapshot,
        SnapshotCount,
        SnapshotPivot,
        SnapshotSignal,
    )
    from wave_alpha.history.service import record_snapshot
    from wave_alpha.history.store import _INITIALIZED, recent, upsert
    from wave_alpha.pipeline import AnalyzeResult, RankedCount
    from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType

    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)

    # Plant a prior unresolved snapshot at Jan 1.
    prior = Snapshot(
        ticker="AAPL",
        as_of=date(2026, 1, 1),
        engine_version="0.7.0",
        top_3=[
            SnapshotCount(
                candidate_id="C1",
                pattern="impulse",
                degree=2,
                last_pivot_label="3",
                pivots=[SnapshotPivot(timestamp=date(2026, 1, 1), price=Decimal("100"))],
                engine_score=0.7,
                llm_prob=None,
                final_score=0.7,
            ),
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
            degree=2,
            confidence=0.7,
            expected_bars_to_target=20,
            max_holding_bars=60,
        ),
        coherence=None,
        top_count_hash="h1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    upsert(prior, db_path=db)

    # Build a minimal AnalyzeResult whose daily_bars include a target-hit on Jan 3.
    def _b(day: int, h: str, low: str, c: str) -> Bar:
        return Bar(
            timestamp=date(2026, 1, day),
            interval=Interval.DAILY,
            open=Decimal("100"),
            high=Decimal(h),
            low=Decimal(low),
            close=Decimal(c),
            volume=1_000_000,
        )

    result = AnalyzeResult(
        ticker="AAPL",
        as_of=date(2026, 1, 3),
        ranked_daily=[
            RankedCount(
                candidate_id="C1",
                count=WaveCount(
                    pattern="impulse",
                    degree=2,
                    pivots=[
                        PivotEvent(
                            timestamp=date(2026, 1, 3),
                            price=Decimal("100"),
                            type=PivotType.HIGH,
                            degree=2,
                            interval=Interval.DAILY,
                            state=PivotState.CONFIRMED,
                        ),
                    ],
                ),
                engine_score=0.7,
                llm_prob=None,
                final_score=0.7,
            ),
        ],
        daily_bars=[
            _b(2, "105", "99", "103"),
            _b(3, "112", "102", "111"),  # target hit (high >= 110)
        ],
    )

    record_snapshot(result, "AAPL", date(2026, 1, 3), db_path=db)

    # Prior snapshot should now have outcome.status=target.
    rows = recent("AAPL", n=10, db_path=db)
    assert len(rows) == 2
    prior_after = next(
        Snapshot.model_validate_json(r.payload_json) for r in rows if r.as_of == date(2026, 1, 1)
    )
    assert prior_after.outcome is not None
    assert prior_after.outcome.status == "target"


def test_record_snapshot_resolver_error_does_not_break_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the resolver raises, today's snapshot must still be written."""
    from decimal import Decimal

    from wave_alpha.domain import Bar, Interval
    from wave_alpha.elliott.models import WaveCount
    from wave_alpha.history import resolver as resolver_mod
    from wave_alpha.history.store import _INITIALIZED, recent
    from wave_alpha.pipeline import AnalyzeResult, RankedCount
    from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("resolver kaboom")

    monkeypatch.setattr(resolver_mod, "resolve_pending_for", _boom)

    db = tmp_path / "history.sqlite"
    _INITIALIZED.discard(db)

    result = AnalyzeResult(
        ticker="AAPL",
        as_of=date(2026, 1, 3),
        ranked_daily=[
            RankedCount(
                candidate_id="C1",
                count=WaveCount(
                    pattern="impulse",
                    degree=2,
                    pivots=[
                        PivotEvent(
                            timestamp=date(2026, 1, 3),
                            price=Decimal("100"),
                            type=PivotType.HIGH,
                            degree=2,
                            interval=Interval.DAILY,
                            state=PivotState.CONFIRMED,
                        ),
                    ],
                ),
                engine_score=0.7,
                llm_prob=None,
                final_score=0.7,
            ),
        ],
        daily_bars=[
            Bar(
                timestamp=date(2026, 1, 3),
                interval=Interval.DAILY,
                open=Decimal("100"),
                high=Decimal("105"),
                low=Decimal("99"),
                close=Decimal("103"),
                volume=1_000_000,
            ),
        ],
    )

    # Must not raise even though resolver is broken.
    service.record_snapshot(result, "AAPL", date(2026, 1, 3), db_path=db)

    # Today's snapshot row exists.
    rows = recent("AAPL", n=10, db_path=db)
    assert len(rows) == 1
