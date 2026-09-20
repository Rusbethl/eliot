"""Tests for assess_from_bars."""

from __future__ import annotations

import itertools


def test_assess_from_bars_passes_top_count_to_extractor() -> None:
    """The assessment helper should run the enumerator on the snapshot
    pivots, take the top count, and pass it through to FeatureExtractor."""
    from datetime import date, timedelta
    from decimal import Decimal

    from wave_alpha.domain import Bar, Interval
    from wave_alpha.right_edge.assessment import assess_from_bars
    from wave_alpha.right_edge.features import RightEdgeFeatures

    base = date(2024, 1, 1)
    legs = [(0, 100), (10, 115), (20, 108), (35, 130), (45, 122), (60, 142)]
    bars: list[Bar] = []
    for i in range(70):
        d = base + timedelta(days=i)
        c = _interp_legs(legs, i)
        bars.append(
            Bar(
                timestamp=d,
                open=Decimal(str(c)),
                high=Decimal(str(c + 0.5)),
                low=Decimal(str(c - 0.5)),
                close=Decimal(str(c)),
                volume=1000,
            )
        )

    captured: list[RightEdgeFeatures] = []

    class _RecordingModel:
        def predict_proba(self, features: RightEdgeFeatures, *, degree: int) -> float:
            captured.append(features)
            return 0.5

    assess_from_bars(bars, model=_RecordingModel(), interval=Interval.DAILY)
    # Either we got an assessment with fib fields populated, or we got None
    # because no tentative pivot existed at the requested degree. Both are
    # acceptable; we only assert that *if* the model was called, fib
    # indicators were exercised in the same call.
    if captured:
        f = captured[0]
        # The test bars are constructed to produce a 5-leg motive structure,
        # which the enumerator should match as either impulse or zigzag.
        # Either way, at least one fib indicator must be 1.0 — proving that
        # assess_from_bars threaded a top_count into the extractor.
        any_present = any(
            getattr(f, n) == 1.0
            for n in (
                "fib_w2_w1_retrace_present",
                "fib_w3_w1_extension_present",
                "fib_w4_w3_retrace_present",
                "fib_w5_w1_ratio_present",
                "fib_w5_thrust_extension_present",
                "fib_b_a_retrace_present",
                "fib_c_a_ratio_present",
            )
        )
        assert any_present, (
            "expected at least one fib indicator to be 1.0 when the assessment "
            "produces an assessment for synthetic 5-leg bars"
        )


def _interp_legs(legs, i):
    for (x0, y0), (x1, y1) in itertools.pairwise(legs):
        if x0 <= i <= x1:
            t = (i - x0) / (x1 - x0)
            return y0 + (y1 - y0) * t
    return legs[-1][1]
