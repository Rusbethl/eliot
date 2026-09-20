from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from wave_alpha.data.stooq import StooqProvider, _parse_stooq_csv, _to_stooq_symbol
from wave_alpha.domain import Interval


def test_symbol_mapping_plain_us_equity() -> None:
    assert _to_stooq_symbol("AAPL") == "aapl.us"
    assert _to_stooq_symbol("MSFT") == "msft.us"
    assert _to_stooq_symbol("GOOGL") == "googl.us"


def test_symbol_mapping_class_share_uses_dash() -> None:
    """Stooq encodes class shares with `-` not `.` (e.g., BRK.B -> brk-b.us)."""
    assert _to_stooq_symbol("BRK.B") == "brk-b.us"
    assert _to_stooq_symbol("BF.B") == "bf-b.us"


def test_symbol_mapping_lowercases() -> None:
    assert _to_stooq_symbol("aapl") == "aapl.us"
    assert _to_stooq_symbol("Aapl") == "aapl.us"


_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_parses_daily_fixture() -> None:
    csv_text = (_FIXTURES / "stooq_aapl_5d_daily.csv").read_text()
    bars = _parse_stooq_csv(csv_text)

    assert len(bars) == 5
    assert bars[0].timestamp == date(2024, 4, 22)
    assert bars[-1].timestamp == date(2024, 4, 26)
    # Ascending order
    assert bars == sorted(bars, key=lambda b: b.timestamp)
    # Decimal precision preserved
    assert bars[0].close == Decimal("165.8400")
    # high >= low always
    assert all(b.high >= b.low for b in bars)


def test_parses_weekly_fixture() -> None:
    csv_text = (_FIXTURES / "stooq_aapl_5w_weekly.csv").read_text()
    bars = _parse_stooq_csv(csv_text)

    assert len(bars) == 5
    assert bars[0].timestamp == date(2024, 3, 29)
    assert bars[-1].timestamp == date(2024, 4, 26)


def test_no_data_sentinel_returns_empty() -> None:
    """Stooq emits literal 'No data' for unknown/delisted tickers."""
    assert _parse_stooq_csv("No data") == []
    assert _parse_stooq_csv("No data\n") == []


def test_empty_string_returns_empty() -> None:
    assert _parse_stooq_csv("") == []


def test_skips_halted_rows_with_dash_sentinel() -> None:
    """Stooq emits '-' for OHLC values on halted/no-trade days."""
    csv_text = (
        "Date,Open,High,Low,Close,Volume\n"
        "2024-04-22,165.52,167.26,164.77,165.84,48117420\n"
        "2024-04-23,-,-,-,-,0\n"
        "2024-04-24,166.21,167.11,164.81,165.05,46291800\n"
    )
    bars = _parse_stooq_csv(csv_text)
    assert len(bars) == 2
    assert [b.timestamp for b in bars] == [date(2024, 4, 22), date(2024, 4, 24)]


def test_resorts_descending_input_to_ascending() -> None:
    """Stooq's response order varies; parser normalizes to ascending."""
    csv_text = (
        "Date,Open,High,Low,Close,Volume\n"
        "2024-04-24,166.21,167.11,164.81,165.05,46291800\n"
        "2024-04-23,166.50,167.05,164.92,166.90,49537770\n"
        "2024-04-22,165.52,167.26,164.77,165.84,48117420\n"
    )
    bars = _parse_stooq_csv(csv_text)
    assert [b.timestamp for b in bars] == [
        date(2024, 4, 22),
        date(2024, 4, 23),
        date(2024, 4, 24),
    ]


def test_supported_intervals_is_daily_and_weekly() -> None:
    assert StooqProvider.supported_intervals == frozenset({Interval.DAILY, Interval.WEEKLY})


def test_supported_intervals_excludes_4h() -> None:
    assert Interval.HOURLY_4 not in StooqProvider.supported_intervals


def test_fetch_4h_returns_empty_defensively(monkeypatch) -> None:
    """If a caller bypasses FallbackProvider's interval skip and asks Stooq
    for 4h directly, return [] rather than hit HTTP."""

    def boom(*args, **kwargs):
        raise AssertionError("HTTP must not be called for unsupported interval")

    monkeypatch.setattr("wave_alpha.data.stooq._http_get", boom)
    bars = StooqProvider().fetch("AAPL", Interval.HOURLY_4)
    assert bars == []


def test_fetch_daily_calls_correct_url(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_http(url: str, *, timeout: float = 30.0) -> str:
        captured["url"] = url
        return "Date,Open,High,Low,Close,Volume\n2024-04-22,165.52,167.26,164.77,165.84,48117420\n"

    monkeypatch.setattr("wave_alpha.data.stooq._http_get", fake_http)
    bars = StooqProvider(api_key="TESTKEY").fetch("AAPL", Interval.DAILY)

    assert captured["url"] == "https://stooq.com/q/d/l/?s=aapl.us&i=d&apikey=TESTKEY"
    assert len(bars) == 1
    assert bars[0].timestamp == date(2024, 4, 22)


def test_fetch_weekly_calls_correct_url(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_http(url: str, *, timeout: float = 30.0) -> str:
        captured["url"] = url
        return "Date,Open,High,Low,Close,Volume\n2024-04-26,165.52,171.34,164.65,169.30,348100000\n"

    monkeypatch.setattr("wave_alpha.data.stooq._http_get", fake_http)
    bars = StooqProvider(api_key="TESTKEY").fetch("AAPL", Interval.WEEKLY)

    assert captured["url"] == "https://stooq.com/q/d/l/?s=aapl.us&i=w&apikey=TESTKEY"
    assert len(bars) == 1


def test_fetch_propagates_http_errors(monkeypatch) -> None:
    """StooqProvider does not retry; transport errors bubble up so
    FallbackProvider can catch them."""
    from urllib.error import URLError

    def fake_http(url: str, *, timeout: float = 30.0) -> str:
        raise URLError("connection refused")

    monkeypatch.setattr("wave_alpha.data.stooq._http_get", fake_http)
    import pytest

    with pytest.raises(URLError):
        StooqProvider(api_key="TESTKEY").fetch("AAPL", Interval.DAILY)


def test_fetch_returns_empty_when_no_api_key(monkeypatch) -> None:
    """Without an API key, StooqProvider must return [] without calling HTTP."""

    def boom(*args, **kwargs):
        raise AssertionError("HTTP must not be called when api_key is None")

    monkeypatch.setattr("wave_alpha.data.stooq._http_get", boom)
    bars = StooqProvider().fetch("AAPL", Interval.DAILY)
    assert bars == []


def test_fetch_returns_empty_when_api_key_is_empty_string(monkeypatch) -> None:
    """Empty-string api_key is treated the same as None — no HTTP."""

    def boom(*args, **kwargs):
        raise AssertionError("HTTP must not be called when api_key is empty")

    monkeypatch.setattr("wave_alpha.data.stooq._http_get", boom)
    bars = StooqProvider(api_key="").fetch("AAPL", Interval.DAILY)
    assert bars == []


def test_fetch_appends_apikey_to_url(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_http(url: str, *, timeout: float = 30.0) -> str:
        captured["url"] = url
        return "Date,Open,High,Low,Close,Volume\n2024-04-22,165.52,167.26,164.77,165.84,48117420\n"

    monkeypatch.setattr("wave_alpha.data.stooq._http_get", fake_http)
    StooqProvider(api_key="MYKEY123").fetch("AAPL", Interval.DAILY)

    assert captured["url"] == "https://stooq.com/q/d/l/?s=aapl.us&i=d&apikey=MYKEY123"
