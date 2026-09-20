from pathlib import Path

from wave_alpha.watchlist.parsers import parse_csv_symbols, parse_tv_txt_symbols


def test_parse_csv_first_column_only(tmp_path: Path) -> None:
    f = tmp_path / "in.csv"
    f.write_text("Symbol,Notes\nAAPL,megacap\nMSFT,\nNVDA,gpu\n")
    assert parse_csv_symbols(f) == ["AAPL", "MSFT", "NVDA"]


def test_parse_csv_skips_header_row_when_first_cell_isnt_a_symbol(tmp_path: Path) -> None:
    f = tmp_path / "in.csv"
    f.write_text("Symbol\nAAPL\nMSFT\n")
    assert parse_csv_symbols(f) == ["AAPL", "MSFT"]


def test_parse_csv_handles_no_header(tmp_path: Path) -> None:
    f = tmp_path / "in.csv"
    f.write_text("AAPL\nMSFT\nNVDA\n")
    assert parse_csv_symbols(f) == ["AAPL", "MSFT", "NVDA"]


def test_parse_csv_dedupes_preserving_order(tmp_path: Path) -> None:
    f = tmp_path / "in.csv"
    f.write_text("AAPL\nMSFT\nAAPL\nNVDA\nMSFT\n")
    assert parse_csv_symbols(f) == ["AAPL", "MSFT", "NVDA"]


def test_parse_tv_txt_strips_exchange_prefix(tmp_path: Path) -> None:
    """TradingView export is one symbol per line, ###NASDAQ:AAPL### or
    NASDAQ:AAPL or just AAPL — all valid."""
    f = tmp_path / "tv.txt"
    f.write_text("###NASDAQ:AAPL###\nNASDAQ:MSFT\nNYSE:GE\nNVDA\n")
    assert parse_tv_txt_symbols(f) == ["AAPL", "MSFT", "GE", "NVDA"]


def test_parse_tv_txt_handles_groups_and_separators(tmp_path: Path) -> None:
    """TV exports use commas and section markers like ###Group###."""
    f = tmp_path / "tv.txt"
    f.write_text("###Mega Cap###,NASDAQ:AAPL,NASDAQ:MSFT,###Defensive###,NYSE:KO\n")
    assert parse_tv_txt_symbols(f) == ["AAPL", "MSFT", "KO"]


def test_parse_tv_txt_skips_invalid_tokens(tmp_path: Path) -> None:
    f = tmp_path / "tv.txt"
    f.write_text("AAPL,---,MSFT,###just a header###\n")
    assert parse_tv_txt_symbols(f) == ["AAPL", "MSFT"]


def test_parse_csv_invalid_symbol_skipped_with_no_error(tmp_path: Path) -> None:
    """Lenient — skip bad rows rather than reject the whole import."""
    f = tmp_path / "in.csv"
    f.write_text("AAPL\n123\nMSFT\n")
    result = parse_csv_symbols(f)
    assert "AAPL" in result and "MSFT" in result and "123" not in result
