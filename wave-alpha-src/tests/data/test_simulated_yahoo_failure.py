"""End-to-end simulation: Yahoo fails, Stooq supplies bars.

Exercises the full production stack: CachedProvider(FallbackProvider([Yahoo, Stooq])).
Stooq's HTTP transport is monkeypatched to a fixture so the test stays hermetic.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from wave_alpha.data.cached import CachedProvider
from wave_alpha.data.fallback import FallbackProvider
from wave_alpha.data.stooq import StooqProvider
from wave_alpha.data.yahoo import YahooProvider
from wave_alpha.domain import Interval

_FIXTURE_CSV = (
    Path(__file__).resolve().parents[1] / "fixtures" / "stooq_aapl_5d_daily.csv"
).read_text()


def test_yahoo_failure_falls_through_to_stooq(tmp_path: Path, monkeypatch) -> None:
    # Yahoo always raises (simulates rate-limit / network error).
    def yahoo_boom(self, ticker, interval, start=None, end=None):
        raise RuntimeError("yfinance unavailable")

    monkeypatch.setattr(YahooProvider, "fetch", yahoo_boom)

    # Stooq is real but its HTTP layer is replaced by the fixture body.
    monkeypatch.setattr(
        "wave_alpha.data.stooq._http_get",
        lambda url, *, timeout=30.0: _FIXTURE_CSV,
    )

    provider = CachedProvider(
        upstream=FallbackProvider([YahooProvider(), StooqProvider(api_key="TESTKEY")]),
        db_path=tmp_path / "cache.sqlite",
    )

    bars = provider.fetch("AAPL", Interval.DAILY)

    assert len(bars) == 5
    assert bars[0].timestamp == date(2024, 4, 22)
    assert bars[-1].timestamp == date(2024, 4, 26)


def test_yahoo_failure_4h_returns_empty_no_stooq_call(tmp_path: Path, monkeypatch) -> None:
    """4h request: Yahoo fails, Stooq is interval-skipped, result is []."""

    def yahoo_boom(self, ticker, interval, start=None, end=None):
        raise RuntimeError("yfinance unavailable")

    monkeypatch.setattr(YahooProvider, "fetch", yahoo_boom)

    stooq_calls: list[str] = []

    def stooq_http(url, *, timeout=30.0):
        stooq_calls.append(url)
        return _FIXTURE_CSV  # would-be data, but we expect this never to run

    monkeypatch.setattr("wave_alpha.data.stooq._http_get", stooq_http)

    provider = CachedProvider(
        upstream=FallbackProvider([YahooProvider(), StooqProvider(api_key="TESTKEY")]),
        db_path=tmp_path / "cache.sqlite",
    )

    bars = provider.fetch("AAPL", Interval.HOURLY_4)
    assert bars == []
    assert stooq_calls == [], "Stooq must not be called for 4h requests"
