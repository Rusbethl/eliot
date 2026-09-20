from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from wave_alpha.scan.archive import DEFAULT_SCAN_DIR, list_reports, load_report
from wave_alpha.scan.ranking import Bias, filter_by_bias, filter_by_min_rr, filter_signals, sort_by

router = APIRouter()


def _scan_dir() -> Path:
    """Indirection so tests can monkeypatch the location."""
    return DEFAULT_SCAN_DIR


@router.get("/scan", response_class=HTMLResponse)
def scan_dashboard(request: Request) -> HTMLResponse:
    ids = list_reports(dir=_scan_dir())
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="scan.html",
        context={"scan_ids": ids, "current_id": ids[0] if ids else None},
    )


@router.get("/scan/{scan_id}", response_class=HTMLResponse)
def scan_view(scan_id: str, request: Request) -> HTMLResponse:
    report = load_report(scan_id, dir=_scan_dir())
    if report is None:
        raise HTTPException(status_code=404, detail=f"scan {scan_id} not found")
    ids = list_reports(dir=_scan_dir())
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="scan.html",
        context={
            "scan_ids": ids,
            "current_id": scan_id,
            "report": report,
            "rows": sort_by(report.rows, "score"),
        },
    )


@router.get("/scan/{scan_id}/table", response_class=HTMLResponse)
def scan_table_fragment(
    scan_id: str,
    request: Request,
    sort: str = "score",
    signals_only: bool = False,
    bias: Bias = "any",
    min_rr: float = Query(0.0, ge=0.0),
) -> HTMLResponse:
    report = load_report(scan_id, dir=_scan_dir())
    if report is None:
        raise HTTPException(status_code=404, detail=f"scan {scan_id} not found")
    rows = report.rows
    if signals_only:
        rows = filter_signals(rows)
    if bias in ("long", "short"):
        rows = filter_by_bias(rows, bias)
    if min_rr > 0.0:
        rows = filter_by_min_rr(rows, min_rr)
    rows = sort_by(rows, sort, reverse=(sort != "ticker"))
    return request.app.state.templates.TemplateResponse(
        request=request, name="_scan_table.html", context={"rows": rows}
    )
