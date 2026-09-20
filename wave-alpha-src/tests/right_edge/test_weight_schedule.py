from datetime import date

import pytest

from wave_alpha.right_edge.features import RightEdgeFeatures
from wave_alpha.right_edge.outcome_dataset import (
    OutcomeRow,
    WeightSchedule,
    assign_weights,
    label_for_status,
)


def _feats() -> RightEdgeFeatures:
    return RightEdgeFeatures(
        retracement_progress=0.5,
        bars_since_candidate=5,
        volume_decline_pct=0.0,
        higher_degree_zone_match=0.0,
        atr_regime=1.0,
    )


def _row(degree: int) -> OutcomeRow:
    return OutcomeRow(
        ticker="AAPL",
        as_of=date(2025, 1, 1),
        degree=degree,
        features=_feats(),
        label=1.0,
        weight=0.0,
        exit_date=date(2024, 1, 1),
    )


def test_weights_zero_when_no_outcomes() -> None:
    weighted = assign_weights(rows=[], n_synth_per_degree={1: 500}, schedule=WeightSchedule())
    assert weighted == []


def test_weights_below_target_get_partial_ramp() -> None:
    # 50 outcome rows at degree 1, 500 synthetic, target 100
    rows = [_row(1) for _ in range(50)]
    schedule = WeightSchedule(n_target_per_degree=100)
    weighted = assign_weights(rows=rows, n_synth_per_degree={1: 500}, schedule=schedule)
    # ramp = 50/100 = 0.5; weight = (500/50) * 0.5 = 5.0
    assert all(r.weight == pytest.approx(5.0) for r in weighted)


def test_weights_at_target_match_synthetic_mass() -> None:
    rows = [_row(1) for _ in range(100)]
    schedule = WeightSchedule(n_target_per_degree=100)
    weighted = assign_weights(rows=rows, n_synth_per_degree={1: 500}, schedule=schedule)
    # ramp = 1.0; weight = 500/100 = 5.0. Total outcome mass = 100 * 5 = 500 = synthetic mass.
    assert all(r.weight == pytest.approx(5.0) for r in weighted)
    assert sum(r.weight for r in weighted) == pytest.approx(500)


def test_weights_above_target_stay_balanced_one_to_one() -> None:
    rows = [_row(1) for _ in range(1000)]
    schedule = WeightSchedule(n_target_per_degree=100)
    weighted = assign_weights(rows=rows, n_synth_per_degree={1: 500}, schedule=schedule)
    # ramp = min(1, 1000/100) = 1.0; weight = 500/1000 = 0.5. Total mass = 500.
    assert all(r.weight == pytest.approx(0.5) for r in weighted)
    assert sum(r.weight for r in weighted) == pytest.approx(500)


def test_weights_per_degree_independent() -> None:
    rows = [_row(1) for _ in range(50)] + [_row(2) for _ in range(100)]
    schedule = WeightSchedule(n_target_per_degree=100)
    weighted = assign_weights(rows=rows, n_synth_per_degree={1: 500, 2: 200}, schedule=schedule)
    d1 = [r for r in weighted if r.degree == 1]
    d2 = [r for r in weighted if r.degree == 2]
    # degree 1: ramp 0.5, weight = (500/50) * 0.5 = 5.0
    assert all(r.weight == pytest.approx(5.0) for r in d1)
    # degree 2: ramp 1.0, weight = 200/100 = 2.0
    assert all(r.weight == pytest.approx(2.0) for r in d2)


def test_weights_zero_when_synthetic_count_zero_for_degree() -> None:
    # Defensive: if no synthetic rows for a degree, outcome rows for that degree
    # carry no weight (we have nothing to balance against).
    rows = [_row(3) for _ in range(50)]
    weighted = assign_weights(
        rows=rows, n_synth_per_degree={}, schedule=WeightSchedule(n_target_per_degree=100)
    )
    assert all(r.weight == 0.0 for r in weighted)


@pytest.mark.parametrize(
    ("status", "expected"),
    [("target", 1.0), ("stop", 0.0)],
)
def test_label_for_status_target_and_stop(status: str, expected: float) -> None:
    schedule = WeightSchedule()
    assert label_for_status(status, schedule=schedule) == expected  # type: ignore[arg-type]


def test_label_for_status_time_stop_included_uses_schedule_value() -> None:
    schedule = WeightSchedule(include_time_stops=True, time_stop_label=0.3)
    assert label_for_status("time_stop", schedule=schedule) == 0.3


def test_label_for_status_time_stop_excluded_returns_none() -> None:
    schedule = WeightSchedule(include_time_stops=False)
    assert label_for_status("time_stop", schedule=schedule) is None


def test_label_for_status_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unexpected outcome_status"):
        label_for_status("garbage", schedule=WeightSchedule())  # type: ignore[arg-type]


def test_weight_schedule_zero_target_raises() -> None:
    with pytest.raises(ValueError, match="n_target_per_degree must be > 0"):
        WeightSchedule(n_target_per_degree=0)


def test_weight_schedule_negative_target_raises() -> None:
    with pytest.raises(ValueError, match="n_target_per_degree must be > 0"):
        WeightSchedule(n_target_per_degree=-5)
