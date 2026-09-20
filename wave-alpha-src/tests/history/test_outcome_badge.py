"""Pure-function tests for compute_outcome_badge."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal


def _outcome(status, realized_R=None, unrealized_R=0.0):  # noqa: N803
    from wave_alpha.history.models import SnapshotOutcome

    return SnapshotOutcome(
        status=status,
        exit_date=None if status == "pending" else date(2026, 1, 5),
        exit_price=None if status == "pending" else Decimal("110"),
        bars_held=None if status == "pending" else 4,
        realized_R=realized_R,
        last_observed_date=date(2026, 1, 5),
        bars_elapsed=4,
        unrealized_R=unrealized_R if status == "pending" else (realized_R or 0.0),
        mfe_R=0.0,
        mae_R=0.0,
        resolved_at=datetime(2026, 1, 6, tzinfo=UTC),
    )


def _snap_with(outcome):
    from wave_alpha.history.models import Snapshot, SnapshotCount, SnapshotPivot

    return Snapshot(
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
        top_count_hash="h",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        outcome=outcome,
    )


def test_compute_outcome_badge_basic():
    from wave_alpha.history.service import compute_outcome_badge

    snaps = [
        _snap_with(_outcome("target", realized_R=2.0)),
        _snap_with(_outcome("target", realized_R=1.5)),
        _snap_with(_outcome("stop", realized_R=-1.0)),
        _snap_with(_outcome("time_stop", realized_R=0.3)),
        _snap_with(_outcome("pending", unrealized_R=0.4)),
        _snap_with(_outcome("pending", unrealized_R=-0.2)),
    ]
    badge = compute_outcome_badge(snaps)
    assert badge is not None
    assert badge.resolved_count == 4
    assert badge.target_count == 2
    assert abs(badge.hit_rate - 0.5) < 1e-9
    assert abs(badge.avg_R - (2.0 + 1.5 + (-1.0) + 0.3) / 4) < 1e-9
    assert badge.in_flight_count == 2


def test_compute_outcome_badge_returns_none_when_no_outcome():
    from wave_alpha.history.service import compute_outcome_badge

    assert compute_outcome_badge([_snap_with(None)]) is None
    assert compute_outcome_badge([]) is None


def test_compute_outcome_badge_zero_resolved_with_pending():
    from wave_alpha.history.service import compute_outcome_badge

    snaps = [
        _snap_with(_outcome("pending", unrealized_R=0.3)),
        _snap_with(_outcome("pending", unrealized_R=-0.1)),
    ]
    badge = compute_outcome_badge(snaps)
    assert badge is not None
    assert badge.resolved_count == 0
    assert badge.target_count == 0
    assert badge.hit_rate == 0.0
    assert badge.avg_R == 0.0
    assert badge.in_flight_count == 2
