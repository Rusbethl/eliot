"""Unit tests for EngineRankerFeatures and its from_count extraction."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from wave_alpha.coherence.score import CoherenceResult
from wave_alpha.domain import Interval
from wave_alpha.elliott.models import WaveCount
from wave_alpha.engine_ranker.features import EngineRankerFeatures
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def _two_pivots() -> list[PivotEvent]:
    return [
        PivotEvent(
            interval=Interval.DAILY,
            degree=2,
            timestamp=date(2024, 1, 1),
            price=Decimal("100"),
            type=PivotType.LOW,
            state=PivotState.CONFIRMED,
        ),
        PivotEvent(
            interval=Interval.DAILY,
            degree=2,
            timestamp=date(2024, 1, 8),
            price=Decimal("120"),
            type=PivotType.HIGH,
            state=PivotState.CONFIRMED,
        ),
    ]


def test_features_from_count_reads_components_and_degree() -> None:
    count = WaveCount(
        pattern="impulse",
        degree=3,
        pivots=_two_pivots(),
        score=0.7,
        template_fit_component=0.9,
        fib_component=0.5,
        recency_component=0.8,
    )
    coh = CoherenceResult(score=0.6, multiplier=1.2, pairwise={}, fallback_used=None)

    f = EngineRankerFeatures.from_count(count, coh)

    assert f.template_fit == pytest.approx(0.9)
    assert f.fib == pytest.approx(0.5)
    assert f.recency == pytest.approx(0.8)
    assert f.coherence == pytest.approx(0.6)
    assert f.degree == 3


def test_features_from_count_raises_when_components_missing() -> None:
    """A WaveCount that was never scored has None components. The feature
    extractor must refuse rather than silently using zeros — that would
    poison the logistic with phantom rows."""
    count = WaveCount(pattern="impulse", degree=2, pivots=_two_pivots())
    coh = CoherenceResult(score=0.5, multiplier=1.0, pairwise={}, fallback_used=None)

    with pytest.raises(ValueError, match="components"):
        EngineRankerFeatures.from_count(count, coh)


def test_features_from_count_handles_none_coherence() -> None:
    """If coherence is None (e.g. single-timeframe path), use 0.5 as a neutral
    prior — equivalent to 'no signal'."""
    count = WaveCount(
        pattern="impulse",
        degree=2,
        pivots=_two_pivots(),
        score=0.5,
        template_fit_component=0.8,
        fib_component=0.4,
        recency_component=0.6,
    )
    f = EngineRankerFeatures.from_count(count, None)
    assert f.coherence == pytest.approx(0.5)
