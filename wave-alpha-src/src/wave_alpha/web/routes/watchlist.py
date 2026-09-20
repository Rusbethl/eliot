from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from wave_alpha.scan.archive import list_reports, load_report
from wave_alpha.watchlist.store import (
    DEFAULT_WATCHLIST_PATH,
    add_entry,
    load_watchlist,
    remove_entry,
)
from wave_alpha.web.watchlist_enrich import enrich

router = APIRouter()


def _watchlist_path() -> Path:
    """Indirection so tests can monkeypatch the location."""
    return DEFAULT_WATCHLIST_PATH


@router.get("/watchlist", response_class=HTMLResponse)
def watchlist_page(request: Request) -> HTMLResponse:
    from wave_alpha.web.routes import scan as scan_routes  # local to avoid import cycle

    entries = load_watchlist(_watchlist_path())
    scan_dir = scan_routes._scan_dir()
    latest_id = next(iter(list_reports(dir=scan_dir)), None)
    latest_report = load_report(latest_id, dir=scan_dir) if latest_id else None
    enriched = enrich(entries, latest_report)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="watchlist.html",
        context={
            "entries": entries,  # kept for backward-compat with current template
            "enriched": enriched,  # used by the redesigned template (Task 10)
            "latest_scan_id": latest_id,  # for the page sub-line
        },
    )


@router.post("/watchlist/add")
def watchlist_add(symbol: str = Form(...)) -> RedirectResponse:
    try:
        add_entry(symbol, path=_watchlist_path())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url="/watchlist", status_code=303)


@router.post("/watchlist/rm")
def watchlist_rm(symbol: str = Form(...)) -> RedirectResponse:
    try:
        remove_entry(symbol, path=_watchlist_path())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url="/watchlist", status_code=303)
