from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal, cast

from wave_alpha.backtest.coherence_bucket import bucket_for as _bucket

Direction = Literal["up", "down"]


@dataclass(frozen=True)
class CountEvaluation:
    """One (ticker, as_of) backtest sample for the count layer."""

    ticker: str
    as_of: date
    predicted_direction: Direction
    actual_direction: Direction
    coherence_score: float
    top1_hit: bool
    top3_directions: tuple[Direction, ...]
    top3_hit: bool


@dataclass
class CountLayerResult:
    evaluations: list[CountEvaluation] = field(default_factory=list)
    n_bars_forward: int = 20

    @property
    def sample_size(self) -> int:
        return len(self.evaluations)

    @property
    def top1_hit_rate(self) -> float:
        if not self.evaluations:
            return 0.0
        return sum(1 for e in self.evaluations if e.top1_hit) / len(self.evaluations)

    @property
    def top3_hit_rate(self) -> float:
        if not self.evaluations:
            return 0.0
        return sum(1 for e in self.evaluations if e.top3_hit) / len(self.evaluations)

    def by_coherence_bucket(self) -> dict[str, dict[str, float]]:
        groups: dict[str, list[CountEvaluation]] = {"full": [], "partial": [], "disagreement": []}
        for e in self.evaluations:
            groups[_bucket(e.coherence_score)].append(e)
        return {
            name: {
                "sample_size": len(rows),
                "top1_hit_rate": (sum(1 for r in rows if r.top1_hit) / len(rows)) if rows else 0.0,
                "top3_hit_rate": (sum(1 for r in rows if r.top3_hit) / len(rows)) if rows else 0.0,
            }
            for name, rows in groups.items()
        }

    def by_year(self) -> dict[int, dict[str, float]]:
        groups: dict[int, list[CountEvaluation]] = {}
        for e in self.evaluations:
            groups.setdefault(e.as_of.year, []).append(e)
        return {
            yr: {
                "sample_size": len(rows),
                "top1_hit_rate": sum(1 for r in rows if r.top1_hit) / len(rows),
                "top3_hit_rate": sum(1 for r in rows if r.top3_hit) / len(rows),
            }
            for yr, rows in sorted(groups.items())
        }


@dataclass
class CountLayerEvaluator:
    """Walks (ticker x as_ofs) inlining pivot detection + count enumeration,
    derives top-1 and top-3 predicted directions, compares to actual
    n_bars-forward direction, and accumulates a CountLayerResult.

    Bypasses pipeline.run_analysis entirely — count enumeration is invoked
    directly so the LLM seam is never reached. Backtest is reproducible
    without an API key by construction.
    """

    provider: object  # OHLCVProvider — typed as object to avoid circular import
    n_bars_forward: int = 20

    def evaluate(self, ticker: str, as_ofs: Iterable[date]) -> CountLayerResult:
        # Fetch ALL bars once — the as_of filter is applied per-eval via
        # PointInTimeView. fetch(end=last_as_of + buffer) so forward history
        # is available for label computation.
        from wave_alpha.data.provider import OHLCVProvider
        from wave_alpha.domain import Interval

        as_of_list = list(as_ofs)
        if not as_of_list:
            return CountLayerResult(evaluations=[], n_bars_forward=self.n_bars_forward)

        provider: OHLCVProvider = self.provider  # type: ignore[assignment]

        # Buffer = 2x n_bars_forward to ensure enough forward history.
        end = max(as_of_list) + timedelta(days=self.n_bars_forward * 3)
        daily_all = provider.fetch(ticker, Interval.DAILY, end=end)
        weekly_all = provider.fetch(ticker, Interval.WEEKLY, end=end)
        h4_all = provider.fetch(ticker, Interval.HOURLY_4, end=end)

        result = CountLayerResult(evaluations=[], n_bars_forward=self.n_bars_forward)
        for as_of in as_of_list:
            ev = self._evaluate_one(
                ticker=ticker,
                as_of=as_of,
                daily_all=daily_all,
                weekly_all=weekly_all,
                h4_all=h4_all,
            )
            if ev is not None:
                result.evaluations.append(ev)
        return result

    def _evaluate_one(
        self,
        *,
        ticker: str,
        as_of: date,
        daily_all: list,
        weekly_all: list,
        h4_all: list,
    ) -> CountEvaluation | None:
        from wave_alpha.backtest.labels import forward_direction_label
        from wave_alpha.coherence.score import (
            CoherenceInputs,
            predicted_direction,
            three_way,
        )
        from wave_alpha.data.point_in_time import (
            LookaheadError,
            PointInTimeView,
            UnsortedBarsError,
        )
        from wave_alpha.domain import Interval

        actual = forward_direction_label(daily_all, as_of=as_of, n_bars=self.n_bars_forward)
        if actual is None:
            return None
        try:
            daily_view = PointInTimeView(daily_all, as_of).visible()
            weekly_view = PointInTimeView(weekly_all, as_of).visible() if weekly_all else []
            h4_view = PointInTimeView(h4_all, as_of).visible() if h4_all else []
        except (LookaheadError, UnsortedBarsError):
            raise
        except Exception:
            return None
        if not daily_view:
            return None

        counts_daily = self._counts_for(daily_view, Interval.DAILY)
        if not counts_daily:
            return None
        counts_weekly = self._counts_for(weekly_view, Interval.WEEKLY) if weekly_view else []
        counts_h4 = self._counts_for(h4_view, Interval.HOURLY_4) if h4_view else []

        coh = three_way(
            CoherenceInputs(
                weekly=counts_weekly[0] if counts_weekly else None,
                daily=counts_daily[0] if counts_daily else None,
                hourly4=counts_h4[0] if counts_h4 else None,
            )
        )

        top1_dir = predicted_direction(counts_daily[0])
        if top1_dir is None:
            return None
        top1_hit = top1_dir == actual

        top3 = []
        for c in counts_daily[:3]:
            d = predicted_direction(c)
            if d is not None:
                top3.append(d)
        top3_hit = actual in top3

        return CountEvaluation(
            ticker=ticker,
            as_of=as_of,
            predicted_direction=cast(Direction, top1_dir),
            actual_direction=actual,
            coherence_score=coh.score,
            top1_hit=top1_hit,
            top3_directions=tuple(cast(Direction, d) for d in top3),
            top3_hit=top3_hit,
        )

    def _counts_for(self, bars: list, interval: object) -> list:
        from wave_alpha.elliott.enumerator import enumerate_counts
        from wave_alpha.pipeline import _DEFAULT_ATR_MULTIPLES, _DEFAULT_DEGREE_FOR_COUNT
        from wave_alpha.pivots.multi_degree import detect_multi_degree

        if not bars:
            return []
        by_degree = detect_multi_degree(
            bars,
            interval=interval,  # type: ignore[arg-type]
            atr_multiples=_DEFAULT_ATR_MULTIPLES,
        )
        pivots = by_degree.get(_DEFAULT_DEGREE_FOR_COUNT, [])
        return enumerate_counts(pivots, degree=_DEFAULT_DEGREE_FOR_COUNT, top_k=5)
