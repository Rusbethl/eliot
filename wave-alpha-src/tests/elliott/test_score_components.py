"""Verifies enumerate_counts() populates the three component sub-score fields on WaveCount."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from wave_alpha.domain import Interval
from wave_alpha.elliott.enumerator import (
    WEIGHT_BASE,
    WEIGHT_CONFLUENCE,
    WEIGHT_RECENCY,
    enumerate_counts,
)
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


def _seed_impulse_pivots() -> list[PivotEvent]:
    """Six pivots forming a clean upward impulse: L H L H L H."""
    return [
        _piv(0, "100", PivotType.LOW),
        _piv(7, "130", PivotType.HIGH),
        _piv(14, "115", PivotType.LOW),
        _piv(21, "160", PivotType.HIGH),
        _piv(28, "145", PivotType.LOW),
        _piv(35, "190", PivotType.HIGH),
    ]


def test_score_components_populated_on_winning_count() -> None:
    pivots = _seed_impulse_pivots()
    counts = enumerate_counts(pivots, degree=2, top_k=5)
    assert counts, "expected at least one count from a clean impulse fixture"
    top = counts[0]
    assert top.template_fit_component is not None
    assert top.fib_component is not None
    assert top.recency_component is not None
    # template_fit_component is the raw 1 - 0.10*N(soft) value; can dip
    # below zero for counts with > 10 soft violations (the scalar `score`
    # is what's clamped). We assert only the meaningful upper bound here.
    assert top.template_fit_component <= 1.0
    assert 0.0 <= top.fib_component <= 1.0
    assert 0.2 <= top.recency_component <= 1.0


def test_components_recombine_to_legacy_score() -> None:
    """The hand-tuned linear combination of the three components must equal
    `count.score` exactly, so HeuristicEngineRanker (built on the components)
    reproduces legacy behavior bit-identically."""
    pivots = _seed_impulse_pivots()
    counts = enumerate_counts(pivots, degree=2, top_k=5)
    for c in counts:
        # enumerate_counts always populates components; the None-guard is
        # defensive symmetry with the field's float | None typing.
        if c.template_fit_component is None:
            continue
        expected = max(
            0.0,
            WEIGHT_BASE * c.template_fit_component
            + WEIGHT_CONFLUENCE * c.fib_component
            + WEIGHT_RECENCY * c.recency_component,
        )
        assert c.score == pytest.approx(expected, abs=1e-12), (
            f"{c.pattern}: components do not recombine to score; "
            f"got components ({c.template_fit_component}, {c.fib_component}, "
            f"{c.recency_component}) vs score={c.score}"
        )


def test_handbuilt_count_components_default_to_none() -> None:
    """WaveCount instances built outside the enumerator (test fixtures,
    handbuilt golden cases) have None components. Backwards-compat for
    callers that never go through enumerate_counts()."""
    from wave_alpha.elliott.models import WaveCount

    c = WaveCount(pattern="impulse", degree=2, pivots=_seed_impulse_pivots())
    assert c.template_fit_component is None
    assert c.fib_component is None
    assert c.recency_component is None
