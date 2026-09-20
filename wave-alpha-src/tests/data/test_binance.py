from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from wave_alpha.data.binance import BinanceProvider, _klines_to_bars
from wave_alpha.data.binance_stack import IntervalRoutedProvider, binance_provider_stack
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval


def _kline(open_time: datetime, o: str, h: str, low: str, c: str, vol: str) -> list:
    """Build one Binance kline row (only the fields wave_alpha reads)."""
    ms = int(open_time.timestamp() * 1000)
    return [ms, o, h, low, c, vol, ms + 14_400_000 - 1, "0", 1, "0", "0", "0"]


def test_klines_to_bars_basic_parsing() -> None:
    rows = [
        _kline(datetime(2024, 3, 5, 0, 0, tzinfo=timezone.utc), "60000", "61000", "59500", "60500", "123.456")
    ]
    bars = _klines_to_bars(rows)
    assert len(bars) == 1
    b = bars[0]
    assert b.timestamp == date(2024, 3, 5)
    assert b.open == Decimal("60000")
    assert b.high == Decimal("61000")
    assert b.low == Decimal("59500")
    assert b.close == Decimal("60500")
    assert b.volume == 123  # int() truncation, matches Yahoo/Stooq convention


def test_klines_to_bars_skips_malformed_rows() -> None:
    good = _kline(datetime(2024, 3, 5, tzinfo=timezone.utc), "1", "2", "0.5", "1.5", "10")
    bad = [None]  # will raise IndexError/TypeError when indexed
    bars = _klines_to_bars([good, bad])
    assert len(bars) == 1


def test_klines_to_bars_multiple_bars_same_calendar_date() -> None:
    """The exact real-world case that motivated bar_index in the pivot
    layer: BTCUSDT trades 24/7, so a single UTC date has six 4h candles.
    All six must survive parsing (this provider's job is just to parse
    correctly; the caching bypass in binance_stack.py is what keeps them
    from being collapsed downstream)."""
    day = datetime(2024, 3, 5, tzinfo=timezone.utc)
    rows = [
        _kline(day.replace(hour=h), str(100 + h), str(101 + h), str(99 + h), str(100 + h), "5")
        for h in (0, 4, 8, 12, 16, 20)
    ]
    bars = _klines_to_bars(rows)
    assert len(bars) == 6
    assert all(b.timestamp == date(2024, 3, 5) for b in bars)
    # Distinct prices prove these are six real, distinct bars, not duplicates.
    assert len({b.open for b in bars}) == 6


def test_fetch_returns_empty_for_unsupported_interval() -> None:
    provider = BinanceProvider()
    # HOURLY_4 is supported; construct a provider call for something absent
    # from the enum-derived mapping by monkeypatching supported_intervals.
    provider.supported_intervals = frozenset()
    assert provider.fetch("BTCUSDT", Interval.DAILY) == []


def test_fetch_single_page(monkeypatch) -> None:
    calls: list[str] = []
    day = datetime(2024, 3, 5, tzinfo=timezone.utc)
    page = [_kline(day, "100", "101", "99", "100.5", "10")]

    def fake_http_get_json(url: str, *, timeout: float = 30.0) -> list:
        calls.append(url)
        return page

    monkeypatch.setattr("wave_alpha.data.binance._http_get_json", fake_http_get_json)
    provider = BinanceProvider()
    bars = provider.fetch("BTCUSDT", Interval.HOURLY_4)

    assert len(bars) == 1
    assert bars[0].timestamp == date(2024, 3, 5)
    assert len(calls) == 1
    assert "symbol=BTCUSDT" in calls[0]
    assert "interval=4h" in calls[0]


def test_fetch_paginates_past_1000_limit(monkeypatch) -> None:
    """Binance caps each response at 1000 klines. Verify the provider keeps
    requesting with an advancing startTime until a short page signals the
    end, and that it doesn't loop forever."""
    day0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    full_page = [
        _kline(
            datetime.fromtimestamp(day0.timestamp() + i * 14_400, tz=timezone.utc),
            "100",
            "101",
            "99",
            "100",
            "1",
        )
        for i in range(1000)
    ]
    short_page = [
        _kline(
            datetime.fromtimestamp(day0.timestamp() + 1000 * 14_400, tz=timezone.utc),
            "200",
            "201",
            "199",
            "200",
            "1",
        )
    ]
    responses = [full_page, short_page]

    def fake_http_get_json(url: str, *, timeout: float = 30.0) -> list:
        return responses.pop(0) if responses else []

    monkeypatch.setattr("wave_alpha.data.binance._http_get_json", fake_http_get_json)
    provider = BinanceProvider()
    bars = provider.fetch("BTCUSDT", Interval.HOURLY_4)

    assert len(bars) == 1001
    assert not responses  # exactly two calls consumed, no runaway pagination


def test_fetch_stops_on_repeated_page_to_avoid_infinite_loop(monkeypatch) -> None:
    day = datetime(2024, 3, 5, tzinfo=timezone.utc)
    stuck_page = [_kline(day, "100", "101", "99", "100", "1")]
    call_count = {"n": 0}

    def fake_http_get_json(url: str, *, timeout: float = 30.0) -> list:
        call_count["n"] += 1
        if call_count["n"] > 5:
            raise AssertionError("provider did not detect the stuck cursor and looped")
        return stuck_page

    monkeypatch.setattr("wave_alpha.data.binance._http_get_json", fake_http_get_json)
    provider = BinanceProvider()
    bars = provider.fetch("BTCUSDT", Interval.HOURLY_4)
    assert len(bars) == 1


def test_fetch_returns_empty_on_http_error(monkeypatch) -> None:
    def boom(url: str, *, timeout: float = 30.0) -> list:
        raise TimeoutError("upstream down")

    monkeypatch.setattr("wave_alpha.data.binance._http_get_json", boom)
    provider = BinanceProvider()
    assert provider.fetch("BTCUSDT", Interval.DAILY) == []


class _FixedProvider(OHLCVProvider):
    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars

    def fetch(self, ticker, interval, start=None, end=None) -> list[Bar]:
        return self._bars


def test_interval_routed_provider_dispatches_by_interval() -> None:
    daily_bar = Bar(
        timestamp=date(2024, 1, 1),
        open=Decimal("1"),
        high=Decimal("1"),
        low=Decimal("1"),
        close=Decimal("1"),
        volume=1,
    )
    hourly_bar = Bar(
        timestamp=date(2024, 1, 2),
        open=Decimal("2"),
        high=Decimal("2"),
        low=Decimal("2"),
        close=Decimal("2"),
        volume=2,
    )
    router = IntervalRoutedProvider(
        {
            Interval.DAILY: _FixedProvider([daily_bar]),
            Interval.HOURLY_4: _FixedProvider([hourly_bar]),
        }
    )
    assert router.fetch("BTCUSDT", Interval.DAILY) == [daily_bar]
    assert router.fetch("BTCUSDT", Interval.HOURLY_4) == [hourly_bar]
    assert router.fetch("BTCUSDT", Interval.WEEKLY) == []  # not registered
    assert router.supported_intervals == frozenset({Interval.DAILY, Interval.HOURLY_4})


def test_binance_provider_stack_routes_4h_around_the_disk_cache(tmp_path, monkeypatch) -> None:
    """The whole point of binance_stack.py: HOURLY_4 must reach BinanceProvider
    directly on every call (never satisfied from OHLCVCache), while DAILY goes
    through the cache. We verify this by counting live fetch calls."""
    call_log: list[Interval] = []
    day = datetime(2024, 3, 5, tzinfo=timezone.utc)
    page = [_kline(day, "100", "101", "99", "100", "1")]

    def fake_http_get_json(url: str, *, timeout: float = 30.0) -> list:
        call_log.append(Interval.HOURLY_4 if "4h" in url else Interval.DAILY)
        return page

    monkeypatch.setattr("wave_alpha.data.binance._http_get_json", fake_http_get_json)
    stack = binance_provider_stack(db_path=tmp_path / "cache.sqlite")

    # Two consecutive 4h fetches: both must hit the network (no caching).
    stack.fetch("BTCUSDT", Interval.HOURLY_4)
    stack.fetch("BTCUSDT", Interval.HOURLY_4)
    assert call_log.count(Interval.HOURLY_4) == 2

    # Two consecutive DAILY fetches with the same end date: the second
    # should be served from cache (only one network call).
    call_log.clear()
    today = date(2024, 3, 5)
    stack.fetch("BTCUSDT", Interval.DAILY, end=today)
    stack.fetch("BTCUSDT", Interval.DAILY, end=today)
    assert call_log.count(Interval.DAILY) == 1
