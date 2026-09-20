from datetime import date
from decimal import Decimal

from wave_alpha.data.cached import CachedProvider
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval


class _CallCountingProvider(OHLCVProvider):
    """Returns a fixed bar set and counts how many times fetch() was invoked.

    Intentionally ignores start/end — filtering is the test subject
    (CachedProvider's responsibility), not this fixture's.
    """

    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars
        self.calls = 0

    def fetch(
        self,
        ticker: str,
        interval: Interval,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        self.calls += 1
        return list(self._bars)


def _bar(d: date, price: str = "100") -> Bar:
    p = Decimal(price)
    return Bar(timestamp=d, open=p, high=p, low=p, close=p, volume=1)


def test_cached_provider_calls_upstream_once_then_serves_from_cache(tmp_path) -> None:
    upstream = _CallCountingProvider(bars=[_bar(date(2024, 1, d)) for d in (2, 3, 4)])
    cached = CachedProvider(upstream=upstream, db_path=tmp_path / "c.sqlite")

    out_first = cached.fetch("AAPL", Interval.DAILY)
    out_second = cached.fetch("AAPL", Interval.DAILY)

    assert len(out_first) == 3
    assert len(out_second) == 3
    assert upstream.calls == 1, f"expected 1 upstream call after cache write, got {upstream.calls}"


def test_cached_provider_isolates_tickers_and_intervals(tmp_path) -> None:
    upstream = _CallCountingProvider(bars=[_bar(date(2024, 1, 2))])
    cached = CachedProvider(upstream=upstream, db_path=tmp_path / "c.sqlite")

    cached.fetch("AAPL", Interval.DAILY)
    cached.fetch("AAPL", Interval.WEEKLY)
    cached.fetch("MSFT", Interval.DAILY)

    assert upstream.calls == 3


def test_cached_provider_does_not_persist_empty_results(tmp_path) -> None:
    upstream = _CallCountingProvider(bars=[])
    cached = CachedProvider(upstream=upstream, db_path=tmp_path / "c.sqlite")

    cached.fetch("AAPL", Interval.DAILY)
    cached.fetch("AAPL", Interval.DAILY)

    assert upstream.calls == 2  # both calls hit upstream because cache stays empty


def test_cached_provider_old_end_after_one_upstream_call_no_more_upstreams(tmp_path) -> None:
    """Backtest pattern: many sequential old `end` dates must trigger only ONE
    upstream call (the first one populates the cache; subsequent calls hit
    the freshness short-circuit even though their filtered slice is empty)."""
    upstream_bars = [_bar(date(2024, 5, d)) for d in (1, 2, 3, 4, 5)]
    upstream = _CallCountingProvider(bars=upstream_bars)
    cached = CachedProvider(upstream=upstream, db_path=tmp_path / "c.sqlite")

    out_a = cached.fetch("AAPL", Interval.HOURLY_4, end=date(2020, 1, 9))
    out_b = cached.fetch("AAPL", Interval.HOURLY_4, end=date(2020, 1, 10))
    out_c = cached.fetch("AAPL", Interval.HOURLY_4, end=date(2020, 1, 11))

    assert upstream.calls == 1, f"expected 1 upstream call, got {upstream.calls}"
    assert out_a == [], "old end → empty slice"
    assert out_b == [], "old end → empty slice"
    assert out_c == [], "old end → empty slice"


def test_cached_provider_first_call_populates_cache_with_full_upstream_window(tmp_path) -> None:
    """The full upstream payload must be persisted, not just the end-filtered subset."""
    upstream_bars = [_bar(date(2024, 5, d)) for d in (1, 2, 3, 4, 5)]
    upstream = _CallCountingProvider(bars=upstream_bars)
    cached = CachedProvider(upstream=upstream, db_path=tmp_path / "c.sqlite")

    cached.fetch("AAPL", Interval.HOURLY_4, end=date(2020, 1, 9))
    # Cache should now hold the full 5 bars even though caller asked for end=2020.
    assert cached._cache.max_ts("AAPL", Interval.HOURLY_4) == date(2024, 5, 5)


def test_cached_provider_filter_window_respects_start_and_end(tmp_path) -> None:
    """When the cache holds many bars, fetch must filter by start AND end."""
    upstream_bars = [_bar(date(2024, 1, d)) for d in (10, 11, 12, 13, 14)]
    upstream = _CallCountingProvider(bars=upstream_bars)
    cached = CachedProvider(upstream=upstream, db_path=tmp_path / "c.sqlite")

    cached.fetch("AAPL", Interval.DAILY)  # populate cache

    out = cached.fetch("AAPL", Interval.DAILY, start=date(2024, 1, 11), end=date(2024, 1, 13))
    assert [b.timestamp for b in out] == [date(2024, 1, 11), date(2024, 1, 12), date(2024, 1, 13)]
