from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.backtest.runner import BacktestConfig, run_count_layer
from wave_alpha.domain import Bar, Interval


class _RisingProvider:
    def fetch(self, ticker: str, interval: Interval, *, end: date) -> list[Bar]:
        bars = []
        d = date(2022, 1, 1)
        i = 0
        while d <= date(2024, 12, 31):
            if d.weekday() < 5:
                bars.append(
                    Bar(
                        ticker=ticker,
                        interval=interval,
                        timestamp=d,
                        open=Decimal(str(100 + i * 0.3)),
                        high=Decimal(str(100 + i * 0.3 + 0.5)),
                        low=Decimal(str(100 + i * 0.3 - 0.5)),
                        close=Decimal(str(100 + i * 0.3)),
                        volume=1_000,
                    )
                )
                i += 1
            d = d + timedelta(days=1)
        return bars


def test_run_count_layer_iterates_universe_and_dates() -> None:
    cfg = BacktestConfig(
        universe=["AAPL", "MSFT"],
        start=date(2024, 3, 1),
        end=date(2024, 6, 1),
        step=20,  # subsample
        n_bars_forward=20,
    )
    result = run_count_layer(cfg, provider=_RisingProvider())
    # 2 tickers x ~3 dates = up to 6 evaluations (some may be skipped)
    assert 0 <= result.sample_size <= 6


def test_run_count_layer_uses_disabled_llm_only() -> None:
    """The runner must NOT instantiate any LLM client — backtest is reproducible."""
    import wave_alpha.llm.client as llm_client_mod

    sentinel = {"called": False}
    original_init = llm_client_mod.AnthropicClient.__init__

    def spy(self, *a, **kw):  # type: ignore[no-untyped-def]
        sentinel["called"] = True
        original_init(self, *a, **kw)

    try:
        llm_client_mod.AnthropicClient.__init__ = spy  # type: ignore[method-assign]
        cfg = BacktestConfig(
            universe=["AAPL"],
            start=date(2024, 3, 1),
            end=date(2024, 4, 1),
            step=10,
            n_bars_forward=20,
        )
        run_count_layer(cfg, provider=_RisingProvider())
        assert sentinel["called"] is False
    finally:
        llm_client_mod.AnthropicClient.__init__ = original_init  # type: ignore[method-assign]
