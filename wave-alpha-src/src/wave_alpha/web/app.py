from __future__ import annotations

from importlib.resources import files

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from wave_alpha import __version__
from wave_alpha.web.format import fmt_date, fmt_decimal, fmt_pct

DISCLAIMER: str = "⚠ Informational only. Confirm thesis. Not investment advice."


def create_app() -> FastAPI:
    app = FastAPI(title="wave-alpha", version=__version__)

    static_dir = files("wave_alpha.web") / "static"
    templates_dir = files("wave_alpha.web") / "templates"

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    templates = Jinja2Templates(directory=str(templates_dir))
    templates.env.globals["disclaimer"] = DISCLAIMER
    templates.env.globals["app_version"] = __version__
    # Tied to package version (not wall-clock) so browser cache survives
    # uvicorn restarts. Bumping the version on each release invalidates
    # cached assets exactly when their bytes may have changed.
    templates.env.globals["asset_v"] = __version__
    templates.env.filters["fmt_decimal"] = fmt_decimal
    templates.env.filters["fmt_pct"] = fmt_pct
    templates.env.filters["fmt_date"] = fmt_date
    app.state.templates = templates

    # Routes
    from wave_alpha.web.routes import about, deepdive, home, scan, settings, system, watchlist

    app.include_router(system.router)
    app.include_router(home.router)
    app.include_router(about.router)
    app.include_router(settings.router)
    app.include_router(deepdive.router)
    app.include_router(watchlist.router)
    app.include_router(scan.router)
    return app
