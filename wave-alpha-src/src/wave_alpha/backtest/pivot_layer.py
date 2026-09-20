from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from wave_alpha.backtest.brier import brier_score as _brier
from wave_alpha.backtest.brier import calibration_error as _calibration
from wave_alpha.backtest.labels import pivot_confirmation_label
from wave_alpha.data.point_in_time import PointInTimeView
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Interval
from wave_alpha.pipeline import _DEFAULT_ATR_MULTIPLES
from wave_alpha.pivots.models import PivotState
from wave_alpha.pivots.multi_degree import detect_multi_degree, detect_multi_degree_with_snapshots
from wave_alpha.right_edge.extractor import FeatureExtractor
from wave_alpha.right_edge.loader import ConfirmationModel, HeuristicAdapter

Outcome = Literal["confirmed", "invalidated"]


@dataclass(frozen=True)
class PivotEvaluation:
    ticker: str
    as_of_initial: str
    as_of_final: str
    degree: int
    outcome: Outcome
    predicted_proba: float

    @property
    def actual(self) -> int:
        return 1 if self.outcome == "confirmed" else 0


@dataclass
class PivotLayerResult:
    evaluations: list[PivotEvaluation] = field(default_factory=list)

    @property
    def sample_size(self) -> int:
        return len(self.evaluations)

    def confirmation_rate_per_degree(self) -> dict[int, float]:
        groups: dict[int, list[PivotEvaluation]] = {}
        for e in self.evaluations:
            groups.setdefault(e.degree, []).append(e)
        return {d: sum(e.actual for e in rows) / len(rows) for d, rows in sorted(groups.items())}

    def brier_score(self) -> float:
        if not self.evaluations:
            return 0.0
        return _brier([(e.predicted_proba, e.actual) for e in self.evaluations])

    def calibration_error(self, *, n_bins: int = 10) -> float:
        if not self.evaluations:
            return 0.0
        return _calibration(
            [(e.predicted_proba, e.actual) for e in self.evaluations], n_bins=n_bins
        )


@dataclass
class PivotLayerEvaluator:
    """Compares a pivot snapshot at `initial` to a later snapshot at `final`.

    Each TENTATIVE pivot in the initial snapshot becomes one PivotEvaluation:
    its predicted_proba comes from the right-edge model (default: heuristic via
    HeuristicAdapter) using real point-in-time features computed by
    FeatureExtractor, and its realized outcome is determined by
    pivot_confirmation_label against the final snapshot.
    This is the basis for per-degree confirmation rate and Brier score.

    Pass a ``LogisticConfirmationModel`` loaded from config to use the trained
    model instead of the heuristic at CLI / web call sites.
    """

    provider: OHLCVProvider
    lookahead_bars: int = 20
    interval: Interval = Interval.DAILY
    model: ConfirmationModel = field(default_factory=lambda: HeuristicAdapter())

    def evaluate(self, ticker: str, *, initial: date, final: date) -> PivotLayerResult:
        if final <= initial:
            raise ValueError(f"initial {initial} must be before final {final}")
        # Fetch full series; clamp via PointInTimeView per snapshot.
        bars = self.provider.fetch(
            ticker, self.interval, end=final + timedelta(days=self.lookahead_bars * 2)
        )
        if not bars:
            return PivotLayerResult(evaluations=[])
        initial_view = PointInTimeView(bars, initial).visible()
        final_view = PointInTimeView(bars, final).visible()
        if not initial_view or not final_view:
            return PivotLayerResult(evaluations=[])

        initial_snapshots = detect_multi_degree_with_snapshots(
            initial_view, interval=self.interval, atr_multiples=_DEFAULT_ATR_MULTIPLES
        )
        final_pivots_by_degree = detect_multi_degree(
            final_view, interval=self.interval, atr_multiples=_DEFAULT_ATR_MULTIPLES
        )

        extractor = FeatureExtractor()
        evaluations: list[PivotEvaluation] = []

        for degree, snap in initial_snapshots.items():
            tentative = [p for p in snap.pivots if p.state == PivotState.TENTATIVE]
            later = final_pivots_by_degree.get(degree, [])
            for p in tentative:
                outcome = pivot_confirmation_label(p, later_pivots=later)
                if outcome is None:
                    continue
                if snap.atr_at_last_bar is None or snap.required_move <= 0:
                    continue
                features = extractor.extract(
                    candidate=p,
                    point_in_time_bars=initial_view,
                    atr_at_candidate=snap.atr_at_last_bar,
                    required_move=snap.required_move,
                    higher_degree_zone_match=0.0,  # not yet wired; same for training and inference
                )
                proba = self.model.predict_proba(features, degree=degree)
                evaluations.append(
                    PivotEvaluation(
                        ticker=ticker,
                        as_of_initial=initial.isoformat(),
                        as_of_final=final.isoformat(),
                        degree=degree,
                        outcome=outcome,
                        predicted_proba=proba,
                    )
                )

        return PivotLayerResult(evaluations=evaluations)
