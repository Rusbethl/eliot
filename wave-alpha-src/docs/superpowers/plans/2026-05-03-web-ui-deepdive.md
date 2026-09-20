# wave-alpha v0.3 — Web UI Deep-Dive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a single-ticker deep-dive web UI on top of the v0.1 engine and v0.2 LLM contract — `wave-alpha ui` opens a local FastAPI server in the browser at `/deepdive/{ticker}` showing chart, multi-TF coherence row, top-3 counts with LLM narrative, an auto-derived trade plan, and right-edge scenarios. No watchlist, no scanner; that's v0.4.

**Architecture:** FastAPI + Jinja + HTMX with vendored TradingView Lightweight Charts (MIT). The web layer is a thin presentation tier that calls already-public seams: `run_analysis`, `load_config`, `LLMRunner` — no business logic in `web/`. Each page section is its own HTMX fragment endpoint so heavy panels (LLM-narrated counts) can stream in lazily while the chart paints sub-second. Static assets are vendored — no CDN at runtime, no node, no npm.

**Tech Stack:** FastAPI 0.110+, uvicorn, Jinja2, python-multipart (form posts), HTMX (vendored), Alpine.js (vendored), TradingView Lightweight Charts v5 (MIT, vendored). Reuses existing engine + LLM code from v0.1 / v0.2.

**Reference spec:** `docs/superpowers/specs/2026-05-03-wave-alpha-design.md` §3 (tech stack), §11 (v0.1 UI), §13 (privacy).

**Depends on:** v0.1 engine core + v0.2 LLM contract (both merged to `main`).

---

## File structure

```
src/wave_alpha/web/
├── __init__.py
├── app.py                       # FastAPI factory
├── format.py                    # jinja filters: fmt_decimal, fmt_pct, fmt_date
├── chart_data.py                # AnalyzeResult → JSON-serializable chart payload
├── deps.py                      # FastAPI dependencies (settings, runner, etc.)
├── routes/
│   ├── __init__.py
│   ├── deepdive.py              # /deepdive/{ticker} + fragment endpoints
│   ├── settings.py              # /settings GET/POST
│   └── system.py                # /, /healthz, /quit
├── templates/
│   ├── base.html                # outer shell, asset links, disclaimer footer
│   ├── deepdive.html            # deep-dive page composition
│   ├── settings.html
│   ├── _toolbar.html            # ticker selector + period + degree slider
│   ├── _chart.html              # chart container + init script
│   ├── _coherence_row.html      # tri-badge + score + 1-paragraph narrative
│   ├── _counts_list.html        # top-3 candidates with probabilities and narratives
│   ├── _trade_plan.html         # entry/stop/target card
│   └── _scenarios.html          # right-edge scenarios
└── static/
    ├── app.css                  # minimal hand-written CSS (no Tailwind in v0.3)
    ├── deepdive.js              # chart init + HTMX hooks
    └── vendor/
        ├── lightweight-charts/
        │   └── lightweight-charts.standalone.production.js  (MIT, ~50KB)
        ├── htmx/
        │   └── htmx.min.js  (BSD, ~15KB)
        └── alpine/
            └── alpine.min.js  (MIT, ~15KB)

src/wave_alpha/cli/
└── app.py                       # add `ui` command (port discovery 8765-8775)

tests/
├── web/
│   ├── __init__.py
│   ├── test_app_factory.py
│   ├── test_chart_data.py
│   ├── test_routes_system.py
│   ├── test_routes_settings.py
│   └── test_routes_deepdive.py
└── test_cli_ui.py
```

---

## Phase A — Deps and scaffolding

### Task 1: Add the [ui] optional extra

**Files:**
- Modify: `/Users/alexchen/Documents/project/wave_alpha/pyproject.toml`

Mirror wash-alpha: web is an optional extra so headless / CI installs stay lean.

- [ ] **Step 1: Add the extra**

In `pyproject.toml` `[project.optional-dependencies]`, add:

```toml
ui = [
  "fastapi>=0.110",
  "uvicorn[standard]>=0.27",
  "jinja2>=3.1",
  "python-multipart>=0.0.9",
]
```

- [ ] **Step 2: Sync**

Run: `cd /Users/alexchen/Documents/project/wave_alpha && uv sync --extra dev --extra ui`
Expected: lockfile updates; no resolution errors.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "$(cat <<'EOF'
chore(deps): add [ui] optional extra (fastapi, uvicorn, jinja2)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Empty package layout

**Files:**
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/__init__.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/routes/__init__.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/templates/.gitkeep`
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/static/.gitkeep`
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/static/vendor/.gitkeep`
- Create: `/Users/alexchen/Documents/project/wave_alpha/tests/web/__init__.py`

- [ ] **Step 1: Create empty package files**

All files contain a single empty line; `__init__.py` files are zero-byte; `.gitkeep` files exist to commit empty dirs.

- [ ] **Step 2: Verify import works**

Run: `uv run python -c "import wave_alpha.web; import wave_alpha.web.routes"`
Expected: no error.

- [ ] **Step 3: Commit**

```bash
git add src/wave_alpha/web/__init__.py src/wave_alpha/web/routes/__init__.py src/wave_alpha/web/templates/.gitkeep src/wave_alpha/web/static/.gitkeep src/wave_alpha/web/static/vendor/.gitkeep tests/web/__init__.py
git commit -m "$(cat <<'EOF'
chore(web): empty package scaffolding

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase B — Vendor static assets

Static assets are vendored, never CDN-loaded. Pin versions, document license headers in commit messages.

### Task 3: Vendor TradingView Lightweight Charts (MIT)

**Files:**
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/static/vendor/lightweight-charts/lightweight-charts.standalone.production.js`

- [ ] **Step 1: Download pinned version**

Run:

```bash
mkdir -p src/wave_alpha/web/static/vendor/lightweight-charts
curl -fsSL -o src/wave_alpha/web/static/vendor/lightweight-charts/lightweight-charts.standalone.production.js \
  https://unpkg.com/lightweight-charts@5.0.3/dist/lightweight-charts.standalone.production.js
```

- [ ] **Step 2: Verify file integrity**

Run: `wc -c src/wave_alpha/web/static/vendor/lightweight-charts/lightweight-charts.standalone.production.js`
Expected: file size > 30000 bytes (real bundle, not an HTML 404 page).

Run: `head -c 80 src/wave_alpha/web/static/vendor/lightweight-charts/lightweight-charts.standalone.production.js`
Expected: starts with a license comment block referencing TradingView and MIT.

- [ ] **Step 3: Commit**

```bash
git add src/wave_alpha/web/static/vendor/lightweight-charts/
git commit -m "$(cat <<'EOF'
chore(vendor): TradingView Lightweight Charts v5.0.3 (MIT)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Vendor HTMX (BSD)

**Files:**
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/static/vendor/htmx/htmx.min.js`

- [ ] **Step 1: Download pinned version**

```bash
mkdir -p src/wave_alpha/web/static/vendor/htmx
curl -fsSL -o src/wave_alpha/web/static/vendor/htmx/htmx.min.js \
  https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js
```

- [ ] **Step 2: Verify**

Run: `wc -c src/wave_alpha/web/static/vendor/htmx/htmx.min.js`
Expected: > 30000 bytes.

- [ ] **Step 3: Commit**

```bash
git add src/wave_alpha/web/static/vendor/htmx/
git commit -m "$(cat <<'EOF'
chore(vendor): HTMX v1.9.12 (BSD)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Vendor Alpine.js (MIT)

**Files:**
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/static/vendor/alpine/alpine.min.js`

- [ ] **Step 1: Download pinned version**

```bash
mkdir -p src/wave_alpha/web/static/vendor/alpine
curl -fsSL -o src/wave_alpha/web/static/vendor/alpine/alpine.min.js \
  https://unpkg.com/alpinejs@3.13.10/dist/cdn.min.js
```

- [ ] **Step 2: Verify**

Run: `wc -c src/wave_alpha/web/static/vendor/alpine/alpine.min.js`
Expected: > 30000 bytes.

- [ ] **Step 3: Commit**

```bash
git add src/wave_alpha/web/static/vendor/alpine/
git commit -m "$(cat <<'EOF'
chore(vendor): Alpine.js v3.13.10 (MIT)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Hand-written app.css

**Files:**
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/static/app.css`

Skip Tailwind for v0.3 — a single 200-line hand-written stylesheet is faster to ship and easier to maintain at this scale. Tailwind comes later if the surface grows.

- [ ] **Step 1: Write the stylesheet**

Create `src/wave_alpha/web/static/app.css`:

```css
:root {
  --bg: #0e1117;
  --bg-elev: #161b22;
  --fg: #e6edf3;
  --fg-dim: #8d96a0;
  --accent: #2f81f7;
  --good: #3fb950;
  --warn: #d29922;
  --bad: #f85149;
  --border: #30363d;
  --mono: ui-monospace, SFMono-Regular, "JetBrains Mono", Menlo, monospace;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: var(--bg); color: var(--fg);
  font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
a { color: var(--accent); }
.container { max-width: 1200px; margin: 0 auto; padding: 16px; }
.card { background: var(--bg-elev); border: 1px solid var(--border);
  border-radius: 8px; padding: 16px; margin-bottom: 16px; }
.card h2 { margin: 0 0 12px; font-size: 15px; color: var(--fg-dim);
  text-transform: uppercase; letter-spacing: 0.04em; }
.toolbar { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
  font: 12px/1.6 var(--mono); border: 1px solid var(--border); }
.badge.good  { color: var(--good);  border-color: var(--good); }
.badge.warn  { color: var(--warn);  border-color: var(--warn); }
.badge.bad   { color: var(--bad);   border-color: var(--bad); }
.kpi { font: 600 24px/1 var(--mono); }
.kpi-label { font-size: 12px; color: var(--fg-dim); text-transform: uppercase; }
.row { display: flex; gap: 16px; flex-wrap: wrap; }
.col { flex: 1; min-width: 220px; }
.disclaimer { color: var(--fg-dim); font-size: 12px; padding: 16px;
  text-align: center; border-top: 1px solid var(--border); margin-top: 24px; }
.count-row { padding: 12px 0; border-top: 1px solid var(--border); }
.count-row:first-child { border-top: 0; }
.count-row .pattern { font: 600 13px/1.4 var(--mono); }
.count-row .narrative { color: var(--fg-dim); margin-top: 6px; }
form input, form select { background: var(--bg); color: var(--fg);
  border: 1px solid var(--border); border-radius: 4px; padding: 6px 8px; }
button { background: var(--accent); color: white; border: 0; border-radius: 4px;
  padding: 8px 14px; cursor: pointer; }
.htmx-indicator { display: none; color: var(--fg-dim); font-size: 12px; }
.htmx-request .htmx-indicator { display: inline; }
#chart { width: 100%; height: 480px; }
```

- [ ] **Step 2: Commit**

```bash
git add src/wave_alpha/web/static/app.css
git commit -m "$(cat <<'EOF'
feat(web): hand-written app.css for v0.3 deep-dive UI

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Hatch packaging includes web/static and web/templates

**Files:**
- Modify: `/Users/alexchen/Documents/project/wave_alpha/pyproject.toml`

The existing wheel target only force-includes `elliott/templates_data`. We need to ensure web/ is shipped too — it already is, because hatch packages everything under `src/wave_alpha/`, but static files (non-`.py`) require explicit inclusion.

- [ ] **Step 1: Extend force-include**

Replace `[tool.hatch.build.targets.wheel.force-include]` with:

```toml
[tool.hatch.build.targets.wheel.force-include]
"src/wave_alpha/elliott/templates_data" = "wave_alpha/elliott/templates_data"
"src/wave_alpha/web/templates" = "wave_alpha/web/templates"
"src/wave_alpha/web/static" = "wave_alpha/web/static"
```

- [ ] **Step 2: Verify build**

Run:

```bash
uv build
ls dist/
unzip -l dist/wave_alpha-*.whl | grep -E "(static|templates)" | head
```

Expected: wheel contains `wave_alpha/web/static/...` and `wave_alpha/web/templates/...` entries.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "$(cat <<'EOF'
chore(build): force-include web/templates and web/static in wheel

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase C — App factory and system routes

### Task 8: FastAPI app factory

**Files:**
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/app.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/format.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/tests/web/test_app_factory.py`

- [ ] **Step 1: Write failing test**

`tests/web/test_app_factory.py`:

```python
from fastapi.testclient import TestClient

from wave_alpha.web.app import create_app


def test_create_app_returns_fastapi_instance() -> None:
    app = create_app()
    assert app.title == "wave-alpha"


def test_static_files_mounted() -> None:
    app = create_app()
    client = TestClient(app)
    res = client.get("/static/app.css")
    assert res.status_code == 200
    assert "container" in res.text


def test_jinja_disclaimer_global_available() -> None:
    app = create_app()
    # Jinja env is reachable via app.state.templates
    env = app.state.templates.env
    assert "disclaimer" in env.globals
    assert "Informational only" in env.globals["disclaimer"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_app_factory.py -v`
Expected: FAIL — `wave_alpha.web.app` undefined.

- [ ] **Step 3: Implement format helpers**

Create `src/wave_alpha/web/format.py`:

```python
from __future__ import annotations

from datetime import date
from decimal import Decimal


def fmt_decimal(value: Decimal | float | None, *, places: int = 2) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{places}f}"


def fmt_pct(value: float | None, *, places: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.{places}f}%"


def fmt_date(value: date | None) -> str:
    return value.isoformat() if value else "—"
```

- [ ] **Step 4: Implement app factory**

Create `src/wave_alpha/web/app.py`:

```python
from __future__ import annotations

import time
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
    templates.env.globals["asset_v"] = str(int(time.time()))
    templates.env.filters["fmt_decimal"] = fmt_decimal
    templates.env.filters["fmt_pct"] = fmt_pct
    templates.env.filters["fmt_date"] = fmt_date
    app.state.templates = templates

    # Routes (registered in subsequent tasks)
    from wave_alpha.web.routes import deepdive, settings, system

    app.include_router(system.router)
    app.include_router(settings.router)
    app.include_router(deepdive.router)
    return app
```

System / settings / deepdive routers are stubbed in subsequent tasks — for this task, create minimal empty router files so the import works:

`src/wave_alpha/web/routes/system.py`:

```python
from fastapi import APIRouter

router = APIRouter()
```

`src/wave_alpha/web/routes/settings.py`:

```python
from fastapi import APIRouter

router = APIRouter()
```

`src/wave_alpha/web/routes/deepdive.py`:

```python
from fastapi import APIRouter

router = APIRouter()
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/web/test_app_factory.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/web/app.py src/wave_alpha/web/format.py src/wave_alpha/web/routes/system.py src/wave_alpha/web/routes/settings.py src/wave_alpha/web/routes/deepdive.py tests/web/test_app_factory.py
git commit -m "$(cat <<'EOF'
feat(web): FastAPI app factory with templates, static, and disclaimer

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: System routes (root, healthz, quit)

**Files:**
- Modify: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/routes/system.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/tests/web/test_routes_system.py`

- [ ] **Step 1: Write failing test**

`tests/web/test_routes_system.py`:

```python
from fastapi.testclient import TestClient

from wave_alpha.web.app import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def test_root_redirects_to_settings_on_first_run() -> None:
    res = _client().get("/", follow_redirects=False)
    assert res.status_code == 307
    assert res.headers["location"] == "/settings"


def test_healthz_returns_ok() -> None:
    res = _client().get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
```

- [ ] **Step 2: Run test, verify failure**

Run: `uv run pytest tests/web/test_routes_system.py -v`
Expected: FAIL — root returns 404, healthz returns 404.

- [ ] **Step 3: Implement system router**

Replace `src/wave_alpha/web/routes/system.py`:

```python
from __future__ import annotations

import os
import signal

from fastapi import APIRouter
from fastapi.responses import JSONResponse, RedirectResponse

router = APIRouter()


@router.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/settings", status_code=307)


@router.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@router.post("/quit")
def quit_server() -> JSONResponse:
    """Local-tool convenience: server shuts itself down on POST /quit."""
    os.kill(os.getpid(), signal.SIGINT)
    return JSONResponse({"status": "shutting down"})
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/web/test_routes_system.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/web/routes/system.py tests/web/test_routes_system.py
git commit -m "$(cat <<'EOF'
feat(web): /healthz, / redirect, /quit system routes

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase D — Chart data and engine plumbing

### Task 10: chart_data builder

**Files:**
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/chart_data.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/tests/web/test_chart_data.py`

The chart fragment needs OHLCV bars + pivot overlay markers in the JSON shape Lightweight Charts consumes (`{time: "YYYY-MM-DD", open, high, low, close, value, color, ...}`). Build a pure function that converts the engine outputs into that shape.

- [ ] **Step 1: Write failing test**

`tests/web/test_chart_data.py`:

```python
from datetime import date
from decimal import Decimal

from wave_alpha.domain import Bar, Interval
from wave_alpha.elliott.models import WaveCount
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
from wave_alpha.web.chart_data import build_chart_payload


def _bar(d: date, c: float) -> Bar:
    p = Decimal(f"{c:.2f}")
    return Bar(timestamp=d, open=p, high=p, low=p, close=p, volume=1000)


def _piv(d: date, price: str, t: PivotType, state: PivotState = PivotState.CONFIRMED) -> PivotEvent:
    return PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=d,
        price=Decimal(price),
        type=t,
        state=state,
        confirmed_at=d if state is PivotState.CONFIRMED else None,
    )


def test_build_chart_payload_serializes_bars() -> None:
    bars = [_bar(date(2024, 1, i + 1), 100.0 + i) for i in range(5)]
    payload = build_chart_payload(bars=bars, top_count=None)
    assert len(payload["bars"]) == 5
    assert payload["bars"][0] == {
        "time": "2024-01-01",
        "open": 100.0,
        "high": 100.0,
        "low": 100.0,
        "close": 100.0,
    }


def test_build_chart_payload_includes_pivot_markers_when_count_present() -> None:
    bars = [_bar(date(2024, 1, i + 1), 100.0 + i) for i in range(20)]
    count = WaveCount(
        pattern="impulse",
        degree=2,
        score=0.7,
        pivots=[
            _piv(date(2024, 1, 1), "100", PivotType.LOW),
            _piv(date(2024, 1, 5), "110", PivotType.HIGH),
            _piv(date(2024, 1, 8), "104", PivotType.LOW),
            _piv(date(2024, 1, 15), "130", PivotType.HIGH, state=PivotState.TENTATIVE),
        ],
    )
    payload = build_chart_payload(bars=bars, top_count=count)
    assert "markers" in payload
    assert len(payload["markers"]) == 4
    confirmed = next(m for m in payload["markers"] if m["text"] == "P1")
    tentative = next(m for m in payload["markers"] if m["text"] == "P4")
    assert confirmed["shape"] == "circle"
    assert tentative["shape"] == "square"  # tentative is dashed/different shape


def test_build_chart_payload_no_count_yields_empty_markers() -> None:
    bars = [_bar(date(2024, 1, 1), 100.0)]
    payload = build_chart_payload(bars=bars, top_count=None)
    assert payload["markers"] == []
```

- [ ] **Step 2: Run test, verify failure**

Run: `uv run pytest tests/web/test_chart_data.py -v`
Expected: FAIL — module undefined.

- [ ] **Step 3: Implement builder**

Create `src/wave_alpha/web/chart_data.py`:

```python
from __future__ import annotations

from typing import Any

from wave_alpha.domain import Bar
from wave_alpha.elliott.models import WaveCount
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def build_chart_payload(*, bars: list[Bar], top_count: WaveCount | None) -> dict[str, Any]:
    """Convert engine outputs into the JSON shape Lightweight Charts expects.

    bars     → list of {time, open, high, low, close} dicts (sorted)
    markers  → one per pivot in `top_count`, labeled P1..PN; confirmed pivots
               render as circles, tentative as squares
    """
    bar_payload = [
        {
            "time": b.timestamp.isoformat(),
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "close": float(b.close),
        }
        for b in bars
    ]
    markers = _markers(top_count) if top_count is not None else []
    return {"bars": bar_payload, "markers": markers}


def _markers(count: WaveCount) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, p in enumerate(count.pivots, start=1):
        out.append(
            {
                "time": p.timestamp.isoformat(),
                "price": float(p.price),
                "position": "belowBar" if p.type is PivotType.LOW else "aboveBar",
                "shape": "circle" if p.state is PivotState.CONFIRMED else "square",
                "color": "#3fb950" if p.type is PivotType.LOW else "#f85149",
                "text": f"P{i}",
            }
        )
    return out
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/web/test_chart_data.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/web/chart_data.py tests/web/test_chart_data.py
git commit -m "$(cat <<'EOF'
feat(web): chart_data builder converts engine output to Lightweight Charts JSON

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: deps module — analyze() seam

**Files:**
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/deps.py`

The deepdive routes need to call `run_analysis` with a configured provider and (optionally) an LLMRunner. Centralize that wiring so route tests can monkeypatch a single seam.

- [ ] **Step 1: Implement analyze seam**

Create `src/wave_alpha/web/deps.py`:

```python
from __future__ import annotations

from datetime import date
from pathlib import Path

from wave_alpha.data.cached import CachedProvider
from wave_alpha.data.yahoo import YahooProvider
from wave_alpha.llm.cache import LLMResponseCache
from wave_alpha.llm.client import AnthropicClient, MissingAPIKeyError
from wave_alpha.llm.modes import LLMMode
from wave_alpha.llm.runner import LLMRunner
from wave_alpha.pipeline import AnalyzeRequest, AnalyzeResult, run_analysis
from wave_alpha.prefs.config import load_config, resolve_api_key

_DATA_DIR = Path.home() / ".wave_alpha"


def analyze(
    ticker: str, as_of: date, *, llm_mode: LLMMode = LLMMode.DISABLED
) -> AnalyzeResult:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    provider = CachedProvider(upstream=YahooProvider(), db_path=_DATA_DIR / "cache.sqlite")
    runner: LLMRunner | None = None
    if llm_mode is not LLMMode.DISABLED:
        try:
            client = AnthropicClient(api_key=resolve_api_key(cfg), model=cfg.llm.scan_model)
        except MissingAPIKeyError:
            runner = None  # silently fall back to engine-only when key missing
        else:
            cache = LLMResponseCache(db_path=_DATA_DIR / "llm_cache.sqlite")
            runner = LLMRunner(client=client, cache=cache, mode=llm_mode)
    req = AnalyzeRequest(ticker=ticker.upper(), as_of=as_of)
    return run_analysis(req, provider=provider, llm_mode=llm_mode, llm_runner=runner, alpha=cfg.llm.alpha)
```

- [ ] **Step 2: Verify import**

Run: `uv run python -c "from wave_alpha.web.deps import analyze; print(analyze)"`
Expected: prints `<function analyze at 0x...>`.

- [ ] **Step 3: Commit**

```bash
git add src/wave_alpha/web/deps.py
git commit -m "$(cat <<'EOF'
feat(web): deps.analyze seam composes provider and LLMRunner

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase E — Deep-dive page shell

### Task 12: base.html and deepdive.html templates

**Files:**
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/templates/base.html`
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/templates/deepdive.html`
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/templates/_toolbar.html`

- [ ] **Step 1: Write base.html**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}wave-alpha{% endblock %}</title>
  <link rel="stylesheet" href="/static/app.css?v={{ asset_v }}">
  <script defer src="/static/vendor/htmx/htmx.min.js?v={{ asset_v }}"></script>
  <script defer src="/static/vendor/alpine/alpine.min.js?v={{ asset_v }}"></script>
</head>
<body>
  <div class="container">
    {% block content %}{% endblock %}
  </div>
  <p class="disclaimer">{{ disclaimer }}</p>
</body>
</html>
```

- [ ] **Step 2: Write deepdive.html**

```html
{% extends "base.html" %}
{% block title %}{{ ticker }} — wave-alpha{% endblock %}
{% block content %}
  {% include "_toolbar.html" %}

  <section class="card">
    <h2>Chart</h2>
    <div id="chart"
         hx-get="/deepdive/{{ ticker }}/chart?as_of={{ as_of }}"
         hx-trigger="load"
         hx-swap="innerHTML">
      <span class="htmx-indicator">loading chart…</span>
    </div>
  </section>

  <section class="card"
           hx-get="/deepdive/{{ ticker }}/coherence?as_of={{ as_of }}"
           hx-trigger="load"
           hx-swap="innerHTML">
    <h2>Multi-TF Coherence</h2>
    <span class="htmx-indicator">loading coherence…</span>
  </section>

  <section class="card"
           hx-get="/deepdive/{{ ticker }}/counts?as_of={{ as_of }}&llm={{ llm_mode }}"
           hx-trigger="load"
           hx-swap="innerHTML">
    <h2>Counts</h2>
    <span class="htmx-indicator">running engine + LLM…</span>
  </section>

  <section class="card"
           hx-get="/deepdive/{{ ticker }}/plan?as_of={{ as_of }}"
           hx-trigger="load delay:200ms"
           hx-swap="innerHTML">
    <h2>Trade Plan</h2>
    <span class="htmx-indicator">loading plan…</span>
  </section>

  <section class="card"
           hx-get="/deepdive/{{ ticker }}/scenarios?as_of={{ as_of }}"
           hx-trigger="load delay:200ms"
           hx-swap="innerHTML">
    <h2>Right-Edge Scenarios</h2>
    <span class="htmx-indicator">loading scenarios…</span>
  </section>
{% endblock %}
```

- [ ] **Step 3: Write _toolbar.html**

```html
<section class="card">
  <form class="toolbar" method="get" action="/deepdive/{{ ticker }}">
    <label>Ticker
      <input type="text" name="t" value="{{ ticker }}" autocapitalize="characters" size="6">
    </label>
    <label>As-of
      <input type="date" name="as_of" value="{{ as_of }}">
    </label>
    <label>LLM
      <select name="llm">
        <option value="disabled" {% if llm_mode == 'disabled' %}selected{% endif %}>disabled</option>
        <option value="cached"   {% if llm_mode == 'cached'   %}selected{% endif %}>cached</option>
        <option value="live"     {% if llm_mode == 'live'     %}selected{% endif %}>live</option>
      </select>
    </label>
    <button type="submit">Reload</button>
  </form>
</section>
```

Note: the form posts back to `/deepdive/{{ ticker }}` with the new params; the route reads `?t=` to decide whether to redirect.

- [ ] **Step 4: Commit**

```bash
git add src/wave_alpha/web/templates/base.html src/wave_alpha/web/templates/deepdive.html src/wave_alpha/web/templates/_toolbar.html
git commit -m "$(cat <<'EOF'
feat(web): base and deepdive templates with HTMX fragment loaders

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: GET /deepdive/{ticker} renders the shell

**Files:**
- Modify: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/routes/deepdive.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/tests/web/test_routes_deepdive.py`

- [ ] **Step 1: Write failing test**

`tests/web/test_routes_deepdive.py`:

```python
from datetime import date

from fastapi.testclient import TestClient

from wave_alpha.web.app import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def test_deepdive_shell_renders_toolbar_and_fragment_targets() -> None:
    res = _client().get("/deepdive/AAPL?as_of=2024-12-31")
    assert res.status_code == 200
    body = res.text
    assert "AAPL" in body
    assert "2024-12-31" in body
    assert 'id="chart"' in body
    assert "/deepdive/AAPL/chart" in body
    assert "/deepdive/AAPL/coherence" in body
    assert "/deepdive/AAPL/counts" in body


def test_deepdive_shell_redirects_when_ticker_changed_in_form() -> None:
    res = _client().get("/deepdive/AAPL?t=MSFT&as_of=2024-12-31", follow_redirects=False)
    assert res.status_code == 307
    assert res.headers["location"] == "/deepdive/MSFT?as_of=2024-12-31&llm=disabled"


def test_deepdive_shell_defaults_as_of_to_today_when_missing() -> None:
    res = _client().get("/deepdive/AAPL")
    assert res.status_code == 200
    assert "AAPL" in res.text
```

- [ ] **Step 2: Run test, verify failure**

Run: `uv run pytest tests/web/test_routes_deepdive.py -v`
Expected: all FAIL.

- [ ] **Step 3: Implement shell route**

Replace `src/wave_alpha/web/routes/deepdive.py`:

```python
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from wave_alpha.llm.modes import LLMMode

router = APIRouter(prefix="/deepdive")


def _resolve_as_of(value: str | None) -> date:
    return date.fromisoformat(value) if value else date.today()


@router.get("/{ticker}", response_class=HTMLResponse)
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/web/test_routes_deepdive.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/web/routes/deepdive.py tests/web/test_routes_deepdive.py
git commit -m "$(cat <<'EOF'
feat(web): GET /deepdive/{ticker} renders shell with HTMX targets

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase F — Chart fragment

### Task 14: GET /deepdive/{ticker}/chart returns chart HTML + JSON

**Files:**
- Modify: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/routes/deepdive.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/templates/_chart.html`
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/static/deepdive.js`
- Modify: `/Users/alexchen/Documents/project/wave_alpha/tests/web/test_routes_deepdive.py`

The chart fragment returns a small HTML snippet that contains an inline JSON payload + a `<script>` block invoking the chart init function. Using HTMX `hx-swap="innerHTML"` on the parent `<div id="chart">` swaps in the inner content; the script then calls `window.WaveAlpha.renderChart(payload)`.

- [ ] **Step 1: Write failing test**

Append to `tests/web/test_routes_deepdive.py`:

```python
from unittest.mock import patch

from datetime import date as _date
from decimal import Decimal as _Decimal

from wave_alpha.domain import Bar as _Bar
from wave_alpha.pipeline import AnalyzeResult


def _stub_analyze_result(ticker: str = "AAPL") -> AnalyzeResult:
    bars = [_Bar(timestamp=_date(2024, 1, i + 1), open=_Decimal("100"),
                 high=_Decimal("100"), low=_Decimal("100"), close=_Decimal("100"),
                 volume=1) for i in range(5)]
    return AnalyzeResult(ticker=ticker, as_of=_date(2024, 12, 31), counts_daily=[])


def test_chart_fragment_returns_inline_json_and_render_call(monkeypatch) -> None:
    from wave_alpha.web.routes import deepdive as deepdive_routes

    bars = [_Bar(timestamp=_date(2024, 1, i + 1), open=_Decimal("100"),
                 high=_Decimal("100"), low=_Decimal("100"), close=_Decimal("100"),
                 volume=1) for i in range(3)]
    monkeypatch.setattr(deepdive_routes, "_load_bars", lambda ticker, as_of: bars)
    monkeypatch.setattr(deepdive_routes, "_load_top_count", lambda ticker, as_of: None)

    res = _client().get("/deepdive/AAPL/chart?as_of=2024-12-31")
    assert res.status_code == 200
    assert "WaveAlpha.renderChart" in res.text
    assert "2024-01-01" in res.text  # bar timestamp shows up in the JSON payload
```

- [ ] **Step 2: Run test, verify failure**

Run: `uv run pytest tests/web/test_routes_deepdive.py::test_chart_fragment_returns_inline_json_and_render_call -v`
Expected: FAIL — route undefined.

- [ ] **Step 3: Implement chart route + helpers**

Replace `src/wave_alpha/web/routes/deepdive.py` to add chart route + helpers (keep existing shell route):

```python
from __future__ import annotations

import json
from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from wave_alpha.domain import Bar, Interval
from wave_alpha.elliott.models import WaveCount
from wave_alpha.llm.modes import LLMMode
from wave_alpha.web import deps
from wave_alpha.web.chart_data import build_chart_payload

router = APIRouter(prefix="/deepdive")


def _resolve_as_of(value: str | None) -> date:
    return date.fromisoformat(value) if value else date.today()


def _load_bars(ticker: str, as_of: date) -> list[Bar]:
    """Direct provider read for chart rendering — separate seam from `analyze`
    so the chart paints sub-second without LLM latency."""
    from pathlib import Path

    from wave_alpha.data.cached import CachedProvider
    from wave_alpha.data.point_in_time import PointInTimeView
    from wave_alpha.data.yahoo import YahooProvider

    data_dir = Path.home() / ".wave_alpha"
    data_dir.mkdir(parents=True, exist_ok=True)
    provider = CachedProvider(upstream=YahooProvider(), db_path=data_dir / "cache.sqlite")
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


@router.get("/{ticker}", response_class=HTMLResponse)
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
    try:
        LLMMode(llm)
    except ValueError:
        llm = "disabled"
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="deepdive.html",
        context={"ticker": ticker.upper(), "as_of": resolved.isoformat(), "llm_mode": llm},
    )


@router.get("/{ticker}/chart", response_class=HTMLResponse)
def chart_fragment(
    ticker: str, request: Request, as_of: str | None = None
) -> HTMLResponse:
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
```

Create `src/wave_alpha/web/templates/_chart.html`:

```html
<div id="chart-canvas" style="width:100%;height:480px;"></div>
<script>
  (function () {
    var payload = {{ payload_json | safe }};
    if (window.WaveAlpha && window.WaveAlpha.renderChart) {
      window.WaveAlpha.renderChart('chart-canvas', payload);
    } else {
      window._waveAlphaPending = payload;
    }
  })();
</script>
```

Create `src/wave_alpha/web/static/deepdive.js`:

```javascript
(function (global) {
  function renderChart(containerId, payload) {
    var el = document.getElementById(containerId);
    if (!el || !global.LightweightCharts) {
      return;
    }
    el.innerHTML = "";
    var chart = global.LightweightCharts.createChart(el, {
      layout: { background: { color: "#0e1117" }, textColor: "#e6edf3" },
      grid: { vertLines: { color: "#30363d" }, horzLines: { color: "#30363d" } },
      timeScale: { timeVisible: false, borderColor: "#30363d" },
      rightPriceScale: { borderColor: "#30363d" },
    });
    var series = chart.addCandlestickSeries({
      upColor: "#3fb950", downColor: "#f85149",
      borderUpColor: "#3fb950", borderDownColor: "#f85149",
      wickUpColor: "#3fb950", wickDownColor: "#f85149",
    });
    series.setData(payload.bars);
    if (payload.markers && payload.markers.length) {
      series.setMarkers(payload.markers);
    }
  }
  global.WaveAlpha = global.WaveAlpha || {};
  global.WaveAlpha.renderChart = renderChart;
  if (global._waveAlphaPending) {
    renderChart("chart-canvas", global._waveAlphaPending);
    global._waveAlphaPending = null;
  }
})(window);
```

Update `src/wave_alpha/web/templates/base.html` `<head>` block to load the chart bundle and the helper:

```html
  <script defer src="/static/vendor/lightweight-charts/lightweight-charts.standalone.production.js?v={{ asset_v }}"></script>
  <script defer src="/static/deepdive.js?v={{ asset_v }}"></script>
```

(Add these AFTER htmx and Alpine.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/web/test_routes_deepdive.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/web/routes/deepdive.py src/wave_alpha/web/templates/_chart.html src/wave_alpha/web/static/deepdive.js src/wave_alpha/web/templates/base.html tests/web/test_routes_deepdive.py
git commit -m "$(cat <<'EOF'
feat(web): chart fragment with Lightweight Charts pivot overlay

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase G — Coherence, counts, plan, scenarios fragments

### Task 15: Coherence fragment

**Files:**
- Modify: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/routes/deepdive.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/templates/_coherence_row.html`

- [ ] **Step 1: Write failing test**

Append to `tests/web/test_routes_deepdive.py`:

```python
def test_coherence_fragment_renders_score_and_pairwise(monkeypatch) -> None:
    from wave_alpha.coherence.score import CoherenceResult
    from wave_alpha.web import deps as deps_module
    from wave_alpha.pipeline import AnalyzeResult

    def fake_analyze(ticker, as_of, llm_mode):  # noqa: ARG001
        return AnalyzeResult(
            ticker=ticker, as_of=as_of,
            coherence=CoherenceResult(
                score=0.82, multiplier=1.07,
                pairwise={"weekly_daily": 0.9, "daily_4h": 0.8, "weekly_4h": 0.7},
                fallback_used=None,
            ),
        )
    monkeypatch.setattr(deps_module, "analyze", fake_analyze)
    res = _client().get("/deepdive/AAPL/coherence?as_of=2024-12-31")
    assert res.status_code == 200
    assert "0.82" in res.text
    assert "weekly_daily" in res.text or "W ✓ D" in res.text
```

- [ ] **Step 2: Run test, verify failure**

Run: `uv run pytest tests/web/test_routes_deepdive.py::test_coherence_fragment_renders_score_and_pairwise -v`
Expected: FAIL.

- [ ] **Step 3: Implement coherence route**

Append to `src/wave_alpha/web/routes/deepdive.py`:

```python
@router.get("/{ticker}/coherence", response_class=HTMLResponse)
def coherence_fragment(
    ticker: str, request: Request, as_of: str | None = None
) -> HTMLResponse:
    resolved = _resolve_as_of(as_of)
    result = deps.analyze(ticker, resolved, llm_mode=LLMMode.DISABLED)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="_coherence_row.html",
        context={"coh": result.coherence},
    )
```

Create `src/wave_alpha/web/templates/_coherence_row.html`:

```html
{% if coh %}
  <div class="row">
    <div class="col">
      <div class="kpi-label">Coherence</div>
      <div class="kpi">{{ "%.2f"|format(coh.score) }}</div>
    </div>
    <div class="col">
      <div class="kpi-label">Multiplier</div>
      <div class="kpi">{{ "%.2f"|format(coh.multiplier) }}×</div>
    </div>
    <div class="col">
      <div class="kpi-label">Pairwise</div>
      {% for k, v in coh.pairwise.items() %}
        <div><span class="badge">{{ k }}</span> {{ "%.2f"|format(v) }}</div>
      {% endfor %}
      {% if coh.fallback_used %}
        <div class="badge warn">{{ coh.fallback_used }}</div>
      {% endif %}
    </div>
  </div>
{% else %}
  <p>Insufficient history for coherence.</p>
{% endif %}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/web/test_routes_deepdive.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/web/routes/deepdive.py src/wave_alpha/web/templates/_coherence_row.html tests/web/test_routes_deepdive.py
git commit -m "$(cat <<'EOF'
feat(web): coherence fragment with score, multiplier, and pairwise breakdown

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 16: Counts fragment with LLM narratives

**Files:**
- Modify: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/routes/deepdive.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/templates/_counts_list.html`

- [ ] **Step 1: Write failing test**

Append to `tests/web/test_routes_deepdive.py`:

```python
def test_counts_fragment_renders_top_three_with_narratives(monkeypatch) -> None:
    from datetime import date
    from decimal import Decimal
    from wave_alpha.domain import Interval
    from wave_alpha.elliott.models import WaveCount
    from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
    from wave_alpha.pipeline import AnalyzeResult, RankedCount
    from wave_alpha.llm.responses import ScanCandidate, ScanResponse
    from wave_alpha.web import deps as deps_module

    def piv(d, p, t):
        return PivotEvent(interval=Interval.DAILY, degree=2, timestamp=date(2024, 1, d),
                          price=Decimal(p), type=t, state=PivotState.CONFIRMED, confirmed_at=date(2024, 1, d))

    count = WaveCount(pattern="impulse", degree=2, score=0.7,
                      pivots=[piv(1, "100", PivotType.LOW), piv(5, "110", PivotType.HIGH)])

    def fake_analyze(ticker, as_of, llm_mode):  # noqa: ARG001
        rc = RankedCount(candidate_id="C1", count=count, engine_score=0.7, llm_prob=0.55, final_score=0.66)
        scan = ScanResponse(
            candidates=[ScanCandidate(id="C1", probability=0.55, narrative="solid impulse setup")],
            engine_missed=False,
        )
        return AnalyzeResult(ticker=ticker, as_of=as_of, counts_daily=[count],
                             ranked_daily=[rc], llm_scan=scan)

    monkeypatch.setattr(deps_module, "analyze", fake_analyze)
    res = _client().get("/deepdive/AAPL/counts?as_of=2024-12-31&llm=cached")
    assert res.status_code == 200
    assert "C1" in res.text
    assert "impulse" in res.text
    assert "solid impulse setup" in res.text
    assert "0.66" in res.text  # final score
```

- [ ] **Step 2: Run test, verify failure**

Run: `uv run pytest tests/web/test_routes_deepdive.py::test_counts_fragment_renders_top_three_with_narratives -v`
Expected: FAIL.

- [ ] **Step 3: Implement counts route**

Append to `src/wave_alpha/web/routes/deepdive.py`:

```python
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
```

Create `src/wave_alpha/web/templates/_counts_list.html`:

```html
{% if not ranked %}
  <p>No candidates produced for this date.</p>
{% endif %}
{% for rc in ranked %}
  <div class="count-row">
    <div class="pattern">
      <span class="badge">{{ rc.candidate_id }}</span>
      {{ rc.count.pattern }}
      <span class="badge">final {{ "%.2f"|format(rc.final_score) }}</span>
      <span class="badge">engine {{ "%.2f"|format(rc.engine_score) }}</span>
      {% if rc.llm_prob is not none %}
        <span class="badge">llm {{ "%.2f"|format(rc.llm_prob) }}</span>
      {% endif %}
    </div>
    {% set narrative = narratives.get(rc.candidate_id) %}
    {% if narrative %}<p class="narrative">{{ narrative }}</p>{% endif %}
    {% if rc.count.soft_violations %}
      <p class="narrative"><em>soft: {{ rc.count.soft_violations|join(', ') }}</em></p>
    {% endif %}
  </div>
{% endfor %}
{% if engine_missed %}
  <p class="badge warn">LLM flagged engine_missed — consider expanding template set.</p>
{% endif %}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/web/test_routes_deepdive.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/web/routes/deepdive.py src/wave_alpha/web/templates/_counts_list.html tests/web/test_routes_deepdive.py
git commit -m "$(cat <<'EOF'
feat(web): counts fragment with engine, llm, and final scores plus narratives

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 17: Trade plan fragment

**Files:**
- Modify: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/routes/deepdive.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/templates/_trade_plan.html`

- [ ] **Step 1: Write failing test**

Append to `tests/web/test_routes_deepdive.py`:

```python
def test_plan_fragment_renders_top_signal_when_present(monkeypatch) -> None:
    from datetime import date
    from decimal import Decimal
    from wave_alpha.pipeline import AnalyzeResult
    from wave_alpha.trades.models import TradeSignal
    from wave_alpha.web import deps as deps_module

    sig = TradeSignal(
        ticker="AAPL", as_of=date(2024, 12, 31), direction="long",
        entry_price=Decimal("120"), stop_price=Decimal("118"), target_price=Decimal("130"),
        count_id="C1", count_pattern="impulse", wave_label="5",
        confidence=0.66, expected_bars_to_target=8, max_holding_bars=20,
    )

    def fake_analyze(ticker, as_of, llm_mode):  # noqa: ARG001
        return AnalyzeResult(ticker=ticker, as_of=as_of, signals=[sig])

    monkeypatch.setattr(deps_module, "analyze", fake_analyze)
    res = _client().get("/deepdive/AAPL/plan?as_of=2024-12-31")
    assert res.status_code == 200
    assert "LONG" in res.text
    assert "120" in res.text
    assert "118" in res.text
    assert "130" in res.text


def test_plan_fragment_renders_empty_state_when_no_signal(monkeypatch) -> None:
    from datetime import date
    from wave_alpha.pipeline import AnalyzeResult
    from wave_alpha.web import deps as deps_module

    monkeypatch.setattr(deps_module, "analyze",
                        lambda ticker, as_of, llm_mode: AnalyzeResult(ticker=ticker, as_of=as_of))
    res = _client().get("/deepdive/AAPL/plan?as_of=2024-12-31")
    assert res.status_code == 200
    assert "no" in res.text.lower()
```

- [ ] **Step 2: Run test, verify failure**

Run: `uv run pytest tests/web/test_routes_deepdive.py::test_plan_fragment_renders_top_signal_when_present -v`
Expected: FAIL.

- [ ] **Step 3: Implement plan route**

Append to `src/wave_alpha/web/routes/deepdive.py`:

```python
@router.get("/{ticker}/plan", response_class=HTMLResponse)
def plan_fragment(
    ticker: str, request: Request, as_of: str | None = None
) -> HTMLResponse:
    resolved = _resolve_as_of(as_of)
    result = deps.analyze(ticker, resolved, llm_mode=LLMMode.DISABLED)
    top = result.signals[0] if result.signals else None
    return request.app.state.templates.TemplateResponse(
        request=request, name="_trade_plan.html", context={"sig": top}
    )
```

Create `src/wave_alpha/web/templates/_trade_plan.html`:

```html
{% if not sig %}
  <p>No trade plan derived for this count set (none meeting min_confidence).</p>
{% else %}
  <div class="row">
    <div class="col">
      <div class="kpi-label">Direction</div>
      <div class="kpi">{{ sig.direction|upper }}</div>
    </div>
    <div class="col">
      <div class="kpi-label">Entry</div>
      <div class="kpi">{{ sig.entry_price }}</div>
    </div>
    <div class="col">
      <div class="kpi-label">Stop</div>
      <div class="kpi">{{ sig.stop_price }}</div>
    </div>
    <div class="col">
      <div class="kpi-label">Target</div>
      <div class="kpi">{{ sig.target_price }}</div>
    </div>
    <div class="col">
      <div class="kpi-label">R:R</div>
      <div class="kpi">{{ "%.2f"|format(sig.rr) }}</div>
    </div>
    <div class="col">
      <div class="kpi-label">Bars to target</div>
      <div class="kpi">{{ sig.expected_bars_to_target }}</div>
    </div>
    <div class="col">
      <div class="kpi-label">Max hold</div>
      <div class="kpi">{{ sig.max_holding_bars }}</div>
    </div>
  </div>
  <p class="narrative">
    Pattern: {{ sig.count_pattern }} (W{{ sig.wave_label }}) · confidence {{ "%.2f"|format(sig.confidence) }}
  </p>
{% endif %}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/web/test_routes_deepdive.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/web/routes/deepdive.py src/wave_alpha/web/templates/_trade_plan.html tests/web/test_routes_deepdive.py
git commit -m "$(cat <<'EOF'
feat(web): trade plan fragment with entry/stop/target/RR card

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 18: Right-edge scenarios fragment

**Files:**
- Modify: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/routes/deepdive.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/templates/_scenarios.html`

For v0.3, "scenarios" surfaces the second and third candidates (alternates) since the engine doesn't yet emit a separate scenarios stream. Spec §11.6 anticipates 1-3 right-edge candidates with explicit invalidation; we use `count.pivots[-1]` as the invalidation reference for now.

- [ ] **Step 1: Write failing test**

Append to `tests/web/test_routes_deepdive.py`:

```python
def test_scenarios_fragment_renders_alternates_with_invalidation(monkeypatch) -> None:
    from datetime import date
    from decimal import Decimal
    from wave_alpha.domain import Interval
    from wave_alpha.elliott.models import WaveCount
    from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
    from wave_alpha.pipeline import AnalyzeResult, RankedCount
    from wave_alpha.web import deps as deps_module

    def piv(d, p, t):
        return PivotEvent(interval=Interval.DAILY, degree=2, timestamp=date(2024, 1, d),
                          price=Decimal(p), type=t, state=PivotState.CONFIRMED, confirmed_at=date(2024, 1, d))

    counts = [
        WaveCount(pattern="impulse", degree=2, score=0.7,
                  pivots=[piv(1, "100", PivotType.LOW), piv(5, "118", PivotType.LOW)]),
        WaveCount(pattern="zigzag", degree=2, score=0.5,
                  pivots=[piv(1, "100", PivotType.HIGH), piv(5, "92", PivotType.HIGH)]),
    ]
    ranked = [RankedCount(candidate_id=f"C{i+1}", count=c, engine_score=c.score,
                          llm_prob=None, final_score=c.score) for i, c in enumerate(counts)]

    def fake_analyze(ticker, as_of, llm_mode):  # noqa: ARG001
        return AnalyzeResult(ticker=ticker, as_of=as_of, ranked_daily=ranked, counts_daily=counts)

    monkeypatch.setattr(deps_module, "analyze", fake_analyze)
    res = _client().get("/deepdive/AAPL/scenarios?as_of=2024-12-31")
    assert res.status_code == 200
    assert "C2" in res.text
    assert "zigzag" in res.text
    assert "92" in res.text  # invalidation = last pivot price of alt
```

- [ ] **Step 2: Run test, verify failure**

Run: `uv run pytest tests/web/test_routes_deepdive.py::test_scenarios_fragment_renders_alternates_with_invalidation -v`
Expected: FAIL.

- [ ] **Step 3: Implement scenarios route**

Append to `src/wave_alpha/web/routes/deepdive.py`:

```python
@router.get("/{ticker}/scenarios", response_class=HTMLResponse)
def scenarios_fragment(
    ticker: str, request: Request, as_of: str | None = None
) -> HTMLResponse:
    resolved = _resolve_as_of(as_of)
    result = deps.analyze(ticker, resolved, llm_mode=LLMMode.DISABLED)
    alternates = result.ranked_daily[1:4]  # C2..C4
    return request.app.state.templates.TemplateResponse(
        request=request, name="_scenarios.html", context={"alternates": alternates}
    )
```

Create `src/wave_alpha/web/templates/_scenarios.html`:

```html
{% if not alternates %}
  <p>No alternate counts at this date.</p>
{% endif %}
{% for rc in alternates %}
  <div class="count-row">
    <span class="badge">{{ rc.candidate_id }}</span>
    <strong>{{ rc.count.pattern }}</strong>
    <span class="badge">final {{ "%.2f"|format(rc.final_score) }}</span>
    {% if rc.count.pivots %}
      <span class="badge warn">invalidates at {{ rc.count.pivots[-1].price }}</span>
    {% endif %}
  </div>
{% endfor %}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/web/test_routes_deepdive.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/web/routes/deepdive.py src/wave_alpha/web/templates/_scenarios.html tests/web/test_routes_deepdive.py
git commit -m "$(cat <<'EOF'
feat(web): right-edge scenarios fragment surfaces alt counts with invalidation

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase H — Settings page

### Task 19: GET/POST /settings

**Files:**
- Modify: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/routes/settings.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/web/templates/settings.html`
- Create: `/Users/alexchen/Documents/project/wave_alpha/tests/web/test_routes_settings.py`

- [ ] **Step 1: Write failing test**

`tests/web/test_routes_settings.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient

from wave_alpha.web.app import create_app


def _client(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setattr(
        "wave_alpha.web.routes.settings._config_path", lambda: tmp_path / "config.yaml"
    )
    return TestClient(create_app())


def test_settings_get_renders_form(tmp_path: Path, monkeypatch) -> None:
    res = _client(monkeypatch, tmp_path).get("/settings")
    assert res.status_code == 200
    assert "Anthropic" in res.text or "API" in res.text
    assert "alpha" in res.text


def test_settings_post_persists_values(tmp_path: Path, monkeypatch) -> None:
    cfg = tmp_path / "config.yaml"
    res = _client(monkeypatch, tmp_path).post(
        "/settings",
        data={"api_key": "sk-test", "scan_model": "claude-haiku-4-5-20251001",
              "deep_model": "claude-sonnet-4-6", "alpha": "0.7"},
    )
    assert res.status_code in (200, 303)
    assert "sk-test" in cfg.read_text()
    assert "alpha: 0.7" in cfg.read_text()


def test_settings_post_invalid_alpha_returns_400(tmp_path: Path, monkeypatch) -> None:
    res = _client(monkeypatch, tmp_path).post(
        "/settings",
        data={"api_key": "", "scan_model": "x", "deep_model": "y", "alpha": "1.5"},
    )
    assert res.status_code == 400
```

- [ ] **Step 2: Run test, verify failure**

Run: `uv run pytest tests/web/test_routes_settings.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement settings route**

Replace `src/wave_alpha/web/routes/settings.py`:

```python
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
    scan_model: str = Form("claude-haiku-4-5-20251001"),
    deep_model: str = Form("claude-sonnet-4-6"),
    alpha: float = Form(0.6),
) -> RedirectResponse:
    if not 0.0 <= alpha <= 1.0:
        raise HTTPException(status_code=400, detail="alpha must be in [0, 1]")
    new_cfg = AppConfig(
        llm=LLMConfig(
            api_key=api_key or None,
            scan_model=scan_model,
            deep_model=deep_model,
            alpha=alpha,
        )
    )
    save_config(new_cfg, _config_path())
    return RedirectResponse(url="/settings", status_code=303)
```

Create `src/wave_alpha/web/templates/settings.html`:

```html
{% extends "base.html" %}
{% block title %}Settings — wave-alpha{% endblock %}
{% block content %}
  <section class="card">
    <h2>Settings</h2>
    <form method="post" action="/settings">
      <p>
        <label>Anthropic API key
          <input type="password" name="api_key" value="{{ cfg.llm.api_key or '' }}" size="50">
        </label>
      </p>
      <p>
        <label>Scan model
          <input type="text" name="scan_model" value="{{ cfg.llm.scan_model }}" size="40">
        </label>
      </p>
      <p>
        <label>Deep-dive model
          <input type="text" name="deep_model" value="{{ cfg.llm.deep_model }}" size="40">
        </label>
      </p>
      <p>
        <label>α (engine vs LLM blend, 0..1)
          <input type="number" step="0.05" min="0" max="1" name="alpha" value="{{ cfg.llm.alpha }}">
        </label>
      </p>
      <button type="submit">Save</button>
    </form>
    <p class="narrative">
      Stored at <code>~/.wave_alpha/config.yaml</code>. Environment variable
      <code>ANTHROPIC_API_KEY</code> overrides the file at runtime.
    </p>
    <p><a href="/deepdive/SPY">→ deep-dive SPY</a></p>
  </section>
{% endblock %}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/web/test_routes_settings.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/web/routes/settings.py src/wave_alpha/web/templates/settings.html tests/web/test_routes_settings.py
git commit -m "$(cat <<'EOF'
feat(web): /settings page reads/writes ~/.wave_alpha/config.yaml

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase I — `wave-alpha ui` CLI command

### Task 20: ui command with port discovery and browser launch

**Files:**
- Modify: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/cli/app.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/tests/test_cli_ui.py`

- [ ] **Step 1: Write failing test**

`tests/test_cli_ui.py`:

```python
import socket
from typer.testing import CliRunner

from wave_alpha.cli.app import app
from wave_alpha.cli.app import _pick_port  # exposed for testing

runner = CliRunner()


def test_ui_help_documents_port_and_no_browser_flags() -> None:
    res = runner.invoke(app, ["ui", "--help"])
    assert res.exit_code == 0
    assert "--port" in res.stdout
    assert "--no-browser" in res.stdout


def test_pick_port_returns_first_free(monkeypatch) -> None:
    # Bind 8765 to simulate it being taken; pick_port should return 8766
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 8765))
    try:
        port = _pick_port(start=8765, end=8775)
        assert port == 8766
    finally:
        s.close()


def test_pick_port_raises_when_no_ports_free(monkeypatch) -> None:
    sockets = []
    for p in range(9000, 9003):
        sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sk.bind(("127.0.0.1", p))
        sockets.append(sk)
    try:
        import pytest
        with pytest.raises(RuntimeError, match="no free port"):
            _pick_port(start=9000, end=9002)
    finally:
        for sk in sockets:
            sk.close()
```

- [ ] **Step 2: Run test, verify failure**

Run: `uv run pytest tests/test_cli_ui.py -v`
Expected: FAIL — `_pick_port` and `ui` command undefined.

- [ ] **Step 3: Implement ui command**

Append to `src/wave_alpha/cli/app.py`:

```python
import socket
import webbrowser


def _pick_port(*, start: int = 8765, end: int = 8775) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"no free port in {start}-{end}")


@app.command()
def ui(
    port: int | None = typer.Option(
        None, "--port", help="TCP port to bind. Default: first free port in 8765-8775."
    ),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Don't open the browser automatically."
    ),
    reload: bool = typer.Option(False, "--reload", help="uvicorn auto-reload (dev only)."),
) -> None:
    """Launch the local web UI in a browser."""
    import uvicorn  # local import — uvicorn is in [ui] extra

    chosen_port = port or _pick_port()
    url = f"http://127.0.0.1:{chosen_port}"
    typer.echo(f"wave-alpha UI on {url}  (Ctrl-C to quit)")
    if not no_browser:
        webbrowser.open(url)
    uvicorn.run(
        "wave_alpha.web.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=chosen_port,
        reload=reload,
        log_level="info",
    )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_cli_ui.py -v`
Expected: all PASS.

- [ ] **Step 5: Smoke-test the command (manual)**

Run: `uv run wave-alpha ui --no-browser --port 8765`
Expected: `wave-alpha UI on http://127.0.0.1:8765`. Visit `http://127.0.0.1:8765/settings` in a browser; the form renders. Visit `/deepdive/SPY`; chart paints, coherence/counts/plan fragments load. Ctrl-C cleanly stops.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/cli/app.py tests/test_cli_ui.py
git commit -m "$(cat <<'EOF'
feat(cli): wave-alpha ui launches local FastAPI UI with port discovery

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase J — Wrap-up

### Task 21: Bump version, README, smoke-test full suite

**Files:**
- Modify: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/__init__.py`
- Modify: `/Users/alexchen/Documents/project/wave_alpha/pyproject.toml`
- Modify: `/Users/alexchen/Documents/project/wave_alpha/README.md`

- [ ] **Step 1: Bump version**

Modify `src/wave_alpha/__init__.py`:

```python
__version__ = "0.3.0"
```

Modify `pyproject.toml`:

```toml
version = "0.3.0"
```

- [ ] **Step 2: Document UI in README**

Append to `README.md`:

```markdown
## Web UI (v0.3)

Single-ticker deep-dive web UI on top of the engine and LLM contract.

```bash
# Install with the [ui] extra
uv sync --extra ui

# Launch the UI (binds to 127.0.0.1 on the first free port in 8765-8775)
uv run wave-alpha ui

# Or pin a port and skip browser launch
uv run wave-alpha ui --port 8765 --no-browser
```

Then visit `http://127.0.0.1:8765/settings` (paste your Anthropic API key) and `http://127.0.0.1:8765/deepdive/SPY` for a deep-dive.

The page composition mirrors spec §11: chart with pivot overlays (TradingView Lightweight Charts), multi-TF coherence row, top-3 counts with LLM narrative, auto-derived trade plan, right-edge alternates. All static assets are vendored — no CDN at runtime.
```

- [ ] **Step 3: Run the full suite**

Run:

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

All three must pass.

- [ ] **Step 4: Commit**

```bash
git add src/wave_alpha/__init__.py pyproject.toml README.md
git commit -m "$(cat <<'EOF'
chore(release): bump to v0.3.0; document Web UI

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final review

- Spec §11 coverage:
  - Toolbar (ticker, period, llm-mode select) — Task 12 + 13
  - Chart fragment with EW overlay — Tasks 10, 14
  - Multi-TF coherence row — Task 15
  - Counts list with LLM narrative — Task 16
  - Trade plan card — Task 17
  - Right-edge scenarios — Task 18
  - Settings page (API key, model, default period replaced by alpha) — Task 19
  - Disclaimer footer — Task 8 (base.html)
- Spec §11 deferred:
  - **Vision verify** (point 7 — opt-in Sonnet vision on rendered chart PNG): requires headless-browser screenshot of Lightweight Charts canvas, which adds Playwright. Deferred to v0.3.1 follow-up; the spec marks it "optional."
  - **Fib retracement/extension lines on chart**: spec point 2 mentions them; v0.3 ships pivot markers only. Lines drawn on top of Lightweight Charts via `addLineSeries` is a focused follow-up.
  - **"↗ Open in TradingView" deep-link**: low-effort follow-up; not on critical path.
  - **Lazy-load order tuning**: chart fragment uses `hx-trigger="load"` (immediate), counts/plan/scenarios use `delay:200ms` to let the chart paint first. Spec calls for sub-second chart; this approximates without a measurement harness.
  - **Toolbar period selector + degree slider + freshness indicator** (spec §11.1): not in v0.3 toolbar; current toolbar has Ticker + As-of + LLM only. Add when there's a clear UX requirement (or when scan view in v0.4 needs them).
  - **Multi-TF coherence tri-badge `W ✓ D ✓ 4h ⚠`** (spec §11.3): current row shows score/multiplier/raw pairwise dict. Tri-badge requires per-TF threshold logic that doesn't exist in CoherenceResult yet.
  - **LLM paragraph explanation of coherence** (spec §11.3): would require a deep-dive call dedicated to this section. Defer to v0.4 alongside fib lines and vision verify.
- Spec §13 privacy: settings note explicitly states stored location and env-var override; no telemetry added.

## Self-review notes (writer)

- Type consistency: `RankedCount.candidate_id` matches narratives keyed by ScanCandidate.id (`f"C{i+1}"`); `_load_top_count` returns `WaveCount | None` matching `build_chart_payload`'s `top_count` parameter.
- Test isolation: route tests use `monkeypatch` to stub `wave_alpha.web.deps.analyze`, so no test hits yfinance or Anthropic. Settings tests stub `_config_path`. CLI port tests use real sockets bound to `127.0.0.1`.
- Cost discipline: every fragment route invokes `deps.analyze(...)` independently. v0.3 accepts the duplication for simplicity; the OHLCV cache and (LLM-mode dependent) response cache make repeat calls cheap. A v0.3.1 follow-up could memoize per-request (`functools.lru_cache` keyed by `(ticker, as_of, llm_mode)`) but is out of scope here.
- Vendor commit hygiene: each vendored asset gets its own commit with version + license in the message — auditable.
