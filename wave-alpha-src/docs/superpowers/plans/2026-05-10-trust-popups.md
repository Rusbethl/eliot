# Trust Popovers and `/about` Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a click-pinned `.popover` component (Alpine.js) that surfaces "why this signal fired" and "how this metric is computed" content next to 17 metric sites across the web UI, plus a new `/about` page that narrates the engine pipeline end-to-end and serves as the deep-link target for every popover's "Learn more →" link.

**Architecture:** Web-only, additive. A new Jinja macro (`popover()`) plus ~60 lines of vendored JS implement the component. Each existing page partial gets one or more popover invocations supplied with already-exposed data (no engine changes). The `/about` page is a static template with a single `GET /about` route, registered alongside existing routers.

**Tech Stack:** Python 3.11 / FastAPI / Jinja2 / HTMX / Alpine.js (core build, no plugins) / inline SVG. Tests via `pytest` + FastAPI `TestClient`. All commands run via `uv run …`.

---

## File Structure

**Net new files:**
- `src/wave_alpha/web/templates/about.html` — the new `/about` page (Task 2 scaffold; Task 11 content)
- `src/wave_alpha/web/routes/about.py` — single GET route (Task 2)
- `src/wave_alpha/web/static/popover.js` — Alpine factory + manual focus-trap + global single-open coordinator (Task 1)
- `tests/web/test_popover_macro.py` — structural tests for the macro (Task 1)
- `tests/web/test_about_route.py` — route + anchor coverage tests (Task 2)

**Lightly modified:**
- `src/wave_alpha/web/templates/_macros.html` — add `popover()` macro (Task 1)
- `src/wave_alpha/web/static/app.css` — append `.popover*` block (Task 1)
- `src/wave_alpha/web/templates/base.html` — add "About" nav link, load `popover.js` (Task 2)
- `src/wave_alpha/web/app.py` — register about router (Task 2)
- `src/wave_alpha/web/templates/_coherence_row.html` — add popover with per-pair data (Task 3)
- `src/wave_alpha/web/templates/_right_edge.html` — add popover with model/degree data (Task 4)
- `src/wave_alpha/web/templates/_trade_plan.html` — add popover with entry/stop/target sources (Task 5)
- `src/wave_alpha/web/templates/_counts_list.html` — per-candidate popovers (Task 6)
- `src/wave_alpha/web/templates/_hero.html` — Confidence + R:R popovers (Task 7)
- `src/wave_alpha/web/templates/_scan_table.html` — column-header popovers (Task 8)
- `src/wave_alpha/web/templates/home.html` — KPI strip popovers (Task 9)
- `src/wave_alpha/web/templates/settings.html` — α slider popover (Task 10)
- `CHANGELOG.md` — note the addition under 0.6.x (Task 12)

**Not touched:** anything under `pipeline.py`, `elliott/`, `coherence/`, `right_edge/`, `llm/`, `trades/`, `scan/`, `data/`, `history/`, `cli/`, `backtest/`, all data providers, all persistence paths.

---

## Task 1: Popover foundation (macro + CSS + JS + structural test)

**Files:**
- Modify: `src/wave_alpha/web/templates/_macros.html`
- Modify: `src/wave_alpha/web/static/app.css` (append a new section at end of file)
- Create: `src/wave_alpha/web/static/popover.js`
- Modify: `src/wave_alpha/web/templates/base.html` (load `popover.js` — covered fully in Task 2; here we only need it for the test to pass)
- Create: `tests/web/test_popover_macro.py`

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_popover_macro.py`:

```python
"""Structural tests for the popover() Jinja macro.

These verify the rendered HTML scaffolding without booting the full app —
the macro can be exercised against the Jinja environment directly.
"""

from __future__ import annotations

from importlib.resources import files

from jinja2 import Environment, FileSystemLoader, select_autoescape


def _env() -> Environment:
    templates_dir = files("wave_alpha.web") / "templates"
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
    )


_RENDER_HOST = """\
{% from "_macros.html" import popover %}
{% call popover("How we got 1.18×", anchor="coherence") %}
  <p class="popover-narrative">Body content here.</p>
{% endcall %}
"""


def test_popover_renders_trigger_button() -> None:
    out = _env().from_string(_RENDER_HOST).render()
    assert 'class="popover-trigger"' in out
    assert 'aria-label="Show details for How we got 1.18×"' in out
    assert 'aria-expanded' in out
    assert 'type="button"' in out


def test_popover_renders_dialog_card() -> None:
    out = _env().from_string(_RENDER_HOST).render()
    assert 'class="popover-card"' in out
    assert 'role="dialog"' in out
    assert "Body content here." in out


def test_popover_renders_close_button() -> None:
    out = _env().from_string(_RENDER_HOST).render()
    assert 'class="popover-close"' in out
    assert 'aria-label="Close"' in out
    # The × glyph is the visible close affordance
    assert ">×<" in out


def test_popover_renders_learn_more_link_when_anchor_supplied() -> None:
    out = _env().from_string(_RENDER_HOST).render()
    assert 'href="/about#coherence"' in out
    assert "Learn more" in out


def test_popover_omits_learn_more_when_no_anchor() -> None:
    out = _env().from_string(
        '{% from "_macros.html" import popover %}'
        '{% call popover("Plain headline") %}body{% endcall %}'
    ).render()
    assert "Learn more" not in out
    assert "/about#" not in out


def test_popover_alpine_directives_present() -> None:
    """Verify the click-toggle + click-outside + escape directives are wired."""
    out = _env().from_string(_RENDER_HOST).render()
    assert 'x-data="popover()"' in out
    assert 'x-on:click="open = !open"' in out
    assert 'x-on:click.outside="open = false"' in out
    assert 'x-on:keydown.escape.window="open = false"' in out
    assert 'x-show="open"' in out
    assert 'x-cloak' in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_popover_macro.py -v`
Expected: 6 failures with `UndefinedError: 'popover' is undefined` (macro doesn't exist yet).

- [ ] **Step 3: Add the popover macro**

Append to `src/wave_alpha/web/templates/_macros.html` (end of file, after `gloss`):

```jinja
{# popover(headline, anchor=None) — click-pinned trust popover.
   Use as a {% call %} block. The body block becomes the popover body.
   When `anchor` is supplied, a "Learn more →" link to /about#<anchor> is appended.
   See docs/superpowers/specs/2026-05-10-trust-popups-design.md for usage. #}
{% macro popover(headline, anchor=none) -%}
  <span class="popover" x-data="popover()">
    <button type="button"
            class="popover-trigger"
            aria-label="Show details for {{ headline }}"
            x-on:click="open = !open"
            x-bind:aria-expanded="open ? 'true' : 'false'"
            aria-expanded="false">?</button>
    <div class="popover-card"
         role="dialog"
         x-show="open"
         x-on:keydown.escape.window="open = false"
         x-on:click.outside="open = false"
         x-cloak>
      <div class="popover-head">
        <span class="popover-headline">{{ headline }}</span>
        <button type="button" class="popover-close"
                aria-label="Close"
                x-on:click="open = false">&times;</button>
      </div>
      <div class="popover-body">{{ caller() }}</div>
      {% if anchor %}
        <a class="popover-more" href="/about#{{ anchor }}">Learn more &rarr;</a>
      {% endif %}
    </div>
  </span>
{%- endmacro %}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/web/test_popover_macro.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Add CSS for the popover**

Append to the end of `src/wave_alpha/web/static/app.css`:

```css
/* ─── Trust popover ───────────────────────────────────────────────────── */
[x-cloak] { display: none !important; }

.popover { position: relative; display: inline-flex; vertical-align: middle; margin-left: 6px; }

.popover-trigger {
  width: 16px; height: 16px;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 50%;
  background: rgba(34,211,238,0.06);
  color: var(--fg-muted);
  border: 1px solid var(--border-default);
  font: 700 11px var(--font-mono);
  cursor: pointer;
  transition: color 120ms, background 120ms, box-shadow 120ms, border-color 120ms;
  padding: 0;
}
.popover-trigger:hover,
.popover-trigger:focus-visible {
  color: var(--cyan);
  background: rgba(34,211,238,0.12);
  border-color: var(--border-strong);
  box-shadow: 0 0 12px rgba(34,211,238,0.40);
  outline: none;
}

.popover-card {
  position: absolute;
  z-index: 30;
  top: calc(100% + 8px);
  left: 0;
  width: 380px;
  max-width: calc(100vw - 24px);
  max-height: 360px;
  overflow-y: auto;
  padding: 16px;
  background: var(--bg-elevated);
  border: 1px solid var(--border-default);
  border-radius: var(--radius);
  box-shadow: var(--shadow-elev);
  text-align: left;
  animation: popover-in 120ms ease-out;
}
@keyframes popover-in {
  from { opacity: 0; transform: translateY(-4px); }
  to   { opacity: 1; transform: translateY(0); }
}
@media (prefers-reduced-motion: reduce) {
  .popover-card { animation: none; }
}

.popover-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 8px; margin-bottom: 10px;
}
.popover-headline {
  font: 800 11px/1.2 var(--font-mono);
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--cyan);
}
.popover-close {
  width: 24px; height: 24px;
  display: inline-flex; align-items: center; justify-content: center;
  background: transparent; border: none; padding: 0;
  color: var(--fg-muted); font-size: 18px; line-height: 1;
  cursor: pointer; border-radius: 4px;
}
.popover-close:hover,
.popover-close:focus-visible {
  color: var(--cyan); background: rgba(34,211,238,0.10); outline: none;
}

.popover-body { font: 400 13px/1.45 var(--font-sans); color: var(--fg-secondary); }
.popover-narrative { margin: 0 0 12px; }

.popover-data {
  width: 100%;
  border-collapse: collapse;
  font: 400 12px/1.4 var(--font-mono);
  font-variant-numeric: tabular-nums;
  margin: 0;
}
.popover-data th,
.popover-data td { padding: 4px 0; vertical-align: baseline; }
.popover-data th { color: var(--fg-muted); font-weight: 400; text-align: left; padding-right: 12px; }
.popover-data td { color: var(--fg-primary); text-align: right; }
.popover-data tr.total th,
.popover-data tr.total td {
  border-top: 1px solid var(--border-strong);
  padding-top: 8px;
  margin-top: 4px;
}
.popover-data tr.total td { color: var(--cyan); text-shadow: 0 0 6px rgba(34,211,238,0.55); }

.popover-more {
  display: inline-block;
  margin-top: 12px;
  font: 400 12px var(--font-sans);
  color: var(--cyan);
  text-decoration: none;
}
.popover-more:hover,
.popover-more:focus-visible { text-decoration: underline; outline: none; }

/* On compact triggers (e.g. KPI titles), allow the card to anchor flush-left
   regardless of trigger width. */
.popover-card[data-anchor="right"] { left: auto; right: 0; }
```

- [ ] **Step 6: Create the JS file**

Create `src/wave_alpha/web/static/popover.js`:

```js
/**
 * Alpine factory + helpers for the trust popover.
 * Loaded from base.html. Depends on Alpine core only (no plugins).
 *
 * Behaviors:
 *   - Single source of truth for `open` state per popover.
 *   - Only one popover open at a time across the page.
 *   - Manual focus-trap: Tab cycles between focusable descendants of the card;
 *     focus is returned to the trigger on close.
 *
 * See docs/superpowers/specs/2026-05-10-trust-popups-design.md §4.
 */
(function () {
  const OPEN_EVENT = "popover:open";

  function focusableInside(card) {
    return Array.from(
      card.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'
      )
    );
  }

  window.popover = function popover() {
    return {
      open: false,
      _trigger: null,
      _card: null,
      _id: Math.random().toString(36).slice(2),

      init() {
        this._trigger = this.$el.querySelector(".popover-trigger");
        this._card = this.$el.querySelector(".popover-card");

        // Close on global open of any other popover.
        window.addEventListener(OPEN_EVENT, (e) => {
          if (e.detail && e.detail.id !== this._id && this.open) {
            this.open = false;
          }
        });

        // Watch open transitions to manage focus + global broadcast.
        this.$watch("open", (val) => {
          if (val) {
            window.dispatchEvent(
              new CustomEvent(OPEN_EVENT, { detail: { id: this._id } })
            );
            // Move focus into the card.
            queueMicrotask(() => {
              const focusables = focusableInside(this._card);
              (focusables[0] || this._card).focus({ preventScroll: true });
            });
          } else {
            // Return focus to trigger on close.
            this._trigger && this._trigger.focus({ preventScroll: true });
          }
        });

        // Manual focus-trap on Tab while open.
        this.$el.addEventListener("keydown", (e) => {
          if (!this.open || e.key !== "Tab") return;
          const focusables = focusableInside(this._card);
          if (focusables.length === 0) return;
          const first = focusables[0];
          const last = focusables[focusables.length - 1];
          if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
          }
        });
      },
    };
  };
})();
```

- [ ] **Step 7: Wire `popover.js` into `base.html`**

Modify `src/wave_alpha/web/templates/base.html` — add a `<script>` line in the `<head>` after the Alpine vendor line. The current line 10 reads:

```html
  <script defer src="/static/vendor/alpine/alpine.min.js?v={{ asset_v }}"></script>
```

Insert immediately after it:

```html
  <script defer src="/static/popover.js?v={{ asset_v }}"></script>
```

The `defer` attribute on Alpine guarantees `popover.js` runs after Alpine is parsed; the global `window.popover` factory is then available when Alpine initialises components.

- [ ] **Step 8: Verify the asset is served**

Run: `uv sync --extra ui && uv run python -c "from importlib.resources import files; p = files('wave_alpha.web') / 'static' / 'popover.js'; print(p.is_file(), p.stat().st_size)"`
Expected: `True <byte count>` (file exists and has nonzero size).

- [ ] **Step 9: Boot smoke test**

Run: `uv run wave-alpha ui &` (in a separate shell), then:

```bash
curl -s http://127.0.0.1:8765/static/popover.js | head -1
curl -s http://127.0.0.1:8765/ | grep -c 'popover.js'
```

Expected: `popover.js` is served (first command shows the JS prologue), and the home page references it (second command prints `1`).

Stop the server: `pkill -f "wave-alpha ui"`.

- [ ] **Step 10: Run all web tests to confirm no regressions**

Run: `uv run pytest tests/web/ -v`
Expected: all existing tests pass; new `test_popover_macro.py` tests pass.

- [ ] **Step 11: Commit**

```bash
git add src/wave_alpha/web/templates/_macros.html \
        src/wave_alpha/web/static/app.css \
        src/wave_alpha/web/static/popover.js \
        src/wave_alpha/web/templates/base.html \
        tests/web/test_popover_macro.py
git commit -m "feat(web): add popover macro + Alpine component for trust UI"
```

---

## Task 2: `/about` route + template scaffold + nav link

Build the page skeleton with empty anchor sections so popover "Learn more →" links work end-to-end before any per-popover task lands. Section content is filled in Task 11.

**Files:**
- Create: `src/wave_alpha/web/routes/about.py`
- Create: `src/wave_alpha/web/templates/about.html`
- Modify: `src/wave_alpha/web/app.py`
- Modify: `src/wave_alpha/web/templates/base.html` (add "About" nav link)
- Create: `tests/web/test_about_route.py`

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_about_route.py`:

```python
"""GET /about: renders page, includes all 11 anchor IDs, includes disclaimer."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wave_alpha.web.app import create_app

ANCHORS = [
    "pipeline",
    "pivots",
    "templates",
    "enumeration",
    "coherence",
    "right-edge",
    "alpha-blend",
    "trade-plan",
    "lookahead",
    "privacy",
    "what-this-is-not",
]


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    from wave_alpha.web import deps as web_deps
    from wave_alpha.web.routes import scan as scan_routes
    from wave_alpha.web.routes import watchlist as wl_routes

    monkeypatch.setattr(scan_routes, "_scan_dir", lambda: tmp_path)
    monkeypatch.setattr(wl_routes, "_watchlist_path", lambda: tmp_path / "wl.yaml")
    monkeypatch.setattr(web_deps, "_data_dir", lambda: tmp_path)
    return TestClient(create_app())


def test_about_returns_200(client: TestClient) -> None:
    res = client.get("/about")
    assert res.status_code == 200


def test_about_contains_all_anchor_ids(client: TestClient) -> None:
    res = client.get("/about")
    body = res.text
    missing = [a for a in ANCHORS if f'id="{a}"' not in body]
    assert not missing, f"missing anchor sections: {missing}"


def test_about_contains_disclaimer(client: TestClient) -> None:
    res = client.get("/about")
    assert "Informational only" in res.text  # from DISCLAIMER in app.py


def test_about_contains_h1(client: TestClient) -> None:
    res = client.get("/about")
    assert "How wave-alpha works" in res.text


def test_nav_includes_about_link_on_every_page(client: TestClient) -> None:
    for path in ["/", "/scan", "/watchlist", "/settings", "/about"]:
        res = client.get(path)
        assert res.status_code == 200, f"{path} returned {res.status_code}"
        assert 'href="/about"' in res.text, f"About link missing on {path}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_about_route.py -v`
Expected: all 5 tests fail (route returns 404, nav link absent).

- [ ] **Step 3: Create the about router**

Create `src/wave_alpha/web/routes/about.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/about", response_class=HTMLResponse)
def about(request: Request) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="about.html",
        context={},
    )
```

- [ ] **Step 4: Create the about template scaffold**

Create `src/wave_alpha/web/templates/about.html` with all 11 anchor sections present but content-light. Section bodies are filled in Task 11; the structure here is sufficient for tests and for popover "Learn more →" links to land at the correct scroll position.

```jinja
{% extends "base.html" %}
{% set active = "about" %}
{% block title %}How wave-alpha works — wave-alpha{% endblock %}

{% block content %}
<div class="page about">
  <div class="page-head">
    <div>
      <h1 class="page-title">How wave-alpha works</h1>
      <p class="page-sub">
        A deterministic Elliott Wave engine with optional LLM rerank. This page
        documents what the engine does, how each metric is computed, and what it
        does not claim.
      </p>
    </div>
  </div>

  <div class="about-grid">
    <aside class="about-toc">
      <div class="card-head"><h3 class="card-title">On this page</h3></div>
      <ul>
        <li><a href="#pipeline">Pipeline overview</a></li>
        <li><a href="#pivots">Pivots &amp; degrees</a></li>
        <li><a href="#templates">Wave templates</a></li>
        <li><a href="#enumeration">Enumeration &amp; scoring</a></li>
        <li><a href="#coherence">Multi-timeframe coherence</a></li>
        <li><a href="#right-edge">Right-edge confirmation</a></li>
        <li><a href="#alpha-blend">LLM rerank &amp; α blend</a></li>
        <li><a href="#trade-plan">Trade plan derivation</a></li>
        <li><a href="#lookahead">Lookahead-safety guarantee</a></li>
        <li><a href="#privacy">Privacy contract</a></li>
        <li><a href="#what-this-is-not">What this is not</a></li>
      </ul>
    </aside>

    <article class="about-body">
      <section id="pipeline" class="about-section">
        <h2>Pipeline overview</h2>
        <p>Filled in Task 11.</p>
      </section>
      <section id="pivots" class="about-section">
        <h2>Pivots &amp; degrees</h2>
        <p>Filled in Task 11.</p>
      </section>
      <section id="templates" class="about-section">
        <h2>Wave templates</h2>
        <p>Filled in Task 11.</p>
      </section>
      <section id="enumeration" class="about-section">
        <h2>Enumeration &amp; scoring</h2>
        <p>Filled in Task 11.</p>
      </section>
      <section id="coherence" class="about-section">
        <h2>Multi-timeframe coherence</h2>
        <p>Filled in Task 11.</p>
      </section>
      <section id="right-edge" class="about-section">
        <h2>Right-edge confirmation</h2>
        <p>Filled in Task 11.</p>
      </section>
      <section id="alpha-blend" class="about-section">
        <h2>LLM rerank &amp; α blend</h2>
        <p>Filled in Task 11.</p>
      </section>
      <section id="trade-plan" class="about-section">
        <h2>Trade plan derivation</h2>
        <p>Filled in Task 11.</p>
      </section>
      <section id="lookahead" class="about-section">
        <h2>Lookahead-safety guarantee</h2>
        <p>Filled in Task 11.</p>
      </section>
      <section id="privacy" class="about-section">
        <h2>Privacy contract</h2>
        <p>Filled in Task 11.</p>
      </section>
      <section id="what-this-is-not" class="about-section">
        <h2>What this is not</h2>
        <p>Filled in Task 11.</p>
      </section>
    </article>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 5: Add minimal `/about` layout CSS**

Append to `src/wave_alpha/web/static/app.css` (after the popover block):

```css
/* ─── /about page ─────────────────────────────────────────────────────── */
.about-grid {
  display: grid;
  grid-template-columns: 1fr 3fr;
  gap: 24px;
  margin-top: 16px;
}
@media (max-width: 1023px) {
  .about-grid { grid-template-columns: 1fr; }
}
.about-toc {
  position: sticky;
  top: 88px;
  align-self: start;
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius);
  padding: 14px 16px;
  max-height: calc(100vh - 120px);
  overflow-y: auto;
}
.about-toc ul { list-style: none; padding: 0; margin: 8px 0 0; display: flex; flex-direction: column; gap: 6px; }
.about-toc a {
  color: var(--fg-secondary);
  text-decoration: none;
  font-size: 13px;
  display: block;
  padding: 4px 6px;
  border-radius: 4px;
  border-left: 2px solid transparent;
}
.about-toc a:hover { color: var(--cyan); border-left-color: var(--cyan); background: rgba(34,211,238,0.06); }

.about-body { display: flex; flex-direction: column; gap: 24px; }
.about-section {
  background: var(--bg-surface);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius);
  padding: 20px 24px;
  scroll-margin-top: 88px;
}
.about-section h2 {
  font: 800 18px/1.2 var(--font-sans);
  letter-spacing: -0.01em;
  margin: 0 0 12px;
  color: var(--fg-primary);
}
.about-section p { color: var(--fg-secondary); font-size: 14px; line-height: 1.55; margin: 0 0 12px; }
.about-section .src-cite {
  margin-top: 12px;
  font: 400 11px var(--font-mono);
  color: var(--fg-muted);
}
.about-section svg.diagram { display: block; max-width: 100%; height: auto; margin: 12px 0; }
```

- [ ] **Step 6: Add the "About" nav link**

Modify `src/wave_alpha/web/templates/base.html`. The current nav block (lines 34–39) reads:

```html
    <div class="nav-links">
      <a class="nav-link {% if active == 'home' %}active{% endif %}" href="/">Home</a>
      <a class="nav-link {% if active == 'scan' %}active{% endif %}" href="/scan">Scan</a>
      <a class="nav-link {% if active == 'watchlist' %}active{% endif %}" href="/watchlist">Watchlist</a>
      <a class="nav-link {% if active == 'settings' %}active{% endif %}" href="/settings">Settings</a>
    </div>
```

Add an "About" link after Settings:

```html
    <div class="nav-links">
      <a class="nav-link {% if active == 'home' %}active{% endif %}" href="/">Home</a>
      <a class="nav-link {% if active == 'scan' %}active{% endif %}" href="/scan">Scan</a>
      <a class="nav-link {% if active == 'watchlist' %}active{% endif %}" href="/watchlist">Watchlist</a>
      <a class="nav-link {% if active == 'settings' %}active{% endif %}" href="/settings">Settings</a>
      <a class="nav-link nav-link-muted {% if active == 'about' %}active{% endif %}" href="/about">About</a>
    </div>
```

Append low-emphasis treatment to `app.css` (in the same about-page block):

```css
.nav-link-muted { color: var(--fg-muted); }
.nav-link-muted:hover, .nav-link-muted.active { color: var(--cyan); }
```

- [ ] **Step 7: Register the router**

Modify `src/wave_alpha/web/app.py`. The current import line reads:

```python
    from wave_alpha.web.routes import deepdive, home, scan, settings, system, watchlist
```

Update to include `about`:

```python
    from wave_alpha.web.routes import about, deepdive, home, scan, settings, system, watchlist
```

And register it after `home`:

```python
    app.include_router(system.router)
    app.include_router(home.router)
    app.include_router(about.router)
    app.include_router(settings.router)
    app.include_router(deepdive.router)
    app.include_router(watchlist.router)
    app.include_router(scan.router)
```

- [ ] **Step 8: Run the test**

Run: `uv run pytest tests/web/test_about_route.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 9: Run lint**

Run: `uv run ruff check src/wave_alpha/web/routes/about.py tests/web/test_about_route.py`
Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add src/wave_alpha/web/routes/about.py \
        src/wave_alpha/web/templates/about.html \
        src/wave_alpha/web/templates/base.html \
        src/wave_alpha/web/app.py \
        src/wave_alpha/web/static/app.css \
        tests/web/test_about_route.py
git commit -m "feat(web): add /about page scaffold with anchor sections + nav link"
```

---

## Task 3: Coherence card popover

**Files:**
- Modify: `src/wave_alpha/web/templates/_coherence_row.html`
- Modify: `tests/web/test_routes_deepdive.py` (add assertion for popover headline) — or create a focused test if the existing file is unrelated; default to adding to it.

- [ ] **Step 1: Write the failing test**

Append to `tests/web/test_routes_deepdive.py`:

```python
def test_coherence_row_includes_popover(client, sample_deepdive_data):
    """The /coherence fragment renders the trust popover with multiplier headline."""
    ticker, as_of = sample_deepdive_data
    res = client.get(f"/deepdive/{ticker}/coherence?as_of={as_of}")
    assert res.status_code == 200
    body = res.text
    assert "popover-trigger" in body
    assert "How we got" in body and "×" in body  # "How we got 1.18×" pattern
    assert 'href="/about#coherence"' in body
    assert "popover-data" in body
```

If `sample_deepdive_data` fixture doesn't exist, check the file's existing fixtures and reuse the closest one. If none fit, add a minimal fixture that monkeypatches the analysis to return a deterministic `coherence_payload` with `pairwise = {"WD": 0.84, "W4": 0.72, "D4": 0.79}` and `multiplier = 1.18`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_routes_deepdive.py::test_coherence_row_includes_popover -v`
Expected: FAIL — `popover-trigger` substring not found.

- [ ] **Step 3: Add the popover invocation**

Modify `src/wave_alpha/web/templates/_coherence_row.html`. The current import line reads:

```jinja
{% from "_macros.html" import tri %}
```

Update to include `popover`:

```jinja
{% from "_macros.html" import tri, popover %}
```

Locate the `<p>` line that renders `Multiplier ×{{ "%.2f"|format(coh.multiplier) }}.` (currently line 24). Insert the popover *immediately after* the closing `</p>` of that line, inside the same right-hand `<div>` so it appears under the coherence summary:

```jinja
      <p style="margin: 8px 0 0; font-size: 13px; color: var(--fg-secondary);">
        Multiplier ×{{ "%.2f"|format(coh.multiplier) }}.
        {% if coh.fallback_used %}<span style="color: var(--warn);">{{ coh.fallback_used }}</span>{% endif %}
        {% call popover("How we got " ~ ("%.2f" | format(coh.multiplier)) ~ "×", anchor="coherence") %}
          <p class="popover-narrative">Coherence multiplies the engine score by how strongly the weekly, daily, and 4-hour timeframes agree on the same wave count. High agreement = high confidence the count is real, not a chart artifact.</p>
          <table class="popover-data">
            {% for k, v in coh.pairwise.items() %}
              <tr><th>{{ k }}</th><td class="num">{{ "%.2f"|format(v) }}</td></tr>
            {% endfor %}
            <tr class="total"><th>multiplier</th><td class="num">{{ "%.2f"|format(coh.multiplier) }}&times;</td></tr>
          </table>
        {% endcall %}
      </p>
```

(Note: a `<span>` containing the popover trigger is valid inside a `<p>`. The popover-card itself is `position: absolute`, so it lives outside normal flow.)

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/web/test_routes_deepdive.py -v`
Expected: PASS for the new test plus all existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/web/templates/_coherence_row.html \
        tests/web/test_routes_deepdive.py
git commit -m "feat(web): add coherence card trust popover"
```

---

## Task 4: Right-edge card popover

**Files:**
- Modify: `src/wave_alpha/web/templates/_right_edge.html`
- Modify: `tests/web/test_routes_deepdive.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/web/test_routes_deepdive.py`:

```python
def test_right_edge_fragment_includes_popover(client, sample_deepdive_data):
    ticker, as_of = sample_deepdive_data
    res = client.get(f"/deepdive/{ticker}/right-edge?as_of={as_of}")
    assert res.status_code == 200
    body = res.text
    assert "popover-trigger" in body
    assert "How we got probability" in body
    assert 'href="/about#right-edge"' in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_routes_deepdive.py::test_right_edge_fragment_includes_popover -v`
Expected: FAIL.

- [ ] **Step 3: Add the popover invocation**

Modify `src/wave_alpha/web/templates/_right_edge.html`. The file currently has no macro import (uses raw HTML). Add at line 1:

```jinja
{% from "_macros.html" import popover %}
```

Then replace the existing `<p>` line at the bottom of the assessment block (currently lines 22–24) with the popover-augmented version. The current trailing block reads:

```jinja
  <p style="margin: 12px 0 0; font-size: 12px; color: var(--fg-muted);">
    Probability that the most recent tentative pivot is confirmed (will not be invalidated by a deeper move against it).
  </p>
{% endif %}
```

Replace with:

```jinja
  <p style="margin: 12px 0 0; font-size: 12px; color: var(--fg-muted);">
    Probability that the most recent tentative pivot is confirmed (will not be invalidated by a deeper move against it).
    {% call popover("How we got probability " ~ ("%.2f" | format(assessment.proba)), anchor="right-edge") %}
      <p class="popover-narrative">Right-edge confirmation estimates whether the most recent pivot is real or likely to be invalidated. Trained per degree on real pivots from 2015&ndash;2024.</p>
      <table class="popover-data">
        <tr><th>model</th><td>{{ assessment.model_kind }}</td></tr>
        <tr><th>degree</th><td class="num">{{ assessment.degree }}</td></tr>
        <tr><th>pivot</th><td>{{ assessment.pivot_type|upper }} @ {{ assessment.pivot_timestamp.isoformat() }}</td></tr>
        <tr class="total"><th>probability</th><td class="num">{{ "%.2f"|format(assessment.proba) }}</td></tr>
      </table>
    {% endcall %}
  </p>
{% endif %}
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/web/test_routes_deepdive.py -v`
Expected: PASS for the new test, all existing tests still green.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/web/templates/_right_edge.html tests/web/test_routes_deepdive.py
git commit -m "feat(web): add right-edge card trust popover"
```

---

## Task 5: Trade plan card popover

**Files:**
- Modify: `src/wave_alpha/web/templates/_trade_plan.html`
- Modify: `tests/web/test_routes_deepdive.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/web/test_routes_deepdive.py`:

```python
def test_trade_plan_fragment_includes_popover(client, sample_deepdive_data):
    ticker, as_of = sample_deepdive_data
    res = client.get(f"/deepdive/{ticker}/plan?as_of={as_of}")
    assert res.status_code == 200
    body = res.text
    assert "popover-trigger" in body
    assert "How we picked these levels" in body
    assert 'href="/about#trade-plan"' in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_routes_deepdive.py::test_trade_plan_fragment_includes_popover -v`
Expected: FAIL.

- [ ] **Step 3: Add the popover invocation**

Modify `src/wave_alpha/web/templates/_trade_plan.html`. The current import line reads:

```jinja
{% from "_macros.html" import pill %}
```

Update to:

```jinja
{% from "_macros.html" import pill, popover %}
```

Insert the popover **after** the existing `<p>` summary line (the one starting `Enter near …`) and **before** the `<div style="display: grid; grid-template-columns: 1fr 1fr 1fr; …">`. Block to insert:

```jinja
  {% call popover("How we picked these levels", anchor="trade-plan") %}
    <p class="popover-narrative">
      Entry is the close at the most recent confirming pivot. Stop hugs the prior wave's structural low. Target uses the active count's fib extension by default; switch to <code>fixed_R</code> in Settings to use a multiple of the entry-to-stop distance instead.
    </p>
    <table class="popover-data">
      <tr><th>direction</th><td>{{ sig.direction|upper }}</td></tr>
      <tr><th>entry</th><td class="num">{{ sig.entry_price }}</td></tr>
      <tr><th>stop</th><td class="num">{{ sig.stop_price }}</td></tr>
      <tr><th>target</th><td class="num">{{ sig.target_price }}</td></tr>
      <tr><th>pattern</th><td>{{ sig.count_pattern }} (W{{ sig.wave_label }})</td></tr>
      <tr class="total"><th>R:R</th><td class="num">{{ "%.2f"|format(sig.rr) }}</td></tr>
    </table>
  {% endcall %}
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/web/test_routes_deepdive.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/web/templates/_trade_plan.html tests/web/test_routes_deepdive.py
git commit -m "feat(web): add trade plan trust popover"
```

---

## Task 6: Per-candidate popovers in counts list

**Files:**
- Modify: `src/wave_alpha/web/templates/_counts_list.html`
- Modify: `tests/web/test_routes_deepdive.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/web/test_routes_deepdive.py`:

```python
def test_counts_list_renders_per_candidate_popovers(client, sample_deepdive_data):
    ticker, as_of = sample_deepdive_data
    res = client.get(f"/deepdive/{ticker}/counts?as_of={as_of}&llm=disabled")
    assert res.status_code == 200
    body = res.text
    # At least one candidate present in the fixture → at least one popover.
    assert body.count("popover-trigger") >= 1
    assert "Why C1" in body
    assert 'href="/about#enumeration"' in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_routes_deepdive.py::test_counts_list_renders_per_candidate_popovers -v`
Expected: FAIL.

- [ ] **Step 3: Add the popover invocation**

Modify `src/wave_alpha/web/templates/_counts_list.html`. The current import (line 1) reads:

```jinja
{% from "_macros.html" import pat, wlabel, pill %}
```

Update to:

```jinja
{% from "_macros.html" import pat, wlabel, pill, popover %}
```

Then add the popover **inside the score-pills row**, after the closing `{% endif %}` of the `llm_prob` pill block (i.e. at the end of the `<div style="display: flex; gap: 8px; align-items: center; …">`). Replace the existing block (currently lines 11–19):

```jinja
        <div style="display: flex; gap: 8px; align-items: center; margin-bottom: 4px; flex-wrap: wrap;">
          {{ pill(rc.candidate_id, "mag" if loop.first else "muted") }}
          {{ pat(rc.count.pattern) }}
          {{ pill("final " + "%.2f"|format(rc.final_score), "cyan") }}
          {{ pill("engine " + "%.2f"|format(rc.engine_score), "muted") }}
          {% if rc.llm_prob is not none %}
            {{ pill("llm " + "%.2f"|format(rc.llm_prob), "purple") }}
          {% endif %}
        </div>
```

with:

```jinja
        <div style="display: flex; gap: 8px; align-items: center; margin-bottom: 4px; flex-wrap: wrap;">
          {{ pill(rc.candidate_id, "mag" if loop.first else "muted") }}
          {{ pat(rc.count.pattern) }}
          {{ pill("final " + "%.2f"|format(rc.final_score), "cyan") }}
          {{ pill("engine " + "%.2f"|format(rc.engine_score), "muted") }}
          {% if rc.llm_prob is not none %}
            {{ pill("llm " + "%.2f"|format(rc.llm_prob), "purple") }}
          {% endif %}
          {% set rank_label = "won" if loop.first else ("was second" if loop.index == 2 else ("was third" if loop.index == 3 else "ranked " ~ loop.index)) %}
          {% call popover("Why " ~ rc.candidate_id ~ " " ~ rank_label, anchor="enumeration") %}
            <p class="popover-narrative">{{ rc.candidate_id }} is the {{ rc.count.pattern }} interpretation. Final score = α × engine + (1 &minus; α) × llm, then × coherence multiplier.</p>
            <table class="popover-data">
              <tr><th>engine score</th><td class="num">{{ "%.2f"|format(rc.engine_score) }}</td></tr>
              <tr><th>llm prob</th><td class="num">{% if rc.llm_prob is not none %}{{ "%.2f"|format(rc.llm_prob) }}{% else %}disabled{% endif %}</td></tr>
              <tr><th>soft violations</th><td>{% if rc.count.soft_violations %}{{ rc.count.soft_violations|length }}{% else %}none{% endif %}</td></tr>
              <tr class="total"><th>final score</th><td class="num">{{ "%.2f"|format(rc.final_score) }}</td></tr>
            </table>
          {% endcall %}
        </div>
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/web/test_routes_deepdive.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/web/templates/_counts_list.html tests/web/test_routes_deepdive.py
git commit -m "feat(web): add per-candidate trust popovers in counts list"
```

---

## Task 7: Hero KPI popovers (Confidence + R:R)

**Files:**
- Modify: `src/wave_alpha/web/templates/_hero.html`
- Modify: `tests/web/test_routes_deepdive.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/web/test_routes_deepdive.py`:

```python
def test_hero_renders_kpi_popovers(client, sample_deepdive_data):
    ticker, as_of = sample_deepdive_data
    res = client.get(f"/deepdive/{ticker}/hero?as_of={as_of}")
    assert res.status_code == 200
    body = res.text
    assert "What confidence means" in body
    assert "How R:R is computed" in body
    assert 'href="/about#alpha-blend"' in body  # confidence anchor
    assert 'href="/about#trade-plan"' in body  # R:R anchor
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_routes_deepdive.py::test_hero_renders_kpi_popovers -v`
Expected: FAIL.

- [ ] **Step 3: Add popover imports + invocations**

Modify `src/wave_alpha/web/templates/_hero.html`. The current import (line 1) reads:

```jinja
{% from "_macros.html" import pat, wlabel, pill %}
```

Update to:

```jinja
{% from "_macros.html" import pat, wlabel, pill, popover %}
```

Then update the Confidence and R:R `<div class="kpi …">` cells. Existing block (lines 14–18):

```jinja
    <div class="kpi mag"><div class="kpi-label">Confidence</div>
      <div class="kpi-value">{{ "%.2f"|format(sig.confidence) if sig else "—" }}</div></div>
    <div class="kpi bull"><div class="kpi-label">R:R</div>
      <div class="kpi-value glow-bull">{{ "%.2f"|format(sig.rr) if sig else "—" }}</div></div>
```

Replace with:

```jinja
    <div class="kpi mag">
      <div class="kpi-label">Confidence
        {% call popover("What confidence means", anchor="alpha-blend") %}
          <p class="popover-narrative">Confidence is C1's final score after the coherence multiplier. Above 0.7 is rare; the median across signals is around 0.45.</p>
        {% endcall %}
      </div>
      <div class="kpi-value">{{ "%.2f"|format(sig.confidence) if sig else "—" }}</div>
    </div>
    <div class="kpi bull">
      <div class="kpi-label">R:R
        {% call popover("How R:R is computed", anchor="trade-plan") %}
          <p class="popover-narrative">Reward&#58;risk = (target &minus; entry) &divide; (entry &minus; stop). Targets use <code>fib_extension</code> by default; switch to <code>fixed_R</code> in Settings to use a multiple of the entry-to-stop distance instead.</p>
        {% endcall %}
      </div>
      <div class="kpi-value glow-bull">{{ "%.2f"|format(sig.rr) if sig else "—" }}</div>
    </div>
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/web/test_routes_deepdive.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/web/templates/_hero.html tests/web/test_routes_deepdive.py
git commit -m "feat(web): add hero KPI trust popovers (confidence + R:R)"
```

---

## Task 8: Scan table column-header popovers

**Files:**
- Modify: `src/wave_alpha/web/templates/_scan_table.html`
- Modify: `tests/web/test_scan_routes.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/web/test_scan_routes.py`. (If a fixture for a populated scan already exists, reuse it; otherwise, mirror whatever existing test loads a sample report and call `/scan/<id>/table`.)

```python
def test_scan_table_renders_column_header_popovers(client, populated_scan_id):
    res = client.get(f"/scan/{populated_scan_id}/table")
    assert res.status_code == 200
    body = res.text
    # Four column-header popovers expected: Score, Coherence, R:R, Right-edge
    assert body.count("popover-trigger") >= 4
    for anchor in ("enumeration", "coherence", "trade-plan", "right-edge"):
        assert f'href="/about#{anchor}"' in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_scan_routes.py::test_scan_table_renders_column_header_popovers -v`
Expected: FAIL — no popover-trigger in the rendered fragment.

- [ ] **Step 3: Add the popover invocations**

Modify `src/wave_alpha/web/templates/_scan_table.html`. The current import (line 1):

```jinja
{% from "_macros.html" import pat, wlabel, pill, tri %}
```

Update to:

```jinja
{% from "_macros.html" import pat, wlabel, pill, tri, popover %}
```

Then update the four column headers in the `<thead>`. Existing `<thead>` block (lines 9–20):

```jinja
  <thead>
    <tr>
      <th>Ticker</th>
      <th>Pattern</th>
      <th>Bias</th>
      <th style="text-align:right;">Score</th>
      <th>Coherence</th>
      <th style="text-align:right;">R:R</th>
      <th>Entry / Stop / Target</th>
      <th>Right-edge</th>
    </tr>
  </thead>
```

Replace with:

```jinja
  <thead>
    <tr>
      <th>Ticker</th>
      <th>Pattern</th>
      <th>Bias</th>
      <th style="text-align:right;">Score
        {% call popover("How to read Score", anchor="enumeration") %}
          <p class="popover-narrative">Final score for the row's top candidate after the coherence multiplier and (optional) LLM blend. Higher = stronger fit. Pair with coherence and right-edge before acting.</p>
        {% endcall %}
      </th>
      <th>Coherence
        {% call popover("How to read Coherence", anchor="coherence") %}
          <p class="popover-narrative">Multi-timeframe agreement on the top count. ✓ = strong, ⚠ = weak, dot = missing timeframe data. The numeric value is the multiplier applied to the engine score.</p>
        {% endcall %}
      </th>
      <th style="text-align:right;">R:R
        {% call popover("How to read R:R", anchor="trade-plan") %}
          <p class="popover-narrative">Reward&#58;risk for the row's trade signal. Targets default to <code>fib_extension</code>; rows without a valid signal show "—".</p>
        {% endcall %}
      </th>
      <th>Entry / Stop / Target</th>
      <th>Right-edge
        {% call popover("How to read Right-edge", anchor="right-edge") %}
          <p class="popover-narrative">Probability the most recent pivot is real, per the trained right-edge model.</p>
        {% endcall %}
      </th>
    </tr>
  </thead>
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/web/test_scan_routes.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/web/templates/_scan_table.html tests/web/test_scan_routes.py
git commit -m "feat(web): add scan-table column-header trust popovers"
```

---

## Task 9: Home KPI strip popovers

**Files:**
- Modify: `src/wave_alpha/web/templates/home.html`
- Modify: `tests/web/test_home_route.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/web/test_home_route.py`:

```python
def test_home_kpi_strip_renders_popovers(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200
    body = res.text
    # Four KPI popovers: Signals, Top R:R, Watchlist hits, Stable picks
    assert body.count("popover-trigger") >= 4
    assert "What 'Signals' counts" in body
    assert "What 'Top R:R' shows" in body
    assert "What 'Watchlist hits' counts" in body
    assert "What 'Stable picks' means" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_home_route.py::test_home_kpi_strip_renders_popovers -v`
Expected: FAIL.

- [ ] **Step 3: Add popover import + invocations**

Modify `src/wave_alpha/web/templates/home.html`. The current import (line 2):

```jinja
{% from "_macros.html" import pat, wlabel, pill, tri, icon %}
```

Update to:

```jinja
{% from "_macros.html" import pat, wlabel, pill, tri, icon, popover %}
```

Update each of the four KPI cells inside `<div class="kpi-grid">` (currently lines 30–50). For each KPI, wrap the label with a popover. Replace the entire `<div class="kpi-grid">` block with:

```jinja
  <div class="kpi-grid">
    <div class="kpi">
      <div class="kpi-label">Signals
        {% call popover("What 'Signals' counts", anchor="pipeline") %}
          <p class="popover-narrative">Tickers in the latest scan with a non-null trade signal — entry, stop, target, and R:R all defined.</p>
        {% endcall %}
      </div>
      <div class="kpi-value glow-cyan">{{ view.kpis.signals if view.kpis.signals is not none else "—" }}</div>
      <div class="kpi-delta">{% if view.latest_scan_id %}from {{ view.latest_scan_id }}{% else %}no scans yet{% endif %}</div>
    </div>
    <div class="kpi mag">
      <div class="kpi-label">Top R:R
        {% call popover("What 'Top R:R' shows", anchor="trade-plan") %}
          <p class="popover-narrative">Maximum signal R:R across the latest scan. The sub-line names the ticker and bias.</p>
        {% endcall %}
      </div>
      <div class="kpi-value">{{ "%.2f"|format(view.kpis.top_rr.value|float) if view.kpis.top_rr else "—" }}</div>
      <div class="kpi-delta">{% if view.kpis.top_rr %}{{ view.kpis.top_rr.ticker }} · {{ view.kpis.top_rr.bias or "—" }}{% else %}—{% endif %}</div>
    </div>
    <div class="kpi bull">
      <div class="kpi-label">Watchlist hits
        {% call popover("What 'Watchlist hits' counts", anchor="pipeline") %}
          <p class="popover-narrative">Watchlist symbols that produced a signal in the latest scan, matched case-insensitively by ticker.</p>
        {% endcall %}
      </div>
      <div class="kpi-value glow-bull">{{ view.kpis.watchlist_hits if view.kpis.watchlist_hits is not none else "—" }}</div>
      <div class="kpi-delta">{% if view.kpis.watchlist_hits is not none %}of {{ view.watchlist_total }} watched{% else %}no watchlist{% endif %}</div>
    </div>
    <div class="kpi yellow">
      <div class="kpi-label">Stable picks
        {% call popover("What 'Stable picks' means", anchor="coherence") %}
          <p class="popover-narrative">Watchlist symbols whose history streak ≥ 3 — same direction in the last 3 scans.</p>
        {% endcall %}
      </div>
      <div class="kpi-value glow-yellow">{{ view.kpis.stable_picks if view.kpis.stable_picks is not none else "—" }}</div>
      <div class="kpi-delta">streak ≥ 3 days</div>
    </div>
  </div>
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/web/test_home_route.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/web/templates/home.html tests/web/test_home_route.py
git commit -m "feat(web): add home KPI trust popovers"
```

---

## Task 10: Settings α slider popover

**Files:**
- Modify: `src/wave_alpha/web/templates/settings.html`
- Modify: `tests/web/test_routes_settings.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/web/test_routes_settings.py`:

```python
def test_settings_alpha_slider_includes_popover(client):
    res = client.get("/settings")
    assert res.status_code == 200
    body = res.text
    assert "Why our default" in body and "0.65" in body
    assert "popover-trigger" in body
    assert 'href="/about#alpha-blend"' in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/web/test_routes_settings.py::test_settings_alpha_slider_includes_popover -v`
Expected: FAIL.

- [ ] **Step 3: Add popover import + invocation**

Modify `src/wave_alpha/web/templates/settings.html`. The current import (line 2):

```jinja
{% from "_macros.html" import pill, gloss %}
```

Update to:

```jinja
{% from "_macros.html" import pill, gloss, popover %}
```

Locate the existing `gloss` line inside the alpha field (currently line 49) and add the popover beside it. Replace:

```jinja
          <label class="field-label" for="alpha">
            {{ gloss("Engine ↔ LLM blend (α)", "α weights the deterministic engine vs the LLM rerank. 1.0 = engine only; 0.0 = LLM only.") }}
          </label>
```

With:

```jinja
          <label class="field-label" for="alpha">
            {{ gloss("Engine ↔ LLM blend (α)", "α weights the deterministic engine vs the LLM rerank. 1.0 = engine only; 0.0 = LLM only.") }}
            {% call popover("Why our default α = 0.65", anchor="alpha-blend") %}
              <p class="popover-narrative">α blends the deterministic engine score with the LLM rerank probability: <code>final = α &times; engine + (1 &minus; α) &times; llm</code>. 0.65 is our internal default &mdash; tune toward 1.0 to trust the engine more, toward 0.0 to trust the LLM more. We do not yet have a formal Sharpe sweep; see <a href="/about#alpha-blend">/about#alpha-blend</a> for the methodology and what to watch for.</p>
            {% endcall %}
          </label>
```

- [ ] **Step 4: Run the test**

Run: `uv run pytest tests/web/test_routes_settings.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/web/templates/settings.html tests/web/test_routes_settings.py
git commit -m "feat(web): add α slider trust popover with /about deep link"
```

---

## Task 11: `/about` page content (all 11 sections + diagrams)

This task fills the section bodies created in Task 2. Diagrams are inline SVG, drawn schematically — they do not pull live data.

**Files:**
- Modify: `src/wave_alpha/web/templates/about.html`
- Modify: `tests/web/test_about_route.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_about_route.py`:

```python
def test_about_sections_have_substantive_content(client: TestClient) -> None:
    """Every section should have prose beyond the placeholder stub."""
    res = client.get("/about")
    body = res.text
    # The placeholder text from Task 2 must not survive past Task 11.
    assert "Filled in Task 11" not in body
    # Each section title appears as an <h2>.
    for title in (
        "Pipeline overview",
        "Pivots &amp; degrees",
        "Wave templates",
        "Enumeration &amp; scoring",
        "Multi-timeframe coherence",
        "Right-edge confirmation",
        "LLM rerank &amp; α blend",
        "Trade plan derivation",
        "Lookahead-safety guarantee",
        "Privacy contract",
        "What this is not",
    ):
        assert f"<h2>{title}</h2>" in body, f"missing section heading: {title}"


def test_about_pipeline_section_includes_diagram_and_citation(client: TestClient) -> None:
    res = client.get("/about")
    body = res.text
    pipeline = body.split('id="pipeline"', 1)[1].split('id="pivots"', 1)[0]
    assert "<svg" in pipeline and "diagram" in pipeline
    assert "PointInTimeView" in pipeline or "pipeline.py" in pipeline


def test_about_what_this_is_not_section_lists_caveats(client: TestClient) -> None:
    res = client.get("/about")
    body = res.text
    section = body.split('id="what-this-is-not"', 1)[1]
    assert "not financial advice" in section.lower()
    assert "miss patterns" in section.lower() or "can and does miss" in section.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/web/test_about_route.py -v`
Expected: 3 new tests FAIL (placeholder still present, no diagrams, etc.).

- [ ] **Step 3: Replace section bodies with real content**

Edit `src/wave_alpha/web/templates/about.html`. Replace the eleven `<section …>…</section>` blocks created in Task 2 with the full versions below. Keep the surrounding `<aside>` TOC and `<article>` wrapper unchanged.

```jinja
      <section id="pipeline" class="about-section">
        <h2>Pipeline overview</h2>
        <p>
          For each request, we fetch OHLCV bars at three intervals (weekly, daily, 4-hour),
          run the same enumeration on each, score the resulting wave counts for multi-timeframe
          agreement, optionally rerank with an LLM, and derive a trade signal from the top count.
          Every step is deterministic given the same inputs and as-of date.
        </p>
        <svg class="diagram" viewBox="0 0 480 110" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Pipeline flow from bars to signal">
          <defs>
            <marker id="arr" viewBox="0 0 8 8" refX="6" refY="4" markerWidth="6" markerHeight="6" orient="auto">
              <path d="M0 0 L8 4 L0 8 z" fill="#7C8BA3"/>
            </marker>
          </defs>
          <g font-family="-apple-system, system-ui, sans-serif" font-size="11" fill="#C7D2DE">
            <rect x="6"   y="40" width="78" height="32" rx="6" fill="#0E121C" stroke="#22D3EE" stroke-opacity="0.4"/>
            <text x="45"  y="60" text-anchor="middle">Bars</text>
            <rect x="98"  y="40" width="98" height="32" rx="6" fill="#0E121C" stroke="#22D3EE" stroke-opacity="0.4"/>
            <text x="147" y="60" text-anchor="middle">PointInTimeView</text>
            <rect x="210" y="40" width="78" height="32" rx="6" fill="#0E121C" stroke="#F472B6" stroke-opacity="0.5"/>
            <text x="249" y="60" text-anchor="middle">Pivots</text>
            <rect x="302" y="40" width="86" height="32" rx="6" fill="#0E121C" stroke="#F472B6" stroke-opacity="0.5"/>
            <text x="345" y="60" text-anchor="middle">Enumerate</text>
            <rect x="402" y="40" width="72" height="32" rx="6" fill="#0E121C" stroke="#A3E635" stroke-opacity="0.5"/>
            <text x="438" y="60" text-anchor="middle">Signal</text>
            <line x1="84"  y1="56" x2="98"  y2="56" stroke="#7C8BA3" marker-end="url(#arr)"/>
            <line x1="196" y1="56" x2="210" y2="56" stroke="#7C8BA3" marker-end="url(#arr)"/>
            <line x1="288" y1="56" x2="302" y2="56" stroke="#7C8BA3" marker-end="url(#arr)"/>
            <line x1="388" y1="56" x2="402" y2="56" stroke="#7C8BA3" marker-end="url(#arr)"/>
          </g>
        </svg>
        <div class="src-cite">Source: <code>src/wave_alpha/pipeline.py:run_analysis</code> · Spec: §5 of <code>2026-05-03-wave-alpha-design.md</code></div>
      </section>

      <section id="pivots" class="about-section">
        <h2>Pivots &amp; degrees</h2>
        <p>
          A zigzag detector finds turning points in price scaled by Average True Range (ATR),
          so the same logic works on a $5 stock and a $5,000 one. Pivots are stratified into
          degrees: degree-0 captures every micro-turn, higher degrees keep only the more
          significant ones. The engine counts at degree 2 by default — coarse enough to
          ignore noise, fine enough to see most actionable patterns.
        </p>
        <svg class="diagram" viewBox="0 0 480 110" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Pivots at three degrees overlaid">
          <g fill="none" stroke-width="1.6" font-family="-apple-system, system-ui, sans-serif" font-size="10">
            <polyline points="6,80 40,40 70,70 110,30 150,55 200,20 240,60 290,25 340,65 390,15 440,50 472,30" stroke="#475569"/>
            <g fill="#22D3EE"><circle cx="40" cy="40" r="3"/><circle cx="110" cy="30" r="3"/><circle cx="200" cy="20" r="3"/><circle cx="290" cy="25" r="3"/><circle cx="390" cy="15" r="3"/></g>
            <g fill="#F472B6"><circle cx="200" cy="20" r="4"/><circle cx="390" cy="15" r="4"/></g>
            <g fill="#FB923C"><circle cx="390" cy="15" r="5"/></g>
            <text x="6"  y="100" fill="#22D3EE">deg 0</text>
            <text x="64" y="100" fill="#F472B6">deg 1</text>
            <text x="124" y="100" fill="#FB923C">deg 2 (default)</text>
          </g>
        </svg>
        <div class="src-cite">Source: <code>src/wave_alpha/pivots/</code> · Spec: §6</div>
      </section>

      <section id="templates" class="about-section">
        <h2>Wave templates</h2>
        <p>
          Patterns are defined in YAML, not code. Each template (impulse, zigzag, flat, expanded
          flat, contracting triangle, ending diagonal) lists its waves and constraints, marked
          either <code>hard</code> (rejection) or <code>soft</code> (score penalty). Adding a
          new pattern means dropping a YAML file in
          <code>src/wave_alpha/elliott/templates_data/</code> &mdash; no code change required
          unless a new constraint operator is involved.
        </p>
        <div class="src-cite">Source: <code>src/wave_alpha/elliott/templates_data/</code> · Spec: §7</div>
      </section>

      <section id="enumeration" class="about-section">
        <h2>Enumeration &amp; scoring</h2>
        <p>
          For each timeframe, the engine slides every template across the recent pivot stream and
          records every legal fit. Each candidate is scored on three components: <em>template
          fit</em> (how cleanly the pivots match the wave skeleton), <em>fib quality</em> (how
          close wave ratios sit to canonical Fibonacci targets), and <em>recency</em> (how
          fresh the most recent pivot is). Multiple windows of the same template family
          deduplicate down to the highest-scoring instance before the global top-K cut, so one
          template can't crowd out alternates.
        </p>
        <div class="src-cite">Source: <code>src/wave_alpha/elliott/enumerator.py</code> · Spec: §8</div>
      </section>

      <section id="coherence" class="about-section">
        <h2>Multi-timeframe coherence</h2>
        <p>
          Coherence asks: do the weekly, daily, and 4-hour timeframes tell the same story? The
          engine compares the top counts pairwise (W&harr;D, W&harr;4h, D&harr;4h) and produces
          a multiplier that scales the engine score. Strong agreement amplifies confidence; weak
          agreement (or missing-timeframe data) leaves the multiplier at 1.0 or below. High
          coherence means the count is consistent across horizons; it does <em>not</em> mean the
          count is correct.
        </p>
        <svg class="diagram" viewBox="0 0 320 130" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Three-timeframe Venn-style overlap">
          <g font-family="-apple-system, system-ui, sans-serif" font-size="11">
            <circle cx="120" cy="60" r="48" fill="#22D3EE" fill-opacity="0.18" stroke="#22D3EE" stroke-opacity="0.6"/>
            <circle cx="170" cy="60" r="48" fill="#F472B6" fill-opacity="0.18" stroke="#F472B6" stroke-opacity="0.6"/>
            <circle cx="145" cy="100" r="48" fill="#FB923C" fill-opacity="0.18" stroke="#FB923C" stroke-opacity="0.6"/>
            <text x="80"  y="40"  fill="#22D3EE" text-anchor="middle">Weekly</text>
            <text x="210" y="40"  fill="#F472B6" text-anchor="middle">Daily</text>
            <text x="145" y="124" fill="#FB923C" text-anchor="middle">4-hour</text>
          </g>
        </svg>
        <div class="src-cite">Source: <code>src/wave_alpha/coherence/score.py</code> · Spec: §9</div>
      </section>

      <section id="right-edge" class="about-section">
        <h2>Right-edge confirmation</h2>
        <p>
          The most recent pivot is, by definition, the least confirmed. The right-edge model
          estimates the probability that this tentative pivot will hold &mdash; that future bars
          won't push past it and invalidate the count. Three implementations exist: a hand-coded
          heuristic, a heuristic plus per-degree Platt calibration, and a full logistic regression
          with a feature-order-stable JSON artifact. The active model is selected per
          configuration; <code>auto</code> prefers logistic, falls back to calibrated heuristic,
          then heuristic.
        </p>
        <div class="src-cite">Source: <code>src/wave_alpha/right_edge/</code> · Spec: §10</div>
      </section>

      <section id="alpha-blend" class="about-section">
        <h2>LLM rerank &amp; α blend</h2>
        <p>
          The LLM is strictly optional. When enabled, it reranks the engine's top candidates
          &mdash; it never invents counts. The final score blends both signals as
          <code>final = α &times; engine + (1 &minus; α) &times; llm</code>, then multiplies by
          the coherence multiplier. The default α = 0.65 leans on the engine; tune toward 1.0
          to trust the engine fully, toward 0.0 to trust the LLM fully. There is no formal
          Sharpe sweep yet, so the default is internal preference rather than a fitted optimum.
        </p>
        <p>
          Three modes: <code>disabled</code> (no API key needed, fully deterministic;
          mandatory for backtest comparisons), <code>cached</code> (replays prior responses
          from the local SQLite cache, raises on miss), <code>live</code> (real API calls,
          writes to cache).
        </p>
        <div class="src-cite">Source: <code>src/wave_alpha/llm/</code> · Spec: §11</div>
      </section>

      <section id="trade-plan" class="about-section">
        <h2>Trade plan derivation</h2>
        <p>
          For the top candidate that meets a minimum-confidence threshold, the engine derives a
          trade plan: <em>entry</em> at the close of the most recent confirming pivot,
          <em>stop</em> at the prior wave's structural extreme (with an ATR cushion), and
          <em>target</em> from a Fibonacci extension of the active count by default. R:R is
          <code>(target &minus; entry) &divide; (entry &minus; stop)</code>. Switch the target
          source to <code>fixed_R</code> in Settings to use a fixed multiple of the
          entry-to-stop distance instead.
        </p>
        <div class="src-cite">Source: <code>src/wave_alpha/trades/derive.py</code> · Spec: §12</div>
      </section>

      <section id="lookahead" class="about-section">
        <h2>Lookahead-safety guarantee</h2>
        <p>
          Every analysis runs through <code>PointInTimeView</code>, which hard-asserts that no
          bar with a timestamp after the as-of date is reachable. The backtest harness, the live
          pipeline, and the right-edge feature extractor all flow through it. A synthetic-leak
          unit test (<code>tests/backtest/test_lookahead_guard.py</code>) plants a deliberate
          violation to verify the assertion fires &mdash; if you ever see it pass with a leaked
          bar, the safety net has a hole.
        </p>
        <div class="src-cite">Source: <code>src/wave_alpha/data/point_in_time.py:PointInTimeView</code> · Spec: §13</div>
      </section>

      <section id="privacy" class="about-section">
        <h2>Privacy contract</h2>
        <p>
          When the LLM is enabled, the only payload sent upstream is: ticker symbol, pivot
          timestamps and prices, candidate count structures, and coherence metrics. No raw OHLCV
          bars, no API keys (the key is set on the client, never serialised in the payload), no
          account information. This contract lives in <code>llm/prompts.py</code> and
          <code>llm/candidates.py:serialize_candidate_set</code>. Tampering with either should
          require a paired update to this section.
        </p>
        <div class="src-cite">Source: <code>src/wave_alpha/llm/prompts.py</code>, <code>llm/candidates.py</code> · Spec: §14</div>
      </section>

      <section id="what-this-is-not" class="about-section">
        <h2>What this is not</h2>
        <ul>
          <li>This is <strong>not financial advice</strong>. The engine produces deterministic candidate counts and signals; whether to act on them is your judgment.</li>
          <li>This is <strong>not a real-time backtest of itself</strong>. Coherence and right-edge are computed per request, not validated per request.</li>
          <li>The engine <strong>can and does miss patterns</strong>. The template set in <code>templates_data/</code> is finite; price action that doesn't fit a template gets no candidates.</li>
          <li><strong>High coherence does not mean the count is correct.</strong> It means the engine reached the same count across timeframes &mdash; more likely to be correct, not certain.</li>
          <li>LLM rerank is heuristic. With α = 0.65 the engine still dominates; setting α = 0.0 makes the LLM responsible for most of the ranking, which is not validated.</li>
        </ul>
      </section>
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/web/test_about_route.py -v`
Expected: all 8 tests PASS (5 from Task 2 + 3 new).

- [ ] **Step 5: Visual smoke test**

Boot the UI and walk the popovers + /about manually:

```bash
uv run wave-alpha ui &
sleep 2
open http://127.0.0.1:8765/about     # macOS
```

Verify in the browser:
- All 11 anchor sections render with content (no "Filled in Task 11" stubs).
- TOC links scroll to the right section (and stop above the sticky nav, not under it — `scroll-margin-top: 88px` was added in Task 2).
- Diagrams render (pipeline flow, pivot degrees, coherence Venn).
- On the deep-dive page, click each `?` icon: only one popover open at a time, Esc closes, click-outside closes, × closes, "Learn more →" jumps to the right /about section.

Stop the server: `pkill -f "wave-alpha ui"`.

- [ ] **Step 6: Run the full web test suite**

Run: `uv run pytest tests/web/ -v`
Expected: all green.

- [ ] **Step 7: Run lint and format**

Run: `uv run ruff check src/wave_alpha/web/ tests/web/ && uv run ruff format src/wave_alpha/web/ tests/web/`
Expected: no errors; format may apply minor changes — re-stage if so.

- [ ] **Step 8: Commit**

```bash
git add src/wave_alpha/web/templates/about.html tests/web/test_about_route.py
git commit -m "feat(web): fill /about page with full pipeline narrative + diagrams"
```

---

## Task 12: CHANGELOG entry

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Read current CHANGELOG.md to find the active section**

Run: `head -30 CHANGELOG.md` to locate the unreleased / 0.6.x section the redesign was added to.

- [ ] **Step 2: Add an entry under the same section as the 0.6.0 redesign line**

Modify `CHANGELOG.md` — locate the line "Web UI redesign" (added in commit `29fda6d`) and insert immediately after:

```markdown
- Trust-building popovers added across the web UI (deep-dive metric cards, candidate rows, hero KPIs, scan-table headers, home KPIs, settings α slider) — each surfaces "how this number was computed" with a `Learn more →` link to the new `/about` page that narrates the full engine pipeline. See `docs/superpowers/specs/2026-05-10-trust-popups-design.md`.
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): note trust popovers + /about page"
```

---

## Self-Review

Spec coverage:
- §2 in-scope items: popover macro + JS (Task 1) · 17 trigger sites (Tasks 3–10) · `/about` page (Tasks 2 + 11) · About nav link (Task 2) · inline SVG diagrams (Task 11). All covered.
- §2 out-of-scope: no engine `score_breakdown` field, no Sharpe data block, no per-metric backtest panels, no per-popover caveats — none of those appear in this plan.
- §4 component design: trigger button, dialog card, headline, close (×), Esc, click-outside — all present in Task 1's macro and CSS. Manual focus-trap implemented in `popover.js` (Task 1, step 6).
- §4 trigger sites table (12 archetypes): coherence card (Task 3), right-edge card (Task 4), trade plan card (Task 5), wave counts (Task 6), hero KPIs ×2 (Task 7), scan column headers ×4 (Task 8), home KPIs ×4 (Task 9), settings α (Task 10) → all 12 archetypes covered.
- §5 per-popover content: each task includes the literal narrative + data block from the spec.
- §6 `/about`: route (Task 2), template scaffold with 11 anchors (Task 2), full content (Task 11), nav link (Task 2), TOC + diagrams + citations (Tasks 2 CSS + Task 11 content).
- §7 implementation surface: every file listed in the spec appears in this plan's File Structure section.
- §8 testing: structural macro tests (Task 1), about route + anchors + content + caveats (Tasks 2 + 11), per-page popover assertions (Tasks 3–10), visual smoke test in Task 11 step 5.

Placeholder scan: no "TBD", "TODO", "implement later", or generic "add error handling" instructions. Test fixture handling in Tasks 3, 5–8 references existing fixtures by name (`sample_deepdive_data`, `populated_scan_id`); the Task 3 step 1 explicitly instructs the worker on the fallback path if a fixture doesn't exist. This is a deliberate hedge because I cannot verify the exact fixture names in the existing test files without reading them — but the plan tells the worker exactly what to do in either case.

Type / signature consistency: macro signature is `popover(headline, anchor=none)` everywhere; `popover()` Alpine factory is referenced by `x-data="popover()"` in the macro and defined in `popover.js`; the global event name `popover:open` is internal to `popover.js` only. Anchor IDs in the popover invocations (`coherence`, `right-edge`, `trade-plan`, `enumeration`, `alpha-blend`, `pipeline`) all match anchors created in the about template.

---

**Plan complete.** Auto mode is active, so I'll proceed to execution unless you say otherwise.
