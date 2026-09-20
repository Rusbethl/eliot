"""Tests for the outcome-training evaluation harness.

See docs/superpowers/specs/2026-05-11-outcome-training-eval-harness-design.md.
"""

from datetime import date, datetime

import pytest


def test_outcome_eval_config_defaults():
    """OutcomeEvalConfig must have sensible defaults matching the spec."""
    from wave_alpha.right_edge.outcome_eval import OutcomeEvalConfig

    cfg = OutcomeEvalConfig(end=date(2024, 12, 31), universe=["AAPL", "MSFT"])
    assert cfg.quarters == 16
    assert cfg.fold_count == 4
    assert cfg.holdout_fraction == 0.20
    assert cfg.seed == 42
    assert cfg.with_fib is False
    # schedule must default to a WeightSchedule with n_target_per_degree=100
    assert cfg.schedule.n_target_per_degree == 100
    assert cfg.schedule.include_time_stops is True


def test_arm_metrics_construction():
    """ArmMetrics is a frozen dataclass carrying per-degree breakdowns."""
    from wave_alpha.right_edge.outcome_eval import ArmMetrics

    m = ArmMetrics(
        brier_overall=0.18,
        brier_per_degree={2: 0.15, 3: 0.21},
        ece_per_degree={2: 0.04, 3: 0.07},
        log_loss_overall=0.42,
        n_holdout_per_degree={2: 30, 3: 12},
        low_n_degrees=[3],
    )
    assert m.brier_overall == 0.18
    assert m.low_n_degrees == [3]
    # frozen: cannot mutate
    with pytest.raises(AttributeError):
        m.brier_overall = 0.5  # type: ignore[misc]


def test_outcome_eval_result_construction():
    """OutcomeEvalResult bundles config + counts + both arms + delta + verdict."""
    from wave_alpha.right_edge.outcome_eval import (
        ArmMetrics,
        OutcomeEvalConfig,
        OutcomeEvalResult,
    )

    arm = ArmMetrics(
        brier_overall=0.2,
        brier_per_degree={2: 0.2},
        ece_per_degree={2: 0.05},
        log_loss_overall=0.5,
        n_holdout_per_degree={2: 20},
        low_n_degrees=[],
    )
    res = OutcomeEvalResult(
        config=OutcomeEvalConfig(end=date(2024, 12, 31), universe=["AAPL"]),
        n_synth_rows=500,
        n_train_outcomes_per_degree={2: 80},
        n_holdout_outcomes_per_degree={2: 20},
        arm_a=arm,
        arm_b=arm,
        brier_delta_ci=(-0.01, 0.0, 0.01),
        verdict="no_effect",
        generated_at=datetime(2024, 12, 31, 12, 0, 0),
    )
    assert res.verdict == "no_effect"
    assert res.brier_delta_ci == (-0.01, 0.0, 0.01)


def _row(*, ticker: str = "AAPL", as_of_day: int = 1, degree: int = 2, exit_day: int):
    """Build a minimal OutcomeRow for split tests. features can be a stub --
    the split function never touches them."""
    from datetime import date

    from wave_alpha.right_edge.outcome_dataset import OutcomeRow

    return OutcomeRow(
        ticker=ticker,
        as_of=date(2024, 1, as_of_day),
        degree=degree,
        features=object(),  # type: ignore[arg-type]  # stub -- split never touches features
        label=1.0,
        weight=0.0,
        exit_date=date(2024, 1, exit_day),
    )


def test_split_outcomes_by_exit_date_holds_out_most_recent_fraction():
    """holdout = most-recent fraction by exit_date."""
    from wave_alpha.right_edge.outcome_eval import _split_outcomes_temporal

    rows = [_row(exit_day=d) for d in range(1, 11)]  # 10 rows, exit days 1..10
    train, holdout = _split_outcomes_temporal(rows, fraction=0.20)
    # 20% of 10 = 2; most-recent 2 (exit days 9, 10) in holdout
    assert len(train) == 8
    assert len(holdout) == 2
    holdout_exit_days = sorted(r.exit_date.day for r in holdout)
    assert holdout_exit_days == [9, 10]


def test_split_outcomes_ties_at_boundary_go_to_train():
    """When the boundary row has the same exit_date as the next training row,
    all tied rows go to the training side."""
    from datetime import date as _date

    from wave_alpha.right_edge.outcome_dataset import OutcomeRow
    from wave_alpha.right_edge.outcome_eval import _split_outcomes_temporal

    def mk(d):
        return OutcomeRow(
            ticker="AAPL",
            as_of=_date(2024, 1, 1),
            degree=2,
            features=object(),  # type: ignore[arg-type]
            label=1.0,
            weight=0.0,
            exit_date=_date(2024, 1, d),
        )

    # 10 rows; exit days [1,2,3,4,5,5,5,5,5,8]. fraction=0.20 -> 2 holdout.
    # Naive holdout would include one day-5 row; tie-pushing moves all day-5
    # rows to train. Only day 8 ends up in holdout.
    rows = [mk(d) for d in [1, 2, 3, 4, 5, 5, 5, 5, 5, 8]]
    train, holdout = _split_outcomes_temporal(rows, fraction=0.20)
    assert {r.exit_date.day for r in holdout} == {8}
    assert all(r.exit_date.day <= 5 for r in train)
    assert len(train) + len(holdout) == 10


def test_split_outcomes_zero_holdout_raises():
    """fraction x N < 1 must raise -- meaningless report otherwise."""
    from wave_alpha.right_edge.outcome_eval import _split_outcomes_temporal

    rows = [_row(exit_day=1), _row(exit_day=2)]  # N=2, fraction=0.20 -> floor(0.4)=0
    with pytest.raises(ValueError, match="holdout"):
        _split_outcomes_temporal(rows, fraction=0.20)


def test_split_outcomes_empty_raises():
    """Empty input is a hard error."""
    from wave_alpha.right_edge.outcome_eval import _split_outcomes_temporal

    with pytest.raises(ValueError):
        _split_outcomes_temporal([], fraction=0.20)


def test_score_arm_from_predictions_brier_matches_brier_score():
    """ArmMetrics.brier_overall must equal backtest.brier.brier_score on the
    same (proba, label) pairs."""
    from wave_alpha.backtest.brier import brier_score
    from wave_alpha.right_edge.outcome_eval import _score_arm_from_predictions

    # 10 holdout predictions across mixed correct/incorrect.
    preds = [
        (0.9, 1, 2),
        (0.9, 1, 2),
        (0.1, 0, 2),
        (0.1, 0, 2),
        (0.9, 1, 2),
        (0.1, 0, 2),
        (0.9, 0, 2),
        (0.1, 1, 2),
        (0.9, 0, 2),
        (0.1, 1, 2),
    ]  # (calibrated_proba, label, degree)
    metrics = _score_arm_from_predictions(preds, low_n_threshold=5)
    expected = brier_score([(p, lab) for p, lab, _ in preds])
    assert abs(metrics.brier_overall - expected) < 1e-9


def test_score_arm_from_predictions_per_degree_breakdown():
    """brier_per_degree, ece_per_degree, n_holdout_per_degree all keyed by
    degrees present in input."""
    from wave_alpha.right_edge.outcome_eval import _score_arm_from_predictions

    preds = [
        (0.8, 1, 2),
        (0.2, 0, 2),
        (0.6, 1, 2),
        (0.9, 1, 3),
        (0.1, 0, 3),
    ]
    m = _score_arm_from_predictions(preds, low_n_threshold=20)
    assert set(m.brier_per_degree) == {2, 3}
    assert set(m.ece_per_degree) == {2, 3}
    assert m.n_holdout_per_degree == {2: 3, 3: 2}


def test_score_arm_low_n_flag():
    """Degrees with row count < threshold appear in low_n_degrees."""
    from wave_alpha.right_edge.outcome_eval import _score_arm_from_predictions

    preds = [(0.5, 1, 2), (0.5, 0, 2), (0.5, 1, 3)]  # d=2 has 2, d=3 has 1
    m = _score_arm_from_predictions(preds, low_n_threshold=20)
    assert set(m.low_n_degrees) == {2, 3}

    m_strict = _score_arm_from_predictions(preds, low_n_threshold=2)
    assert m_strict.low_n_degrees == [3]


def test_brier_delta_ci_deterministic_seed():
    """Same (preds_a, preds_b, seed) → byte-identical (lo, median, hi)."""
    from wave_alpha.right_edge.outcome_eval import _brier_delta_ci

    preds_a = [(0.9, 1, 2), (0.1, 0, 2), (0.6, 1, 2), (0.4, 0, 2)] * 5
    preds_b = [(0.8, 1, 2), (0.2, 0, 2), (0.7, 1, 2), (0.3, 0, 2)] * 5
    ci1 = _brier_delta_ci(preds_a, preds_b, n_resamples=200, seed=42)
    ci2 = _brier_delta_ci(preds_a, preds_b, n_resamples=200, seed=42)
    assert ci1 == ci2
    assert len(ci1) == 3
    lo, mid, hi = ci1
    assert lo <= mid <= hi


def test_brier_delta_ci_b_better_when_b_more_confident_correct():
    """If B has lower per-row Brier than A on every row, the median delta
    must be negative (B - A < 0 -> B improves)."""
    from wave_alpha.right_edge.outcome_eval import _brier_delta_ci

    preds_a = [(0.6, 1, 2)] * 30  # per-row Brier = (1 - 0.6)^2 = 0.16
    preds_b = [(0.9, 1, 2)] * 30  # per-row Brier = (1 - 0.9)^2 = 0.01
    _lo, mid, hi = _brier_delta_ci(preds_a, preds_b, n_resamples=200, seed=42)
    assert mid < 0
    assert hi < 0  # CI excludes zero — B strictly improves


def test_brier_delta_ci_pair_length_mismatch_raises():
    """preds_a and preds_b must have equal length (paired)."""
    from wave_alpha.right_edge.outcome_eval import _brier_delta_ci

    with pytest.raises(ValueError, match="length"):
        _brier_delta_ci([(0.5, 1, 2)], [(0.5, 1, 2), (0.5, 0, 2)], n_resamples=10, seed=42)


def test_verdict_improves_when_ci_strictly_negative():
    from wave_alpha.right_edge.outcome_eval import _verdict_from_ci

    assert _verdict_from_ci((-0.05, -0.03, -0.01)) == "improves"


def test_verdict_degrades_when_ci_strictly_positive():
    from wave_alpha.right_edge.outcome_eval import _verdict_from_ci

    assert _verdict_from_ci((0.01, 0.03, 0.05)) == "degrades"


def test_verdict_no_effect_when_ci_straddles_zero():
    from wave_alpha.right_edge.outcome_eval import _verdict_from_ci

    assert _verdict_from_ci((-0.02, 0.0, 0.02)) == "no_effect"
    assert _verdict_from_ci((-0.02, -0.01, 0.01)) == "no_effect"
    assert _verdict_from_ci((-0.01, 0.01, 0.02)) == "no_effect"


# ---------------------------------------------------------------------------
# Task 7 — run_outcome_eval end-to-end determinism test
# ---------------------------------------------------------------------------


def _make_zigzag_bars(start: date, prices: list[float]) -> list:
    """Build bars from pivot prices with 10-bar linear interpolation segments."""
    from datetime import timedelta
    from decimal import Decimal

    from wave_alpha.domain import Bar

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


class _FakeBarProvider:
    """Minimal OHLCVProvider returning canned bars filtered by end date."""

    def __init__(self, bars_by_ticker: dict):
        self._bars = bars_by_ticker

    def fetch(self, ticker: str, interval, *, end: date) -> list:
        bars = self._bars.get(ticker, [])
        return [b for b in bars if b.timestamp <= end]


def _build_history_db(db_path, *, count_id: str, pattern: str, last_pivot) -> None:
    """Insert 6 resolved degree-2 snapshots with distinct exit_dates into db_path."""
    from datetime import UTC, datetime, timedelta
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

    as_of_dates = [
        date(2023, 4, 1),
        date(2023, 4, 15),
        date(2023, 5, 1),
        date(2023, 5, 15),
        date(2023, 6, 1),
        date(2023, 6, 15),
    ]
    statuses = ["target", "stop", "target", "stop", "target", "stop"]

    for as_of, status in zip(as_of_dates, statuses, strict=True):
        snap = Snapshot(
            ticker="AAPL",
            as_of=as_of,
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
                entry_price=Decimal("200"),
                stop_price=Decimal("180"),
                target_price=Decimal("230"),
                rr=3.0,
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
            created_at=datetime(as_of.year, as_of.month, as_of.day, 16, 0, tzinfo=UTC),
            outcome=SnapshotOutcome(
                status=status,
                exit_date=as_of + timedelta(days=10),
                exit_price=(Decimal("230") if status == "target" else Decimal("180")),
                bars_held=10,
                realized_R=(2.0 if status == "target" else -1.0),
                last_observed_date=as_of + timedelta(days=10),
                bars_elapsed=10,
                unrealized_R=0.0,
                mfe_R=0.0,
                mae_R=0.0,
                resolved_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        )
        store.upsert(snap, db_path=db_path)


def test_run_outcome_eval_deterministic_end_to_end(tmp_path) -> None:
    """Calling run_outcome_eval twice with the same inputs yields an identical
    OutcomeEvalResult on all fields except generated_at.

    Exercises the real walk_forward, build_outcome_dataset, and
    train_blended_final production code paths — no mocks of internals.
    """
    from wave_alpha.data.point_in_time import PointInTimeView
    from wave_alpha.domain import Interval
    from wave_alpha.elliott.enumerator import enumerate_counts
    from wave_alpha.pipeline import _DEFAULT_ATR_MULTIPLES
    from wave_alpha.pivots.multi_degree import detect_multi_degree_with_snapshots
    from wave_alpha.right_edge.features import RightEdgeFeatures
    from wave_alpha.right_edge.outcome_dataset import WeightSchedule
    from wave_alpha.right_edge.outcome_eval import OutcomeEvalConfig, run_outcome_eval
    from wave_alpha.right_edge.training import TrainingRow, quarter_folds
    from wave_alpha.trades.derive import _count_id

    # --- Build a bar series that produces deterministic Elliott counts ---
    # Classic zigzag that produces 5 counts at degree 2 from Q1 2023 onwards.
    pivot_prices = [100, 80, 140, 110, 160, 130, 200, 170, 230]
    bars = _make_zigzag_bars(date(2023, 1, 1), pivot_prices)

    # Extract the real count_id at an as_of date within the bar series.
    as_of_ref = date(2023, 4, 1)
    pit = PointInTimeView(bars, as_of_ref).visible()
    snaps = detect_multi_degree_with_snapshots(
        pit, interval=Interval.DAILY, atr_multiples=_DEFAULT_ATR_MULTIPLES
    )
    snap_deg2 = snaps[2]
    counts = enumerate_counts(snap_deg2.pivots, degree=2, top_k=5)
    assert counts, "fixture bars must produce at least one count at degree=2"
    top_count = counts[0]
    real_count_id = _count_id(top_count)
    real_pattern = top_count.pattern

    # --- History DB with 6 resolved degree-2 snapshots ---
    db_path = tmp_path / "h.sqlite"
    _build_history_db(
        db_path,
        count_id=real_count_id,
        pattern=real_pattern,
        last_pivot=top_count.pivots[-1],
    )

    # --- Synthetic rows_by_quarter (degree=2 so n_synth_per_degree[2] > 0) ---
    # Two quarters of mixed training rows; enough to train a logistic.
    def _feats(progress: float = 0.5) -> RightEdgeFeatures:
        return RightEdgeFeatures(
            retracement_progress=progress,
            bars_since_candidate=5,
            volume_decline_pct=0.0,
            higher_degree_zone_match=0.0,
            atr_regime=1.0,
        )

    q1 = date(2021, 1, 1)
    q2 = date(2021, 4, 1)
    q3 = date(2021, 7, 1)
    q4 = date(2021, 10, 1)
    rows_by_quarter = {
        q1: [TrainingRow(ticker="S", as_of=q1, degree=2, features=_feats(0.7), outcome=1)] * 20
        + [TrainingRow(ticker="S", as_of=q1, degree=2, features=_feats(0.3), outcome=0)] * 20,
        q2: [TrainingRow(ticker="S", as_of=q2, degree=2, features=_feats(0.7), outcome=1)] * 20
        + [TrainingRow(ticker="S", as_of=q2, degree=2, features=_feats(0.3), outcome=0)] * 20,
        q3: [TrainingRow(ticker="S", as_of=q3, degree=2, features=_feats(0.7), outcome=1)] * 15
        + [TrainingRow(ticker="S", as_of=q3, degree=2, features=_feats(0.3), outcome=0)] * 15,
        q4: [TrainingRow(ticker="S", as_of=q4, degree=2, features=_feats(0.7), outcome=1)] * 10
        + [TrainingRow(ticker="S", as_of=q4, degree=2, features=_feats(0.3), outcome=0)] * 10,
    }
    folds = quarter_folds(date(2021, 1, 1), date(2022, 1, 1), min_train_quarters=2)

    # --- Bar provider used by build_outcome_dataset ---
    provider = _FakeBarProvider({"AAPL": bars})

    # --- Config: holdout_fraction=0.50 so 3 of 6 rows go to holdout ---
    cfg = OutcomeEvalConfig(
        end=date(2023, 12, 31),
        universe=["AAPL"],
        holdout_fraction=0.50,
        seed=42,
        schedule=WeightSchedule(n_target_per_degree=50),
    )

    # --- Run twice — results must be identical except generated_at ---
    result1 = run_outcome_eval(
        cfg,
        rows_by_quarter=rows_by_quarter,
        folds=folds,
        bar_provider=provider,
        history_db_path=db_path,
    )
    result2 = run_outcome_eval(
        cfg,
        rows_by_quarter=rows_by_quarter,
        folds=folds,
        bar_provider=provider,
        history_db_path=db_path,
    )

    assert result1.verdict == result2.verdict
    assert result1.brier_delta_ci == result2.brier_delta_ci
    assert result1.arm_a == result2.arm_a
    assert result1.arm_b == result2.arm_b
    assert result1.n_synth_rows == result2.n_synth_rows
    assert result1.n_train_outcomes_per_degree == result2.n_train_outcomes_per_degree
    assert result1.n_holdout_outcomes_per_degree == result2.n_holdout_outcomes_per_degree

    # Sanity: the function actually produced a non-trivial result.
    assert result1.n_synth_rows == 130  # 20+20+20+20+15+15+10+10
    assert sum(result1.n_train_outcomes_per_degree.values()) >= 1
    assert sum(result1.n_holdout_outcomes_per_degree.values()) >= 1
    assert result1.verdict in ("improves", "no_effect", "degrades")


def test_format_markdown_report_includes_required_sections():
    """Report must contain: config block, row counts, arm A metrics,
    arm B metrics, delta CI, verdict line."""
    from datetime import date, datetime

    from wave_alpha.right_edge.outcome_eval import (
        ArmMetrics,
        OutcomeEvalConfig,
        OutcomeEvalResult,
        format_markdown_report,
    )

    arm = ArmMetrics(
        brier_overall=0.18,
        brier_per_degree={2: 0.15, 3: 0.21},
        ece_per_degree={2: 0.04, 3: 0.07},
        log_loss_overall=0.42,
        n_holdout_per_degree={2: 30, 3: 12},
        low_n_degrees=[3],
    )
    res = OutcomeEvalResult(
        config=OutcomeEvalConfig(end=date(2024, 12, 31), universe=["AAPL", "MSFT"]),
        n_synth_rows=500,
        n_train_outcomes_per_degree={2: 80, 3: 15},
        n_holdout_outcomes_per_degree={2: 30, 3: 12},
        arm_a=arm,
        arm_b=arm,
        brier_delta_ci=(-0.02, -0.01, 0.005),
        verdict="no_effect",
        generated_at=datetime(2024, 12, 31, 12, 0, 0),
    )
    md = format_markdown_report(res)
    assert "# Outcome-Training Evaluation" in md
    assert "## Verdict" in md
    assert "no_effect" in md.lower()
    assert "Arm A (synthetic-only)" in md
    assert "Arm B (synthetic + outcomes)" in md
    assert "## Δ Brier (B - A)" in md or "Delta Brier" in md
    assert "2024-12-31" in md  # end date
    assert "low_n" in md.lower()  # the [3] low_n flag must surface
