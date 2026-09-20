from __future__ import annotations

from datetime import date
from pathlib import Path

from wave_alpha.data.binance import BinanceProvider
from wave_alpha.data.cached import CachedProvider
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval


class IntervalRoutedProvider(OHLCVProvider):
    """Dispatches `fetch` to a different upstream provider per interval.

    Exists to keep wave_alpha's shared SQLite OHLCVCache (keyed on
    (ticker, interval, date) — see data/cache.py) away from any interval
    that can have more than one bar per calendar date. For a 24/7 market
    like BTCUSDT, HOURLY_4 has 6 bars/day, HOURLY_1 has 24, and MINUTE_30
    has 48; the cache's INSERT OR REPLACE would silently keep only the
    last one written per day. Routing all three straight to the uncached
    provider avoids that data loss entirely — Binance's public klines
    endpoint is cheap enough to call fresh every time.
    """

    def __init__(self, by_interval: dict[Interval, OHLCVProvider]) -> None:
        self._by_interval = by_interval

    @property
    def supported_intervals(self) -> frozenset[Interval]:
        return frozenset(self._by_interval.keys())

    def fetch(
        self,
        ticker: str,
        interval: Interval,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        provider = self._by_interval.get(interval)
        if provider is None:
            return []
        return provider.fetch(ticker, interval, start=start, end=end)


def binance_provider_stack(db_path: Path | str) -> OHLCVProvider:
    """Production provider for Binance symbols (e.g. "BTCUSDT").

    WEEKLY and DAILY (at most one bar per calendar date) are safely cached
    on disk via CachedProvider, the same as wave_alpha's stock pipeline.
    HOURLY_4, HOURLY_1 and MINUTE_30 all bypass the disk cache — see
    IntervalRoutedProvider's docstring for why — and always hit Binance's
    REST API directly. Registering all five here (rather than just the
    three the default Swing preset uses) lets `run_analysis` be called
    with any timeframe triple — e.g. the Scalping preset (4h/1h/30m) in
    dashboard.py — without needing a separate provider stack.
    """
    cached_daily_weekly = CachedProvider(upstream=BinanceProvider(), db_path=db_path)
    uncached = BinanceProvider()  # always fresh, never cached
    return IntervalRoutedProvider(
        {
            Interval.WEEKLY: cached_daily_weekly,
            Interval.DAILY: cached_daily_weekly,
            Interval.HOURLY_4: uncached,
            Interval.HOURLY_1: uncached,
            Interval.MINUTE_30: uncached,
        }
    )
