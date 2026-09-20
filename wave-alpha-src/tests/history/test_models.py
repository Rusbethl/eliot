import dataclasses
from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from wave_alpha.history.models import (
    RecentHistory,
    Snapshot,
    SnapshotCoherence,
    SnapshotCount,
    SnapshotPivot,
    SnapshotSignal,
    StabilityBadge,
)


def _make_snapshot() -> Snapshot:
    return Snapshot(
        ticker="AAPL",
        as_of=date(2024, 12, 31),
        engine_version="0.5.11",
        top_3=[
            SnapshotCount(
                candidate_id="C1",
                pattern="impulse",
                degree=1,
                last_pivot_label="3",
                pivots=[
                    SnapshotPivot(timestamp=date(2024, 1, 1), price=Decimal("100")),
                    SnapshotPivot(timestamp=date(2024, 6, 1), price=Decimal("130")),
                ],
                engine_score=0.72,
                llm_prob=None,
                final_score=0.72,
            )
        ],
        signal=SnapshotSignal(
            direction="long",
            entry_price=Decimal("130.50"),
            stop_price=Decimal("125.00"),
            target_price=Decimal("145.00"),
            rr=2.6,
        ),
        coherence=SnapshotCoherence(
            score=0.81,
            multiplier=1.05,
            pairwise={"weekly_daily": 0.9},
            fallback_used=None,
        ),
        top_count_hash="a1b2c3d4e5f60718",
        created_at=datetime(2024, 12, 31, 16, 30, 0),
    )


def test_snapshot_round_trips_via_json() -> None:
    snap = _make_snapshot()
    raw = snap.model_dump_json()
    restored = Snapshot.model_validate_json(raw)
    assert restored == snap


def test_snapshot_extra_fields_in_json_are_ignored() -> None:
    """Forward-compat: payloads written by a future engine with extra
    fields must still load against the current model."""
    snap = _make_snapshot()
    raw = snap.model_dump_json()
    # inject a new top-level field not in the schema
    raw_obj = raw.replace('"ticker":"AAPL"', '"ticker":"AAPL","new_v2_field":42')
    restored = Snapshot.model_validate_json(raw_obj)
    assert restored.ticker == "AAPL"


def test_snapshot_is_frozen() -> None:
    snap = _make_snapshot()
    with pytest.raises(ValidationError):
        snap.ticker = "MSFT"  # type: ignore[misc]


def test_stability_badge_and_recent_history_are_dataclasses() -> None:
    badge = StabilityBadge(streak=5, matches_in_window=9, window=14, last_changed_on=None)
    assert badge.streak == 5
    rh = RecentHistory(snapshots=[_make_snapshot()], badge=badge)
    assert len(rh.snapshots) == 1
    assert rh.badge is badge


def test_stability_badge_is_frozen() -> None:
    badge = StabilityBadge(streak=5, matches_in_window=9, window=14, last_changed_on=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        badge.streak = 99  # type: ignore[misc]


def test_snapshot_rejects_more_than_three_top_counts() -> None:
    """top_3 is locked at exactly the top-3 ranked counts; over-population
    should surface as ValidationError, not silently break UI assumptions."""
    pivots = [SnapshotPivot(timestamp=date(2024, 1, 1), price=Decimal("100"))]

    def _count(cid: str) -> SnapshotCount:
        return SnapshotCount(
            candidate_id=cid,
            pattern="impulse",
            degree=1,
            last_pivot_label="3",
            pivots=pivots,
            engine_score=0.7,
            llm_prob=None,
            final_score=0.7,
        )

    with pytest.raises(ValidationError):
        Snapshot(
            ticker="AAPL",
            as_of=date(2024, 12, 31),
            engine_version="0.5.11",
            top_3=[_count(f"C{i}") for i in range(4)],  # 4 > 3
            signal=None,
            coherence=None,
            top_count_hash="x" * 16,
            created_at=datetime(2024, 12, 31, 16, 30, 0),
        )


def test_snapshot_rejects_empty_top_3() -> None:
    """top_3 must have at least 1 item; an empty list is rejected at parse time
    so the read template never IndexErrors on s.top_3[0]."""
    with pytest.raises(ValidationError):
        Snapshot(
            ticker="AAPL",
            as_of=date(2024, 12, 31),
            engine_version="0.5.11",
            top_3=[],
            signal=None,
            coherence=None,
            top_count_hash="x" * 16,
            created_at=datetime(2024, 12, 31, 16, 30, 0),
        )


def test_snapshot_signal_accepts_optional_resolver_fields():
    """SnapshotSignal gains optional fields needed by the outcome resolver.
    All default to None so v1 snapshots round-trip without migration."""
    from decimal import Decimal

    from wave_alpha.history.models import SnapshotSignal

    sig = SnapshotSignal(
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
    assert sig.max_holding_bars == 60
    assert sig.count_id == "C1"


def test_snapshot_signal_v1_compat_defaults_to_none():
    """A SnapshotSignal constructed without the new fields (v1 shape)
    still validates; new fields default to None."""
    from decimal import Decimal

    from wave_alpha.history.models import SnapshotSignal

    sig = SnapshotSignal(
        direction="long",
        entry_price=Decimal("100"),
        stop_price=Decimal("95"),
        target_price=Decimal("110"),
        rr=2.0,
    )
    assert sig.max_holding_bars is None
    assert sig.count_id is None


def test_snapshot_outcome_model_round_trips():
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from wave_alpha.history.models import SnapshotOutcome

    out = SnapshotOutcome(
        status="target",
        exit_date=date(2026, 1, 5),
        exit_price=Decimal("110"),
        bars_held=4,
        realized_R=2.0,
        last_observed_date=date(2026, 1, 5),
        bars_elapsed=4,
        unrealized_R=2.0,
        mfe_R=2.4,
        mae_R=-0.6,
        resolved_at=datetime(2026, 1, 6, 12, 0, tzinfo=UTC),
    )
    blob = out.model_dump_json()
    rt = SnapshotOutcome.model_validate_json(blob)
    assert rt == out


def test_snapshot_outcome_pending_allows_none_finals():
    from datetime import UTC, date, datetime

    from wave_alpha.history.models import SnapshotOutcome

    out = SnapshotOutcome(
        status="pending",
        exit_date=None,
        exit_price=None,
        bars_held=None,
        realized_R=None,
        last_observed_date=date(2026, 1, 5),
        bars_elapsed=4,
        unrealized_R=0.8,
        mfe_R=1.2,
        mae_R=-0.2,
        resolved_at=datetime(2026, 1, 6, 12, 0, tzinfo=UTC),
    )
    assert out.realized_R is None
    assert out.exit_price is None


def test_snapshot_has_optional_outcome_field_defaulting_none():
    """v1 payloads (no outcome key) deserialize cleanly with outcome=None."""
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from wave_alpha.history.models import Snapshot, SnapshotCount, SnapshotPivot

    snap = Snapshot(
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
        signal=None,
        coherence=None,
        top_count_hash="abc",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert snap.outcome is None


def test_snapshot_validates_legacy_payload_without_outcome_key():
    """Simulate a v1 SQLite row: payload JSON has no 'outcome' field at all."""
    legacy_json = """{
        "ticker": "AAPL",
        "as_of": "2026-01-01",
        "engine_version": "0.7.0",
        "top_3": [{"candidate_id":"C1","pattern":"impulse","degree":2,
                   "last_pivot_label":"3","pivots":[],"engine_score":0.7,
                   "llm_prob":null,"final_score":0.7}],
        "signal": null,
        "coherence": null,
        "top_count_hash": "abc",
        "created_at": "2026-01-01T00:00:00+00:00"
    }"""
    from wave_alpha.history.models import Snapshot

    snap = Snapshot.model_validate_json(legacy_json)
    assert snap.outcome is None
