"""End-to-end guard: a provider that injects a future bar must trigger the
PointInTimeView lookahead assertion. If this test ever passes silently when
a future bar leaks, the harness has a hole."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from wave_alpha.backtest.runner import BacktestConfig, run_count_layer
from wave_alpha.data.point_in_time import LookaheadError, UnsortedBarsError
from wave_alpha.domain import Bar, Interval


class _UnsortedProvider:
    """Returns bars NOT in ascending timestamp order — should trip
    UnsortedBarsError when wrapped in PointInTimeView."""

    def fetch(self, ticker: str, interval: Interval, *, end: date) -> list[Bar]:
        bars = []
        d = date(2024, 1, 1)
        i = 0
        while d <= date(2024, 6, 1):
            if d.weekday() < 5:
                bars.append(_bar(d, 100 + i))
                i += 1
            d = d + timedelta(days=1)
        # Inject an out-of-order bar at the end (timestamp BEFORE the last)
        bars.append(_bar(date(2024, 3, 1), 999))
        return bars


def _bar(d: date, p: float) -> Bar:
    return Bar(
        timestamp=d,
        open=Decimal(str(p)),
        high=Decimal(str(p + 1)),
        low=Decimal(str(p - 1)),
        close=Decimal(str(p)),
        volume=1_000,
    )


def test_unsorted_provider_trips_assertion_during_count_backtest() -> None:
    cfg = BacktestConfig(
        universe=["AAPL"],
        start=date(2024, 2, 1),
        end=date(2024, 4, 1),
        step=5,
        n_bars_forward=10,
    )
    with pytest.raises((LookaheadError, UnsortedBarsError)):
        run_count_layer(cfg, provider=_UnsortedProvider())
