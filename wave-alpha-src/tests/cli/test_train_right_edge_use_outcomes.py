"""Smoke test for `wave-alpha train right-edge --use-outcomes`."""

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from wave_alpha.cli.app import app
from wave_alpha.right_edge.outcome_dataset import OutcomeDataset, WeightSchedule


@pytest.fixture
def stub_walk_forward(monkeypatch: pytest.MonkeyPatch):
    """Replace the heavy walk_forward + universe loader with a stub returning
    a minimal WalkForwardResult so the CLI test stays fast."""
    from wave_alpha.right_edge.training import (
        Fold,
        LogisticParams,
        PlattParams,
        WalkForwardResult,
    )

    fake_logistic = LogisticParams(
        feature_order=(
            "retracement_progress",
            "bars_since_candidate",
            "volume_decline_pct",
            "higher_degree_zone_match",
            "atr_regime",
        ),
        weights=(0.0, 0.0, 0.0, 0.0, 0.0),
        intercept=0.0,
    )
    fake_platt = {1: PlattParams(a=1.0, b=0.0)}
    fake_wf = WalkForwardResult(
        folds=[
            Fold(
                train_until=date(2025, 1, 1), test_start=date(2025, 1, 1), test_end=date(2025, 4, 1)
            )
        ],
        final_logistic=fake_logistic,
        platt_per_degree=fake_platt,
        oos_predictions=[],
        n_train_rows=10,
        n_oos_rows=0,
    )
    fake_loader = MagicMock(
        return_value=(date(2024, 1, 1), date(2025, 1, 1), [], {date(2024, 1, 1): []})
    )
    monkeypatch.setattr("wave_alpha.cli.app._load_rows_by_quarter", fake_loader)
    monkeypatch.setattr(
        "wave_alpha.right_edge.training.walk_forward",
        MagicMock(return_value=fake_wf),
    )
    monkeypatch.setattr(
        "wave_alpha.right_edge.training.train_blended_final",
        MagicMock(return_value=(fake_logistic, fake_platt)),
    )
    return fake_wf


def test_use_outcomes_flag_is_accepted(tmp_path: Path, stub_walk_forward) -> None:
    out_path = tmp_path / "out.json"
    runner = CliRunner()
    with patch(
        "wave_alpha.right_edge.outcome_dataset.build_outcome_dataset",
        return_value=OutcomeDataset(
            rows=[],
            skipped_drift=0,
            skipped_no_bars=0,
            skipped_wrong_degree=0,
            schedule=WeightSchedule(),
        ),
    ) as stub_build:
        res = runner.invoke(
            app,
            [
                "train",
                "right-edge",
                "--start",
                "2024-01-01",
                "--end",
                "2025-01-01",
                "--use-outcomes",
                "--output",
                str(out_path),
            ],
        )
    assert res.exit_code == 0, res.output
    assert stub_build.called
    assert "outcome" in res.output.lower()


def test_use_outcomes_writes_metadata_into_artifact(tmp_path: Path, stub_walk_forward) -> None:
    import json as _json

    out_path = tmp_path / "out.json"
    runner = CliRunner()
    ds = OutcomeDataset(
        rows=[],
        skipped_drift=2,
        skipped_no_bars=1,
        skipped_wrong_degree=0,
        schedule=WeightSchedule(),
    )
    with patch(
        "wave_alpha.right_edge.outcome_dataset.build_outcome_dataset",
        return_value=ds,
    ):
        res = runner.invoke(
            app,
            [
                "train",
                "right-edge",
                "--start",
                "2024-01-01",
                "--end",
                "2025-01-01",
                "--use-outcomes",
                "--force",
                "--write",
                "--output",
                str(out_path),
            ],
        )
    assert out_path.exists(), res.output
    artifact = _json.loads(out_path.read_text())
    assert "outcome_training" in artifact
    assert artifact["outcome_training"]["skipped_drift"] == 2
    assert artifact["outcome_training"]["skipped_no_bars"] == 1
