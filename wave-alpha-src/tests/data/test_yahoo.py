from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd

from wave_alpha.data.yahoo import YahooProvider, bars_from_dataframe
from wave_alpha.domain import Interval


def test_bars_from_dataframe_parses_csv_fixture() -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "yahoo_aapl_5d_daily.csv"
    df = pd.read_csv(fixture, parse_dates=["Date"]).set_index("Date")
    bars = bars_from_dataframe(df, interval=Interval.DAILY)

    assert len(bars) == 5
    assert bars[0].timestamp == date(2024, 4, 22)
    assert bars[0].close == Decimal("165.84")
    # ascending order
    assert bars == sorted(bars, key=lambda b: b.timestamp)
    # high >= low always
    assert all(b.high >= b.low for b in bars)


def test_yahoo_returns_unfiltered_window(monkeypatch) -> None:
    """YahooProvider must return whatever yfinance gave it — caller filters."""
    df = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [101.0, 102.0],
            "Low": [99.0, 100.0],
            "Close": [100.5, 101.5],
            "Volume": [1000, 1000],
        },
        index=pd.to_datetime(["2024-01-01", "2024-01-02"]),
    )

    def fake_download(*args, **kwargs):
        return df

    monkeypatch.setattr("wave_alpha.data.yahoo.yf.download", fake_download)

    bars = YahooProvider().fetch("AAPL", Interval.DAILY, end=date(2020, 1, 1))

    # The end-filter is now a CachedProvider responsibility — YahooProvider
    # returns the full upstream payload regardless of `end`.
    assert len(bars) == 2
    assert bars[0].timestamp == date(2024, 1, 1)
    assert bars[1].timestamp == date(2024, 1, 2)
