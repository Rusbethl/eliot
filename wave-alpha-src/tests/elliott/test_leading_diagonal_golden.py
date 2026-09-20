"""Golden tests for leading_diagonal classification.

Each fixture in ``tests/elliott/fixtures/leading_diagonal_golden/*.json``
is loaded and run through ``run_analysis`` (or, for synthetic fixtures,
a builder function in this module). The test asserts the expected
top-of-top-K pattern at degree 2.

Add real-chart fixtures by dropping a JSON file in the fixtures dir
and filling in the ``bars`` array. Spec §5.2 requires >= 5 fixtures
(>= 3 positives + 2 negatives) before merge; analyst contributes
incrementally.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from wave_alpha.domain import Bar, Interval
from wave_alpha.pipeline import AnalyzeRequest, run_analysis

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "leading_diagonal_golden"


def _bars_synthetic_ld_positive() -> list[Bar]:
    """Reuse the same bar builder as the pipeline integration test.

    The helper produces a degree-2 LD that ties in score with
    contracting_triangle (both at 0.978). LD is present in degree-2
    top-K but is not guaranteed to be rank-0 — the fixture uses
    ``leading_diagonal_anywhere`` accordingly.
    """
    from tests.test_pipeline import _build_bars_for_leading_diagonal_at_degree_2

    return _build_bars_for_leading_diagonal_at_degree_2()


def _bars_synthetic_ed_negative() -> list[Bar]:
    """Shallow-W2 5-wave window where LD does not appear at degree 2.

    The flat-interpolation bars produce a zigzag + wxy at degree 2 —
    LD is absent entirely, so it trivially is not #1. The fixture uses
    ``primitive`` (any non-LD pattern) as the expected value.
    """
    pivot_days_prices = [
        (0, "100"),
        (10, "120"),
        (20, "112.4"),
        (30, "128"),
        (40, "118"),
        (50, "132"),
    ]
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
    "synthetic_ld_positive": _bars_synthetic_ld_positive,
    "synthetic_ed_negative": _bars_synthetic_ed_negative,
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
def test_leading_diagonal_golden(fixture_path: Path):
    fx = json.loads(fixture_path.read_text())
    bars = _load_bars_from_fixture(fx)
    as_of = date.fromisoformat(fx["as_of"])

    class _FakeProvider:
        def fetch(self, ticker, interval, *, end):
            return list(bars)

    req = AnalyzeRequest(ticker=fx["ticker"], as_of=as_of)
    # leading_diagonal is opt-in (see TemplatesConfig + spec §10);
    # golden fixtures specifically exercise LD presence/absence so we
    # enable it for the duration of the test.
    result = run_analysis(
        req,
        provider=_FakeProvider(),
        enabled_opt_in_templates=frozenset({"leading_diagonal"}),
    )

    # Sort by score descending so rank-0 is the highest-scoring candidate.
    # counts_daily pools all degrees without a global sort, so we sort here.
    degree_2_counts = sorted(
        [c for c in result.counts_daily if c.degree == 2],
        key=lambda c: c.score,
        reverse=True,
    )
    if not degree_2_counts:
        pytest.fail(f"{fx['name']}: no degree-2 candidates produced")
    top = degree_2_counts[0]
    patterns_in_topk = {c.pattern for c in degree_2_counts}

    expected = fx["expected_top_pattern_at_degree_2"]
    if expected == "leading_diagonal":
        assert top.pattern == "leading_diagonal", (
            f"{fx['name']}: expected LD at top of degree-2; got {top.pattern}"
        )
    elif expected == "leading_diagonal_anywhere":
        # LD need not be rank-0; assert it appears somewhere in degree-2 top-K.
        assert "leading_diagonal" in patterns_in_topk, (
            f"{fx['name']}: expected leading_diagonal somewhere in degree-2 top-K; "
            f"got patterns: {patterns_in_topk}"
        )
    elif expected in {"ending_diagonal", "impulse"}:
        assert top.pattern != "leading_diagonal", (
            f"{fx['name']}: LD must NOT be #1 (expected {expected}); got {top.pattern}"
        )
    elif expected == "primitive":
        assert top.pattern != "leading_diagonal", (
            f"{fx['name']}: LD must NOT be #1 - primitive negative violated; got {top.pattern}"
        )
    else:
        pytest.fail(f"{fx['name']}: unrecognized expected: {expected}")

    runner_up = fx.get("expected_runner_up_in_topk")
    if runner_up is not None:
        assert runner_up in patterns_in_topk, (
            f"{fx['name']}: expected {runner_up} in top-K; got {patterns_in_topk}"
        )
