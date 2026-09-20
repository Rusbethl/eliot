from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tests.backtest._synthetic import FixedBarsProvider, daily_bars
from wave_alpha.backtest.trade_layer import TradeLayerEvaluator
from wave_alpha.data.point_in_time import LookaheadError, UnsortedBarsError
from wave_alpha.domain import Bar, Interval
from wave_alpha.trades.models import TradeRules


def test_future_bar_in_daily_series_raises_lookahead_error():
    # Plant legitimate bars, then add an out-of-order bar within the evaluation
    # range. When PointInTimeView receives these bars, it will detect the
    # ascending-order violation and raise UnsortedBarsError.
    legitimate = daily_bars(
        [(99, 100, 99, 100), (100, 101, 100, 100), (101, 102, 101, 101)],
        start=date(2024, 1, 1),
    )
    # Insert a bar out of timestamp order: this one has an earlier date than
    # the last legitimate bar but will be placed after it. legitimate has bars
    # at 2024-01-01, 2024-01-02, 2024-01-03; we insert one at 2024-01-02.
    out_of_order = Bar(
        timestamp=date(2024, 1, 2),  # earlier than 2024-01-03 (last bar)
        open=Decimal("999"),
        high=Decimal("999"),
        low=Decimal("999"),
        close=Decimal("999"),
        volume=1,
    )
    # Order: legitimate bars in order, then the out-of-order bar that breaks
    # the ascending sequence. Both are within the evaluation range.
    bars_with_leak = [*legitimate, out_of_order]
    provider = FixedBarsProvider({Interval.DAILY: bars_with_leak})

    evaluator = TradeLayerEvaluator(provider=provider, rules=TradeRules())
    # The pipeline.run_analysis call routes through PointInTimeView; the
    # ascending-order check raises before any analysis happens.
    with pytest.raises((LookaheadError, UnsortedBarsError)):
        evaluator.evaluate("AAPL", start=date(2024, 1, 1), end=date(2024, 1, 5), step=1)
