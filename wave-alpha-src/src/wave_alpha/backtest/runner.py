from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from wave_alpha.backtest.count_layer import CountLayerEvaluator, CountLayerResult
from wave_alpha.backtest.pivot_layer import PivotLayerEvaluator, PivotLayerResult
from wave_alpha.backtest.trade_layer import TradeLayerEvaluator, TradeLayerResult
from wave_alpha.backtest.walk_forward import iter_business_days
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.trades.models import TradeRules


@dataclass(frozen=True)
class BacktestConfig:
    universe: list[str]
    start: date
    end: date
    step: int = 1
    n_bars_forward: int = 20


def run_count_layer(cfg: BacktestConfig, *, provider: OHLCVProvider) -> CountLayerResult:
    """Run the count layer over (universe x dates).

    Returns one CountLayerResult aggregating all (ticker, as_of) samples.
    The runner deliberately does NOT instantiate any LLM client — count-layer
    backtest is `--llm-mode disabled` by spec §8.
    """
    as_ofs = list(iter_business_days(cfg.start, cfg.end, step=cfg.step))
    evaluator = CountLayerEvaluator(provider=provider, n_bars_forward=cfg.n_bars_forward)
    aggregated = CountLayerResult(evaluations=[], n_bars_forward=cfg.n_bars_forward)
    for ticker in cfg.universe:
        per_ticker = evaluator.evaluate(ticker, as_ofs)
        aggregated.evaluations.extend(per_ticker.evaluations)
    return aggregated


def run_pivot_layer(
    cfg: BacktestConfig, *, provider: OHLCVProvider, lookahead_bars: int = 20
) -> PivotLayerResult:
    """Run the pivot layer over (universe x initial_date pairs).

    For each ticker, takes a snapshot at `cfg.start` and a final snapshot at
    `cfg.end`. v0.4 keeps this simple: one initial+final pair per ticker.
    Walk-forward extension lands in v0.4.x once the harness shape is proven.
    """
    evaluator = PivotLayerEvaluator(provider=provider, lookahead_bars=lookahead_bars)
    aggregated = PivotLayerResult(evaluations=[])
    for ticker in cfg.universe:
        per_ticker = evaluator.evaluate(ticker, initial=cfg.start, final=cfg.end)
        aggregated.evaluations.extend(per_ticker.evaluations)
    return aggregated


def run_trade_layer(
    cfg: BacktestConfig, *, provider: OHLCVProvider, rules: TradeRules
) -> TradeLayerResult:
    """Run the trade layer over (universe x dates). Returns aggregated
    TradeLayerResult. LLM is disabled by construction; signal generation
    routes through pipeline.run_analysis which uses PointInTimeView."""
    aggregated = TradeLayerResult()
    for ticker in cfg.universe:
        evaluator = TradeLayerEvaluator(provider=provider, rules=rules)
        per_ticker = evaluator.evaluate(ticker, start=cfg.start, end=cfg.end, step=cfg.step)
        aggregated.trades.extend(per_ticker.trades)
        aggregated.n_atr_skipped += per_ticker.n_atr_skipped
    return aggregated
