from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wave_alpha.history import store
from wave_alpha.history.models import (
    Snapshot,
    SnapshotCoherence,
    SnapshotCount,
    SnapshotOutcome,
    SnapshotPivot,
    SnapshotSignal,
)
from wave_alpha.web import deps
from wave_alpha.web.app import create_app


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
                engine_score=0.72,
                llm_prob=None,
                final_score=0.72,
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
def temp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(deps, "_data_dir", lambda: tmp_path)
    return tmp_path


def test_history_route_empty(temp_data_dir: Path) -> None:
    client = TestClient(create_app())
    r = client.get("/deepdive/AAPL/history")
    assert r.status_code == 200
    assert "No snapshots yet" in r.text


def test_history_route_renders_snapshots(temp_data_dir: Path) -> None:
    db = temp_data_dir / "history.sqlite"
    for i in range(5, 0, -1):
        store.upsert(_snap(as_of=date(2024, 12, i), top_hash="h-A"), db_path=db)
    client = TestClient(create_app())
    r = client.get("/deepdive/AAPL/history")
    assert r.status_code == 200
    assert "2024-12-05" in r.text
    assert "impulse" in r.text
    assert "stable 5 days" in r.text


def test_history_route_lowercases_to_canonical_ticker(temp_data_dir: Path) -> None:
    db = temp_data_dir / "history.sqlite"
    store.upsert(_snap(as_of=date(2024, 12, 31), top_hash="h-A"), db_path=db)
    client = TestClient(create_app())
    r = client.get("/deepdive/aapl/history")
    assert r.status_code == 200
    assert "2024-12-31" in r.text


def test_history_route_swallows_read_errors(
    temp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read failures must render the empty state, not 500."""
    from wave_alpha.history import service as history_service

    def boom(*args, **kwargs):
        raise RuntimeError("simulated DB corruption")

    monkeypatch.setattr(history_service, "get_recent_with_badge", boom)
    client = TestClient(create_app())
    r = client.get("/deepdive/AAPL/history")
    assert r.status_code == 200
    assert "No snapshots yet" in r.text


def test_counts_fragment_writes_history_snapshot(
    temp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Visiting /deepdive/<ticker>/counts must populate the history DB."""
    from datetime import date as _date
    from decimal import Decimal as _Decimal

    from wave_alpha.coherence.score import CoherenceResult
    from wave_alpha.domain import Interval
    from wave_alpha.elliott.models import WaveCount
    from wave_alpha.pipeline import AnalyzeResult, RankedCount
    from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
    from wave_alpha.trades.models import TradeSignal
    from wave_alpha.web import deps as web_deps

    pivots = [
        PivotEvent(
            degree=1,
            timestamp=_date(2024, 1, 1),
            price=_Decimal("100"),
            interval=Interval.DAILY,
            type=PivotType.LOW,
            state=PivotState.CONFIRMED,
        ),
        PivotEvent(
            degree=1,
            timestamp=_date(2024, 6, 1),
            price=_Decimal("130"),
            interval=Interval.DAILY,
            type=PivotType.HIGH,
            state=PivotState.CONFIRMED,
        ),
    ]
    fake = AnalyzeResult(
        ticker="AAPL",
        as_of=_date(2024, 6, 1),
        ranked_daily=[
            RankedCount(
                candidate_id="C1",
                count=WaveCount(pattern="impulse", degree=1, pivots=pivots, score=0.7),
                engine_score=0.7,
                llm_prob=None,
                final_score=0.7,
            )
        ],
        signals=[
            TradeSignal(
                ticker="AAPL",
                as_of=_date(2024, 6, 1),
                direction="long",
                entry_price=_Decimal("130"),
                stop_price=_Decimal("125"),
                target_price=_Decimal("145"),
                count_id="C1",
                count_pattern="impulse",
                wave_label="3",
                degree=1,
                confidence=0.7,
                expected_bars_to_target=20,
                max_holding_bars=60,
            )
        ],
        coherence=CoherenceResult(score=0.8, multiplier=1.05, pairwise={}, fallback_used=None),
    )
    monkeypatch.setattr(web_deps, "analyze", lambda *a, **k: fake)

    client = TestClient(create_app())
    r = client.get("/deepdive/AAPL/counts?as_of=2024-06-01")
    assert r.status_code == 200

    db = temp_data_dir / "history.sqlite"
    assert db.exists()
    rows = store.recent("AAPL", 10, db_path=db)
    assert len(rows) == 1
    assert rows[0].as_of == _date(2024, 6, 1)


def test_history_fragment_includes_outcome_badge_in_context(temp_data_dir: Path) -> None:
    """history_fragment must pass outcome_badge into the template context without 500."""
    db = temp_data_dir / "history.sqlite"
    outcome = SnapshotOutcome(
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
        resolved_at=datetime(2026, 1, 6, 0, 0, 0),
    )
    snap = _snap(as_of=date(2026, 1, 1), top_hash="h-outcome")
    snap = snap.model_copy(update={"outcome": outcome})
    store.upsert(snap, db_path=db)

    client = TestClient(create_app())
    r = client.get("/deepdive/AAPL/history")
    # outcome_badge kwarg must not cause a 500; template renders successfully.
    assert r.status_code == 200
    # Snapshot row should appear confirming template rendered with data.
    assert "2026-01-01" in r.text


def test_history_fragment_renders_outcome_badge_text(temp_data_dir: Path) -> None:
    """Outcome badge summary line and per-row chip must appear in rendered HTML."""
    db = temp_data_dir / "history.sqlite"
    outcome = SnapshotOutcome(
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
        resolved_at=datetime(2026, 1, 6, 0, 0, 0),
    )
    snap = _snap(as_of=date(2026, 1, 1), top_hash="h-outcome")
    snap = snap.model_copy(update={"outcome": outcome})
    store.upsert(snap, db_path=db)

    client = TestClient(create_app())
    r = client.get("/deepdive/AAPL/history")
    assert r.status_code == 200

    # Outcome badge summary line
    assert "Outcomes:" in r.text
    assert "hit target" in r.text
    assert "+2.0R" in r.text

    # Per-row chip: target glyph and R value
    assert "✓" in r.text
