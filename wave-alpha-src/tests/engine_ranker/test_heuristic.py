"""Verifies HeuristicEngineRanker reproduces enumerator legacy scoring bit-for-bit."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from wave_alpha.coherence.score import CoherenceResult
from wave_alpha.domain import Interval
from wave_alpha.elliott.enumerator import (
    WEIGHT_BASE,
    WEIGHT_CONFLUENCE,
    WEIGHT_RECENCY,
    enumerate_counts,
)
from wave_alpha.engine_ranker.features import EngineRankerFeatures
from wave_alpha.engine_ranker.heuristic import HeuristicEngineRanker
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def _piv(d_off: int, price: str, t: PivotType) -> PivotEvent:
    return PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=date(2024, 1, 1) + timedelta(days=d_off),
        price=Decimal(price),
        type=t,
        state=PivotState.CONFIRMED,
        confirmed_at=date(2024, 1, 1) + timedelta(days=d_off + 1),
    )


def _impulse_pivots() -> list[PivotEvent]:
    prices = ["100", "130", "115", "160", "145", "190"]
    types = [
        PivotType.LOW,
        PivotType.HIGH,
        PivotType.LOW,
        PivotType.HIGH,
        PivotType.LOW,
        PivotType.HIGH,
    ]
    return [_piv(7 * i, p, t) for i, (p, t) in enumerate(zip(prices, types, strict=True))]


def test_heuristic_matches_legacy_score_exactly() -> None:
    """For every count produced by enumerate_counts, HeuristicEngineRanker
    on the same components must return the exact same score (un-cohered raw)."""
    counts = enumerate_counts(_impulse_pivots(), degree=2, top_k=5)
    assert counts
    coh = CoherenceResult(score=0.7, multiplier=1.0, pairwise={}, fallback_used=None)
    h = HeuristicEngineRanker()
    for c in counts:
        f = EngineRankerFeatures.from_count(c, coh)
        produced = h.predict_proba(f)
        expected = max(
            0.0,
            WEIGHT_BASE * c.template_fit_component
            + WEIGHT_CONFLUENCE * c.fib_component
            + WEIGHT_RECENCY * c.recency_component,
        )
        assert produced == pytest.approx(expected, abs=1e-12), (
            f"heuristic ranker diverged from legacy score: got {produced}, expected {expected}"
        )


def test_heuristic_ignores_coherence_in_raw_score() -> None:
    """The heuristic returns the UN-cohered raw; coherence enters via the
    outside multiplier in blend_scores. The two features (coh, degree) must
    not affect the heuristic's output."""
    f1 = EngineRankerFeatures(template_fit=0.9, fib=0.5, recency=0.8, coherence=0.1, degree=2)
    f2 = EngineRankerFeatures(template_fit=0.9, fib=0.5, recency=0.8, coherence=0.9, degree=3)
    h = HeuristicEngineRanker()
    assert h.predict_proba(f1) == h.predict_proba(f2)
