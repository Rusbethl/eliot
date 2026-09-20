from datetime import date as _date
from decimal import Decimal as _Decimal

from fastapi.testclient import TestClient

from wave_alpha.domain import Bar as _Bar
from wave_alpha.pipeline import AnalyzeResult
from wave_alpha.web.app import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def _stub_analyze_result(ticker: str = "AAPL") -> AnalyzeResult:
    return AnalyzeResult(ticker=ticker, as_of=_date(2024, 12, 31), counts_daily=[])


def test_deepdive_shell_renders_toolbar_and_fragment_targets() -> None:
    res = _client().get("/deepdive/AAPL?as_of=2024-12-31")
    assert res.status_code == 200
    body = res.text
    assert "AAPL" in body
    assert "2024-12-31" in body
    assert 'id="chart"' in body
    assert "/deepdive/AAPL/chart" in body
    assert "/deepdive/AAPL/coherence" in body
    assert "/deepdive/AAPL/counts" in body
    assert "/deepdive/AAPL/right-edge" in body
    assert "/deepdive/AAPL/hero" in body


def test_deepdive_shell_redirects_when_ticker_changed_in_form() -> None:
    res = _client().get("/deepdive/AAPL?t=MSFT&as_of=2024-12-31", follow_redirects=False)
    assert res.status_code == 307
    assert res.headers["location"] == "/deepdive/MSFT?as_of=2024-12-31&llm=disabled"


def test_deepdive_shell_defaults_as_of_to_today_when_missing() -> None:
    res = _client().get("/deepdive/AAPL")
    assert res.status_code == 200
    assert "AAPL" in res.text


def test_chart_fragment_returns_inline_json_and_render_call(monkeypatch) -> None:
    from wave_alpha.web.routes import deepdive as deepdive_routes

    bars = [
        _Bar(
            timestamp=_date(2024, 1, i + 1),
            open=_Decimal("100"),
            high=_Decimal("100"),
            low=_Decimal("100"),
            close=_Decimal("100"),
            volume=1,
        )
        for i in range(3)
    ]
    monkeypatch.setattr(deepdive_routes, "_load_bars", lambda ticker, as_of: bars)
    monkeypatch.setattr(deepdive_routes, "_load_top_count", lambda ticker, as_of: None)

    res = _client().get("/deepdive/AAPL/chart?as_of=2024-12-31")
    assert res.status_code == 200
    assert "WaveAlpha.renderChart" in res.text
    assert "2024-01-01" in res.text  # bar timestamp shows up in the JSON payload


def test_coherence_fragment_renders_score_and_pairwise(monkeypatch) -> None:
    from wave_alpha.coherence.score import CoherenceResult
    from wave_alpha.pipeline import AnalyzeResult
    from wave_alpha.web import deps as deps_module

    def fake_analyze(ticker, as_of, llm_mode):
        return AnalyzeResult(
            ticker=ticker,
            as_of=as_of,
            coherence=CoherenceResult(
                score=0.82,
                multiplier=1.07,
                pairwise={"weekly_daily": 0.9, "daily_4h": 0.8, "weekly_4h": 0.7},
                fallback_used=None,
            ),
        )

    monkeypatch.setattr(deps_module, "analyze", fake_analyze)
    res = _client().get("/deepdive/AAPL/coherence?as_of=2024-12-31")
    assert res.status_code == 200
    assert "0.82" in res.text
    assert "weekly_daily" in res.text or "W" in res.text
    assert 'class="tri"' in res.text
    assert "tri-cell" in res.text
    assert "W" in res.text


def test_counts_fragment_renders_top_three_with_narratives(monkeypatch) -> None:
    from datetime import date
    from decimal import Decimal

    from wave_alpha.domain import Interval
    from wave_alpha.elliott.models import WaveCount
    from wave_alpha.llm.responses import ScanCandidate, ScanResponse
    from wave_alpha.pipeline import AnalyzeResult, RankedCount
    from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
    from wave_alpha.web import deps as deps_module

    def piv(d, p, t):
        return PivotEvent(
            interval=Interval.DAILY,
            degree=2,
            timestamp=date(2024, 1, d),
            price=Decimal(p),
            type=t,
            state=PivotState.CONFIRMED,
            confirmed_at=date(2024, 1, d),
        )

    count = WaveCount(
        pattern="impulse",
        degree=2,
        score=0.7,
        pivots=[piv(1, "100", PivotType.LOW), piv(5, "110", PivotType.HIGH)],
    )

    def fake_analyze(ticker, as_of, llm_mode):
        rc = RankedCount(
            candidate_id="C1",
            count=count,
            engine_score=0.7,
            llm_prob=0.55,
            final_score=0.66,
        )
        scan = ScanResponse(
            candidates=[ScanCandidate(id="C1", probability=0.55, narrative="solid impulse setup")],
            engine_missed=False,
        )
        return AnalyzeResult(
            ticker=ticker,
            as_of=as_of,
            counts_daily=[count],
            ranked_daily=[rc],
            llm_scan=scan,
        )

    monkeypatch.setattr(deps_module, "analyze", fake_analyze)
    res = _client().get("/deepdive/AAPL/counts?as_of=2024-12-31&llm=cached")
    assert res.status_code == 200
    assert "C1" in res.text
    assert "impulse" in res.text
    assert "solid impulse setup" in res.text
    assert "0.66" in res.text  # final score


def test_plan_fragment_renders_top_signal_when_present(monkeypatch) -> None:
    from datetime import date
    from decimal import Decimal

    from wave_alpha.pipeline import AnalyzeResult
    from wave_alpha.trades.models import TradeSignal
    from wave_alpha.web import deps as deps_module

    sig = TradeSignal(
        ticker="AAPL",
        as_of=date(2024, 12, 31),
        direction="long",
        entry_price=Decimal("120"),
        stop_price=Decimal("118"),
        target_price=Decimal("130"),
        count_id="C1",
        count_pattern="impulse",
        wave_label="5",
        degree=2,
        confidence=0.66,
        expected_bars_to_target=8,
        max_holding_bars=20,
    )

    def fake_analyze(ticker, as_of, llm_mode):
        return AnalyzeResult(ticker=ticker, as_of=as_of, signals=[sig])

    monkeypatch.setattr(deps_module, "analyze", fake_analyze)
    res = _client().get("/deepdive/AAPL/plan?as_of=2024-12-31")
    assert res.status_code == 200
    assert "LONG" in res.text
    assert "120" in res.text
    assert "118" in res.text
    assert "130" in res.text


def test_plan_fragment_renders_empty_state_when_no_signal(monkeypatch) -> None:
    from wave_alpha.pipeline import AnalyzeResult
    from wave_alpha.web import deps as deps_module

    monkeypatch.setattr(
        deps_module,
        "analyze",
        lambda ticker, as_of, llm_mode: AnalyzeResult(ticker=ticker, as_of=as_of),
    )
    res = _client().get("/deepdive/AAPL/plan?as_of=2024-12-31")
    assert res.status_code == 200
    assert "no" in res.text.lower()


def test_scenarios_fragment_renders_alternates_with_invalidation(monkeypatch) -> None:
    from datetime import date
    from decimal import Decimal

    from wave_alpha.domain import Interval
    from wave_alpha.elliott.models import WaveCount
    from wave_alpha.pipeline import AnalyzeResult, RankedCount
    from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
    from wave_alpha.web import deps as deps_module

    def piv(d, p, t):
        return PivotEvent(
            interval=Interval.DAILY,
            degree=2,
            timestamp=date(2024, 1, d),
            price=Decimal(p),
            type=t,
            state=PivotState.CONFIRMED,
            confirmed_at=date(2024, 1, d),
        )

    counts = [
        WaveCount(
            pattern="impulse",
            degree=2,
            score=0.7,
            pivots=[piv(1, "100", PivotType.LOW), piv(5, "118", PivotType.LOW)],
        ),
        WaveCount(
            pattern="zigzag",
            degree=2,
            score=0.5,
            pivots=[piv(1, "100", PivotType.HIGH), piv(5, "92", PivotType.HIGH)],
        ),
    ]
    ranked = [
        RankedCount(
            candidate_id=f"C{i + 1}",
            count=c,
            engine_score=c.score,
            llm_prob=None,
            final_score=c.score,
        )
        for i, c in enumerate(counts)
    ]

    def fake_analyze(ticker, as_of, llm_mode):
        return AnalyzeResult(ticker=ticker, as_of=as_of, ranked_daily=ranked, counts_daily=counts)

    monkeypatch.setattr(deps_module, "analyze", fake_analyze)
    res = _client().get("/deepdive/AAPL/scenarios?as_of=2024-12-31")
    assert res.status_code == 200
    assert "C2" in res.text
    assert "zigzag" in res.text
    assert "92" in res.text  # invalidation = last pivot price of alt


def test_right_edge_fragment_renders_proba_when_assessment_present(monkeypatch) -> None:
    from wave_alpha.web import right_edge as right_edge_module

    monkeypatch.setattr(
        right_edge_module,
        "compute_right_edge_assessment",
        lambda ticker, as_of: right_edge_module.RightEdgeAssessment(
            proba=0.73,
            degree=2,
            pivot_timestamp=_date(2024, 11, 15),
            pivot_type="high",
            model_kind="LogisticConfirmationModel",
        ),
    )
    res = _client().get("/deepdive/AAPL/right-edge?as_of=2024-12-31")
    assert res.status_code == 200
    body = res.text
    assert "0.73" in body
    assert "2024-11-15" in body
    assert "HIGH" in body
    assert "LogisticConfirmationModel" in body


def test_right_edge_fragment_renders_empty_state_when_no_tentative_pivot(monkeypatch) -> None:
    from wave_alpha.web import right_edge as right_edge_module

    monkeypatch.setattr(
        right_edge_module, "compute_right_edge_assessment", lambda ticker, as_of: None
    )
    res = _client().get("/deepdive/AAPL/right-edge?as_of=2024-12-31")
    assert res.status_code == 200
    assert "no tentative right-edge pivot" in res.text.lower()


def test_hero_fragment_returns_signal_kpis_when_present(monkeypatch) -> None:
    from datetime import date
    from decimal import Decimal

    from wave_alpha.domain import Bar, Interval
    from wave_alpha.elliott.models import WaveCount
    from wave_alpha.pipeline import AnalyzeResult, RankedCount
    from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
    from wave_alpha.trades.models import TradeSignal
    from wave_alpha.web import deps as deps_module
    from wave_alpha.web.routes import deepdive as deepdive_routes

    def piv(d, p, t):
        return PivotEvent(
            interval=Interval.DAILY,
            degree=2,
            timestamp=date(2024, 1, d),
            price=Decimal(p),
            type=t,
            state=PivotState.CONFIRMED,
            confirmed_at=date(2024, 1, d),
        )

    count = WaveCount(
        pattern="impulse",
        degree=2,
        score=0.7,
        pivots=[piv(1, "100", PivotType.LOW), piv(5, "110", PivotType.HIGH)],
    )
    rc = RankedCount(
        candidate_id="C1",
        count=count,
        engine_score=0.7,
        llm_prob=None,
        final_score=0.7,
    )
    sig = TradeSignal(
        ticker="AAPL",
        as_of=date(2024, 12, 31),
        direction="long",
        entry_price=Decimal("120"),
        stop_price=Decimal("118"),
        target_price=Decimal("130"),
        count_id="C1",
        count_pattern="impulse",
        wave_label="3",
        degree=2,
        confidence=0.66,
        expected_bars_to_target=8,
        max_holding_bars=20,
    )

    def fake_analyze(ticker, as_of, llm_mode):
        return AnalyzeResult(
            ticker=ticker, as_of=as_of, ranked_daily=[rc], counts_daily=[count], signals=[sig]
        )

    bars = [
        Bar(
            timestamp=date(2024, 1, i + 1),
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("125.50"),
            volume=1,
        )
        for i in range(3)
    ]
    monkeypatch.setattr(deps_module, "analyze", fake_analyze)
    monkeypatch.setattr(deepdive_routes, "_load_bars", lambda ticker, as_of: bars)

    res = _client().get("/deepdive/AAPL/hero?as_of=2024-12-31")
    assert res.status_code == 200
    body = res.text
    assert "LONG" in body
    assert "0.66" in body  # confidence
    assert "impulse" in body
    assert "125.50" in body  # last close
    assert "5.00" in body  # rr = 10 / 2


def test_hero_fragment_renders_no_signal_state_when_empty(monkeypatch) -> None:
    from wave_alpha.pipeline import AnalyzeResult
    from wave_alpha.web import deps as deps_module
    from wave_alpha.web.routes import deepdive as deepdive_routes

    monkeypatch.setattr(
        deps_module,
        "analyze",
        lambda ticker, as_of, llm_mode: AnalyzeResult(ticker=ticker, as_of=as_of),
    )
    monkeypatch.setattr(deepdive_routes, "_load_bars", lambda ticker, as_of: [])

    res = _client().get("/deepdive/AAPL/hero?as_of=2024-12-31")
    assert res.status_code == 200
    assert "no signal" in res.text.lower()


def test_right_edge_model_loader_caches() -> None:
    from wave_alpha.web import right_edge as right_edge_module

    right_edge_module._model.cache_clear()
    m1, kind1 = right_edge_module._model()
    m2, kind2 = right_edge_module._model()
    assert m1 is m2
    assert kind1 == kind2


def test_coherence_fragment_includes_trust_popover(monkeypatch) -> None:
    """The /coherence fragment renders the trust popover (narrative-only; pairwise data
    lives in the always-visible flex-row, covered by test_coherence_fragment_renders_score_and_pairwise)."""
    from wave_alpha.coherence.score import CoherenceResult
    from wave_alpha.pipeline import AnalyzeResult
    from wave_alpha.web import deps as deps_module

    def fake_analyze(ticker, as_of, llm_mode):
        return AnalyzeResult(
            ticker=ticker,
            as_of=as_of,
            coherence=CoherenceResult(
                score=0.82,
                multiplier=1.18,
                pairwise={"weekly_daily": 0.84, "daily_4h": 0.79, "weekly_4h": 0.72},
                fallback_used=None,
            ),
        )

    monkeypatch.setattr(deps_module, "analyze", fake_analyze)
    res = _client().get("/deepdive/AAPL/coherence?as_of=2024-12-31")
    assert res.status_code == 200
    body = res.text
    assert "popover-trigger" in body
    assert "How we got" in body and "1.18" in body  # headline
    assert 'href="/about#coherence"' in body


def test_right_edge_fragment_includes_trust_popover(monkeypatch) -> None:
    """The /right-edge fragment renders the trust popover with model/degree breakdown."""
    from wave_alpha.web import right_edge as right_edge_module

    monkeypatch.setattr(
        right_edge_module,
        "compute_right_edge_assessment",
        lambda ticker, as_of: right_edge_module.RightEdgeAssessment(
            proba=0.64,
            degree=2,
            pivot_timestamp=_date(2024, 11, 15),
            pivot_type="high",
            model_kind="logistic",
        ),
    )
    res = _client().get("/deepdive/AAPL/right-edge?as_of=2024-12-31")
    assert res.status_code == 200
    body = res.text
    assert "popover-trigger" in body
    assert "How we got probability" in body and "0.64" in body
    assert 'href="/about#right-edge"' in body
    assert "logistic" in body


def test_trade_plan_fragment_includes_trust_popover(monkeypatch) -> None:
    """The /plan fragment renders the trust popover explaining entry/stop/target derivation."""
    from datetime import date
    from decimal import Decimal

    from wave_alpha.pipeline import AnalyzeResult
    from wave_alpha.trades.models import TradeSignal
    from wave_alpha.web import deps as deps_module

    sig = TradeSignal(
        ticker="AAPL",
        as_of=date(2024, 12, 31),
        direction="long",
        entry_price=Decimal("100"),
        stop_price=Decimal("95"),
        target_price=Decimal("110"),
        count_id="C1",
        count_pattern="impulse",
        wave_label="3",
        degree=2,
        confidence=0.66,
        expected_bars_to_target=8,
        max_holding_bars=20,
    )

    def fake_analyze(ticker, as_of, llm_mode):
        return AnalyzeResult(ticker=ticker, as_of=as_of, signals=[sig])

    monkeypatch.setattr(deps_module, "analyze", fake_analyze)
    res = _client().get("/deepdive/AAPL/plan?as_of=2024-12-31")
    assert res.status_code == 200
    body = res.text
    assert "popover-trigger" in body
    assert "How we picked these levels" in body
    assert 'href="/about#trade-plan"' in body
    assert "popover-data" in body


def test_counts_list_renders_per_candidate_popover(monkeypatch) -> None:
    """The /counts fragment renders one popover per ranked candidate with rank-suffix copy."""
    from datetime import date
    from decimal import Decimal

    from wave_alpha.domain import Interval
    from wave_alpha.elliott.models import WaveCount
    from wave_alpha.pipeline import AnalyzeResult, RankedCount
    from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
    from wave_alpha.web import deps as deps_module

    def piv(d, p, t):
        return PivotEvent(
            interval=Interval.DAILY,
            degree=2,
            timestamp=date(2024, 1, d),
            price=Decimal(p),
            type=t,
            state=PivotState.CONFIRMED,
            confirmed_at=date(2024, 1, d),
        )

    counts = [
        WaveCount(
            pattern="impulse",
            degree=2,
            score=0.82,
            pivots=[piv(1, "100", PivotType.LOW), piv(5, "118", PivotType.HIGH)],
        ),
        WaveCount(
            pattern="zigzag",
            degree=2,
            score=0.55,
            pivots=[piv(1, "100", PivotType.HIGH), piv(5, "92", PivotType.LOW)],
        ),
        WaveCount(
            pattern="flat",
            degree=2,
            score=0.41,
            pivots=[piv(1, "100", PivotType.HIGH), piv(5, "95", PivotType.LOW)],
        ),
    ]
    ranked = [
        RankedCount(
            candidate_id="C1",
            count=counts[0],
            engine_score=0.82,
            llm_prob=0.78,
            final_score=0.96,
        ),
        RankedCount(
            candidate_id="C2",
            count=counts[1],
            engine_score=0.55,
            llm_prob=0.50,
            final_score=0.62,
        ),
        RankedCount(
            candidate_id="C3",
            count=counts[2],
            engine_score=0.41,
            llm_prob=None,
            final_score=0.45,
        ),
    ]

    def fake_analyze(ticker, as_of, llm_mode):
        return AnalyzeResult(
            ticker=ticker,
            as_of=as_of,
            counts_daily=counts,
            ranked_daily=ranked,
        )

    monkeypatch.setattr(deps_module, "analyze", fake_analyze)
    res = _client().get("/deepdive/AAPL/counts?as_of=2024-12-31&llm=cached")
    assert res.status_code == 200
    body = res.text
    # One popover trigger per ranked candidate.
    assert body.count("popover-trigger") >= 3
    # Rank-suffix copy is correct for each candidate.
    assert "Why C1 won" in body
    assert "Why C2 was second" in body
    assert "Why C3 was third" in body
    # Trust popover wiring: "Learn more" link + data table.
    assert 'href="/about#enumeration"' in body
    assert "popover-data" in body


def test_hero_renders_kpi_popovers(monkeypatch) -> None:
    """The /hero fragment renders Confidence and R:R popovers."""
    from datetime import date
    from decimal import Decimal

    from wave_alpha.domain import Bar, Interval
    from wave_alpha.elliott.models import WaveCount
    from wave_alpha.pipeline import AnalyzeResult, RankedCount
    from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
    from wave_alpha.trades.models import TradeSignal
    from wave_alpha.web import deps as deps_module
    from wave_alpha.web.routes import deepdive as deepdive_routes

    def piv(d, p, t):
        return PivotEvent(
            interval=Interval.DAILY,
            degree=2,
            timestamp=date(2024, 1, d),
            price=Decimal(p),
            type=t,
            state=PivotState.CONFIRMED,
            confirmed_at=date(2024, 1, d),
        )

    count = WaveCount(
        pattern="impulse",
        degree=2,
        score=0.7,
        pivots=[piv(1, "100", PivotType.LOW), piv(5, "110", PivotType.HIGH)],
    )
    rc = RankedCount(
        candidate_id="C1",
        count=count,
        engine_score=0.7,
        llm_prob=None,
        final_score=0.7,
    )
    sig = TradeSignal(
        ticker="AAPL",
        as_of=date(2024, 12, 31),
        direction="long",
        entry_price=Decimal("120"),
        stop_price=Decimal("118"),
        target_price=Decimal("130"),
        count_id="C1",
        count_pattern="impulse",
        wave_label="3",
        degree=2,
        confidence=0.66,
        expected_bars_to_target=8,
        max_holding_bars=20,
    )

    def fake_analyze(ticker, as_of, llm_mode):
        return AnalyzeResult(
            ticker=ticker, as_of=as_of, ranked_daily=[rc], counts_daily=[count], signals=[sig]
        )

    bars = [
        Bar(
            timestamp=date(2024, 1, i + 1),
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("125.50"),
            volume=1,
        )
        for i in range(3)
    ]
    monkeypatch.setattr(deps_module, "analyze", fake_analyze)
    monkeypatch.setattr(deepdive_routes, "_load_bars", lambda ticker, as_of: bars)

    res = _client().get("/deepdive/AAPL/hero?as_of=2024-12-31")
    assert res.status_code == 200
    body = res.text
    assert "What confidence means" in body
    assert "How R:R is computed" in body
    assert 'href="/about#alpha-blend"' in body
    assert 'href="/about#trade-plan"' in body
