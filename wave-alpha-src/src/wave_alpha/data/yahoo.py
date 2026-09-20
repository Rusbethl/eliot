from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import yfinance as yf
from loguru import logger

from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval

_INTERVAL_TO_YF: dict[Interval, str] = {
    Interval.WEEKLY: "1wk",
    Interval.DAILY: "1d",
    Interval.HOURLY_4: "1h",  # we resample 1h to 4h client-side
}


def bars_from_dataframe(df: pd.DataFrame, interval: Interval) -> list[Bar]:
    """Convert a yfinance OHLCV DataFrame (DatetimeIndex) to canonical Bars."""
    if interval is Interval.HOURLY_4:
        df = _resample_to_4h(df)

    bars: list[Bar] = []
    for ts, row in df.iterrows():
        if pd.isna(row["Open"]) or pd.isna(row["Close"]):
            continue
        bars.append(
            Bar(
                timestamp=_to_date(ts),
                open=Decimal(f"{row['Open']:.4f}"),
                high=Decimal(f"{row['High']:.4f}"),
                low=Decimal(f"{row['Low']:.4f}"),
                close=Decimal(f"{row['Close']:.4f}"),
                volume=int(row.get("Volume", 0) or 0),
            )
        )
    return bars


def _resample_to_4h(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.resample("4h")
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        .dropna(subset=["Open", "Close"])
    )


def _to_date(ts: pd.Timestamp) -> date:
    return ts.date() if hasattr(ts, "date") else ts


class YahooProvider(OHLCVProvider):
    def fetch(
        self,
        ticker: str,
        interval: Interval,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        # yfinance's `period` and `start`/`end` are mutually exclusive: passing
        # `period` causes it to ignore `end`. To get full history when only `end`
        # is supplied (the pipeline's typical call shape), use `period` and omit
        # `end` from the wire call — only honor `end` on the wire when paired with
        # an explicit `start`. Post-fetch end-filtering is the CachedProvider's
        # responsibility, applied uniformly to both cache-hit and cache-miss paths
        # so that the full upstream payload (e.g. 730d of 4h bars) lands in the
        # cache verbatim and backtest calls with old `end` dates hit the cache.
        kwargs: dict = {"interval": _INTERVAL_TO_YF[interval], "auto_adjust": True}
        if start:
            kwargs["start"] = start.isoformat()
            if end:
                kwargs["end"] = end.isoformat()
        else:
            kwargs["period"] = "max" if interval is not Interval.HOURLY_4 else "730d"

        logger.debug(
            "yfinance fetch ticker={} interval={} kwargs={}", ticker, interval.value, kwargs
        )
        df = yf.download(ticker, progress=False, threads=False, **kwargs)
        if df.empty:
            return []
        if isinstance(df.columns, pd.MultiIndex):
            df = df.droplevel(1, axis=1)
        return bars_from_dataframe(df, interval=interval)
