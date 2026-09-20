# tests/backtest/test_universe.py
from pathlib import Path

import pytest

from wave_alpha.backtest.universe import load_universe


def test_load_universe_reads_one_symbol_per_line(tmp_path: Path) -> None:
    f = tmp_path / "u.txt"
    f.write_text("AAPL\nMSFT\nNVDA\n")
    assert load_universe(f) == ["AAPL", "MSFT", "NVDA"]


def test_load_universe_skips_blank_and_comment_lines(tmp_path: Path) -> None:
    f = tmp_path / "u.txt"
    f.write_text("# tech\nAAPL\n\n# defensive\nKO\n  # indented comment\nPG\n")
    assert load_universe(f) == ["AAPL", "KO", "PG"]


def test_load_universe_uppercases_and_strips(tmp_path: Path) -> None:
    f = tmp_path / "u.txt"
    f.write_text(" aapl \n  msft\n")
    assert load_universe(f) == ["AAPL", "MSFT"]


def test_load_universe_dedupes_preserving_order(tmp_path: Path) -> None:
    f = tmp_path / "u.txt"
    f.write_text("AAPL\nMSFT\nAAPL\nNVDA\nMSFT\n")
    assert load_universe(f) == ["AAPL", "MSFT", "NVDA"]


def test_load_universe_raises_on_empty(tmp_path: Path) -> None:
    f = tmp_path / "u.txt"
    f.write_text("# nothing here\n\n")
    with pytest.raises(ValueError, match="empty universe"):
        load_universe(f)


def test_load_universe_raises_on_invalid_symbol(tmp_path: Path) -> None:
    f = tmp_path / "u.txt"
    f.write_text("AAPL\nbad symbol with space\n")
    with pytest.raises(ValueError, match="invalid symbol"):
        load_universe(f)
