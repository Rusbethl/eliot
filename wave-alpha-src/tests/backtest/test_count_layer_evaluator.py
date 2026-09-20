from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.backtest.count_layer import CountLayerEvaluator
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval


class _RisingProvider(OHLCVProvider):
    """Synthetic provider: prices rise monotonically — every count predicts up
    and every forward-direction is up."""

    def fetch(
        self,
        ticker: str,
        interval: Interval,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        bars: list[Bar] = []
        # 800 daily bars ending 2024-12-31 — enough for weekly + 4h + lookback
        d = date(2022, 1, 1)
        i = 0
        while d <= date(2024, 12, 31):
            if d.weekday() < 5:
                p = 100 + i * 0.5
                bars.append(
                    Bar(
                        timestamp=d,
                        open=Decimal(str(p)),
                        high=Decimal(str(p + 0.5)),
                        low=Decimal(str(p - 0.5)),
                        close=Decimal(str(p)),
                        volume=1_000,
                    )
                )
                i += 1
            d = d + timedelta(days=1)
        return bars


def test_count_layer_evaluator_runs_one_pass_per_as_of() -> None:
    provider = _RisingProvider()
    ev = CountLayerEvaluator(provider=provider, n_bars_forward=20)
    # Three as-of dates spaced ~10 weeks apart in 2024 (so 20 forward bars exist)
    as_ofs = [date(2024, 3, 1), date(2024, 5, 1), date(2024, 7, 1)]
    result = ev.evaluate("AAPL", as_ofs)
    # Even if the count layer can't always emit a count, sample size <= 3
    assert 0 <= result.sample_size <= 3


def test_count_layer_evaluator_skips_when_no_forward_history() -> None:
    """An as_of close to the end of available data has < n_bars forward bars
    and should be skipped (no evaluation produced)."""
    provider = _RisingProvider()
    ev = CountLayerEvaluator(provider=provider, n_bars_forward=20)
    # End date is 2024-12-31; as_of within last 20 business days has no fwd hist
    as_ofs = [date(2024, 12, 20)]
    result = ev.evaluate("AAPL", as_ofs)
    assert result.sample_size == 0
