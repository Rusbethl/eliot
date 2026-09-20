from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from wave_alpha.scan.archive import list_reports, load_report
from wave_alpha.watchlist.store import load_watchlist
from wave_alpha.web.home import RecentlyAnalyzed, build_view

router = APIRouter()
_log = logging.getLogger(__name__)


def _stable_streaks(symbols: list[str]) -> dict[str, int]:
    """Look up history streaks per watchlist symbol. Failures degrade to 0."""
    from wave_alpha.history import service as history_service
    from wave_alpha.web import deps

    db_path = deps._data_dir() / "history.sqlite"
    out: dict[str, int] = {}
    for sym in symbols:
        try:
            recent = history_service.get_recent_with_badge(sym.upper(), db_path=db_path)
            out[sym.upper()] = recent.badge.streak if recent.badge else 0
        except Exception as exc:  # non-fatal
            _log.warning("home: history lookup failed for %s: %s", sym, exc)
            out[sym.upper()] = 0
    return out


def _recently_analyzed(*, db_path: Path, n: int = 6) -> list[RecentlyAnalyzed]:
    """Last N (ticker, as_of) pairs across all symbols. Failures degrade to []."""
    from wave_alpha.history import store as history_store

    try:
        rows = history_store.recent_across_all(db_path=db_path, limit=n)
    except Exception as exc:
        _log.warning("home: recent-history lookup failed: %s", exc)
        return []
    return [RecentlyAnalyzed(ticker=r.ticker, as_of=r.as_of) for r in rows]


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    from wave_alpha.web import deps
    from wave_alpha.web.routes import scan as scan_routes
    from wave_alpha.web.routes import watchlist as wl_routes

    scan_dir = scan_routes._scan_dir()
    latest_id = next(iter(list_reports(dir=scan_dir)), None)
    report = load_report(latest_id, dir=scan_dir) if latest_id else None

    entries = load_watchlist(wl_routes._watchlist_path())
    streaks = _stable_streaks([e.symbol for e in entries]) if entries else {}
    recent = _recently_analyzed(db_path=deps._data_dir() / "history.sqlite")

    view = build_view(
        report=report,
        watchlist=entries,
        stable_streaks=streaks,
        recently_analyzed=recent,
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"view": view},
    )
