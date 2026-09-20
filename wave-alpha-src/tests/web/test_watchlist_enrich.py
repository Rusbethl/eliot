from datetime import date
from decimal import Decimal

from wave_alpha.scan.models import ScanReport, ScanRow
from wave_alpha.watchlist.models import WatchlistEntry
from wave_alpha.web.watchlist_enrich import EnrichedRow, enrich


def _entry(symbol: str, notes: str | None = None) -> WatchlistEntry:
    return WatchlistEntry(symbol=symbol, added_on=date(2026, 5, 1), notes=notes)


def _report(*rows: ScanRow) -> ScanReport:
    from datetime import datetime

    return ScanReport(
        scan_id="2026-05-09T14-30-00",
        started_at=datetime(2026, 5, 9, 14, 30, 0),
        completed_at=datetime(2026, 5, 9, 14, 30, 30),
        as_of=date(2026, 5, 9),
        rows=list(rows),
    )


def test_enrich_returns_one_row_per_entry_in_input_order() -> None:
    entries = [_entry("AAPL"), _entry("MSFT")]
    report = _report(
        ScanRow(ticker="AAPL", as_of=date(2026, 5, 9), top_pattern="impulse", top_score=0.8),
        ScanRow(ticker="MSFT", as_of=date(2026, 5, 9), top_pattern="zigzag", top_score=0.6),
    )
    out = enrich(entries, report)
    assert [r.entry.symbol for r in out] == ["AAPL", "MSFT"]


def test_enrich_finds_row_case_insensitively() -> None:
    entries = [_entry("aapl")]  # lowercased entry
    report = _report(
        ScanRow(ticker="AAPL", as_of=date(2026, 5, 9), top_pattern="impulse", top_score=0.8)
    )
    out = enrich(entries, report)
    assert out[0].row is not None
    assert out[0].row.ticker == "AAPL"


def test_enrich_returns_none_row_for_symbol_not_in_scan() -> None:
    entries = [_entry("GOOG")]
    report = _report(
        ScanRow(ticker="AAPL", as_of=date(2026, 5, 9), top_pattern="impulse", top_score=0.8)
    )
    out = enrich(entries, report)
    assert out[0].row is None


def test_enrich_with_no_report_returns_rows_with_none() -> None:
    entries = [_entry("AAPL"), _entry("MSFT")]
    out = enrich(entries, report=None)
    assert all(r.row is None for r in out)
    assert [r.entry.symbol for r in out] == ["AAPL", "MSFT"]


def test_enriched_row_exposes_signal_helpers() -> None:
    entries = [_entry("AAPL")]
    report = _report(
        ScanRow(
            ticker="AAPL",
            as_of=date(2026, 5, 9),
            top_pattern="impulse",
            top_score=0.82,
            direction="up",
            signal_rr=Decimal("3.21"),
            signal_entry=Decimal("180"),
            signal_stop=Decimal("172"),
            signal_target=Decimal("196"),
        )
    )
    out = enrich(entries, report)
    er: EnrichedRow = out[0]
    assert er.has_signal is True
    assert er.bias == "long"
    assert float(er.row.signal_rr) == 3.21


def test_enriched_row_for_short_signal_reports_short_bias() -> None:
    entries = [_entry("MSFT")]
    report = _report(
        ScanRow(
            ticker="MSFT",
            as_of=date(2026, 5, 9),
            top_pattern="zigzag",
            top_score=0.5,
            direction="down",
            signal_rr=Decimal("2.0"),
            signal_entry=Decimal("420"),
            signal_stop=Decimal("428"),
            signal_target=Decimal("404"),
        )
    )
    out = enrich(entries, report)
    assert out[0].bias == "short"


def test_enriched_row_with_no_signal_reports_none_bias() -> None:
    entries = [_entry("GOOG")]
    out = enrich(entries, report=None)
    assert out[0].bias is None
    assert out[0].has_signal is False
