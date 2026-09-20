from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.data.cache import OHLCVCache
from wave_alpha.domain import Bar, Interval


def _bar(d: date, price: str) -> Bar:
    p = Decimal(price)
    return Bar(timestamp=d, open=p, high=p, low=p, close=p, volume=1)


def test_cache_roundtrip(tmp_path) -> None:
    cache = OHLCVCache(db_path=tmp_path / "cache.sqlite")
    bars = [_bar(date(2024, 1, d), "100") for d in (2, 3, 4)]
    cache.put("AAPL", Interval.DAILY, bars)
    out = cache.get("AAPL", Interval.DAILY)
    assert [b.timestamp for b in out] == [b.timestamp for b in bars]


def test_cache_upsert_replaces_overlapping_dates(tmp_path) -> None:
    cache = OHLCVCache(db_path=tmp_path / "cache.sqlite")
    cache.put("AAPL", Interval.DAILY, [_bar(date(2024, 1, 2), "100")])
    cache.put("AAPL", Interval.DAILY, [_bar(date(2024, 1, 2), "200")])
    out = cache.get("AAPL", Interval.DAILY)
    assert len(out) == 1
    assert out[0].close == Decimal("200")


def test_cache_isolates_intervals(tmp_path) -> None:
    cache = OHLCVCache(db_path=tmp_path / "cache.sqlite")
    cache.put("AAPL", Interval.DAILY, [_bar(date(2024, 1, 2), "100")])
    cache.put("AAPL", Interval.WEEKLY, [_bar(date(2024, 1, 5), "200")])
    assert len(cache.get("AAPL", Interval.DAILY)) == 1
    assert cache.get("AAPL", Interval.DAILY)[0].close == Decimal("100")


def test_cache_handles_concurrent_writes(tmp_path) -> None:
    """Regression: the web UI fans out ~8 HTMX fragments per deep-dive load
    that each independently miss the cache and race to write. Without WAL +
    busy_timeout this raised sqlite3.OperationalError: database is locked.
    """
    cache = OHLCVCache(db_path=tmp_path / "cache.sqlite")

    def write(ticker: str) -> int:
        bars = [_bar(date(2024, 1, 1) + timedelta(days=i), "100") for i in range(200)]
        cache.put(ticker, Interval.DAILY, bars)
        return len(bars)

    tickers = [f"T{i:02d}" for i in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(write, t) for t in tickers]
        # Raises if any worker hit "database is locked".
        for f in as_completed(futures):
            assert f.result() == 200

    for t in tickers:
        assert len(cache.get(t, Interval.DAILY)) == 200
