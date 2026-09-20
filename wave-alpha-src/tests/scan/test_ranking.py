from datetime import date
from decimal import Decimal

from wave_alpha.scan.models import ScanRow
from wave_alpha.scan.ranking import filter_by_bias, filter_by_min_rr, filter_signals, sort_by


def _row(tk: str, score: float, rr: str | None = None) -> ScanRow:
    kwargs = dict(
        ticker=tk,
        as_of=date(2026, 5, 1),
        coherence=0.5,
        top_pattern="impulse",
        top_score=score,
        direction="up",
    )
    if rr:
        kwargs.update(
            signal_rr=Decimal(rr),
            signal_entry=Decimal("100"),
            signal_stop=Decimal("90"),
            signal_target=Decimal("130"),
        )
    return ScanRow(**kwargs)


def test_sort_by_score_descending_default() -> None:
    rows = [_row("A", 0.4), _row("B", 0.8), _row("C", 0.6)]
    out = sort_by(rows, "score")
    assert [r.ticker for r in out] == ["B", "C", "A"]


def test_sort_by_rr_puts_no_signal_last() -> None:
    rows = [
        _row("A", 0.4, rr="3.0"),
        _row("B", 0.8),  # no signal
        _row("C", 0.6, rr="1.5"),
    ]
    out = sort_by(rows, "rr")
    assert [r.ticker for r in out] == ["A", "C", "B"]


def test_sort_by_ticker_alpha() -> None:
    rows = [_row("NVDA", 0.5), _row("AAPL", 0.5), _row("MSFT", 0.5)]
    out = sort_by(rows, "ticker", reverse=False)
    assert [r.ticker for r in out] == ["AAPL", "MSFT", "NVDA"]


def test_sort_by_ticker_honors_reverse_true() -> None:
    rows = [_row("AAPL", 0.5), _row("NVDA", 0.5), _row("MSFT", 0.5)]
    out = sort_by(rows, "ticker", reverse=True)
    assert [r.ticker for r in out] == ["NVDA", "MSFT", "AAPL"]


def test_filter_signals_keeps_only_rows_with_rr() -> None:
    rows = [_row("A", 0.4, rr="3.0"), _row("B", 0.8), _row("C", 0.6, rr="1.5")]
    out = filter_signals(rows)
    assert [r.ticker for r in out] == ["A", "C"]


def test_sort_by_right_edge_puts_none_last() -> None:
    rows = [
        ScanRow(ticker="A", as_of=date(2026, 5, 1), right_edge_proba=0.30),
        ScanRow(ticker="B", as_of=date(2026, 5, 1), right_edge_proba=None),
        ScanRow(ticker="C", as_of=date(2026, 5, 1), right_edge_proba=0.85),
        ScanRow(ticker="D", as_of=date(2026, 5, 1), right_edge_proba=0.55),
    ]
    out = sort_by(rows, "right_edge")
    assert [r.ticker for r in out] == ["C", "D", "A", "B"]


def _row_with(tk: str, *, direction: str | None, rr: str | None = None):
    """Helper variant that lets us set direction explicitly (incl. None)."""
    kwargs = dict(
        ticker=tk,
        as_of=date(2026, 5, 1),
        coherence=0.5,
        top_pattern="impulse",
        top_score=0.5,
        direction=direction,  # type: ignore[arg-type]
    )
    if rr:
        kwargs.update(
            signal_rr=Decimal(rr),
            signal_entry=Decimal("100"),
            signal_stop=Decimal("90"),
            signal_target=Decimal("130"),
        )
    return ScanRow(**kwargs)


def test_filter_by_bias_long_keeps_only_up() -> None:
    rows = [
        _row_with("A", direction="up"),
        _row_with("B", direction="down"),
        _row_with("C", direction="up"),
    ]
    out = filter_by_bias(rows, "long")
    assert [r.ticker for r in out] == ["A", "C"]


def test_filter_by_bias_short_keeps_only_down() -> None:
    rows = [
        _row_with("A", direction="up"),
        _row_with("B", direction="down"),
    ]
    out = filter_by_bias(rows, "short")
    assert [r.ticker for r in out] == ["B"]


def test_filter_by_bias_any_returns_all_rows_unchanged() -> None:
    rows = [
        _row_with("A", direction="up"),
        _row_with("B", direction="down"),
        _row_with("C", direction=None),
    ]
    out = filter_by_bias(rows, "any")
    assert [r.ticker for r in out] == ["A", "B", "C"]


def test_filter_by_bias_excludes_rows_with_no_direction() -> None:
    rows = [
        _row_with("A", direction="up"),
        _row_with("B", direction=None),
    ]
    out = filter_by_bias(rows, "long")
    assert [r.ticker for r in out] == ["A"]


def test_filter_by_min_rr_keeps_rows_at_or_above_threshold() -> None:
    rows = [
        _row_with("A", direction="up", rr="3.0"),
        _row_with("B", direction="up", rr="1.5"),
        _row_with("C", direction="up", rr="2.0"),
    ]
    out = filter_by_min_rr(rows, 2.0)
    assert {r.ticker for r in out} == {"A", "C"}


def test_filter_by_min_rr_drops_rows_with_no_signal() -> None:
    rows = [
        _row_with("A", direction="up", rr="3.0"),
        _row_with("B", direction="up"),  # no rr → no signal
    ]
    out = filter_by_min_rr(rows, 1.0)
    assert [r.ticker for r in out] == ["A"]


def test_filter_by_min_rr_zero_threshold_keeps_all_signalled_rows() -> None:
    rows = [
        _row_with("A", direction="up", rr="3.0"),
        _row_with("B", direction="up", rr="0.5"),
    ]
    out = filter_by_min_rr(rows, 0.0)
    assert {r.ticker for r in out} == {"A", "B"}
