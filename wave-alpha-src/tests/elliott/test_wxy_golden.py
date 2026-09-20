"""Golden tests for W-X-Y classification.

Each fixture in ``tests/elliott/fixtures/wxy_golden/*.json`` is loaded and
run through ``run_analysis``. Synthetic fixtures use builder functions in
this module via the magic string ``_use_helper_in_test_module_`` for the
bars field; real-chart fixtures use a full bars array.

Add real-chart fixtures by dropping a JSON file in the fixtures dir and
filling in the ``bars`` array. Spec §6.2 requires >= 5 fixtures
(positive + negative) before merge; analyst contributes incrementally.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from wave_alpha.domain import Bar, Interval
from wave_alpha.pipeline import AnalyzeRequest, run_analysis

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "wxy_golden"


def _bars_synthetic_positive() -> list[Bar]:
    """Reuses the same construction as
    test_pipeline._build_bars_for_wxy_at_degree_2."""
    from tests.test_pipeline import _build_bars_for_wxy_at_degree_2

    return _build_bars_for_wxy_at_degree_2()


def _bars_synthetic_positive_strict() -> list[Bar]:
    """Same bar series as synthetic_positive — used by the strict fixture
    which asserts wxy appears anywhere in degree-2 candidates (not necessarily
    at rank 0)."""
    from tests.test_pipeline import _build_bars_for_wxy_at_degree_2

    return _build_bars_for_wxy_at_degree_2()


def _bars_synthetic_negative_zigzag() -> list[Bar]:
    """Single zigzag at degree 2: 100 -> 80 -> 90 -> 75. Three segments,
    not enough to trigger a wxy (which needs 3 segments at parent + at
    least 4 sub-pivots in W and Y slots)."""
    pivot_days_prices = [(0, "100"), (10, "80"), (20, "90"), (30, "75")]
    bars: list[Bar] = []
    for i in range(len(pivot_days_prices) - 1):
        d0, p0 = pivot_days_prices[i]
        d1, p1 = pivot_days_prices[i + 1]
        span = d1 - d0
        for k in range(span):
            day = d0 + k
            t = k / span
            price = Decimal(p0) + (Decimal(p1) - Decimal(p0)) * Decimal(t)
            bars.append(
                Bar(
                    timestamp=date(2026, 1, 1) + timedelta(days=day),
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=1000,
                    interval=Interval.DAILY,
                )
            )
    final_day, final_price = pivot_days_prices[-1]
    bars.append(
        Bar(
            timestamp=date(2026, 1, 1) + timedelta(days=final_day),
            open=Decimal(final_price),
            high=Decimal(final_price),
            low=Decimal(final_price),
            close=Decimal(final_price),
            volume=1000,
            interval=Interval.DAILY,
        )
    )
    return bars


_SYNTHETIC_BUILDERS = {
    "synthetic_positive": _bars_synthetic_positive,
    "synthetic_positive_strict": _bars_synthetic_positive_strict,
    "synthetic_negative_zigzag": _bars_synthetic_negative_zigzag,
}


def _load_bars_from_fixture(fx: dict) -> list[Bar]:
    if fx["bars"] == "_use_helper_in_test_module_":
        builder = _SYNTHETIC_BUILDERS[fx["name"]]
        return builder()
    out: list[Bar] = []
    for b in fx["bars"]:
        out.append(
            Bar(
                timestamp=date.fromisoformat(b["timestamp"]),
                open=Decimal(b["open"]),
                high=Decimal(b["high"]),
                low=Decimal(b["low"]),
                close=Decimal(b["close"]),
                volume=int(b["volume"]),
                interval=Interval(b["interval"]),
            )
        )
    return out


def _fixture_files() -> list[Path]:
    return sorted(_FIXTURE_DIR.glob("*.json"))


@pytest.mark.parametrize("fixture_path", _fixture_files(), ids=lambda p: p.stem)
def test_wxy_golden(fixture_path: Path):
    fx = json.loads(fixture_path.read_text())
    bars = _load_bars_from_fixture(fx)
    as_of = date.fromisoformat(fx["as_of"])

    class _FakeProvider:
        def fetch(self, ticker, interval, *, end):
            return list(bars)

    req = AnalyzeRequest(ticker=fx["ticker"], as_of=as_of)
    result = run_analysis(req, provider=_FakeProvider())

    degree_2_counts = [c for c in result.counts_daily if c.degree == 2]
    if not degree_2_counts:
        pytest.fail(f"{fx['name']}: no degree-2 candidates produced")
    top = degree_2_counts[0]

    expected = fx["expected_top_pattern_at_degree_2"]
    if expected == "wxy":
        assert top.pattern == "wxy", (
            f"{fx['name']}: expected wxy at top of degree-2; got {top.pattern}"
        )
        for label, sub_pat in fx.get("expected_subcounts", {}).items():
            sub = top.sub_counts.get(label)
            assert sub is not None, f"{fx['name']}: sub_counts['{label}'] is None"
            assert sub.pattern == sub_pat, (
                f"{fx['name']}: expected {label}={sub_pat}, got {sub.pattern}"
            )
    elif expected == "wxy_anywhere":
        # wxy need not be rank-0; assert it appears somewhere in degree-2 candidates
        wxy_in_d2 = [c for c in degree_2_counts if c.pattern == "wxy"]
        assert wxy_in_d2, (
            f"{fx['name']}: expected wxy somewhere in degree-2 candidates; "
            f"got patterns: {[c.pattern for c in degree_2_counts]}"
        )
        wxy = wxy_in_d2[0]
        for label, sub_pat in fx.get("expected_subcounts", {}).items():
            sub = wxy.sub_counts.get(label)
            assert sub is not None, f"{fx['name']}: sub_counts['{label}'] is None"
            assert sub.pattern == sub_pat, (
                f"{fx['name']}: expected {label}={sub_pat}, got {sub.pattern}"
            )
    elif expected == "primitive":
        assert top.pattern != "wxy", (
            f"{fx['name']}: wxy must NOT appear at top of degree-2 - "
            f"primitive negative case violated"
        )
    else:
        pytest.fail(f"{fx['name']}: unrecognized expected: {expected}")
