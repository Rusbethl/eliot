"""Pipeline integration: engine_ranker wired into _rank.

Two important guarantees:

1. Heuristic-path final_score reproduces the legacy formula bit-identically.
2. Logistic-path produces populated engine_prob on RankedCount and uses
   the new alpha * engine_prob + (1-alpha) * llm_prob formula.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval
from wave_alpha.engine_ranker.heuristic import HeuristicEngineRanker
from wave_alpha.engine_ranker.logistic import LogisticEngineRanker
from wave_alpha.pipeline import AnalyzeRequest, RankedCount, run_analysis


def _crafted_impulse_bars(start_date: date) -> list[Bar]:
    """Hand-crafted synthetic daily series with clear Elliott pivots.

    Reuses the pattern from tests/test_pipeline.py — proven to produce
    non-trivial counts at multiple degrees.
    """
    segments = [
        (30, 100, 130),  # W1 up
        (15, 130, 115),  # W2 down
        (40, 115, 175),  # W3 up (extends)
        (15, 175, 158),  # W4 down
        (30, 158, 195),  # W5 up
        (25, 195, 158),  # WA down
        (15, 158, 175),  # WB up
        (30, 175, 140),  # WC down
    ]
    closes: list[float] = []
    for nbars, a, b in segments:
        for i in range(nbars):
            t = i / max(1, nbars - 1) if nbars > 1 else 1.0
            closes.append(a + t * (b - a))
    bars: list[Bar] = []
    for i, c in enumerate(closes):
        cd = Decimal(f"{c:.4f}")
        rng = Decimal(f"{c * 0.003:.4f}")
        bars.append(
            Bar(
                timestamp=start_date + timedelta(days=i),
                open=cd,
                high=cd + rng,
                low=cd - rng,
                close=cd,
                volume=1_000_000,
            )
        )
    return bars


class _SyntheticImpulseProvider(OHLCVProvider):
    """Provider returning the designed-to-pivot synthetic series for any ticker."""

    def __init__(self) -> None:
        self._daily = _crafted_impulse_bars(start_date=date(2024, 6, 1))

    def fetch(
        self,
        ticker: str,
        interval: Interval,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        bars = self._daily
        if interval is Interval.WEEKLY:
            bars = self._daily[::5]
        if end:
            bars = [b for b in bars if b.timestamp <= end]
        if start:
            bars = [b for b in bars if b.timestamp >= start]
        return bars


@pytest.fixture
def synthetic_provider() -> _SyntheticImpulseProvider:
    return _SyntheticImpulseProvider()


def test_heuristic_path_final_score_matches_legacy_formula(synthetic_provider) -> None:
    """When engine_ranker is the heuristic, final_score = coh * engine_score
    (LLM disabled in this run). engine_prob must be None."""
    req = AnalyzeRequest(ticker="AAPL", as_of=date(2024, 12, 31))
    heuristic = HeuristicEngineRanker()
    result = run_analysis(req, provider=synthetic_provider, engine_ranker=heuristic)
    assert result.coherence is not None
    coh_mult = result.coherence.multiplier
    for rc in result.ranked_daily:
        assert isinstance(rc, RankedCount)
        assert rc.engine_prob is None, "heuristic path must leave engine_prob unset"
        # llm_prob is None in this run, so legacy collapse = coh * engine_score
        assert rc.final_score == pytest.approx(coh_mult * rc.engine_score, abs=1e-9)


def test_logistic_path_populates_engine_prob_and_uses_new_formula(
    synthetic_provider, tmp_path
) -> None:
    """When engine_ranker is logistic, engine_prob must be set and
    final_score = engine_prob (LLM disabled, so alpha collapses to engine_prob)."""
    artifact = {
        "version": "v_test",
        "feature_order": ["template_fit", "fib", "recency", "coherence"],
        "weights": [1.0, 1.0, 1.0, 1.0],
        "intercept_per_degree": {"1": 0.0, "2": 0.0, "3": 0.0},
        "gate": {"verdict": "PROMOTE"},
        "metadata": {},
    }
    art_path = tmp_path / "engine_ranker_logistic_v1.json"
    art_path.write_text(json.dumps(artifact))

    model = LogisticEngineRanker.from_json(art_path)
    req = AnalyzeRequest(ticker="AAPL", as_of=date(2024, 12, 31))
    result = run_analysis(req, provider=synthetic_provider, engine_ranker=model)
    assert result.ranked_daily, "expected some ranked counts on synthetic"
    for rc in result.ranked_daily:
        assert rc.engine_prob is not None
        assert 0.0 < rc.engine_prob <= 1.0
        # LLM disabled, so logistic-path final = engine_prob
        assert rc.final_score == pytest.approx(rc.engine_prob, abs=1e-9)
