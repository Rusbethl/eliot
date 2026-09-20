from datetime import date
from decimal import Decimal
from pathlib import Path

from wave_alpha.data.cache import OHLCVCache
from wave_alpha.data.cached import CachedProvider
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval


class _RecordingUpstream(OHLCVProvider):
    def __init__(self, bars: list[Bar]) -> None:
        self.bars = bars
        self.calls = 0

    def fetch(self, ticker, interval, *, start=None, end=None):
        self.calls += 1
        return self.bars


def _bar(d: date, p: float = 100.0) -> Bar:
    return Bar(
        timestamp=d,
        open=Decimal(str(p)),
        high=Decimal(str(p + 1)),
        low=Decimal(str(p - 1)),
        close=Decimal(str(p)),
        volume=1_000,
    )


def test_max_ts_returns_none_when_cache_empty(tmp_path: Path) -> None:
    cache = OHLCVCache(db_path=tmp_path / "c.sqlite")
    assert cache.max_ts("AAPL", Interval.DAILY) is None


def test_max_ts_returns_latest_cached_date(tmp_path: Path) -> None:
    cache = OHLCVCache(db_path=tmp_path / "c.sqlite")
    cache.put("AAPL", Interval.DAILY, [_bar(date(2024, 1, 5)), _bar(date(2024, 1, 8))])
    assert cache.max_ts("AAPL", Interval.DAILY) == date(2024, 1, 8)


def test_cached_provider_refetches_when_tail_stale(tmp_path: Path) -> None:
    upstream = _RecordingUpstream([_bar(date(2024, 1, 5)), _bar(date(2024, 1, 8))])
    provider = CachedProvider(upstream=upstream, db_path=tmp_path / "c.sqlite")

    # Prime cache with stale data
    provider.fetch("AAPL", Interval.DAILY, end=date(2024, 1, 8))
    assert upstream.calls == 1

    # Same end date → should NOT refetch
    provider.fetch("AAPL", Interval.DAILY, end=date(2024, 1, 8))
    assert upstream.calls == 1

    # Far-future end date → SHOULD refetch
    provider.fetch("AAPL", Interval.DAILY, end=date(2024, 6, 1))
    assert upstream.calls == 2


def test_cached_provider_does_not_refetch_within_tolerance(tmp_path: Path) -> None:
    """Tolerance is 1 day — a same-day or next-day end should not thrash."""
    upstream = _RecordingUpstream([_bar(date(2024, 1, 5)), _bar(date(2024, 1, 8))])
    provider = CachedProvider(upstream=upstream, db_path=tmp_path / "c.sqlite")

    provider.fetch("AAPL", Interval.DAILY, end=date(2024, 1, 8))
    assert upstream.calls == 1

    # end = max_ts + 1 day → still within tolerance (no refetch)
    provider.fetch("AAPL", Interval.DAILY, end=date(2024, 1, 9))
    assert upstream.calls == 1

    # end = max_ts + 2 days → outside tolerance (refetch)
    provider.fetch("AAPL", Interval.DAILY, end=date(2024, 1, 10))
    assert upstream.calls == 2


def test_cached_provider_no_refetch_when_end_omitted(tmp_path: Path) -> None:
    """end=None → caller doesn't care about freshness; serve cache."""
    upstream = _RecordingUpstream([_bar(date(2024, 1, 5))])
    provider = CachedProvider(upstream=upstream, db_path=tmp_path / "c.sqlite")
    provider.fetch("AAPL", Interval.DAILY, end=date(2024, 1, 5))
    assert upstream.calls == 1
    provider.fetch("AAPL", Interval.DAILY)  # no end
    assert upstream.calls == 1
