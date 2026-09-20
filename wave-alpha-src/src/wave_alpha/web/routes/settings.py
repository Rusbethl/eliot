from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from wave_alpha.prefs.config import DEFAULT_CONFIG_PATH, load_config, save_config
from wave_alpha.prefs.models import AppConfig, LLMConfig

router = APIRouter()


def _config_path() -> Path:
    return DEFAULT_CONFIG_PATH


@router.get("/settings", response_class=HTMLResponse)
def settings_get(request: Request) -> HTMLResponse:
    cfg = load_config(_config_path())
    return request.app.state.templates.TemplateResponse(
        request=request, name="settings.html", context={"cfg": cfg}
    )


@router.post("/settings")
def settings_post(
    api_key: str = Form(""),
    scan_model: str = Form(""),
    deep_model: str = Form(""),
    alpha: float = Form(0.6),
) -> RedirectResponse:
    if not 0.0 <= alpha <= 1.0:
        raise HTTPException(status_code=400, detail="alpha must be in [0, 1]")
    existing = load_config(_config_path()).llm
    new_llm = LLMConfig(
        api_key=api_key or existing.api_key,
        scan_model=scan_model or existing.scan_model,
        deep_model=deep_model or existing.deep_model,
        alpha=alpha,
    )
    save_config(AppConfig(llm=new_llm), _config_path())
    return RedirectResponse(url="/settings", status_code=303)
