from datetime import date, timedelta
from datetime import datetime as _datetime
from decimal import Decimal

from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval
from wave_alpha.llm.cache import LLMResponseCache
from wave_alpha.llm.client import LLMRequest as _LLMRequest
from wave_alpha.llm.modes import LLMMode
from wave_alpha.llm.runner import LLMRunner
from wave_alpha.pipeline import AnalyzeRequest, RankedCount, run_analysis
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def _crafted_impulse_bars(start_date: date) -> list[Bar]:
    """Hand-crafted ~250-bar daily series with clear pivots.

    Eight segments form an Elliott-flavored 5-3 sequence at large amplitude
    so the multi-degree ZigZag detector at degree 2 (ATR mult 2) finds pivots
    at every segment turn. Intra-bar range is kept small (~0.3%) so ATR
    doesn't swamp close-to-close moves.
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
        rng = Decimal(f"{c * 0.003:.4f}")  # tight intra-bar so ATR stays small
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
    """Provider returning a designed-to-pivot synthetic series for any ticker.

    ~200 bars of hand-crafted Elliott-flavored daily action — enough for ATR(14)
    to warm up and the multi-degree ZigZag to produce non-trivial pivots at the
    primary (degree=2) ATR multiplier.
    """

    def __init__(self) -> None:
        # 200-day daily walk ending close to 2024-12-31
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
            # rebuild as weekly by taking every 5th daily bar
            bars = self._daily[::5]
        # 4h reuses the daily walk to exercise the three-interval pipeline path
        if end:
            bars = [b for b in bars if b.timestamp <= end]
        if start:
            bars = [b for b in bars if b.timestamp >= start]
        return bars


def test_run_analysis_returns_structured_result() -> None:
    provider = _SyntheticImpulseProvider()
    result = run_analysis(
        AnalyzeRequest(ticker="AAPL", as_of=date(2024, 12, 31)),
        provider=provider,
    )
    assert result.ticker == "AAPL"
    assert result.as_of == date(2024, 12, 31)
    assert result.coherence is not None


def test_run_analysis_produces_at_least_one_count_on_synthetic_impulse() -> None:
    """Regression for the broken-fixture bug: the synthetic impulse walk must
    produce at least one rule-compliant count. Without this assertion, the
    pipeline could silently degrade to empty output and the prior end-to-end
    test would still pass on `[]`."""
    provider = _SyntheticImpulseProvider()
    result = run_analysis(
        AnalyzeRequest(ticker="AAPL", as_of=date(2024, 12, 31)),
        provider=provider,
    )
    total = len(result.counts_weekly) + len(result.counts_daily) + len(result.counts_4h)
    assert total > 0, (
        "pipeline produced no counts at any interval — "
        f"weekly={len(result.counts_weekly)}, "
        f"daily={len(result.counts_daily)}, "
        f"4h={len(result.counts_4h)}"
    )


def test_run_analysis_disabled_mode_produces_engine_only_ranked_counts() -> None:
    provider = _SyntheticImpulseProvider()
    req = AnalyzeRequest(ticker="AAPL", as_of=date(2024, 12, 31))
    result = run_analysis(req, provider=provider, llm_mode=LLMMode.DISABLED)
    assert all(isinstance(rc, RankedCount) for rc in result.ranked_daily)
    if result.ranked_daily:
        rc = result.ranked_daily[0]
        assert rc.llm_prob is None
        # final = coh_multiplier * engine_score when llm_prob is None
        expected = result.coherence.multiplier * rc.engine_score
        assert abs(rc.final_score - expected) < 1e-9


class _StubLLMClient:
    model = "claude-haiku-4-5-20251001"

    def __init__(self, response: str) -> None:
        self.response = response

    def run(self, req: _LLMRequest) -> str:
        return self.response


def test_run_analysis_live_mode_uses_llm_probabilities(tmp_path) -> None:
    # Stub returns a probability vector that boosts C2 dominantly over all engine candidates.
    # Multi-degree enumeration (degrees 1, 2, 3 * top_k=2) can produce up to 6 candidates;
    # all are listed so no unknown-id parse error and probs sum to 1.0.  With alpha=0
    # final = coh * llm_prob, so C2 (0.92) beats every other candidate's engine score.
    raw = (
        '{"candidates": ['
        '{"id": "C1", "probability": 0.016, "narrative": "low"},'
        '{"id": "C2", "probability": 0.920, "narrative": "boosted"},'
        '{"id": "C3", "probability": 0.016, "narrative": "mid"},'
        '{"id": "C4", "probability": 0.016, "narrative": "weak"},'
        '{"id": "C5", "probability": 0.016, "narrative": "weakest"},'
        '{"id": "C6", "probability": 0.016, "narrative": "extra"}],'
        '"engine_missed": false, "engine_missed_reason": null}'
    )
    client = _StubLLMClient(response=raw)
    cache = LLMResponseCache(db_path=tmp_path / "llm.sqlite")
    runner = LLMRunner(
        client=client, cache=cache, mode=LLMMode.LIVE, now=lambda: _datetime(2024, 1, 1)
    )

    provider = _SyntheticImpulseProvider()
    req = AnalyzeRequest(ticker="AAPL", as_of=date(2024, 12, 31))
    result = run_analysis(
        req, provider=provider, llm_mode=LLMMode.LIVE, llm_runner=runner, alpha=0.0
    )
    # alpha=0 -> final_score driven entirely by llm_prob (x coh)
    if len(result.ranked_daily) >= 2:
        top = result.ranked_daily[0]
        assert top.candidate_id == "C2"
        assert top.llm_prob == 0.920


def test_pipeline_skips_bad_atr_candidate_keeps_others(monkeypatch) -> None:
    """One ranked candidate raising ValueError from derive_signal must not
    drop the others in `ranked[:3]`. Pins the per-candidate try/except
    boundary in pipeline.run_analysis."""
    from wave_alpha import pipeline as pipeline_mod
    from wave_alpha.trades.models import TradeRules, TradeSignal

    provider = _SyntheticImpulseProvider()

    call_count = {"n": 0}
    real_derive = pipeline_mod.derive_signal

    def flaky_derive(**kwargs):
        call_count["n"] += 1
        # First candidate raises; remaining candidates use the real path.
        if call_count["n"] == 1:
            raise ValueError("ATR unavailable: synthetic test failure")
        return real_derive(**kwargs)

    monkeypatch.setattr(pipeline_mod, "derive_signal", flaky_derive)

    result = run_analysis(
        AnalyzeRequest(
            ticker="AAPL",
            as_of=date(2024, 12, 31),
            rules=TradeRules(stop_source="atr_multiple"),
        ),
        provider=provider,
    )

    # The first candidate's ValueError was swallowed; subsequent candidates
    # ran. We don't assert an exact signal count (depends on count
    # acceptance), only that we did NOT crash and call_count > 1
    # (i.e. iteration continued past the raising candidate).
    assert call_count["n"] >= 2
    # All produced signals (if any) must be valid TradeSignal instances.
    assert all(isinstance(s, TradeSignal) for s in result.signals)
    # The skip counter increments for the one raising candidate.
    assert result.n_atr_skipped == 1


def test_run_analysis_n_atr_skipped_zero_when_no_skips() -> None:
    """The skip counter stays at 0 for normal runs (default rules use
    count_invalidation; no atr_multiple ValueError path)."""
    provider = _SyntheticImpulseProvider()
    result = run_analysis(
        AnalyzeRequest(ticker="AAPL", as_of=date(2024, 12, 31)),
        provider=provider,
    )
    assert result.n_atr_skipped == 0


def _piv(d: date, price: str, t: PivotType, *, degree: int) -> PivotEvent:
    return PivotEvent(
        interval=Interval.DAILY,
        degree=degree,
        timestamp=d,
        price=Decimal(price),
        type=t,
        state=PivotState.CONFIRMED,
        confirmed_at=d,
    )


def _multi_degree_pivot_dict(start: date) -> dict[int, list[PivotEvent]]:
    """Canned 4-segment impulse pivots replicated at degrees 1, 2, 3.

    Each degree gets the same shape (so enumerate_counts produces real
    candidates at each), with progressively larger price scales so the
    detector-style separation is preserved.
    """
    out: dict[int, list[PivotEvent]] = {}
    for d in (1, 2, 3):
        scale = Decimal(str(10 * d))  # 10, 20, 30 absolute price
        out[d] = [
            _piv(
                start + timedelta(days=0), str(Decimal("100") + scale * 0), PivotType.LOW, degree=d
            ),
            _piv(
                start + timedelta(days=5), str(Decimal("100") + scale * 1), PivotType.HIGH, degree=d
            ),
            _piv(
                start + timedelta(days=8),
                str(Decimal("100") + scale * Decimal("0.4")),
                PivotType.LOW,
                degree=d,
            ),
            _piv(
                start + timedelta(days=15),
                str(Decimal("100") + scale * 3),
                PivotType.HIGH,
                degree=d,
            ),
            _piv(
                start + timedelta(days=18),
                str(Decimal("100") + scale * Decimal("1.8")),
                PivotType.LOW,
                degree=d,
            ),
        ]
    return out


def test_run_analysis_emits_multi_degree_candidates(monkeypatch) -> None:
    """Pipeline must surface candidates across all three configured degrees
    (1, 2, 3) when input pivots support each.  Pins S6 multi-degree
    enumeration: prior to this, only degree-2 candidates were emitted
    regardless of pivot input."""
    canned = _multi_degree_pivot_dict(date(2024, 6, 1))

    def fake_detect(bars, *, interval, atr_multiples):
        return canned

    monkeypatch.setattr("wave_alpha.pipeline.detect_multi_degree", fake_detect)

    provider = _SyntheticImpulseProvider()
    result = run_analysis(
        AnalyzeRequest(ticker="TEST", as_of=date(2024, 12, 31)),
        provider=provider,
        llm_mode=LLMMode.DISABLED,
    )
    degrees = {c.degree for c in result.counts_daily}
    assert len(degrees) >= 2, f"expected ≥2 degrees, got {degrees}"
    assert degrees.issubset({1, 2, 3}), f"unexpected degrees: {degrees - {1, 2, 3}}"


def test_run_analysis_top_k_per_degree_cap(monkeypatch) -> None:
    """No more than 2 candidates per degree are emitted (the
    pipeline._TOP_K_PER_DEGREE cap).  Pins the cap so enumerator changes
    don't accidentally inflate the candidate fan-out."""
    canned = _multi_degree_pivot_dict(date(2024, 6, 1))

    def fake_detect(bars, *, interval, atr_multiples):
        return canned

    monkeypatch.setattr("wave_alpha.pipeline.detect_multi_degree", fake_detect)

    provider = _SyntheticImpulseProvider()
    result = run_analysis(
        AnalyzeRequest(ticker="TEST", as_of=date(2024, 12, 31)),
        provider=provider,
        llm_mode=LLMMode.DISABLED,
    )
    by_deg: dict[int, int] = {}
    for c in result.counts_daily:
        by_deg[c.degree] = by_deg.get(c.degree, 0) + 1
    for degree, count in by_deg.items():
        assert count <= 2, f"degree {degree} produced {count} candidates (>2)"


def test_run_analysis_emits_wxy_when_pivot_structure_matches():
    """End-to-end: synthetic bars engineered to produce a degree-2 wxy with
    valid degree-1 sub-counts must surface a wxy WaveCount in the daily
    counts list. Asserts §3.6 of the complex-corrections spec - pipeline
    wiring delivers sub_pivots to the enumerator."""
    from wave_alpha.pipeline import AnalyzeRequest, run_analysis

    bars = _build_bars_for_wxy_at_degree_2()
    as_of = bars[-1].timestamp

    class _FakeWxyProvider:
        def fetch(self, ticker, interval, *, end):
            return list(bars)

    req = AnalyzeRequest(ticker="TEST", as_of=as_of)
    result = run_analysis(req, provider=_FakeWxyProvider())
    wxys = [c for c in result.counts_daily if c.pattern == "wxy"]
    assert wxys, (
        f"expected wxy in counts_daily; got patterns: {[c.pattern for c in result.counts_daily]}"
    )
    wxy = wxys[0]
    assert wxy.degree == 2
    assert wxy.sub_counts.get("W") is not None
    assert wxy.sub_counts["W"].degree == 1


def _build_bars_for_wxy_at_degree_2() -> list:
    """Build a bar series that, when run through detect_multi_degree, yields
    distinct pivot sets at degree-1 (8 sub-pivots) and degree-2 (4 pivots).

    The design exploits the ATR-scaled ZigZag threshold hierarchy: bars carry
    a 4% intrabar range so ATR(14) settles near 4-5 pts.  The W and Y legs
    each contain internal abc sub-structure whose swings are ~8-10 pts
    (above 1xATR but below 2xATR), while the major W-X-Y endpoints swing
    25-30 pts (well above 2xATR).  That stratification ensures degree-1 sees
    all 8 pivots and degree-2 sees only the 4 outer corners.

    Segments (n_bars, start, end):
      warm-up:  20 flat bars at 90  (ATR initialisation)
      W-a:      90 → 80  (10 pts — degree-1 only)
      W-b:      80 → 88  (8 pts  — degree-1 only)
      W-c:      88 → 60  (28 pts — degree-2 major)
      X:        60 → 72  (12 pts — degree-2 connector)
      Y-a:      72 → 62  (10 pts — degree-1 only)
      Y-b:      62 → 70  (8 pts  — degree-1 only)
      Y-c:      70 → 45  (25 pts — degree-2 major)
    """
    from datetime import date, timedelta
    from decimal import Decimal

    from wave_alpha.domain import Bar, Interval

    segments = [
        (20, 90, 90),  # warm-up flat — ATR(14) initialisation
        (15, 90, 80),  # W-a: small drop (10 pts — degree-1 sub-pivot)
        (10, 80, 88),  # W-b: small rally (8 pts  — degree-1 sub-pivot)
        (25, 88, 60),  # W-c: large drop (28 pts — degree-2 W endpoint)
        (15, 60, 72),  # X:   connector rally (12 pts — degree-2 X)
        (15, 72, 62),  # Y-a: small drop (10 pts — degree-1 sub-pivot)
        (10, 62, 70),  # Y-b: small rally (8 pts  — degree-1 sub-pivot)
        (25, 70, 45),  # Y-c: large drop (25 pts — degree-2 Y endpoint)
    ]
    range_pct = Decimal("0.04")  # 4% intrabar range -> ATR ~4-5 pts at price ~90
    bars: list[Bar] = []
    day = 0
    for nbars, a, b in segments:
        for i in range(nbars):
            t = i / (nbars - 1) if nbars > 1 else 1.0
            close = Decimal(str(round(a + t * (b - a), 4)))
            rng = close * range_pct
            bars.append(
                Bar(
                    timestamp=date(2026, 1, 1) + timedelta(days=day),
                    open=close,
                    high=close + rng,
                    low=close - rng,
                    close=close,
                    volume=1000,
                    interval=Interval.DAILY,
                )
            )
            day += 1
    return bars


def test_analyze_result_exposes_daily_bars() -> None:
    """AnalyzeResult.daily_bars surfaces the daily fetch for downstream
    consumers (outcome resolver). Strictly additive."""
    provider = _SyntheticImpulseProvider()
    req = AnalyzeRequest(ticker="TEST", as_of=date(2024, 12, 31))
    result = run_analysis(req, provider=provider)
    assert hasattr(result, "daily_bars")
    assert isinstance(result.daily_bars, list)
    assert len(result.daily_bars) > 0
    # All bars are <= as_of (lookahead-safe)
    assert all(b.timestamp <= date(2024, 12, 31) for b in result.daily_bars)


def test_run_analysis_emits_leading_diagonal_when_pivot_structure_matches():
    """End-to-end: synthetic bars engineered to produce a degree-2 leading
    diagonal must surface a `leading_diagonal` WaveCount in counts_daily."""

    from wave_alpha.pipeline import AnalyzeRequest, run_analysis

    bars = _build_bars_for_leading_diagonal_at_degree_2()
    as_of = bars[-1].timestamp

    class _FakeProvider:
        def fetch(self, ticker, interval, *, end):
            return list(bars)

    req = AnalyzeRequest(ticker="TEST", as_of=as_of)
    result = run_analysis(
        req,
        provider=_FakeProvider(),
        enabled_opt_in_templates=frozenset({"leading_diagonal"}),
    )
    lds = [c for c in result.counts_daily if c.pattern == "leading_diagonal"]
    assert lds, (
        "expected leading_diagonal in counts_daily; got patterns: "
        f"{[c.pattern for c in result.counts_daily]}"
    )
    assert lds[0].degree == 2


def test_run_analysis_does_not_emit_leading_diagonal_by_default():
    """The leading_diagonal template is opt-in. Without
    enabled_opt_in_templates={'leading_diagonal'}, the pipeline must not
    return any LD counts even on bars that would produce one when
    enabled."""
    from wave_alpha.pipeline import AnalyzeRequest, run_analysis

    bars = _build_bars_for_leading_diagonal_at_degree_2()
    as_of = bars[-1].timestamp

    class _FakeProvider:
        def fetch(self, ticker, interval, *, end):
            return list(bars)

    req = AnalyzeRequest(ticker="TEST", as_of=as_of)
    result = run_analysis(req, provider=_FakeProvider())
    assert all(c.pattern != "leading_diagonal" for c in result.counts_daily), (
        f"LD must not surface on default config; got: {[c.pattern for c in result.counts_daily]}"
    )


def _build_bars_for_leading_diagonal_at_degree_2() -> list:
    """Build a bar series that produces a degree-2 leading diagonal shape.

    Degree stratification exploits the ATR-scaled ZigZag threshold hierarchy.
    Bars carry a 2% intrabar range so ATR(14) settles near 3.6 pts after the
    flat warm-up. Internal sub-swings (+6 / -6 pts) sit above 1x ATR but
    below 2x ATR (~7.2 pts), making them visible only at degree-0/1. The five
    major LD wave swings (18-28 pts) are well above 2x ATR, so degree-2 sees
    exactly the 6 pivots required for a 5-wave leading-diagonal fit.

    Segments are (n_bars, start_price, end_price); linear interpolation.
    """
    from datetime import date, timedelta
    from decimal import Decimal

    from wave_alpha.domain import Bar, Interval

    segments = [
        (20, 90, 90),  # flat warm-up — ATR(14) initialisation at 3.6 pts
        (8, 90, 96),  # sub-swing +6 pts (degree-0/1 only)
        (6, 96, 90),  # sub-swing -6 pts (degree-0/1 only)
        (15, 90, 118),  # W1: +28 pts — degree-2 endpoint
        (15, 118, 96),  # W2: -22 pts (79 % retrace of W1)
        (8, 96, 102),  # sub-swing +6 pts (degree-0/1 only)
        (6, 102, 96),  # sub-swing -6 pts (degree-0/1 only)
        (15, 96, 120),  # W3: +24 pts — degree-2 endpoint
        (15, 120, 100),  # W4: -20 pts (end 100 < W1 end 118 → overlaps W1)
        (15, 100, 126),  # W5: +26 pts
    ]
    range_pct = Decimal("0.02")  # 2 % intrabar range → ATR ≈ 3.6 pts
    bars: list[Bar] = []
    day = 0
    for nbars, a, b in segments:
        for k in range(nbars):
            t = k / (nbars - 1) if nbars > 1 else 1.0
            close = Decimal(str(round(a + t * (b - a), 4)))
            rng = close * range_pct
            bars.append(
                Bar(
                    timestamp=date(2026, 1, 1) + timedelta(days=day),
                    open=close,
                    high=close + rng,
                    low=close - rng,
                    close=close,
                    volume=1000,
                    interval=Interval.DAILY,
                )
            )
            day += 1
    return bars
