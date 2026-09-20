"""Pure data builder for the Home page. No FastAPI imports — keeps the
view buildable from the route, from tests, and (later) from a CLI dump."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

from wave_alpha.scan.models import ScanReport, ScanRow
from wave_alpha.scan.ranking import filter_signals, sort_by
from wave_alpha.watchlist.models import WatchlistEntry

Bias = Literal["long", "short"]


@dataclass(frozen=True)
class RecentlyAnalyzed:
    ticker: str
    as_of: date


@dataclass(frozen=True)
class TopRr:
    value: Decimal
    ticker: str
    bias: Bias | None


@dataclass(frozen=True)
class HomeKpis:
    signals: int | None
    top_rr: TopRr | None
    watchlist_hits: int | None
    stable_picks: int | None


@dataclass(frozen=True)
class WatchlistTopRow:
    """One row for the Home "Your watchlist · top picks" card."""

    symbol: str
    pattern: str | None
    last_pivot_label: str | None  # e.g. "3" or "B"
    bias: Bias | None
    coherence: float | None
    signal_rr: Decimal | None


@dataclass(frozen=True)
class HomeView:
    kpis: HomeKpis
    top_signals: list[ScanRow] = field(default_factory=list)  # up to 5
    watchlist_top: list[WatchlistTopRow] = field(default_factory=list)  # up to 5
    latest_scan_id: str | None = None
    recently_analyzed: list[RecentlyAnalyzed] = field(default_factory=list)
    watchlist_total: int = 0


def _bias_for(direction: str | None) -> Bias | None:
    if direction == "up":
        return "long"
    if direction == "down":
        return "short"
    return None


def _build_kpis(
    report: ScanReport | None,
    watchlist: list[WatchlistEntry],
    stable_streaks: dict[str, int],
) -> HomeKpis:
    if report is None and not watchlist:
        return HomeKpis(signals=None, top_rr=None, watchlist_hits=None, stable_picks=None)

    signals = report.n_with_signal if report is not None else None

    top_rr: TopRr | None = None
    if report is not None and report.n_with_signal > 0:
        signalled = filter_signals(report.rows)
        signalled.sort(key=lambda r: float(r.signal_rr or 0), reverse=True)
        best = signalled[0]
        top_rr = TopRr(
            value=best.signal_rr,  # not None; filter_signals enforces it
            ticker=best.ticker,
            bias=_bias_for(best.direction),
        )

    watchlist_hits: int | None = None
    if watchlist:
        if report is not None:
            by_ticker = {row.ticker.upper(): row for row in report.rows}
            watchlist_hits = sum(
                1
                for e in watchlist
                if (row := by_ticker.get(e.symbol.upper())) is not None and row.has_signal
            )
        else:
            watchlist_hits = 0

    stable_picks: int | None = None
    if watchlist:
        stable_picks = sum(1 for e in watchlist if stable_streaks.get(e.symbol.upper(), 0) >= 3)

    return HomeKpis(
        signals=signals,
        top_rr=top_rr,
        watchlist_hits=watchlist_hits,
        stable_picks=stable_picks,
    )


def build_view(
    *,
    report: ScanReport | None,
    watchlist: list[WatchlistEntry],
    stable_streaks: dict[str, int],
    recently_analyzed: list[RecentlyAnalyzed] | None = None,
) -> HomeView:
    """Pure constructor.
    - `report`: most-recent scan (or None).
    - `watchlist`: persisted watchlist entries.
    - `stable_streaks`: {SYMBOL_UPPER: streak_int} from history service.
    - `recently_analyzed`: last N (ticker, as_of) deep-dives across all symbols.
    """
    kpis = _build_kpis(report, watchlist, stable_streaks)

    top_signals: list[ScanRow] = []
    if report is not None and report.n_with_signal > 0:
        # Top 5 signals, ranked by score descending.
        top_signals = sort_by(filter_signals(report.rows), "score", reverse=True)[:5]

    watchlist_top: list[WatchlistTopRow] = []
    if watchlist and report is not None:
        by_ticker = {row.ticker.upper(): row for row in report.rows}
        for e in watchlist[:5]:
            row = by_ticker.get(e.symbol.upper())
            watchlist_top.append(
                WatchlistTopRow(
                    symbol=e.symbol,
                    pattern=row.top_pattern if row else None,
                    last_pivot_label=None,  # filled later if we wire history per-symbol
                    bias=_bias_for(row.direction) if row else None,
                    coherence=row.coherence if row else None,
                    signal_rr=row.signal_rr if row else None,
                )
            )
    elif watchlist:
        # No scan → still list the symbols so the section isn't empty.
        for e in watchlist[:5]:
            watchlist_top.append(
                WatchlistTopRow(
                    symbol=e.symbol,
                    pattern=None,
                    last_pivot_label=None,
                    bias=None,
                    coherence=None,
                    signal_rr=None,
                )
            )

    return HomeView(
        kpis=kpis,
        top_signals=top_signals,
        watchlist_top=watchlist_top,
        latest_scan_id=report.scan_id if report else None,
        recently_analyzed=recently_analyzed or [],
        watchlist_total=len(watchlist),
    )
