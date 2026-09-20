from datetime import date

import pytest

from wave_alpha.right_edge.features import RightEdgeFeatures
from wave_alpha.right_edge.outcome_dataset import (
    OutcomeDataset,
    OutcomeRow,
    WeightSchedule,
)
from wave_alpha.right_edge.training import TrainingRow, train_blended_final


def _feats(retrace: float = 0.5, atr: float = 1.0) -> RightEdgeFeatures:
    return RightEdgeFeatures(
        retracement_progress=retrace,
        bars_since_candidate=5,
        volume_decline_pct=0.0,
        higher_degree_zone_match=0.0,
        atr_regime=atr,
    )


def _synth(label: int, degree: int = 1) -> TrainingRow:
    return TrainingRow(
        ticker="S",
        as_of=date(2025, 1, 1),
        degree=degree,
        features=_feats(),
        outcome=label,
    )


def _outc(label: float, weight: float, degree: int = 1) -> OutcomeRow:
    return OutcomeRow(
        ticker="O",
        as_of=date(2025, 6, 1),
        degree=degree,
        features=_feats(),
        label=label,
        weight=weight,
        exit_date=date(2024, 1, 1),
    )


def test_train_blended_final_with_empty_outcomes_is_synthetic_only() -> None:
    synth = [_synth(0)] * 50 + [_synth(1)] * 50
    outc = OutcomeDataset(
        rows=[],
        skipped_drift=0,
        skipped_no_bars=0,
        skipped_wrong_degree=0,
        schedule=WeightSchedule(),
    )
    logistic, _platt = train_blended_final(synth, outc, seed=42, with_fib=False)
    # Compare against an unweighted fit on the same synth-only data
    from wave_alpha.right_edge.training import fit_logistic

    expected = fit_logistic(synth, seed=42, with_fib=False)
    assert logistic.weights == pytest.approx(expected.weights, abs=1e-9)
    assert logistic.intercept == pytest.approx(expected.intercept, abs=1e-9)


def test_train_blended_final_includes_outcome_rows() -> None:
    synth = [_synth(0)] * 50
    # 50 outcome rows all labeled 1, weight 10 — should shift the model toward positive.
    outc_rows = [_outc(1.0, weight=10.0) for _ in range(50)]
    outc = OutcomeDataset(
        rows=outc_rows,
        skipped_drift=0,
        skipped_no_bars=0,
        skipped_wrong_degree=0,
        schedule=WeightSchedule(),
    )
    logistic, _platt = train_blended_final(synth, outc, seed=42, with_fib=False)
    # Prediction on the shared feature vector should now be > 0.5 because
    # outcome mass (50 * 10 = 500) >> synthetic mass (50 * 1 = 50).
    from wave_alpha.right_edge.training import predict_logistic

    pred = predict_logistic(logistic, _feats())
    assert pred > 0.5


def test_train_blended_final_fits_per_degree_platt() -> None:
    synth = [_synth(0, degree=1), _synth(1, degree=1)] * 30
    outc = OutcomeDataset(
        rows=[_outc(1.0, weight=1.0, degree=1)],
        skipped_drift=0,
        skipped_no_bars=0,
        skipped_wrong_degree=0,
        schedule=WeightSchedule(),
    )
    _logistic, platt = train_blended_final(synth, outc, seed=42, with_fib=False)
    # Platt fit on the blended (raw, outcome) pairs — must have an entry for degree 1
    assert 1 in platt


def test_train_blended_final_rejects_all_empty() -> None:
    synth: list[TrainingRow] = []
    outc = OutcomeDataset(
        rows=[],
        skipped_drift=0,
        skipped_no_bars=0,
        skipped_wrong_degree=0,
        schedule=WeightSchedule(),
    )
    with pytest.raises(ValueError, match="no rows"):
        train_blended_final(synth, outc, seed=42, with_fib=False)
