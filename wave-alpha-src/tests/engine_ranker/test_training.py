"""Unit tests for engine_ranker.training (NumPy-based fit, no sklearn)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from wave_alpha.engine_ranker.features import EngineRankerFeatures
from wave_alpha.engine_ranker.training import (
    OutcomeDataset,
    OutcomeDatasetRow,
    _assert_no_train_leakage,
    candidate_to_loadable,
    serialize_artifact,
    train_logistic,
    walk_forward_split,
)


def _row(
    label: int,
    fit: float = 0.5,
    fib: float = 0.5,
    rec: float = 0.5,
    coh: float = 0.5,
    degree: int = 2,
    as_of: date = date(2024, 1, 1),
) -> OutcomeDatasetRow:
    return OutcomeDatasetRow(
        ticker="X",
        as_of=as_of,
        features=EngineRankerFeatures(
            template_fit=fit,
            fib=fib,
            recency=rec,
            coherence=coh,
            degree=degree,
        ),
        label=label,
        degree=degree,
    )


def test_walk_forward_split_endpoints_inclusive_on_train_side() -> None:
    """4 distinct dates, split=0.6 → 2 train dates (cutoff = day-10)."""
    rows = [
        _row(0, as_of=date(2024, 1, 1)),
        _row(0, as_of=date(2024, 1, 1)),
        _row(0, as_of=date(2024, 1, 11)),
        _row(0, as_of=date(2024, 1, 21)),
        _row(0, as_of=date(2024, 1, 21)),
        _row(0, as_of=date(2024, 1, 31)),
    ]
    train, holdout = walk_forward_split(rows, split=0.6)
    train_dates = {r.as_of for r in train}
    holdout_dates = {r.as_of for r in holdout}
    assert train_dates.isdisjoint(holdout_dates)
    assert max(train_dates) < min(holdout_dates)
    assert max(train_dates) == date(2024, 1, 11)


def test_walk_forward_planted_leak_detected() -> None:
    train = [_row(0, as_of=date(2024, 6, 1))]
    with pytest.raises(AssertionError, match="lookahead"):
        _assert_no_train_leakage(train, cutoff=date(2024, 3, 1))


def test_train_logistic_deterministic() -> None:
    """Two trainings on the same synthetic dataset produce identical weights."""
    rows = []
    for q in range(8):
        for _ in range(8):
            rows.append(
                _row(
                    label=q % 2,
                    fit=0.7,
                    fib=0.4,
                    rec=0.6,
                    coh=0.5,
                    degree=2,
                    as_of=date(2022, 3, 31) + timedelta(days=90 * q),
                )
            )
    ds = OutcomeDataset(
        rows=rows,
        skipped_no_features=0,
        skipped_unresolved=0,
        n_target=sum(1 for r in rows if r.label == 1),
        n_non_target=sum(1 for r in rows if r.label == 0),
    )
    m1 = train_logistic(ds, random_state=42)
    m2 = train_logistic(ds, random_state=42)
    assert m1.weights == m2.weights
    assert m1.intercept_per_degree == m2.intercept_per_degree


def test_train_logistic_produces_loadable_artifact(tmp_path: Path) -> None:
    """Train → serialize → load through LogisticEngineRanker.from_json round trip."""
    import json as _json

    from wave_alpha.engine_ranker.logistic import LogisticEngineRanker

    rows = []
    for i in range(60):
        label = 1 if i % 2 == 0 else 0
        rows.append(
            _row(
                label=label,
                fit=0.8 if label else 0.3,
                fib=0.6 if label else 0.4,
                rec=0.7,
                coh=0.5,
                degree=2,
                as_of=date(2023, 1, 1) + timedelta(days=i * 5),
            )
        )
    ds = OutcomeDataset(
        rows=rows,
        skipped_no_features=0,
        skipped_unresolved=0,
        n_target=sum(1 for r in rows if r.label == 1),
        n_non_target=sum(1 for r in rows if r.label == 0),
    )
    candidate = train_logistic(ds)
    payload = serialize_artifact(candidate, gate_verdict="PROMOTE")
    art_path = tmp_path / "art.json"
    art_path.write_text(_json.dumps(payload))
    loaded = LogisticEngineRanker.from_json(art_path)
    assert loaded.feature_order == ("template_fit", "fib", "recency", "coherence")
    assert 2 in loaded.intercept_per_degree


def test_candidate_to_loadable_in_process_no_disk() -> None:
    rows = [
        _row(
            label=i % 2,
            fit=0.7,
            fib=0.4,
            rec=0.6,
            coh=0.5,
            degree=2,
            as_of=date(2023, 1, 1) + timedelta(days=i),
        )
        for i in range(40)
    ]
    ds = OutcomeDataset(
        rows=rows,
        skipped_no_features=0,
        skipped_unresolved=0,
        n_target=sum(1 for r in rows if r.label == 1),
        n_non_target=sum(1 for r in rows if r.label == 0),
    )
    candidate = train_logistic(ds)
    loaded = candidate_to_loadable(candidate)
    # Predict should not raise
    p = loaded.predict_proba(
        EngineRankerFeatures(
            template_fit=0.7,
            fib=0.4,
            recency=0.6,
            coherence=0.5,
            degree=2,
        )
    )
    assert 0.0 <= p <= 1.0
