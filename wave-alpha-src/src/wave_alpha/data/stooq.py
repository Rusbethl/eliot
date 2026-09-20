from __future__ import annotations

import csv
import urllib.request
from datetime import date
from decimal import Decimal
from io import StringIO

from loguru import logger

from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval

_HALT_SENTINELS = {"-", "", "null", "NaN"}

_INTERVAL_TO_STOOQ: dict[Interval, str] = {
    Interval.DAILY: "d",
    Interval.WEEKLY: "w",
}


def _to_stooq_symbol(ticker: str) -> str:
    """Convert a US-equity ticker to Stooq's symbol grammar.

    `AAPL` -> `aapl.us`. Class shares use `-` instead of `.`
    (Stooq's convention): `BRK.B` -> `brk-b.us`.
    """
    return ticker.lower().replace(".", "-") + ".us"


def _http_get(url: str, *, timeout: float = 30.0) -> str:
    """Fetch `url` as UTF-8 text. Indirection lets tests monkeypatch."""
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def _parse_stooq_csv(csv_text: str) -> list[Bar]:
    """Parse Stooq's CSV body into ascending-by-date Bars.

    Returns `[]` for the literal "No data" sentinel, an empty body, or
    any input whose first line isn't the expected `Date,...` header.
    Skips rows whose Open or Close is empty or `-` (halted-day sentinel).
    """
    csv_text = csv_text.strip()
    if not csv_text or not csv_text.startswith("Date,"):
        return []

    bars: list[Bar] = []
    for row in csv.DictReader(StringIO(csv_text)):
        raw_open = row.get("Open", "").strip()
        raw_close = row.get("Close", "").strip()
        if raw_open in _HALT_SENTINELS or raw_close in _HALT_SENTINELS:
            continue
        try:
            bars.append(
                Bar(
                    timestamp=date.fromisoformat(row["Date"]),
                    open=Decimal(f"{float(raw_open):.4f}"),
                    high=Decimal(f"{float(row['High']):.4f}"),
                    low=Decimal(f"{float(row['Low']):.4f}"),
                    close=Decimal(f"{float(raw_close):.4f}"),
                    volume=int(row.get("Volume") or 0),
                )
            )
        except (ValueError, KeyError):
            # Malformed row — skip rather than blow up the whole batch.
            continue

    bars.sort(key=lambda b: b.timestamp)
    return bars


class StooqProvider(OHLCVProvider):
    """Stooq CSV-endpoint OHLCV provider. US equities only.

    Stooq has no intraday data; `supported_intervals` excludes 4h.
    Window filtering is the caller's (CachedProvider's) responsibility.

    As of 2026-05, Stooq's free CSV endpoint requires a captcha-issued API
    key (see `https://stooq.com/q/d/?s=<SYM>&get_apikey`). When `api_key`
    is missing or empty, `fetch` short-circuits to `[]` so the upstream
    FallbackProvider can keep going on Yahoo.
    """

    supported_intervals = frozenset({Interval.DAILY, Interval.WEEKLY})

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    def fetch(
        self,
        ticker: str,
        interval: Interval,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        if interval not in self.supported_intervals:
            return []
        if not self._api_key:
            logger.debug(
                "stooq fetch skipped ticker={} interval={}: no API key",
                ticker,
                interval.value,
            )
            return []

        symbol = _to_stooq_symbol(ticker)
        url = (
            f"https://stooq.com/q/d/l/?s={symbol}"
            f"&i={_INTERVAL_TO_STOOQ[interval]}"
            f"&apikey={self._api_key}"
        )
        logger.debug("stooq fetch ticker={} interval={} url={}", ticker, interval.value, url)

        csv_text = _http_get(url)
        return _parse_stooq_csv(csv_text)
