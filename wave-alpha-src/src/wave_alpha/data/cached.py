from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from loguru import logger

from wave_alpha.data.cache import OHLCVCache
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval

_STALENESS_TOLERANCE = timedelta(days=1)


class CachedProvider(OHLCVProvider):
    """Decorator that consults an OHLCVCache before delegating to an upstream
    provider.

    Cache hit semantics: returns cache contents when the tail is fresh enough
    for the requested `end` date (within _STALENESS_TOLERANCE) and the cache
    has been populated at least once for `(ticker, interval)`. An empty
    filtered window (when `end` falls before the cache's earliest bar) is a
    valid cache-hit result — the upstream is not retried. If `end` is None,
    any cached data is served without a freshness check.

    Cache misses (or stale cache) delegate to the upstream provider and the
    result is written back to the cache before returning. If upstream returns
    nothing, cached data is returned as a fallback so transient provider
    outages don't blow away the working set.
    """

    def __init__(self, upstream: OHLCVProvider, db_path: Path | str) -> None:
        self._upstream = upstream
        self._cache = OHLCVCache(db_path=db_path)

    def fetch(
        self,
        ticker: str,
        interval: Interval,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        if self._tail_fresh_enough(ticker, interval, end):
            cached = self._cache.get(ticker, interval, start=start, end=end)
            logger.debug(
                "cache hit ticker={} interval={} bars={}", ticker, interval.value, len(cached)
            )
            return cached
        logger.debug(
            "cache miss or stale ticker={} interval={} end={}; calling upstream",
            ticker,
            interval.value,
            end,
        )
        # Two filtering implementations coexist by design: OHLCVCache.get
        # applies SQL-level start/end filters on the cache-hit and upstream-miss
        # paths, _filter_window applies the same filter in Python on the
        # upstream-success path before the unfiltered bars get persisted.
        bars = self._upstream.fetch(ticker, interval, start=start, end=end)
        if bars:
            self._cache.put(ticker, interval, bars)
            return self._filter_window(bars, start=start, end=end)
        # Upstream returned nothing — return whatever the (possibly empty) cache holds.
        return self._cache.get(ticker, interval, start=start, end=end)

    @staticmethod
    def _filter_window(bars: list[Bar], *, start: date | None, end: date | None) -> list[Bar]:
        if start is not None:
            bars = [b for b in bars if b.timestamp >= start]
        if end is not None:
            bars = [b for b in bars if b.timestamp <= end]
        return bars

    def _tail_fresh_enough(self, ticker: str, interval: Interval, end: date | None) -> bool:
        """True if the cache has data and is recent enough for this end date.

        end=None → caller doesn't care about freshness; hit if cache is non-empty.
        Otherwise: cache must contain a bar within _STALENESS_TOLERANCE of end.
        """
        max_ts = self._cache.max_ts(ticker, interval)
        if max_ts is None:
            return False
        if end is None:
            return True
        return (end - max_ts) <= _STALENESS_TOLERANCE
