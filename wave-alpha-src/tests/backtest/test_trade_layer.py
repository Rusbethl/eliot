from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal

import pytest

from tests.backtest._synthetic import FixedBarsProvider, _record, daily_bars
from wave_alpha.backtest.trade_layer import TradeLayerEvaluator, TradeLayerResult, TradeRecord
from wave_alpha.domain import Interval
from wave_alpha.trades.models import TradeRules, TradeSignal


def test_traderecord_is_frozen():
    rec = _record()
    with pytest.raises(FrozenInstanceError):
        rec.ticker = "MSFT"  # type: ignore[misc]


def test_traderecord_open_at_end_has_none_exit():
    rec = _record(exit_reason="open_at_end", exit_date=None, exit_price=None)
    assert rec.exit_reason == "open_at_end"
    assert rec.exit_date is None
    assert rec.exit_price is None


def test_tradelayerresult_empty_metrics():
    result = TradeLayerResult()
    assert result.closed_trades == 0
    assert result.open_at_end == 0
    assert result.hit_rate == 0.0
    assert result.avg_R == 0.0
    assert result.profit_factor == 0.0
    assert result.max_drawdown_R == 0.0


def test_tradelayerresult_open_at_end_count():
    result = TradeLayerResult(
        trades=[
            _record(),
            _record(exit_reason="open_at_end", exit_date=None, exit_price=None),
        ]
    )
    assert result.closed_trades == 1
    assert result.open_at_end == 1


def _closed(R: float, exit_d: date = date(2024, 1, 10)) -> TradeRecord:  # noqa: N803
    return _record(realized_R=R, exit_date=exit_d, exit_reason="target" if R > 0 else "stop")


def test_hit_rate_three_wins_two_losses():
    result = TradeLayerResult(
        trades=[_closed(1.0), _closed(0.5), _closed(-1.0), _closed(-0.5), _closed(2.0)]
    )
    assert result.hit_rate == 3 / 5


def test_avg_R_simple():  # noqa: N802
    result = TradeLayerResult(trades=[_closed(1.0), _closed(0.0), _closed(-1.0)])
    # 0.0 is not a "win" (R > 0 strict) and contributes 0 to the sum
    assert result.avg_R == 0.0


def test_profit_factor_normal():
    # gains = 3.0, losses = 1.5, PF = 2.0
    result = TradeLayerResult(trades=[_closed(1.0), _closed(2.0), _closed(-1.0), _closed(-0.5)])
    assert result.profit_factor == 2.0


def test_profit_factor_no_losses_returns_inf():
    result = TradeLayerResult(trades=[_closed(1.0), _closed(2.0)])
    assert result.profit_factor == float("inf")


def test_profit_factor_no_trades_returns_zero():
    result = TradeLayerResult(trades=[])
    assert result.profit_factor == 0.0


def test_max_drawdown_known_sequence():
    # cumulative: +1, +1.5, +0.7, +0.2, +1.4
    # peak after trade 2 = 1.5
    # trough after trade 4 = 0.2
    # max_dd = 1.5 - 0.2 = 1.3
    result = TradeLayerResult(
        trades=[
            _closed(1.0, exit_d=date(2024, 1, 1)),
            _closed(0.5, exit_d=date(2024, 1, 2)),
            _closed(-0.8, exit_d=date(2024, 1, 3)),
            _closed(-0.5, exit_d=date(2024, 1, 4)),
            _closed(1.2, exit_d=date(2024, 1, 5)),
        ]
    )
    assert result.max_drawdown_R == pytest.approx(1.3)


def test_max_drawdown_all_wins_zero():
    result = TradeLayerResult(
        trades=[
            _closed(1.0, exit_d=date(2024, 1, 1)),
            _closed(0.5, exit_d=date(2024, 1, 2)),
        ]
    )
    assert result.max_drawdown_R == 0.0


def test_open_at_end_excluded_from_closed_metrics():
    result = TradeLayerResult(
        trades=[
            _closed(1.0),
            _record(exit_reason="open_at_end", exit_date=None, exit_price=None, realized_R=999.0),
        ]
    )
    assert result.hit_rate == 1.0  # only the closed trade counts
    assert result.avg_R == 1.0


def test_by_direction_splits_long_short():
    result = TradeLayerResult(
        trades=[
            _record(direction="long", realized_R=1.0, exit_date=date(2024, 1, 1)),
            _record(
                direction="long", realized_R=-0.5, exit_date=date(2024, 1, 2), exit_reason="stop"
            ),
            _record(direction="short", realized_R=2.0, exit_date=date(2024, 1, 3)),
        ]
    )
    by_dir = result.by_direction()
    assert by_dir["long"]["sample_size"] == 2
    assert by_dir["long"]["hit_rate"] == 0.5
    assert by_dir["long"]["avg_R"] == pytest.approx(0.25)
    assert by_dir["short"]["sample_size"] == 1
    assert by_dir["short"]["hit_rate"] == 1.0


def test_by_pattern_groups_by_pattern():
    result = TradeLayerResult(
        trades=[
            _record(pattern="impulse", realized_R=1.0, exit_date=date(2024, 1, 1)),
            _record(
                pattern="zigzag", realized_R=-1.0, exit_date=date(2024, 1, 2), exit_reason="stop"
            ),
            _record(pattern="impulse", realized_R=0.5, exit_date=date(2024, 1, 3)),
        ]
    )
    by_pat = result.by_pattern()
    assert set(by_pat.keys()) == {"impulse", "zigzag"}
    assert by_pat["impulse"]["sample_size"] == 2


def test_by_coherence_bucket_uses_static_keys():
    result = TradeLayerResult(
        trades=[
            _record(
                coherence_score=0.8,
                coherence_bucket="full",
                realized_R=1.0,
                exit_date=date(2024, 1, 1),
            ),
            _record(
                coherence_score=0.5,
                coherence_bucket="partial",
                realized_R=-0.5,
                exit_date=date(2024, 1, 2),
                exit_reason="stop",
            ),
        ]
    )
    by_coh = result.by_coherence_bucket()
    # static insertion order from COHERENCE_BUCKETS
    assert list(by_coh.keys()) == ["full", "partial", "disagreement"]
    assert by_coh["full"]["sample_size"] == 1
    assert by_coh["partial"]["sample_size"] == 1
    assert by_coh["disagreement"]["sample_size"] == 0


def test_by_year_sorted_ascending():
    result = TradeLayerResult(
        trades=[
            _record(signal_as_of=date(2022, 6, 1), realized_R=1.0, exit_date=date(2022, 6, 10)),
            _record(
                signal_as_of=date(2020, 3, 1),
                realized_R=-0.5,
                exit_date=date(2020, 3, 5),
                exit_reason="stop",
            ),
            _record(signal_as_of=date(2022, 11, 1), realized_R=0.2, exit_date=date(2022, 11, 4)),
        ]
    )
    by_yr = result.by_year()
    assert list(by_yr.keys()) == [2020, 2022]
    assert by_yr[2022]["sample_size"] == 2


def test_by_direction_excludes_open_at_end_from_metrics():
    """open_at_end records must not contaminate breakdown metrics — invariant
    enforced by _metrics_for via the same closed-trades filter as the headline."""
    result = TradeLayerResult(
        trades=[
            _record(direction="long", realized_R=1.0, exit_date=date(2024, 1, 1)),
            _record(
                direction="long",
                exit_reason="open_at_end",
                exit_date=None,
                exit_price=None,
                realized_R=999.0,  # poison value: must not leak into avg_R
            ),
        ]
    )
    by_dir = result.by_direction()
    assert by_dir["long"]["sample_size"] == 1
    assert by_dir["long"]["avg_R"] == 1.0
    assert by_dir["long"]["hit_rate"] == 1.0


def _signal(
    *,
    as_of: date,
    direction: str = "long",
    entry: Decimal = Decimal("100"),
    stop: Decimal = Decimal("96"),
    target: Decimal = Decimal("110"),
    confidence: float = 0.6,
    max_holding_bars: int = 20,
    pattern: str = "impulse",
    wave_label: str = "5",
) -> TradeSignal:
    return TradeSignal(
        ticker="AAPL",
        as_of=as_of,
        direction=direction,
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        count_id="abc123",
        count_pattern=pattern,
        wave_label=wave_label,
        degree=2,
        confidence=confidence,
        expected_bars_to_target=10,
        max_holding_bars=max_holding_bars,
    )


def _stub_pipeline(monkeypatch, signals_by_date: dict, coherence_score: float = 0.8):
    """Patch pipeline.run_analysis to return a canned signal per as_of."""
    from wave_alpha.coherence.score import CoherenceResult
    from wave_alpha.pipeline import AnalyzeResult

    def fake_run(req, *, provider, llm_mode, llm_runner=None, alpha=0.6):
        sig = signals_by_date.get(req.as_of)
        return AnalyzeResult(
            ticker=req.ticker,
            as_of=req.as_of,
            coherence=CoherenceResult(
                score=coherence_score, multiplier=1.0, pairwise={}, fallback_used=None
            ),
            signals=[sig] if sig else [],
        )

    monkeypatch.setattr("wave_alpha.backtest.trade_layer.run_analysis", fake_run)


def test_target_hit_on_fill_bar(monkeypatch):
    # Daily bars: signal date 2024-01-01 (close 100), next bar opens 100, high 115
    # → fill at 100*1.001 = 100.1; target=110 hit on the same bar → exit at 110
    bars = daily_bars(
        [
            (99, 100, 99, 100),  # 2024-01-01: signal date
            (100, 115, 100, 112),  # 2024-01-02: fill bar with target hit
        ],
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})
    sig = _signal(as_of=date(2024, 1, 1))
    _stub_pipeline(monkeypatch, {date(2024, 1, 1): sig})

    rules = TradeRules()
    evaluator = TradeLayerEvaluator(provider=provider, rules=rules)
    result = evaluator.evaluate("AAPL", start=date(2024, 1, 1), end=date(2024, 1, 5), step=1)

    assert len(result.trades) == 1
    rec = result.trades[0]
    assert rec.exit_reason == "target"
    assert rec.fill_date == date(2024, 1, 2)
    assert rec.exit_date == date(2024, 1, 2)  # same-bar exit
    assert rec.fill_price == Decimal("100.1000")  # 100 * 1.001
    assert rec.exit_price == Decimal("109.89")  # 110 * 0.999 (long exit slippage)
    assert rec.bars_held == 0
    assert rec.realized_R > 0


def test_stop_out_long_with_adverse_gap(monkeypatch):
    # Fill bar opens at 100, gaps down next bar to 90 (below stop=96) → exit at 90
    bars = daily_bars(
        [
            (99, 100, 99, 100),  # signal date
            (100, 102, 100, 101),  # fill bar — uneventful
            (90, 95, 88, 92),
        ],  # next bar — gap below stop
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})
    sig = _signal(as_of=date(2024, 1, 1), stop=Decimal("96"), target=Decimal("130"))
    _stub_pipeline(monkeypatch, {date(2024, 1, 1): sig})

    rules = TradeRules()
    result = TradeLayerEvaluator(provider=provider, rules=rules).evaluate(
        "AAPL", start=date(2024, 1, 1), end=date(2024, 1, 10), step=1
    )

    rec = result.trades[0]
    assert rec.exit_reason == "stop"
    # exit at min(open=90, stop=96) = 90, then * 0.999 (long-exit slippage) = 89.91
    assert rec.exit_price == Decimal("89.910")
    assert rec.realized_R < -1  # stop-out worse than -1R due to gap


def test_stop_out_short_with_adverse_gap(monkeypatch):
    bars = daily_bars(
        [
            (101, 102, 100, 100),  # signal date
            (100, 100, 98, 99),  # fill bar
            (110, 115, 109, 112),
        ],  # next bar — gaps above stop=104
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})
    sig = _signal(
        as_of=date(2024, 1, 1),
        direction="short",
        entry=Decimal("100"),
        stop=Decimal("104"),
        target=Decimal("90"),
    )
    _stub_pipeline(monkeypatch, {date(2024, 1, 1): sig})

    rules = TradeRules(short_borrow_annual_pct=0.0)  # zero out borrow for clarity
    result = TradeLayerEvaluator(provider=provider, rules=rules).evaluate(
        "AAPL", start=date(2024, 1, 1), end=date(2024, 1, 10), step=1
    )

    rec = result.trades[0]
    assert rec.exit_reason == "stop"
    # exit at max(open=110, stop=104) = 110 (gap above stop) → * 1.001 short slip
    assert rec.exit_price == Decimal("110.110")
    assert rec.realized_R < -1


def test_time_stop_long(monkeypatch):
    # max_holding_bars=2: bars 0, 1, 2 — exit at close of bar 2
    sideways = [(100, 102, 99, 100)] * 5
    bars = daily_bars(
        [(99, 100, 99, 100), *sideways],
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})
    sig = _signal(
        as_of=date(2024, 1, 1),
        target=Decimal("999"),  # unreachable
        stop=Decimal("1"),  # unreachable
        max_holding_bars=2,
    )
    _stub_pipeline(monkeypatch, {date(2024, 1, 1): sig})

    rules = TradeRules()
    result = TradeLayerEvaluator(provider=provider, rules=rules).evaluate(
        "AAPL", start=date(2024, 1, 1), end=date(2024, 1, 30), step=1
    )

    rec = result.trades[0]
    assert rec.exit_reason == "time_stop"
    assert rec.bars_held == 2
    # exit at close of bar 2, with long-exit slippage
    assert rec.exit_price == Decimal("99.900")  # 100 * 0.999


def test_same_bar_stop_and_target_stop_wins(monkeypatch):
    # Single bar's high >= target AND low <= stop
    bars = daily_bars(
        [
            (99, 100, 99, 100),  # signal date
            (100, 115, 90, 105),
        ],  # fill bar: hits target AND stop
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})
    sig = _signal(
        as_of=date(2024, 1, 1),
        stop=Decimal("96"),
        target=Decimal("110"),
    )
    _stub_pipeline(monkeypatch, {date(2024, 1, 1): sig})

    rules = TradeRules()
    result = TradeLayerEvaluator(provider=provider, rules=rules).evaluate(
        "AAPL", start=date(2024, 1, 1), end=date(2024, 1, 10), step=1
    )

    rec = result.trades[0]
    assert rec.exit_reason == "stop"


def test_confirmation_entry_succeeds(monkeypatch):
    # T+1 closes above its open → confirms long; fill at T+2 open
    bars = daily_bars(
        [
            (99, 100, 99, 100),  # T (2024-01-01): signal date
            (100, 103, 99, 102),  # T+1: close > open → confirms
            (102, 115, 102, 112),
        ],  # T+2: fill bar — target=110 hit
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})
    sig = _signal(as_of=date(2024, 1, 1))
    _stub_pipeline(monkeypatch, {date(2024, 1, 1): sig})

    rules = TradeRules(entry_trigger="confirmation")
    result = TradeLayerEvaluator(provider=provider, rules=rules).evaluate(
        "AAPL", start=date(2024, 1, 1), end=date(2024, 1, 10), step=1
    )

    assert len(result.trades) == 1
    rec = result.trades[0]
    assert rec.fill_date == date(2024, 1, 3)
    assert rec.exit_reason == "target"


def test_confirmation_entry_fails_drops_signal(monkeypatch):
    # T+1 closes below its open → confirmation fails, no trade
    bars = daily_bars(
        [
            (99, 100, 99, 100),  # T: signal date
            (100, 100, 95, 96),  # T+1: close < open → fails confirmation
            (96, 110, 95, 108),
        ],  # T+2: would have hit target but no entry
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})
    sig = _signal(as_of=date(2024, 1, 1))
    # Signal only at T (step=5 to prevent re-fire on T+1, T+2)
    _stub_pipeline(monkeypatch, {date(2024, 1, 1): sig})

    rules = TradeRules(entry_trigger="confirmation")
    result = TradeLayerEvaluator(provider=provider, rules=rules).evaluate(
        "AAPL", start=date(2024, 1, 1), end=date(2024, 1, 5), step=5
    )

    assert len(result.trades) == 0


def test_max_concurrent_blocks_reentry(monkeypatch):
    # Signal fires every business day. With max_concurrent=1, only one trade
    # opens per ticker at a time.
    bars = daily_bars(
        [(99, 100, 99, 100)] * 10,
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})
    # Same signal returned for every as_of in the window
    signals = {
        b.timestamp: _signal(
            as_of=b.timestamp, target=Decimal("999"), stop=Decimal("1"), max_holding_bars=3
        )
        for b in bars
    }
    _stub_pipeline(monkeypatch, signals)

    rules = TradeRules()
    result = TradeLayerEvaluator(provider=provider, rules=rules).evaluate(
        "AAPL", start=date(2024, 1, 1), end=date(2024, 1, 12), step=1
    )

    # Position opens on T+1 (fill bar), exits at time_stop after 3 bars.
    # Next signal eligible at exit_date + 1; second position opens, etc.
    # Should be 2-3 trades in the 10-bar window, NOT 10.
    assert 1 <= len(result.trades) <= 4


def test_min_confidence_filter_drops_signal(monkeypatch):
    bars = daily_bars(
        [(99, 100, 99, 100), (100, 115, 100, 110)],
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})
    # signal.confidence = 0.3, rules.min_confidence = 0.5
    # We bypass the derive_signal filter via the stub, so the trade-layer
    # itself must NOT enforce min_confidence (it's enforced upstream, in
    # derive_signal). The stub returns the low-confidence signal; layer
    # accepts it. This documents the contract.
    sig = _signal(as_of=date(2024, 1, 1), confidence=0.3)
    _stub_pipeline(monkeypatch, {date(2024, 1, 1): sig})

    rules = TradeRules(min_confidence=0.5)
    result = TradeLayerEvaluator(provider=provider, rules=rules).evaluate(
        "AAPL", start=date(2024, 1, 1), end=date(2024, 1, 10), step=1
    )
    # Trade layer trusts the signal it was given — filtering is upstream.
    assert len(result.trades) == 1


def test_no_bars_returns_empty_result(monkeypatch):
    provider = FixedBarsProvider({Interval.DAILY: []})
    rules = TradeRules()
    result = TradeLayerEvaluator(provider=provider, rules=rules).evaluate(
        "AAPL", start=date(2024, 1, 1), end=date(2024, 1, 10), step=1
    )
    assert result.trades == []


def test_run_trade_layer_aggregates_across_universe(monkeypatch):
    from wave_alpha.backtest.runner import BacktestConfig, run_trade_layer

    bars = daily_bars(
        [(99, 100, 99, 100), (100, 115, 100, 112)],
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})

    # Signals for each ticker (same as_of but different tickers)
    # The stub will be called with req.ticker, and we need per-ticker signals
    def fake_run_multi_ticker(req, *, provider, llm_mode, llm_runner=None, alpha=0.6):
        from wave_alpha.coherence.score import CoherenceResult
        from wave_alpha.pipeline import AnalyzeResult

        # Create a signal for any ticker that requests one
        if req.as_of == date(2024, 1, 1):
            sig = TradeSignal(
                ticker=req.ticker,
                as_of=req.as_of,
                direction="long",
                entry_price=Decimal("100"),
                stop_price=Decimal("96"),
                target_price=Decimal("110"),
                count_id="abc123",
                count_pattern="impulse",
                wave_label="5",
                degree=2,
                confidence=0.6,
                expected_bars_to_target=10,
                max_holding_bars=20,
            )
            return AnalyzeResult(
                ticker=req.ticker,
                as_of=req.as_of,
                coherence=CoherenceResult(
                    score=0.8, multiplier=1.0, pairwise={}, fallback_used=None
                ),
                signals=[sig],
            )
        return AnalyzeResult(
            ticker=req.ticker,
            as_of=req.as_of,
            coherence=CoherenceResult(score=0.8, multiplier=1.0, pairwise={}, fallback_used=None),
            signals=[],
        )

    monkeypatch.setattr("wave_alpha.backtest.trade_layer.run_analysis", fake_run_multi_ticker)

    cfg = BacktestConfig(
        universe=["AAPL", "MSFT"],
        start=date(2024, 1, 1),
        end=date(2024, 1, 5),
        step=1,
    )
    result = run_trade_layer(cfg, provider=provider, rules=TradeRules())

    assert len(result.trades) == 2
    assert {t.ticker for t in result.trades} == {"AAPL", "MSFT"}


def _tf_bars() -> list:
    """55-bar synthetic series with two deep retracements.

    The swings are large enough that detect_multi_degree at degree 2 finds
    5 pivots, enumerate_counts fits a pattern, and derive_signal emits a
    long signal — confirmed by running the real pipeline in isolation.

    Price shape (all closes, bars placed on consecutive business days):
      • Bars  0-10: monotone up  100 → 110  (wave up)
      • Bars 11-21: monotone down 110 → 100 (deep retrace)
      • Bars 22-32: monotone up  100 → 120  (wave up)
      • Bars 33-43: monotone down 120 → 100 (deep retrace)
      • Bars 44-54: monotone up  100 → 130  (final push)
    """
    prices = [
        100,
        101,
        102,
        103,
        104,
        105,
        106,
        107,
        108,
        109,
        110,
        110,
        109,
        108,
        107,
        106,
        105,
        104,
        103,
        102,
        101,
        100,
        100,
        102,
        104,
        106,
        108,
        110,
        112,
        114,
        116,
        118,
        120,
        120,
        118,
        116,
        114,
        112,
        110,
        108,
        106,
        104,
        102,
        100,
        100,
        103,
        106,
        109,
        112,
        115,
        118,
        121,
        124,
        127,
        130,
    ]
    return daily_bars(
        [(p - 0.5, p + 0.5, p - 0.5, p) for p in prices],
        start=date(2023, 1, 2),
    )


def test_trade_record_tf_count_3_when_4h_present() -> None:
    """When all three timeframes are present the real coherence layer sets
    fallback_used=None and the trade record carries tf_count == 3.

    Drives the full integration path: FixedBarsProvider → run_analysis →
    three_way coherence → _signal_at → TradeRecord.
    """
    bars = _tf_bars()
    provider = FixedBarsProvider(
        bars_by_interval={
            Interval.WEEKLY: bars,
            Interval.DAILY: bars,
            Interval.HOURLY_4: bars,
        }
    )
    evaluator = TradeLayerEvaluator(provider=provider, rules=TradeRules(min_confidence=0.0))
    result = evaluator.evaluate(
        "TEST",
        start=bars[0].timestamp,
        end=bars[-1].timestamp,
        step=1,
    )

    assert result.trades, "fixture must produce at least one trade"
    assert all(t.tf_count == 3 for t in result.trades)


def test_trade_record_tf_count_2_when_4h_missing() -> None:
    """When 4h bars are absent the real coherence layer sets
    fallback_used='no_4h_history' and the trade record carries tf_count == 2.

    Drives the full integration path: FixedBarsProvider (no HOURLY_4 key) →
    run_analysis → three_way coherence → _signal_at → TradeRecord.
    """
    bars = _tf_bars()
    provider = FixedBarsProvider(
        bars_by_interval={
            Interval.WEEKLY: bars,
            Interval.DAILY: bars,
            # Interval.HOURLY_4 intentionally omitted
        }
    )
    evaluator = TradeLayerEvaluator(provider=provider, rules=TradeRules(min_confidence=0.0))
    result = evaluator.evaluate(
        "TEST",
        start=bars[0].timestamp,
        end=bars[-1].timestamp,
        step=1,
    )

    assert result.trades, "fixture must produce at least one trade"
    assert all(t.tf_count == 2 for t in result.trades)


def test_coverage_4h_with_mixed_records() -> None:
    """coverage_4h = fraction of CLOSED trades with tf_count == 3."""
    result = TradeLayerResult(
        trades=[
            _record(tf_count=3, exit_reason="target"),
            _record(tf_count=3, exit_reason="stop"),
            _record(tf_count=2, exit_reason="target"),
            _record(tf_count=3, exit_reason="open_at_end"),  # excluded from headline
        ]
    )
    assert result.coverage_4h == pytest.approx(2 / 3)


def test_coverage_4h_no_closed_trades_returns_zero() -> None:
    result = TradeLayerResult(trades=[])
    assert result.coverage_4h == 0.0


def test_by_tf_count_breakdown_shape_and_keys() -> None:
    """Mirrors by_direction shape: dict[int, dict[str, float]] with the
    standard inner keys; sorted ascending by key."""
    result = TradeLayerResult(
        trades=[
            _record(tf_count=2, exit_reason="target", realized_R=1.0),
            _record(tf_count=2, exit_reason="stop", realized_R=-1.0),
            _record(tf_count=3, exit_reason="target", realized_R=2.0),
        ]
    )
    breakdown = result.by_tf_count()
    assert list(breakdown.keys()) == [2, 3]
    expected_inner = {"sample_size", "hit_rate", "avg_R", "expectancy", "profit_factor"}
    assert set(breakdown[2].keys()) == expected_inner
    assert breakdown[2]["sample_size"] == 2
    assert breakdown[3]["sample_size"] == 1


def test_render_trade_markdown_contains_required_sections():
    from wave_alpha.backtest.report import render_trade_markdown
    from wave_alpha.backtest.runner import BacktestConfig

    result = TradeLayerResult(
        trades=[
            _record(
                realized_R=1.5,
                exit_date=date(2024, 1, 3),
                direction="long",
                pattern="impulse",
                coherence_bucket="full",
                coherence_score=0.85,
            ),
            _record(
                realized_R=-0.8,
                exit_date=date(2024, 1, 4),
                direction="short",
                pattern="zigzag",
                coherence_bucket="partial",
                coherence_score=0.5,
                exit_reason="stop",
            ),
        ]
    )
    cfg = BacktestConfig(universe=["AAPL", "MSFT"], start=date(2024, 1, 1), end=date(2024, 12, 31))
    rules = TradeRules()

    md = render_trade_markdown(result, cfg=cfg, rules=rules)

    assert "# Trade Layer Backtest Report" in md
    assert "Closed trades:" in md
    assert "## Headline" in md
    assert "## by direction" in md
    assert "## by pattern" in md
    assert "## by coherence bucket" in md
    assert "## by year" in md
    assert "hit rate" in md.lower()
    assert "max drawdown" in md.lower()


def test_trade_record_carries_degree_and_ebt() -> None:
    """TradeRecord must expose degree + expected_bars_to_target so the
    sanity-run inspection can compute per-degree empirical distributions."""
    rec = TradeRecord(
        ticker="TEST",
        signal_as_of=date(2024, 1, 1),
        fill_date=date(2024, 1, 2),
        fill_price=Decimal("100"),
        exit_date=date(2024, 1, 10),
        exit_price=Decimal("105"),
        exit_reason="target",
        direction="long",
        pattern="impulse",
        wave_label="W5",
        coherence_score=0.8,
        coherence_bucket="full",
        bars_held=8,
        risk_per_share=Decimal("2"),
        realized_pnl_per_share=Decimal("5"),
        realized_R=2.5,
        confidence=0.7,
        tf_count=3,
        degree=3,
        expected_bars_to_target=20,
    )
    assert rec.degree == 3
    assert rec.expected_bars_to_target == 20


def test_evaluator_accumulates_n_atr_skipped(monkeypatch) -> None:
    """TradeLayerEvaluator.evaluate sums analysis.n_atr_skipped from each
    _signal_at call. Three as_ofs each reporting 2 skips → 6 total."""
    from wave_alpha.coherence.score import CoherenceResult
    from wave_alpha.pipeline import AnalyzeResult

    bars = daily_bars(
        [(100, 101, 99, 100)] * 5,
        start=date(2024, 1, 1),
    )
    provider = FixedBarsProvider({Interval.DAILY: bars})

    call_count = {"n": 0}

    def fake_run_analysis(req, *, provider, llm_mode, llm_runner=None, alpha=0.6):
        call_count["n"] += 1
        return AnalyzeResult(
            ticker=req.ticker,
            as_of=req.as_of,
            coherence=CoherenceResult(score=0.5, multiplier=1.0, pairwise={}, fallback_used=None),
            signals=[],
            n_atr_skipped=2,
        )

    monkeypatch.setattr("wave_alpha.backtest.trade_layer.run_analysis", fake_run_analysis)

    evaluator = TradeLayerEvaluator(provider=provider, rules=TradeRules(stop_source="atr_multiple"))
    result = evaluator.evaluate("TEST", start=bars[0].timestamp, end=bars[-1].timestamp, step=1)

    assert call_count["n"] >= 1, "stub should have been called at least once"
    assert result.n_atr_skipped == 2 * call_count["n"], (
        f"expected {2 * call_count['n']} cumulative skips, got {result.n_atr_skipped}"
    )


def test_run_trade_layer_aggregates_n_atr_skipped_across_universe(monkeypatch) -> None:
    """run_trade_layer sums per-ticker TradeLayerResult.n_atr_skipped into
    the aggregated result."""
    from wave_alpha.backtest.runner import BacktestConfig, run_trade_layer
    from wave_alpha.coherence.score import CoherenceResult
    from wave_alpha.pipeline import AnalyzeResult

    bars = daily_bars([(100, 101, 99, 100)] * 5, start=date(2024, 1, 1))
    provider = FixedBarsProvider({Interval.DAILY: bars})

    def fake_run_analysis(req, *, provider, llm_mode, llm_runner=None, alpha=0.6):
        return AnalyzeResult(
            ticker=req.ticker,
            as_of=req.as_of,
            coherence=CoherenceResult(score=0.5, multiplier=1.0, pairwise={}, fallback_used=None),
            signals=[],
            n_atr_skipped=1,
        )

    monkeypatch.setattr("wave_alpha.backtest.trade_layer.run_analysis", fake_run_analysis)

    cfg = BacktestConfig(
        universe=["AAA", "BBB", "CCC"],
        start=bars[0].timestamp,
        end=bars[-1].timestamp,
        step=1,
    )
    aggregated = run_trade_layer(cfg, provider=provider, rules=TradeRules())

    # 3 tickers, N _signal_at calls per ticker, 1 skip per call. We don't
    # pin the per-ticker call count (depends on iter_business_days output
    # for the 5-bar window), only assert it's at least 3 (1 per ticker
    # minimum) and divisible by 3 (consistency across the universe).
    assert aggregated.n_atr_skipped >= 3
    assert aggregated.n_atr_skipped % 3 == 0
