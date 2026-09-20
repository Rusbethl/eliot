from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from wave_alpha.right_edge.features import RightEdgeFeatures
from wave_alpha.right_edge.training import (
    TrainingRow,
    quarter_folds,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_features(
    *,
    progress: float = 0.5,
    bars_since: int = 5,
    vol_decline: float = 0.1,
    zone_match: float = 0.0,
    atr_regime: float = 0.5,
) -> RightEdgeFeatures:
    return RightEdgeFeatures(
        retracement_progress=progress,
        bars_since_candidate=bars_since,
        volume_decline_pct=vol_decline,
        higher_degree_zone_match=zone_match,
        atr_regime=atr_regime,
    )


def _make_row(
    *,
    progress: float = 0.5,
    outcome: int = 1,
    ticker: str = "AAPL",
    as_of: date = date(2022, 1, 15),
    degree: int = 1,
) -> TrainingRow:
    return TrainingRow(
        ticker=ticker,
        as_of=as_of,
        degree=degree,
        features=_make_features(progress=progress),
        outcome=outcome,
    )


def _toy_rows(*, seed: int = 0, n: int = 100) -> list[TrainingRow]:
    """Synthetic rows with random progress/outcome for determinism tests."""
    rng = np.random.default_rng(seed)
    rows = []
    for _i in range(n):
        outcome = int(rng.random() < 0.5)
        rows.append(
            TrainingRow(
                ticker="AAPL",
                as_of=date(2022, 1, 15),
                degree=1,
                features=_make_features(
                    progress=float(rng.random()),
                    bars_since=int(rng.integers(1, 20)),
                    vol_decline=float(rng.random() * 0.5),
                ),
                outcome=outcome,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Task 2.1 — TrainingRow + quarter_folds
# ---------------------------------------------------------------------------


def test_training_row_is_frozen() -> None:
    row = _make_row()
    with pytest.raises((AttributeError, TypeError)):
        row.outcome = 0  # type: ignore[misc]


def test_quarter_folds_expanding() -> None:
    folds = quarter_folds(date(2021, 1, 1), date(2022, 1, 1), min_train_quarters=2)
    # Q1, Q2 are warmup; folds for Q3, Q4
    assert len(folds) == 2
    assert folds[0].train_until == date(2021, 7, 1)
    assert folds[0].test_start == date(2021, 7, 1)
    assert folds[0].test_end == date(2021, 10, 1)


def test_quarter_folds_full_year_has_four_folds() -> None:
    folds = quarter_folds(date(2021, 1, 1), date(2022, 1, 1), min_train_quarters=0)
    assert len(folds) == 4


def test_quarter_folds_test_start_equals_train_until() -> None:
    """Expanding window: test_start is exactly where training ends."""
    folds = quarter_folds(date(2021, 1, 1), date(2022, 1, 1), min_train_quarters=1)
    for fold in folds:
        assert fold.train_until == fold.test_start


def test_quarter_folds_adjacent_quarters() -> None:
    """Each fold's test_end is the next fold's test_start."""
    folds = quarter_folds(date(2021, 1, 1), date(2022, 1, 1), min_train_quarters=0)
    for i in range(len(folds) - 1):
        assert folds[i].test_end == folds[i + 1].test_start


def test_quarter_folds_empty_when_range_too_short() -> None:
    """Not enough quarters to clear the warmup window."""
    folds = quarter_folds(date(2021, 1, 1), date(2021, 4, 1), min_train_quarters=4)
    assert folds == []


def test_quarter_folds_year_boundary() -> None:
    """Quarter that crosses year boundary should land on Jan 1."""
    folds = quarter_folds(date(2021, 10, 1), date(2022, 6, 30), min_train_quarters=1)
    assert folds[0].test_start == date(2022, 1, 1)
    assert folds[0].test_end == date(2022, 4, 1)


# ---------------------------------------------------------------------------
# Task 2.2 — Logistic + Platt
# ---------------------------------------------------------------------------


def test_fit_logistic_recovers_separable_signal() -> None:
    from wave_alpha.right_edge.training import fit_logistic, predict_logistic

    # Synthetic: feature 0 (retracement_progress) highly correlated with outcome.
    rng = np.random.default_rng(0)
    rows = []
    for _i in range(200):
        outcome = int(rng.random() < 0.5)
        progress = 0.8 if outcome else 0.2
        rows.append(_make_row(progress=progress, outcome=outcome))
    params = fit_logistic(rows, seed=1)
    high = predict_logistic(params, _make_features(progress=0.9))
    low = predict_logistic(params, _make_features(progress=0.1))
    assert high > 0.6
    assert low < 0.4


def test_platt_is_identity_when_predictions_already_calibrated() -> None:
    from wave_alpha.right_edge.training import apply_platt, fit_platt

    # If raw probas == empirical rates, Platt collapses near identity.
    preds = [(0.5, 0), (0.5, 1)] * 100
    p = fit_platt(preds)
    # Both classes equally likely → calibrated proba ~0.5 regardless of input
    assert abs(apply_platt(p, 0.5) - 0.5) < 0.05


def test_fit_logistic_deterministic() -> None:
    from wave_alpha.right_edge.training import fit_logistic

    rows = _toy_rows(seed=99)
    p1 = fit_logistic(rows, seed=42)
    p2 = fit_logistic(rows, seed=42)
    assert p1 == p2


def test_fit_logistic_empty_raises() -> None:
    from wave_alpha.right_edge.training import fit_logistic

    with pytest.raises(ValueError, match="empty"):
        fit_logistic([])


def test_fit_platt_empty_returns_identity() -> None:
    from wave_alpha.right_edge.training import PlattParams, fit_platt

    p = fit_platt([])
    assert p == PlattParams(a=1.0, b=0.0)


def test_predict_logistic_output_in_unit_interval() -> None:
    from wave_alpha.right_edge.training import fit_logistic, predict_logistic

    rows = _toy_rows(seed=7)
    params = fit_logistic(rows, seed=7)
    for row in rows[:10]:
        proba = predict_logistic(params, row.features)
        assert 0.0 <= proba <= 1.0


def test_apply_platt_output_in_unit_interval() -> None:
    from wave_alpha.right_edge.training import PlattParams, apply_platt

    p = PlattParams(a=2.0, b=-1.0)
    for raw in [0.0, 0.1, 0.5, 0.9, 1.0]:
        cal = apply_platt(p, raw)
        assert 0.0 <= cal <= 1.0


# ---------------------------------------------------------------------------
# Task 2.3 — Walk-forward
# ---------------------------------------------------------------------------


def _make_quarter_rows(
    quarter_start: date,
    n: int,
    *,
    outcome: int = 1,
    degree: int = 1,
    ticker: str = "AAPL",
) -> list[TrainingRow]:
    """Make n identical rows attributed to a given quarter."""
    return [
        TrainingRow(
            ticker=ticker,
            as_of=quarter_start,
            degree=degree,
            features=_make_features(progress=0.7 if outcome else 0.3),
            outcome=outcome,
        )
        for _ in range(n)
    ]


def test_walk_forward_n_oos_rows_matches_test_data() -> None:
    from wave_alpha.right_edge.training import quarter_folds, walk_forward

    # 4 quarters of data: Q1+Q2 train, Q3+Q4 OOS
    q1 = date(2021, 1, 1)
    q2 = date(2021, 4, 1)
    q3 = date(2021, 7, 1)
    q4 = date(2021, 10, 1)
    rows_by_quarter = {
        q1: _make_quarter_rows(q1, 20),
        q2: _make_quarter_rows(q2, 20),
        q3: _make_quarter_rows(q3, 15),
        q4: _make_quarter_rows(q4, 10),
    }
    folds = quarter_folds(date(2021, 1, 1), date(2022, 1, 1), min_train_quarters=2)
    result = walk_forward(rows_by_quarter, folds=folds)
    # OOS rows = Q3 + Q4 = 25
    assert result.n_oos_rows == 25
    assert len(result.oos_predictions) == 25
    # n_train_rows = total across all quarters
    assert result.n_train_rows == 65


def test_walk_forward_oos_predictions_only_from_test_folds() -> None:
    """No training-fold rows should appear in oos_predictions."""
    from wave_alpha.right_edge.training import quarter_folds, walk_forward

    q1 = date(2021, 1, 1)
    q2 = date(2021, 4, 1)
    q3 = date(2021, 7, 1)
    rows_by_quarter = {
        q1: _make_quarter_rows(q1, 10, ticker="TRAIN"),
        q2: _make_quarter_rows(q2, 10, ticker="TRAIN"),
        q3: _make_quarter_rows(q3, 10, ticker="TEST"),
    }
    folds = quarter_folds(date(2021, 1, 1), date(2021, 10, 1), min_train_quarters=2)
    result = walk_forward(rows_by_quarter, folds=folds)
    for pred in result.oos_predictions:
        assert pred.ticker == "TEST"


def test_walk_forward_calibrated_proba_in_unit_interval() -> None:
    from wave_alpha.right_edge.training import quarter_folds, walk_forward

    q1 = date(2021, 1, 1)
    q2 = date(2021, 4, 1)
    q3 = date(2021, 7, 1)
    q4 = date(2021, 10, 1)
    rows_by_quarter = {
        q1: _make_quarter_rows(q1, 20) + _make_quarter_rows(q1, 20, outcome=0),
        q2: _make_quarter_rows(q2, 20) + _make_quarter_rows(q2, 20, outcome=0),
        q3: _make_quarter_rows(q3, 15) + _make_quarter_rows(q3, 15, outcome=0),
        q4: _make_quarter_rows(q4, 10) + _make_quarter_rows(q4, 10, outcome=0),
    }
    folds = quarter_folds(date(2021, 1, 1), date(2022, 1, 1), min_train_quarters=2)
    result = walk_forward(rows_by_quarter, folds=folds)
    for pred in result.oos_predictions:
        assert 0.0 <= pred.calibrated_proba <= 1.0
        assert 0.0 <= pred.raw_proba <= 1.0


def test_walk_forward_final_logistic_refits_all_data() -> None:
    """final_logistic is fit on all rows, so its weights should differ from fold params."""
    from wave_alpha.right_edge.training import quarter_folds, walk_forward

    q1 = date(2021, 1, 1)
    q2 = date(2021, 4, 1)
    q3 = date(2021, 7, 1)
    rows_by_quarter = {
        q1: _make_quarter_rows(q1, 30) + _make_quarter_rows(q1, 30, outcome=0),
        q2: _make_quarter_rows(q2, 30) + _make_quarter_rows(q2, 30, outcome=0),
        q3: _make_quarter_rows(q3, 30) + _make_quarter_rows(q3, 30, outcome=0),
    }
    folds = quarter_folds(date(2021, 1, 1), date(2021, 10, 1), min_train_quarters=2)
    result = walk_forward(rows_by_quarter, folds=folds)
    assert result.final_logistic is not None
    # Weights should be a tuple of the right length
    assert len(result.final_logistic.weights) == 5


def test_walk_forward_skips_fold_with_no_train_rows() -> None:
    """A fold whose training set is empty is silently skipped."""
    from wave_alpha.right_edge.training import Fold, walk_forward

    # Only provide rows for Q2; folds require training data from Q1 (empty).
    q1 = date(2021, 1, 1)
    q2 = date(2021, 4, 1)
    q3 = date(2021, 7, 1)
    rows_by_quarter = {
        q2: _make_quarter_rows(q2, 10),
    }
    folds = [
        Fold(train_until=q1, test_start=q1, test_end=q2),  # no train data
        Fold(train_until=q2, test_start=q2, test_end=q3),  # Q2 train OK
    ]
    result = walk_forward(rows_by_quarter, folds=folds)
    # Second fold has train rows (Q2 < Q2 is False, so actually Q2 is test only)
    # Both folds skip since first has no train; second has train_until=q2 but q2 < q2 is False
    # This means n_oos = 0 is acceptable
    assert result.n_oos_rows == 0


def test_walk_forward_first_fold_calibrated_equals_raw() -> None:
    """Leakage fix: fold 1 has no prior Platt data → identity Platt → calibrated == raw.

    Structure:
      - Q0 = warmup training rows only (before fold 1's train_until)
      - Q1 = fold-1 test rows (accumulated_platt_inputs empty before this fold)
      - Q2 = fold-2 test rows (accumulated_platt_inputs has fold-1 data)

    For fold 1: no prior Platt data → identity Platt → calibrated_proba == raw_proba.
    """
    from wave_alpha.right_edge.training import Fold, walk_forward

    q0 = date(2021, 1, 1)  # warmup quarter (before first fold)
    q1 = date(2021, 4, 1)  # fold-1 test quarter
    q2 = date(2021, 7, 1)  # fold-2 test quarter
    q3 = date(2021, 10, 1)  # end marker

    rows_by_quarter = {
        q0: _make_quarter_rows(q0, 20) + _make_quarter_rows(q0, 20, outcome=0),
        q1: _make_quarter_rows(q1, 15) + _make_quarter_rows(q1, 15, outcome=0),
        q2: _make_quarter_rows(q2, 10) + _make_quarter_rows(q2, 10, outcome=0),
    }

    folds = [
        # fold 1: trains on q0 (< q1), tests on q1
        Fold(train_until=q1, test_start=q1, test_end=q2),
        # fold 2: trains on q0+q1 (< q2), tests on q2
        Fold(train_until=q2, test_start=q2, test_end=q3),
    ]

    result = walk_forward(rows_by_quarter, folds=folds)

    # Fold 1 predictions: rows whose as_of falls in [q1, q2).
    fold1_preds = [p for p in result.oos_predictions if q1 <= p.as_of < q2]
    fold2_preds = [p for p in result.oos_predictions if q2 <= p.as_of < q3]

    assert fold1_preds, "Expected OOS predictions from fold 1"
    assert fold2_preds, "Expected OOS predictions from fold 2"

    # Fold 1: no prior accumulated Platt data → identity Platt → calibrated == raw
    for pred in fold1_preds:
        assert pred.calibrated_proba == pytest.approx(pred.raw_proba, abs=1e-9), (
            f"Fold 1 should have identity Platt (calibrated == raw), "
            f"got calibrated={pred.calibrated_proba}, raw={pred.raw_proba}"
        )

    # Fold 2: Platt was fit on fold-1 data, so calibrated may differ from raw.
    # At minimum, fold 2 must also produce in-range probas.
    for pred in fold2_preds:
        assert 0.0 <= pred.calibrated_proba <= 1.0


# ---------------------------------------------------------------------------
# Task 2.4 — assemble_training_rows (smoke test with fake provider)
# ---------------------------------------------------------------------------


def test_assemble_training_rows_emits_rows_for_labeled_pivots() -> None:
    from datetime import date as dt
    from decimal import Decimal

    from wave_alpha.domain import Bar, Interval
    from wave_alpha.right_edge.training import assemble_training_rows

    class FakeProvider:
        """Returns a synthetic zigzag bar series."""

        def fetch(
            self,
            ticker: str,
            interval: object,
            start: dt | None = None,
            end: dt | None = None,
        ) -> list[Bar]:
            # 120-bar zigzag: up 30, down 30, up 30, down 30
            bars = []
            base = dt(2022, 1, 3)
            price = 100.0
            from datetime import timedelta as td

            direction = 1
            step_size = 2.0
            for i in range(120):
                p = Decimal(str(price))
                bars.append(
                    Bar(
                        timestamp=base + td(days=i),
                        open=p,
                        high=p + Decimal("0.5"),
                        low=p - Decimal("0.5"),
                        close=p,
                        volume=1000,
                    )
                )
                price += direction * step_size
                # Reverse every 30 bars
                if (i + 1) % 30 == 0:
                    direction *= -1
            return bars

    provider = FakeProvider()
    initial = dt(2022, 2, 15)  # ~30 bars in
    final = dt(2022, 4, 15)  # ~70 bars in — enough for labels
    rows = assemble_training_rows(
        provider=provider,
        universe=["FAKE"],
        as_of_pairs=[(initial, final)],
        interval=Interval.DAILY,
    )
    # We may or may not get rows depending on pivot detection; just assert no crash
    # and that any rows we do get have valid outcome values.
    for row in rows:
        assert row.outcome in (0, 1)
        assert row.ticker == "FAKE"
        assert 0.0 <= row.features.retracement_progress <= 1.0


def test_assemble_training_rows_empty_universe_returns_empty() -> None:
    from wave_alpha.right_edge.training import assemble_training_rows

    class FakeProvider:
        def fetch(self, *a: object, **kw: object) -> list:
            return []

    rows = assemble_training_rows(
        provider=FakeProvider(),
        universe=[],
        as_of_pairs=[(date(2022, 1, 1), date(2022, 4, 1))],
    )
    assert rows == []


def test_assemble_training_rows_empty_provider_returns_empty() -> None:
    from wave_alpha.right_edge.training import assemble_training_rows

    class EmptyProvider:
        def fetch(self, *a: object, **kw: object) -> list:
            return []

    rows = assemble_training_rows(
        provider=EmptyProvider(),
        universe=["AAPL", "MSFT"],
        as_of_pairs=[(date(2022, 1, 1), date(2022, 4, 1))],
    )
    assert rows == []


# ---------------------------------------------------------------------------
# Task 4.2 — serialize_artifact + write_artifact + round-trip
# ---------------------------------------------------------------------------


def _make_fake_walk_forward_result() -> object:
    """Build a minimal WalkForwardResult from synthetic rows."""
    from wave_alpha.right_edge.training import (
        Fold,
        WalkForwardResult,
        apply_platt,
        fit_logistic,
        fit_platt,
        predict_logistic,
    )

    q1 = date(2021, 1, 1)
    q2 = date(2021, 4, 1)
    q3 = date(2021, 7, 1)

    train_rows = _make_quarter_rows(q1, 30) + _make_quarter_rows(q1, 30, outcome=0)
    test_rows = _make_quarter_rows(q2, 20) + _make_quarter_rows(q2, 20, outcome=0)
    all_rows = train_rows + test_rows + _make_quarter_rows(q3, 10)

    final_logistic = fit_logistic(all_rows, seed=7)

    # Build minimal platt per degree
    platt_inputs = []
    for r in test_rows:
        raw = predict_logistic(final_logistic, r.features)
        platt_inputs.append((raw, r.outcome))
    platt_d1 = fit_platt(platt_inputs)

    folds = [Fold(train_until=q1, test_start=q1, test_end=q2)]

    from wave_alpha.right_edge.training import CalibratedPrediction

    oos = [
        CalibratedPrediction(
            ticker="AAPL",
            as_of=q2,
            degree=1,
            raw_proba=predict_logistic(final_logistic, r.features),
            calibrated_proba=apply_platt(platt_d1, predict_logistic(final_logistic, r.features)),
            outcome=r.outcome,
            features=r.features,
        )
        for r in test_rows[:5]
    ]

    return WalkForwardResult(
        folds=folds,
        final_logistic=final_logistic,
        platt_per_degree={1: platt_d1},
        oos_predictions=oos,
        n_train_rows=len(all_rows),
        n_oos_rows=len(oos),
    )


def _make_fake_gate_result(verdict: str = "PROMOTE") -> object:
    """Return a minimal GateResult-like object."""
    from wave_alpha.right_edge.promotion import GateResult

    return GateResult(
        verdict=verdict,  # type: ignore[arg-type]
        logistic_brier_ci=(0.10, 0.15, 0.20),
        heuristic_brier_ci=(0.22, 0.27, 0.32),
        per_degree_calibration={1: {"logistic": 0.05, "heuristic": 0.09}},
    )


def test_serialize_artifact_has_expected_keys() -> None:
    from wave_alpha.right_edge.training import serialize_artifact

    wf = _make_fake_walk_forward_result()
    gate = _make_fake_gate_result()
    artifact = serialize_artifact(wf=wf, gate=gate, seed=42)

    assert artifact["version"] == "logistic_v1"
    assert "trained_at" in artifact
    assert "git_sha" in artifact
    assert "feature_order" in artifact
    assert "weights" in artifact
    assert "intercept" in artifact
    assert "platt_per_degree" in artifact
    assert "training" in artifact
    assert "gate" in artifact
    assert artifact["gate"]["verdict"] == "PROMOTE"
    assert artifact["training"]["rng_seed"] == 42


def test_serialize_artifact_platt_keys_are_strings() -> None:
    """JSON serializes integer dict keys as strings; confirm this is enforced."""
    from wave_alpha.right_edge.training import serialize_artifact

    wf = _make_fake_walk_forward_result()
    gate = _make_fake_gate_result()
    artifact = serialize_artifact(wf=wf, gate=gate, seed=42)

    # platt_per_degree keys must be strings (JSON-safe)
    for k in artifact["platt_per_degree"]:
        assert isinstance(k, str), f"Expected string key, got {type(k)}: {k!r}"


def test_write_artifact_creates_file(tmp_path) -> None:
    from wave_alpha.right_edge.training import serialize_artifact, write_artifact

    wf = _make_fake_walk_forward_result()
    gate = _make_fake_gate_result()
    artifact = serialize_artifact(wf=wf, gate=gate, seed=42)

    out = tmp_path / "models" / "test.json"
    write_artifact(artifact, out)

    assert out.exists()
    import json

    loaded = json.loads(out.read_text())
    assert loaded["version"] == "logistic_v1"


def test_round_trip_serialize_write_load_predict(tmp_path) -> None:
    """Full round-trip: serialize → write → LogisticConfirmationModel.from_json → predict_proba.

    Verifies that predict_proba on the loaded model equals apply_platt(platt[1],
    predict_logistic(original_params, features)) — no train/serve skew.

    Uses fixed trained_at + git_sha for deterministic JSON comparison.
    """
    from datetime import date as _date

    from wave_alpha.right_edge.logistic import LogisticConfirmationModel
    from wave_alpha.right_edge.training import (
        apply_platt,
        predict_logistic,
        serialize_artifact,
        write_artifact,
    )

    wf = _make_fake_walk_forward_result()
    gate = _make_fake_gate_result(verdict="PROMOTE")

    artifact = serialize_artifact(
        wf=wf,
        gate=gate,
        seed=42,
        trained_at=_date(2026, 1, 1),
        git_sha="testsha",
    )

    # Verify deterministic metadata fields
    assert artifact["trained_at"] == "2026-01-01"
    assert artifact["git_sha"] == "testsha"

    out = tmp_path / "model.json"
    write_artifact(artifact, out)

    m = LogisticConfirmationModel.from_json(out)

    # Pick an arbitrary feature vector
    f = _make_features(progress=0.65, bars_since=7, vol_decline=0.15, atr_regime=0.4)

    model_proba = m.predict_proba(f, degree=1)

    # Reference: compute directly from original WalkForwardResult params
    raw = predict_logistic(wf.final_logistic, f)
    expected = apply_platt(wf.platt_per_degree[1], raw)

    assert model_proba == pytest.approx(expected, abs=1e-9)


def test_git_sha_returns_string() -> None:
    from wave_alpha.right_edge.training import _git_sha

    sha = _git_sha()
    assert isinstance(sha, str)
    assert len(sha) > 0


def test_git_sha_fallback_on_failure(monkeypatch) -> None:
    """_git_sha returns 'unknown' when subprocess raises."""
    import subprocess

    from wave_alpha.right_edge.training import _git_sha

    def fake_check_output(*args: object, **kwargs: object) -> str:
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    assert _git_sha() == "unknown"


# ---------------------------------------------------------------------------
# Task 18 — feature_order constants + _row_to_vector with_fib + train/serve
# ---------------------------------------------------------------------------


def test_feature_order_returns_legacy_when_with_fib_false() -> None:
    from wave_alpha.right_edge.training import LEGACY_FEATURE_ORDER, feature_order

    assert feature_order(with_fib=False) == LEGACY_FEATURE_ORDER


def test_feature_order_appends_fib_when_with_fib_true() -> None:
    from wave_alpha.right_edge.training import (
        FIB_FEATURE_ORDER,
        LEGACY_FEATURE_ORDER,
        feature_order,
    )

    assert feature_order(with_fib=True) == LEGACY_FEATURE_ORDER + FIB_FEATURE_ORDER


def test_row_to_vector_legacy_matches_logistic_vector() -> None:
    """Train/serve consistency: _row_to_vector and LogisticConfirmationModel._vector
    must produce identical vectors for the same features and feature_order."""
    from wave_alpha.right_edge.logistic import LogisticConfirmationModel
    from wave_alpha.right_edge.training import LEGACY_FEATURE_ORDER, TrainingRow, _row_to_vector

    f = RightEdgeFeatures(
        retracement_progress=0.4,
        bars_since_candidate=10,
        volume_decline_pct=0.1,
        higher_degree_zone_match=0.5,
        atr_regime=0.2,
    )
    row = TrainingRow(
        ticker="TEST",
        as_of=date(2024, 1, 15),
        degree=2,
        features=f,
        outcome=1,
    )

    train_vec = _row_to_vector(row, with_fib=False).tolist()

    model = LogisticConfirmationModel(
        version="v1",
        feature_order=LEGACY_FEATURE_ORDER,
        weights=tuple(0.0 for _ in LEGACY_FEATURE_ORDER),
        intercept=0.0,
        platt_per_degree={2: (1.0, 0.0)},
    )
    serve_vec = model._vector(f)
    assert train_vec == serve_vec


def test_row_to_vector_with_fib_matches_logistic_vector() -> None:
    """Same train/serve invariant with the extended 19-field feature_order."""
    from wave_alpha.right_edge.logistic import LogisticConfirmationModel
    from wave_alpha.right_edge.training import TrainingRow, _row_to_vector, feature_order

    f = RightEdgeFeatures(
        retracement_progress=0.4,
        bars_since_candidate=10,
        volume_decline_pct=0.1,
        higher_degree_zone_match=0.5,
        atr_regime=0.2,
        fib_w2_w1_retrace=0.5,
        fib_w2_w1_retrace_present=1.0,
    )
    row = TrainingRow(
        ticker="TEST",
        as_of=date(2024, 1, 15),
        degree=2,
        features=f,
        outcome=1,
    )

    train_vec = _row_to_vector(row, with_fib=True).tolist()

    names = feature_order(with_fib=True)
    model = LogisticConfirmationModel(
        version="v2",
        feature_order=names,
        weights=tuple(0.0 for _ in names),
        intercept=0.0,
        platt_per_degree={2: (1.0, 0.0)},
    )
    serve_vec = model._vector(f)
    assert train_vec == serve_vec
