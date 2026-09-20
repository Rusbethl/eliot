"""Pure tests for the Home page data builder. No FastAPI involvement."""

from datetime import date, datetime
from decimal import Decimal

from wave_alpha.scan.models import ScanReport, ScanRow
from wave_alpha.watchlist.models import WatchlistEntry
from wave_alpha.web.home import HomeKpis, build_view


def _report_with_signals() -> ScanReport:
    return ScanReport(
        scan_id="2026-05-09T14-30-00",
        started_at=datetime(2026, 5, 9, 14, 30, 0),
        completed_at=datetime(2026, 5, 9, 14, 30, 30),
        as_of=date(2026, 5, 9),
        rows=[
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
            ),
            ScanRow(
                ticker="MSFT",
                as_of=date(2026, 5, 9),
                top_pattern="zigzag",
                top_score=0.74,
                direction="down",
                signal_rr=Decimal("2.40"),
                signal_entry=Decimal("420"),
                signal_stop=Decimal("428"),
                signal_target=Decimal("404"),
            ),
            ScanRow(
                ticker="NVDA",
                as_of=date(2026, 5, 9),
                top_pattern="impulse",
                top_score=0.55,
                direction="up",
                # no signal — score below threshold
            ),
        ],
    )


def test_build_view_returns_empty_kpis_when_no_inputs() -> None:
    view = build_view(report=None, watchlist=[], stable_streaks={})
    assert view.kpis == HomeKpis(signals=None, top_rr=None, watchlist_hits=None, stable_picks=None)
    assert view.top_signals == []
    assert view.watchlist_top == []


def test_build_view_signals_kpi_uses_n_with_signal() -> None:
    view = build_view(report=_report_with_signals(), watchlist=[], stable_streaks={})
    # 2 of 3 rows have signal_rr.
    assert view.kpis.signals == 2


def test_build_view_top_rr_kpi_picks_max_rr() -> None:
    view = build_view(report=_report_with_signals(), watchlist=[], stable_streaks={})
    assert view.kpis.top_rr is not None
    assert view.kpis.top_rr.value == Decimal("3.21")
    assert view.kpis.top_rr.ticker == "AAPL"
    assert view.kpis.top_rr.bias == "long"


def test_build_view_watchlist_hits_counts_signalled_matches_case_insensitive() -> None:
    view = build_view(
        report=_report_with_signals(),
        watchlist=[
            WatchlistEntry(symbol="aapl", added_on=date(2026, 5, 1)),
            WatchlistEntry(symbol="GOOG", added_on=date(2026, 5, 1)),  # not in scan
            WatchlistEntry(symbol="NVDA", added_on=date(2026, 5, 1)),  # in scan but no signal
        ],
        stable_streaks={},
    )
    # Only AAPL matches AND has a signal.
    assert view.kpis.watchlist_hits == 1


def test_build_view_stable_picks_counts_streak_at_or_above_three() -> None:
    view = build_view(
        report=None,
        watchlist=[
            WatchlistEntry(symbol="AAPL", added_on=date(2026, 5, 1)),
            WatchlistEntry(symbol="MSFT", added_on=date(2026, 5, 1)),
            WatchlistEntry(symbol="GOOG", added_on=date(2026, 5, 1)),
        ],
        stable_streaks={"AAPL": 5, "MSFT": 2, "GOOG": 3},
    )
    assert view.kpis.stable_picks == 2  # AAPL (5) and GOOG (3)


def test_build_view_top_signals_returns_first_five_with_signals_sorted_by_score() -> None:
    rep = _report_with_signals()  # 2 signals: AAPL (score 0.82) and MSFT (score 0.74)
    view = build_view(report=rep, watchlist=[], stable_streaks={})
    assert [t.ticker for t in view.top_signals] == ["AAPL", "MSFT"]
    assert len(view.top_signals) <= 5


def test_build_view_includes_recently_analyzed_when_provided() -> None:
    from wave_alpha.web.home import RecentlyAnalyzed

    view = build_view(
        report=None,
        watchlist=[],
        stable_streaks={},
        recently_analyzed=[RecentlyAnalyzed(ticker="AAPL", as_of=date(2026, 5, 9))],
    )
    assert view.recently_analyzed == [RecentlyAnalyzed(ticker="AAPL", as_of=date(2026, 5, 9))]


def test_build_view_watchlist_total_counts_all_entries_not_just_top_five() -> None:
    entries = [WatchlistEntry(symbol=f"S{i}", added_on=date(2026, 5, 1)) for i in range(8)]
    view = build_view(report=None, watchlist=entries, stable_streaks={})
    assert view.watchlist_total == 8
    assert len(view.watchlist_top) <= 5
