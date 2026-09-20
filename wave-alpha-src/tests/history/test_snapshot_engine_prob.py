"""SnapshotCount.engine_prob is optional and round-trips through the store."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from wave_alpha.history import store
from wave_alpha.history.models import (
    Snapshot,
    SnapshotCount,
    SnapshotPivot,
)


def _base_count(**overrides) -> SnapshotCount:
    defaults = dict(
        candidate_id="C1",
        pattern="impulse",
        degree=2,
        last_pivot_label="3",
        pivots=[SnapshotPivot(timestamp=date(2024, 1, 1), price=Decimal("100"))],
        engine_score=0.7,
        llm_prob=None,
        final_score=0.7,
    )
    defaults.update(overrides)
    return SnapshotCount(**defaults)


def test_engine_prob_defaults_to_none() -> None:
    c = _base_count()
    assert c.engine_prob is None


def test_engine_prob_round_trips_through_store(tmp_path: Path) -> None:
    db_path = tmp_path / "history.sqlite"
    snap = Snapshot(
        ticker="AAPL",
        as_of=date(2024, 6, 30),
        engine_version="0.13.0",
        top_3=[
            _base_count(engine_prob=0.42),
            _base_count(candidate_id="C2", engine_prob=None),
        ],
        signal=None,
        coherence=None,
        top_count_hash="h_test",
        created_at=datetime(2024, 6, 30, tzinfo=UTC),
    )
    store.upsert(snap, db_path=db_path)

    rows = store.recent("AAPL", n=1, db_path=db_path)
    assert len(rows) == 1
    loaded = Snapshot.model_validate_json(rows[0].payload_json)
    assert loaded.top_3[0].engine_prob == pytest.approx(0.42)
    assert loaded.top_3[1].engine_prob is None


def test_old_payload_without_field_loads_with_none() -> None:
    """JSON written before this field existed must still load — Pydantic
    optional-with-default handles this, but we lock it in a test."""
    import json

    payload = {
        "ticker": "AAPL",
        "as_of": "2023-01-01",
        "engine_version": "0.11.0",
        "top_3": [
            {
                "candidate_id": "C1",
                "pattern": "impulse",
                "degree": 2,
                "last_pivot_label": "3",
                "pivots": [{"timestamp": "2023-01-01", "price": "100"}],
                "engine_score": 0.5,
                "llm_prob": None,
                "final_score": 0.5,
                # NOTE: no engine_prob key
            }
        ],
        "signal": None,
        "coherence": None,
        "top_count_hash": "h_old",
        "created_at": "2023-01-01T00:00:00+00:00",
    }
    loaded = Snapshot.model_validate_json(json.dumps(payload))
    assert loaded.top_3[0].engine_prob is None
