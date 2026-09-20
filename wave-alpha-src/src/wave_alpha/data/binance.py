from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timezone
from decimal import Decimal

from loguru import logger

from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval

_BASE_URL = "https://api.binance.com/api/v3/klines"

# Binance's own kline interval strings. Unlike Yahoo, Binance serves native
# 4h/1h/30m candles directly — no client-side resampling needed.
_INTERVAL_TO_BINANCE: dict[Interval, str] = {
    Interval.WEEKLY: "1w",
    Interval.DAILY: "1d",
    Interval.HOURLY_4: "4h",
    Interval.HOURLY_1: "1h",
    Interval.MINUTE_30: "30m",
}

_MAX_KLINES_PER_CALL = 1000


def _date_to_ms(d: date) -> int:
    return int(datetime.combine(d, time.min, tzinfo=timezone.utc).timestamp() * 1000)


def _http_get_json(url: str, *, timeout: float = 30.0) -> list:
    """Fetch `url` and parse the JSON body. Indirection lets tests monkeypatch."""
    req = urllib.request.Request(url, headers={"User-Agent": "wave-alpha-binance-provider"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _klines_to_bars(klines: list) -> list[Bar]:
    """Convert Binance kline rows to Bars.

    NOTE: Bar.timestamp is a calendar `date` (see wave_alpha.domain.Bar), not
    a full datetime. For WEEKLY/DAILY this loses nothing. For HOURLY_4,
    HOURLY_1 and MINUTE_30 on a 24/7 market this means multiple bars per
    calendar date carry the same `.timestamp` — that is expected and safe
    for this engine's pivot-detection layer (which keys pivot identity on
    bar position, not on timestamp) but is NOT safe to push through
    wave_alpha's shared SQLite OHLCVCache, whose primary key is (ticker,
    interval, date) and will silently drop same-day bars via INSERT OR
    REPLACE. `binance_provider_stack()` in binance_stack.py wires all three
    sub-daily intervals to bypass that cache entirely for this reason.
    """
    bars: list[Bar] = []
    for row in klines:
        try:
            open_time_ms = row[0]
            ts = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc).date()
            bars.append(
                Bar(
                    timestamp=ts,
                    open=Decimal(str(row[1])),
                    high=Decimal(str(row[2])),
                    low=Decimal(str(row[3])),
                    close=Decimal(str(row[4])),
                    # Bar.volume is int; Binance volume is fractional BTC.
                    # Truncating loses sub-unit precision but matches the
                    # existing YahooProvider/StooqProvider convention.
                    volume=int(float(row[5])),
                )
            )
        except (ValueError, KeyError, IndexError, TypeError):
            continue
    return bars


class BinanceProvider(OHLCVProvider):
    """Binance public REST OHLCV provider (no API key required for klines).

    Paginates past Binance's 1000-candle-per-call limit. Ticker is a
    Binance symbol, e.g. "BTCUSDT" (not "BTC-USD" or "BTC/USDT").
    """

    supported_intervals = frozenset(
        {
            Interval.WEEKLY,
            Interval.DAILY,
            Interval.HOURLY_4,
            Interval.HOURLY_1,
            Interval.MINUTE_30,
        }
    )

    def fetch(
        self,
        ticker: str,
        interval: Interval,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        if interval not in self.supported_intervals:
            return []

        binance_interval = _INTERVAL_TO_BINANCE[interval]
        start_ms = _date_to_ms(start) if start else None
        # Binance's `endTime` is inclusive-of-open-time; add a day so a bar
        # opening exactly on `end` is still included.
        end_ms = _date_to_ms(end) + 86_400_000 if end else None

        all_bars: list[Bar] = []
        cursor = start_ms
        seen_open_times: set[int] = set()

        while True:
            params = {
                "symbol": ticker.upper(),
                "interval": binance_interval,
                "limit": _MAX_KLINES_PER_CALL,
            }
            if cursor is not None:
                params["startTime"] = cursor
            if end_ms is not None:
                params["endTime"] = end_ms
            url = f"{_BASE_URL}?{urllib.parse.urlencode(params)}"
            logger.debug(
                "binance fetch ticker={} interval={} url={}", ticker, interval.value, url
            )

            try:
                klines = _http_get_json(url)
            except Exception as exc:
                logger.warning("binance fetch failed ticker={} error={}", ticker, exc)
                break

            if not klines:
                break

            # Guard against a pathological infinite loop if Binance ever
            # echoes back the same page (e.g. cursor didn't advance).
            open_times = [row[0] for row in klines]
            if all(t in seen_open_times for t in open_times):
                break
            seen_open_times.update(open_times)

            all_bars.extend(_klines_to_bars(klines))

            if len(klines) < _MAX_KLINES_PER_CALL:
                break  # last page

            last_open_time = klines[-1][0]
            cursor = last_open_time + 1
            if end_ms is not None and cursor >= end_ms:
                break

        all_bars.sort(key=lambda b: b.timestamp)
        return all_bars
