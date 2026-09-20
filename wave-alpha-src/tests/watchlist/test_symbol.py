from wave_alpha.watchlist.symbol import SYMBOL_RE


def test_symbol_re_matches_typical_tickers() -> None:
    for sym in ("AAPL", "MSFT", "BRK.B", "BF-B", "X"):
        assert SYMBOL_RE.match(sym)


def test_symbol_re_rejects_invalid() -> None:
    for sym in ("aapl", "1AAPL", "WAY TOO LONG", " AAPL ", ""):
        assert not SYMBOL_RE.match(sym)
