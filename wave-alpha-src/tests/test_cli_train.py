"""End-to-end smoke test for `wave-alpha train right-edge`."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from wave_alpha.cli.app import app
from wave_alpha.domain import Bar, Interval

runner = CliRunner()


class _SyntheticProvider:
    """Fake OHLCV provider that emits a trending bar series over 2+ years.

    Returns the same series for every ticker so the training pipeline has
    real pivot structure to label without touching the network.
    """

    def fetch(self, ticker: str, interval: Interval, *, end: date) -> list[Bar]:
        bars: list[Bar] = []
        d = date(2021, 1, 1)
        i = 0
        # zig-zag: rise for 20 bars, fall for 20 bars, repeat
        while d <= date(2022, 9, 30):
            if d.weekday() < 5:
                phase = (i // 20) % 2
                price = 100.0 + (i % 20) * 2.0 if phase == 0 else 140.0 - (i % 20) * 2.0
                bars.append(
                    Bar(
                        timestamp=d,
                        open=Decimal(str(round(price - 0.5, 2))),
                        high=Decimal(str(round(price + 1.0, 2))),
                        low=Decimal(str(round(price - 1.0, 2))),
                        close=Decimal(str(round(price, 2))),
                        volume=10_000,
                    )
                )
                i += 1
            d = d + timedelta(days=1)
        return bars


def test_train_right_edge_help() -> None:
    res = runner.invoke(app, ["train", "--help"])
    assert res.exit_code == 0
    assert "right-edge" in res.stdout


def test_train_right_edge_runs_end_to_end(tmp_path: Path, monkeypatch) -> None:
    """Full pipeline: assemble rows → walk-forward → gate → print verdict.

    After the warmup-window fix, as_of_pairs spans ALL quarters from start to end
    (not just fold test windows), so training data includes warmup quarters.
    This means n_train_rows > n_oos_rows when there are ≥5 quarters in the range.
    """
    universe_file = tmp_path / "universe.txt"
    universe_file.write_text("AAPL\nMSFT\n# comment line\n\n")

    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_default_provider", lambda: _SyntheticProvider())

    res = runner.invoke(
        app,
        [
            "train",
            "right-edge",
            "--universe",
            str(universe_file),
            "--start",
            "2021-01-01",
            "--end",
            "2022-07-01",
            "--seed",
            "42",
        ],
    )
    assert res.exit_code == 0, res.stdout
    assert "Verdict:" in res.stdout

    # Parse "  → N train rows, M OOS predictions" line to verify warmup rows are present.
    # Training data spans ALL quarters (including warmup), OOS only the test folds.
    import re

    m = re.search(r"(\d+) train rows, (\d+) OOS", res.stdout)
    if m:
        n_train = int(m.group(1))
        n_oos = int(m.group(2))
        assert n_train > n_oos, (
            f"Expected n_train_rows ({n_train}) > n_oos_rows ({n_oos}); "
            "warmup quarters should contribute training rows that are not OOS."
        )


def test_train_right_edge_no_folds_exits_1(tmp_path: Path, monkeypatch) -> None:
    """Too short a date range → no folds → exit 1."""
    universe_file = tmp_path / "universe.txt"
    universe_file.write_text("AAPL\n")

    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_default_provider", lambda: _SyntheticProvider())

    res = runner.invoke(
        app,
        [
            "train",
            "right-edge",
            "--universe",
            str(universe_file),
            "--start",
            "2021-01-01",
            "--end",
            "2021-06-01",  # < 4 full quarters → empty folds
        ],
    )
    assert res.exit_code == 1


def test_train_right_edge_write_without_promote_exits_1(tmp_path: Path, monkeypatch) -> None:
    """--write without --force on a non-PROMOTE verdict should exit 1."""
    universe_file = tmp_path / "universe.txt"
    universe_file.write_text("AAPL\n")
    output_path = tmp_path / "model.json"

    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_default_provider", lambda: _SyntheticProvider())

    # Patch evaluate_promotion to always return HOLD so we can test the write gate
    # without relying on the actual verdict from a small synthetic dataset.
    import wave_alpha.cli.app as _cli_app
    from wave_alpha.right_edge.promotion import GateResult

    def _fake_evaluate(**kwargs):
        return GateResult(
            verdict="HOLD",
            logistic_brier_ci=(0.2, 0.25, 0.3),
            heuristic_brier_ci=(0.18, 0.22, 0.28),
            per_degree_calibration={},
        )

    monkeypatch.setattr(_cli_app, "_evaluate_promotion", _fake_evaluate)

    res = runner.invoke(
        app,
        [
            "train",
            "right-edge",
            "--universe",
            str(universe_file),
            "--start",
            "2021-01-01",
            "--end",
            "2022-07-01",
            "--write",
            "--output",
            str(output_path),
        ],
    )
    assert res.exit_code == 1
    assert not output_path.exists()


def test_train_right_edge_write_force_writes_anyway(tmp_path: Path, monkeypatch) -> None:
    """--write --force on a HOLD verdict writes the artifact."""
    universe_file = tmp_path / "universe.txt"
    universe_file.write_text("AAPL\n")
    output_path = tmp_path / "model.json"

    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_default_provider", lambda: _SyntheticProvider())

    import wave_alpha.cli.app as _cli_app
    from wave_alpha.right_edge.promotion import GateResult

    def _fake_evaluate(**kwargs):
        return GateResult(
            verdict="HOLD",
            logistic_brier_ci=(0.2, 0.25, 0.3),
            heuristic_brier_ci=(0.18, 0.22, 0.28),
            per_degree_calibration={},
        )

    monkeypatch.setattr(_cli_app, "_evaluate_promotion", _fake_evaluate)

    res = runner.invoke(
        app,
        [
            "train",
            "right-edge",
            "--universe",
            str(universe_file),
            "--start",
            "2021-01-01",
            "--end",
            "2022-07-01",
            "--write",
            "--force",
            "--output",
            str(output_path),
        ],
    )
    assert res.exit_code == 0, res.stdout
    assert output_path.exists()


# ---------------------------------------------------------------------------
# Phase 3 — train calibrated-heuristic CLI smoke tests
# ---------------------------------------------------------------------------


def test_train_calibrated_heuristic_help() -> None:
    """calibrated-heuristic subcommand is registered and shows in train --help."""
    res = runner.invoke(app, ["train", "--help"])
    assert res.exit_code == 0
    assert "calibrated-heuristic" in res.stdout


def test_train_calibrated_heuristic_runs_end_to_end(tmp_path: Path, monkeypatch) -> None:
    """Full calibrated-heuristic pipeline: rows → fit Platt → gate → print verdict."""
    universe_file = tmp_path / "universe.txt"
    universe_file.write_text("AAPL\nMSFT\n# comment line\n\n")

    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_default_provider", lambda: _SyntheticProvider())

    res = runner.invoke(
        app,
        [
            "train",
            "calibrated-heuristic",
            "--universe",
            str(universe_file),
            "--start",
            "2021-01-01",
            "--end",
            "2022-07-01",
            "--seed",
            "42",
        ],
    )
    assert res.exit_code == 0, res.stdout
    assert "Verdict:" in res.stdout
    # Output uses "Calibrated-heuristic" vs "Raw heuristic" labels (not "Logistic").
    assert "Calibrated-heuristic" in res.stdout
    assert "Raw heuristic" in res.stdout


def test_train_calibrated_heuristic_write_without_promote_exits_1(
    tmp_path: Path, monkeypatch
) -> None:
    """--write without --force on a non-PROMOTE verdict should exit 1."""
    universe_file = tmp_path / "universe.txt"
    universe_file.write_text("AAPL\n")
    output_path = tmp_path / "cal_model.json"

    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_default_provider", lambda: _SyntheticProvider())

    import wave_alpha.cli.app as _cli_app
    from wave_alpha.right_edge.promotion import GateResult

    def _fake_evaluate(**kwargs):
        return GateResult(
            verdict="HOLD",
            logistic_brier_ci=(0.2, 0.25, 0.3),
            heuristic_brier_ci=(0.18, 0.22, 0.28),
            per_degree_calibration={},
        )

    monkeypatch.setattr(_cli_app, "_evaluate_promotion", _fake_evaluate)

    res = runner.invoke(
        app,
        [
            "train",
            "calibrated-heuristic",
            "--universe",
            str(universe_file),
            "--start",
            "2021-01-01",
            "--end",
            "2022-07-01",
            "--write",
            "--output",
            str(output_path),
        ],
    )
    assert res.exit_code == 1
    assert not output_path.exists()


def test_train_calibrated_heuristic_write_force_writes_anyway(tmp_path: Path, monkeypatch) -> None:
    """--write --force on a HOLD verdict writes the artifact."""
    universe_file = tmp_path / "universe.txt"
    universe_file.write_text("AAPL\n")
    output_path = tmp_path / "cal_model.json"

    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_default_provider", lambda: _SyntheticProvider())

    import wave_alpha.cli.app as _cli_app
    from wave_alpha.right_edge.promotion import GateResult

    def _fake_evaluate(**kwargs):
        return GateResult(
            verdict="HOLD",
            logistic_brier_ci=(0.2, 0.25, 0.3),
            heuristic_brier_ci=(0.18, 0.22, 0.28),
            per_degree_calibration={},
        )

    monkeypatch.setattr(_cli_app, "_evaluate_promotion", _fake_evaluate)

    res = runner.invoke(
        app,
        [
            "train",
            "calibrated-heuristic",
            "--universe",
            str(universe_file),
            "--start",
            "2021-01-01",
            "--end",
            "2022-07-01",
            "--write",
            "--force",
            "--output",
            str(output_path),
        ],
    )
    assert res.exit_code == 0, res.stdout
    assert output_path.exists()

    # Verify the artifact has the expected structure.
    import json

    artifact = json.loads(output_path.read_text())
    assert artifact["version"] == "calibrated_heuristic_v1"
    assert "platt_per_degree" in artifact
    assert "gate" in artifact
    assert artifact["gate"]["verdict"] == "HOLD"


def test_train_calibrated_heuristic_write_promote_calibration_writes_artifact(
    tmp_path: Path, monkeypatch
) -> None:
    """--write on a PROMOTE_CALIBRATION verdict writes the artifact without --force."""
    universe_file = tmp_path / "universe.txt"
    universe_file.write_text("AAPL\n")
    output_path = tmp_path / "cal_model.json"

    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_default_provider", lambda: _SyntheticProvider())

    import wave_alpha.cli.app as _cli_app
    from wave_alpha.right_edge.promotion import GateResult

    def _fake_evaluate(**kwargs):
        return GateResult(
            verdict="PROMOTE_CALIBRATION",
            logistic_brier_ci=(0.2, 0.25, 0.3),
            heuristic_brier_ci=(0.22, 0.26, 0.32),
            per_degree_calibration={1: {"logistic": 0.05, "heuristic": 0.18}},
        )

    monkeypatch.setattr(_cli_app, "_evaluate_promotion", _fake_evaluate)

    res = runner.invoke(
        app,
        [
            "train",
            "calibrated-heuristic",
            "--universe",
            str(universe_file),
            "--start",
            "2021-01-01",
            "--end",
            "2022-07-01",
            "--write",
            "--output",
            str(output_path),
        ],
    )
    assert res.exit_code == 0, res.stdout
    assert output_path.exists()

    import json

    artifact = json.loads(output_path.read_text())
    assert artifact["gate"]["verdict"] == "PROMOTE_CALIBRATION"


# ---------------------------------------------------------------------------
# Task 9 — train outcome-eval CLI smoke test
# ---------------------------------------------------------------------------


def _make_zigzag_bars_cli(start: date, prices: list[float]) -> list[Bar]:
    """Build bars from pivot prices with 10-bar linear interpolation segments."""
    out: list[Bar] = []
    day = start
    for i in range(len(prices) - 1):
        p0, p1 = prices[i], prices[i + 1]
        n = 10
        for j in range(n):
            p = p0 + (p1 - p0) * j / n
            price = Decimal(str(round(p, 2)))
            out.append(
                Bar(
                    timestamp=day,
                    open=price,
                    high=price + Decimal("0.5"),
                    low=price - Decimal("0.5"),
                    close=price,
                    volume=1_000_000,
                )
            )
            day += timedelta(days=1)
    return out


def _build_history_db_cli(
    db_path: Path,
    *,
    count_id: str,
    pattern: str,
    last_pivot,
    as_of: date,
) -> None:
    """Insert 6 resolved degree-2 snapshots with distinct exit_dates into db_path.

    All snapshots share the same as_of date so build_outcome_dataset runs
    enumeration on the same bars and derives the same count_id.  Distinct
    exit_dates are achieved via different ticker names to avoid primary-key
    collisions in the DB while still exercising the temporal split.

    Actually: the store.upsert deduplication key is (ticker, as_of) — so we
    use 6 distinct tickers (AAPL through AAPL6) to insert 6 rows.
    """
    from datetime import UTC, datetime
    from decimal import Decimal

    from wave_alpha.history import store
    from wave_alpha.history.models import (
        Snapshot,
        SnapshotCoherence,
        SnapshotCount,
        SnapshotOutcome,
        SnapshotPivot,
        SnapshotSignal,
    )

    statuses = ["target", "stop", "target", "stop", "target", "stop"]
    # Different exit offsets → distinct exit_date values for temporal split.
    exit_offsets = [10, 15, 20, 25, 30, 35]

    for i, (status, offset) in enumerate(zip(statuses, exit_offsets, strict=True)):
        # Use the same ticker "AAPL" but different as_of offsets so
        # we don't hit the (ticker, as_of) uniqueness constraint.
        row_as_of = as_of + timedelta(days=i)
        snap = Snapshot(
            ticker="AAPL",
            as_of=row_as_of,
            engine_version="0.9.0",
            top_3=[
                SnapshotCount(
                    candidate_id="C1",
                    pattern=pattern,
                    degree=2,
                    last_pivot_label="5",
                    pivots=[
                        SnapshotPivot(
                            timestamp=last_pivot.timestamp,
                            price=last_pivot.price,
                        )
                    ],
                    engine_score=0.7,
                    llm_prob=None,
                    final_score=0.7,
                )
            ],
            signal=SnapshotSignal(
                direction="long",
                entry_price=Decimal("130"),
                stop_price=Decimal("100"),
                target_price=Decimal("160"),
                rr=1.0,
                count_id=count_id,
                count_pattern=pattern,
                wave_label="5",
                degree=2,
                confidence=0.6,
                expected_bars_to_target=10,
                max_holding_bars=20,
            ),
            coherence=SnapshotCoherence(score=0.7, multiplier=1.0, pairwise={}, fallback_used=None),
            top_count_hash="h",
            created_at=datetime(row_as_of.year, row_as_of.month, row_as_of.day, 16, 0, tzinfo=UTC),
            outcome=SnapshotOutcome(
                status=status,
                exit_date=row_as_of + timedelta(days=offset),
                exit_price=(Decimal("160") if status == "target" else Decimal("100")),
                bars_held=offset,
                realized_R=(2.0 if status == "target" else -1.0),
                last_observed_date=row_as_of + timedelta(days=offset),
                bars_elapsed=offset,
                unrealized_R=0.0,
                mfe_R=0.0,
                mae_R=0.0,
                resolved_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        )
        store.upsert(snap, db_path=db_path)


def test_train_outcome_eval_runs_end_to_end(tmp_path: Path, monkeypatch) -> None:
    """Full outcome-eval pipeline: build DB + rows → run_outcome_eval → write report.

    Uses the _SyntheticProvider for both training-row assembly and outcome feature
    recomputation.  The history DB entries' count_id is derived from the same
    _SyntheticProvider bars at the same as_of date stored in the DB, so
    build_outcome_dataset can match the count and extract features.
    """
    from wave_alpha.cli import app as app_module
    from wave_alpha.data.point_in_time import PointInTimeView
    from wave_alpha.domain import Interval
    from wave_alpha.elliott.enumerator import enumerate_counts
    from wave_alpha.pipeline import _DEFAULT_ATR_MULTIPLES
    from wave_alpha.pivots.multi_degree import detect_multi_degree_with_snapshots
    from wave_alpha.trades.derive import _count_id

    # The history DB entries will use this as_of date.
    # It must be within the _SyntheticProvider's bar range (2021-01-01..2022-09-30).
    db_as_of = date(2022, 1, 15)

    # Fetch bars from the SyntheticProvider to derive a stable count_id.
    synth_provider = _SyntheticProvider()
    synth_bars = synth_provider.fetch("AAPL", Interval.DAILY, end=db_as_of)
    assert synth_bars, "SyntheticProvider must return bars for 2022-01-15"

    pit = PointInTimeView(synth_bars, db_as_of).visible()
    snaps = detect_multi_degree_with_snapshots(
        pit, interval=Interval.DAILY, atr_multiples=_DEFAULT_ATR_MULTIPLES
    )
    snap_deg2 = snaps[2]
    counts = enumerate_counts(snap_deg2.pivots, degree=2, top_k=5)
    assert counts, "SyntheticProvider bars must produce at least one degree-2 count"
    top_count = counts[0]
    real_count_id = _count_id(top_count)
    real_pattern = top_count.pattern

    # --- History DB with 6 resolved degree-2 snapshots ---
    # Snapshots at db_as_of, db_as_of+1, ..., db_as_of+5 so build_outcome_dataset
    # can re-derive the count_id from the same synthetic bars.
    fake_history_dir = tmp_path / "history_dir"
    fake_history_dir.mkdir()
    db_path = fake_history_dir / "history.sqlite"
    _build_history_db_cli(
        db_path,
        count_id=real_count_id,
        pattern=real_pattern,
        last_pivot=top_count.pivots[-1],
        as_of=db_as_of,
    )

    # --- Use _SyntheticProvider for training-row assembly AND outcome recompute ---
    monkeypatch.setattr(app_module, "_default_provider", lambda: _SyntheticProvider())
    monkeypatch.setattr(app_module, "_DEFAULT_DATA_DIR", fake_history_dir)

    # --- Build universe file ---
    universe_file = tmp_path / "universe.txt"
    universe_file.write_text("AAPL\n# comment\n\n")

    # --- Output path ---
    out_path = tmp_path / "outcome_eval_report.md"

    res = runner.invoke(
        app,
        [
            "train",
            "outcome-eval",
            "--end",
            "2022-07-01",
            "--start",
            "2021-01-01",
            "--universe",
            str(universe_file),
            "--holdout-fraction",
            "0.50",
            "--seed",
            "42",
            "--out",
            str(out_path),
        ],
    )
    assert res.exit_code == 0, res.stdout + ((res.exception and str(res.exception)) or "")
    assert out_path.exists(), "Report file must be written"
    report_text = out_path.read_text()
    assert "## Verdict" in report_text, "Report must contain verdict section"
    # At least one of the three possible verdict literals must appear
    assert any(v in report_text for v in ("improves", "no_effect", "degrades")), (
        f"No verdict literal found in report:\n{report_text[:500]}"
    )
