from datetime import date

from tests.backtest._synthetic import _record
from wave_alpha.backtest.count_layer import CountEvaluation, CountLayerResult
from wave_alpha.backtest.pivot_layer import PivotEvaluation, PivotLayerResult
from wave_alpha.backtest.report import (
    render_count_markdown,
    render_pivot_markdown,
    render_trade_markdown,
)
from wave_alpha.backtest.runner import BacktestConfig
from wave_alpha.backtest.trade_layer import TradeLayerResult
from wave_alpha.trades.models import TradeRules


def test_render_count_markdown_includes_top1_and_top3_rates() -> None:
    res = CountLayerResult(
        evaluations=[
            CountEvaluation("A", date(2024, 1, 5), "up", "up", 0.8, True, ("up",), True),
            CountEvaluation("B", date(2024, 1, 5), "up", "down", 0.4, False, ("up",), False),
        ],
        n_bars_forward=20,
    )
    md = render_count_markdown(res)
    assert "# Count Layer" in md
    assert "top-1 hit rate" in md
    assert "0.50" in md
    assert "n=2" in md


def test_render_count_markdown_includes_coherence_buckets_and_year_breakdown() -> None:
    res = CountLayerResult(
        evaluations=[
            CountEvaluation("A", date(2023, 6, 1), "up", "up", 0.8, True, ("up",), True),
            CountEvaluation("B", date(2024, 6, 1), "up", "down", 0.5, False, ("up",), False),
            CountEvaluation("C", date(2024, 6, 1), "down", "up", 0.2, False, ("up",), True),
        ],
        n_bars_forward=20,
    )
    md = render_count_markdown(res)
    assert "by coherence bucket" in md
    assert "by year" in md
    assert "2023" in md
    assert "2024" in md


def test_render_pivot_markdown_includes_per_degree_and_brier() -> None:
    res = PivotLayerResult(
        evaluations=[
            PivotEvaluation("A", "2024-01-01", "2024-02-01", 0, "confirmed", 0.7),
            PivotEvaluation("A", "2024-01-01", "2024-02-01", 0, "invalidated", 0.7),
            PivotEvaluation("A", "2024-01-01", "2024-02-01", 1, "confirmed", 0.6),
        ]
    )
    md = render_pivot_markdown(res)
    assert "# Pivot Layer" in md
    assert "per-degree confirmation" in md
    assert "Brier" in md
    assert "d=0" in md
    assert "d=1" in md


def test_render_trade_markdown_includes_4h_coverage_when_trades_exist() -> None:
    """`**4h coverage:**` line is rendered with a percentage and counts."""
    cfg = BacktestConfig(universe=["TEST"], start=date(2024, 1, 1), end=date(2024, 12, 31), step=1)
    rules = TradeRules()
    result = TradeLayerResult(
        trades=[
            _record(tf_count=3, exit_reason="target"),
            _record(tf_count=2, exit_reason="stop"),
        ]
    )
    md = render_trade_markdown(result, cfg=cfg, rules=rules)
    assert "**4h coverage:**" in md
    assert "50% (1 / 2 closed trades had 4h coherence)" in md


def test_render_trade_markdown_4h_coverage_na_when_no_closed() -> None:
    cfg = BacktestConfig(universe=["TEST"], start=date(2024, 1, 1), end=date(2024, 12, 31), step=1)
    rules = TradeRules()
    result = TradeLayerResult(trades=[])
    md = render_trade_markdown(result, cfg=cfg, rules=rules)
    assert "**4h coverage:** n/a" in md
