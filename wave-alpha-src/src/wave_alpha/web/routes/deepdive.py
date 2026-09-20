from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from wave_alpha.data.cached import CachedProvider
from wave_alpha.data.fallback import FallbackProvider
from wave_alpha.data.point_in_time import PointInTimeView
from wave_alpha.data.stooq import StooqProvider
from wave_alpha.data.yahoo import YahooProvider
from wave_alpha.domain import Bar, Interval
from wave_alpha.elliott.models import WaveCount
from wave_alpha.llm.modes import LLMMode
from wave_alpha.prefs.config import load_config, resolve_stooq_api_key
from wave_alpha.web import deps
from wave_alpha.web.chart_data import build_chart_payload

router = APIRouter(prefix="/deepdive")


def _resolve_as_of(value: str | None) -> date:
    return date.fromisoformat(value) if value else date.today()


def _load_bars(ticker: str, as_of: date) -> list[Bar]:
    """Direct provider read for chart rendering — separate seam from `analyze`
    so the chart paints sub-second without LLM latency."""
    data_dir = deps._data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    stooq_key = resolve_stooq_api_key(cfg)
    provider = CachedProvider(
        upstream=FallbackProvider([YahooProvider(), StooqProvider(api_key=stooq_key)]),
        db_path=data_dir / "cache.sqlite",
    )
    raw = provider.fetch(ticker.upper(), Interval.DAILY, end=as_of)
    if not raw:
        return []
    return PointInTimeView(raw, as_of).visible()


def _load_top_count(ticker: str, as_of: date) -> WaveCount | None:
    """Single full pipeline pass for the chart's pivot overlay. The /counts
    fragment runs its own pass — not ideal long-term, but simplest for v0.3.
    Caching mitigates duplicate work."""
    result = deps.analyze(ticker, as_of, llm_mode=LLMMode.DISABLED)
    return result.counts_daily[0] if result.counts_daily else None


@router.get("/{ticker}", response_class=HTMLResponse, response_model=None)
def deepdive_page(
    ticker: str,
    request: Request,
    as_of: str | None = None,
    t: str | None = None,
    llm: str = "disabled",
) -> HTMLResponse | RedirectResponse:
    if t and t.upper() != ticker.upper():
        target_as_of = as_of or date.today().isoformat()
        return RedirectResponse(
            url=f"/deepdive/{t.upper()}?as_of={target_as_of}&llm={llm}", status_code=307
        )
    resolved = _resolve_as_of(as_of)
    # Validate llm mode early; bad input falls back silently
    try:
        LLMMode(llm)
    except ValueError:
        llm = "disabled"
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="deepdive.html",
        context={
            "ticker": ticker.upper(),
            "as_of": resolved.isoformat(),
            "llm_mode": llm,
        },
    )


@router.get("/{ticker}/chart", response_class=HTMLResponse)
def chart_fragment(ticker: str, request: Request, as_of: str | None = None) -> HTMLResponse:
    resolved = _resolve_as_of(as_of)
    bars = _load_bars(ticker, resolved)
    top = _load_top_count(ticker, resolved)
    payload = build_chart_payload(bars=bars, top_count=top)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_chart.html",
        context={"payload_json": json.dumps(payload)},
    )


@router.get("/{ticker}/hero", response_class=HTMLResponse)
def hero_fragment(ticker: str, request: Request, as_of: str | None = None) -> HTMLResponse:
    resolved = _resolve_as_of(as_of)
    result = deps.analyze(ticker, resolved, llm_mode=LLMMode.DISABLED)
    top = result.ranked_daily[0] if result.ranked_daily else None
    sig = result.signals[0] if result.signals else None
    bars = _load_bars(ticker, resolved)
    last_close = bars[-1].close if bars else None
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="_hero.html",
        context={
            "ticker": ticker.upper(),
            "top": top,
            "sig": sig,
            "last_close": last_close,
        },
    )


@router.get("/{ticker}/coherence", response_class=HTMLResponse)
def coherence_fragment(ticker: str, request: Request, as_of: str | None = None) -> HTMLResponse:
    from wave_alpha.web.coherence_badge import build_tri_badge

    resolved = _resolve_as_of(as_of)
    result = deps.analyze(ticker, resolved, llm_mode=LLMMode.DISABLED)
    badge = build_tri_badge(result.coherence) if result.coherence else []
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="_coherence_row.html",
        context={"coh": result.coherence, "badge": badge},
    )


@router.get("/{ticker}/right-edge", response_class=HTMLResponse)
def right_edge_fragment(ticker: str, request: Request, as_of: str | None = None) -> HTMLResponse:
    from wave_alpha.pipeline import _DEFAULT_DEGREE_FOR_COUNT
    from wave_alpha.web import right_edge as right_edge_module

    resolved = _resolve_as_of(as_of)
    assessment = right_edge_module.compute_right_edge_assessment(ticker, resolved)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="_right_edge.html",
        context={
            "assessment": assessment,
            "default_degree": _DEFAULT_DEGREE_FOR_COUNT,
        },
    )


@router.get("/{ticker}/plan", response_class=HTMLResponse)
def plan_fragment(ticker: str, request: Request, as_of: str | None = None) -> HTMLResponse:
    resolved = _resolve_as_of(as_of)
    result = deps.analyze(ticker, resolved, llm_mode=LLMMode.DISABLED)
    top = result.signals[0] if result.signals else None
    return request.app.state.templates.TemplateResponse(
        request=request, name="_trade_plan.html", context={"sig": top}
    )


@router.get("/{ticker}/scenarios", response_class=HTMLResponse)
def scenarios_fragment(ticker: str, request: Request, as_of: str | None = None) -> HTMLResponse:
    resolved = _resolve_as_of(as_of)
    result = deps.analyze(ticker, resolved, llm_mode=LLMMode.DISABLED)
    alternates = result.ranked_daily[1:4]  # C2..C4
    return request.app.state.templates.TemplateResponse(
        request=request, name="_scenarios.html", context={"alternates": alternates}
    )


@router.get("/{ticker}/history", response_class=HTMLResponse)
def history_fragment(ticker: str, request: Request) -> HTMLResponse:
    import logging

    from wave_alpha.history import service as history_service

    db_path = deps._data_dir() / "history.sqlite"
    try:
        recent = history_service.get_recent_with_badge(ticker.upper(), db_path=db_path)
        snapshots = recent.snapshots
        badge = recent.badge
        outcome_badge = history_service.compute_outcome_badge(snapshots)
    except Exception as exc:
        logging.getLogger("wave_alpha.web.routes.deepdive").warning(
            "history fragment read failed for %s: %s", ticker, exc
        )
        snapshots = []
        badge = None
        outcome_badge = None
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="_history.html",
        context={"snapshots": snapshots, "badge": badge, "outcome_badge": outcome_badge},
    )


@router.get("/{ticker}/counts", response_class=HTMLResponse)
def counts_fragment(
    ticker: str, request: Request, as_of: str | None = None, llm: str = "disabled"
) -> HTMLResponse:
    resolved = _resolve_as_of(as_of)
    try:
        mode = LLMMode(llm)
    except ValueError:
        mode = LLMMode.DISABLED
    result = deps.analyze(ticker, resolved, llm_mode=mode)
    from wave_alpha.history import service as history_service

    history_service.record_snapshot(
        result, ticker.upper(), resolved, db_path=deps._data_dir() / "history.sqlite"
    )
    narratives: dict[str, str] = {}
    if result.llm_scan is not None:
        narratives = {c.id: c.narrative for c in result.llm_scan.candidates}
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="_counts_list.html",
        context={
            "ranked": result.ranked_daily[:3],
            "narratives": narratives,
            "engine_missed": (
                result.llm_scan.engine_missed if result.llm_scan is not None else False
            ),
        },
    )
