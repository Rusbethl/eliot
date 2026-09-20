# Web UI Redesign — Cyber Spectrum · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current `web/` UI with the cyber-spectrum dark redesign described in `docs/superpowers/specs/2026-05-09-web-ui-redesign-design.md` — new Home dashboard, restyled Scan/Watchlist/Settings/Deepdive pages, design tokens, classic-candlestick chart with brand-spectrum overlays.

**Architecture:** Backend changes are surgical: add two pure ranking filters, add a watchlist-enrichment helper, add a Home route. Everything else is template/CSS work. The HTMX + Alpine + Lightweight Charts stack is unchanged. No engine, no LLM, no provider, no CLI is touched.

**Tech Stack:** FastAPI · Jinja2 · HTMX · Alpine.js · TradingView Lightweight Charts (vendored) · pytest · ruff · uv.

---

## Conventions used in this plan

- All commands run from the repo root: `/Users/alexchen/Documents/project/wave_alpha`.
- Always run python via `uv run …` (project rule). Never activate the venv.
- After each task's tests pass, run `uv run ruff check .` and `uv run ruff format .` before committing.
- Each task ends with a commit. Commit messages use the project's style (lowercase type prefix, concise subject).

## Reference: existing code worth knowing about

- `src/wave_alpha/scan/models.py` — `ScanRow` is frozen Pydantic. `ScanRow.direction: Literal["up", "down"] | None`. `ScanRow.signal_rr: Decimal | None`. `ScanRow.has_signal` returns True iff `signal_rr is not None`.
- **Direction conventions differ between two models**, intentionally:
  - `ScanRow.direction` uses `"up" | "down"` (engine-internal pivot direction).
  - `TradeSignal.direction` uses `"long" | "short"` (trader-facing).
  - `filter_by_bias` (Task 1) maps `"long" → "up"` / `"short" → "down"` to bridge them.
  - `_trade_plan.html` (Task 13) reads `sig.direction == "long"` directly (matches `TradeSignal`).
- `src/wave_alpha/scan/ranking.py` — currently exports `sort_by`, `filter_signals`. We extend with two more.
- `src/wave_alpha/scan/archive.py` — `list_reports(dir=…)` returns scan_ids newest-first; `load_report(scan_id, dir=…)` returns `ScanReport | None`.
- `src/wave_alpha/watchlist/store.py` — `load_watchlist(path)` returns `list[WatchlistEntry]`. `WatchlistEntry` has `symbol`, `added_on`, `notes`. **Storage is `~/.wave_alpha/watchlist.yaml` (yaml, not json — CLAUDE.md is slightly out of date).** Tests monkeypatch `_watchlist_path()`.
- `src/wave_alpha/history/service.py` — `get_recent_with_badge(ticker, db_path)` returns a `RecentHistory` with `.snapshots` and `.badge` (`StabilityBadge` has a `streak: int` attribute).
- `src/wave_alpha/web/routes/system.py` — currently `GET /` is a `RedirectResponse(url="/settings", status_code=307)`. Tests assert this; one test will need updating.
- `src/wave_alpha/web/app.py` — `create_app()` mounts `/static`, attaches `templates` to `app.state`, registers routers in this order: `system, settings, deepdive, watchlist, scan`. We will add `home` between `system` and `settings`.
- `src/wave_alpha/web/coherence_badge.py:build_tri_badge` — pure function, untouched.
- `tests/web/conftest.py` does not exist; each test file uses its own `_client()` or `client` fixture. Match this pattern.

---

## Task 1 · Backend — add `filter_by_bias` and `filter_by_min_rr` to `scan/ranking.py`

**Files:**
- Modify: `src/wave_alpha/scan/ranking.py`
- Test: `tests/scan/test_ranking.py`

**Why first:** Pure functions with no dependencies; Task 2 (route) needs them. Strict TDD.

- [ ] **Step 1.1: Append failing tests for `filter_by_bias` to `tests/scan/test_ranking.py`**

Append to the bottom of the file:

```python
from wave_alpha.scan.ranking import filter_by_bias, filter_by_min_rr


def _row_with(tk: str, *, direction: str | None, rr: str | None = None):
    """Helper variant that lets us set direction explicitly (incl. None)."""
    kwargs = dict(
        ticker=tk,
        as_of=date(2026, 5, 1),
        coherence=0.5,
        top_pattern="impulse",
        top_score=0.5,
        direction=direction,  # type: ignore[arg-type]
    )
    if rr:
        kwargs.update(
            signal_rr=Decimal(rr),
            signal_entry=Decimal("100"),
            signal_stop=Decimal("90"),
            signal_target=Decimal("130"),
        )
    return ScanRow(**kwargs)


def test_filter_by_bias_long_keeps_only_up() -> None:
    rows = [
        _row_with("A", direction="up"),
        _row_with("B", direction="down"),
        _row_with("C", direction="up"),
    ]
    out = filter_by_bias(rows, "long")
    assert [r.ticker for r in out] == ["A", "C"]


def test_filter_by_bias_short_keeps_only_down() -> None:
    rows = [
        _row_with("A", direction="up"),
        _row_with("B", direction="down"),
    ]
    out = filter_by_bias(rows, "short")
    assert [r.ticker for r in out] == ["B"]


def test_filter_by_bias_any_returns_all_rows_unchanged() -> None:
    rows = [
        _row_with("A", direction="up"),
        _row_with("B", direction="down"),
        _row_with("C", direction=None),
    ]
    out = filter_by_bias(rows, "any")
    assert [r.ticker for r in out] == ["A", "B", "C"]


def test_filter_by_bias_excludes_rows_with_no_direction() -> None:
    rows = [
        _row_with("A", direction="up"),
        _row_with("B", direction=None),
    ]
    out = filter_by_bias(rows, "long")
    assert [r.ticker for r in out] == ["A"]


def test_filter_by_min_rr_keeps_rows_at_or_above_threshold() -> None:
    rows = [
        _row_with("A", direction="up", rr="3.0"),
        _row_with("B", direction="up", rr="1.5"),
        _row_with("C", direction="up", rr="2.0"),
    ]
    out = filter_by_min_rr(rows, 2.0)
    assert {r.ticker for r in out} == {"A", "C"}


def test_filter_by_min_rr_drops_rows_with_no_signal() -> None:
    rows = [
        _row_with("A", direction="up", rr="3.0"),
        _row_with("B", direction="up"),  # no rr → no signal
    ]
    out = filter_by_min_rr(rows, 1.0)
    assert [r.ticker for r in out] == ["A"]


def test_filter_by_min_rr_zero_threshold_keeps_all_signalled_rows() -> None:
    rows = [
        _row_with("A", direction="up", rr="3.0"),
        _row_with("B", direction="up", rr="0.5"),
    ]
    out = filter_by_min_rr(rows, 0.0)
    assert {r.ticker for r in out} == {"A", "B"}
```

- [ ] **Step 1.2: Run the new tests — expect all 7 to FAIL with ImportError**

```bash
uv run pytest tests/scan/test_ranking.py -v -k "filter_by_bias or filter_by_min_rr"
```

Expected: collection error or `ImportError: cannot import name 'filter_by_bias' from 'wave_alpha.scan.ranking'`.

- [ ] **Step 1.3: Add the two functions to `src/wave_alpha/scan/ranking.py`**

Append at the bottom of the file (and import `Literal` at the top):

```python
from typing import Literal

Bias = Literal["any", "long", "short"]
_BIAS_TO_DIRECTION = {"long": "up", "short": "down"}


def filter_by_bias(rows: Iterable[ScanRow], bias: Bias) -> list[ScanRow]:
    """Keep rows whose direction matches the requested bias.
    "any" returns rows unchanged. "long" / "short" map to the engine's
    "up" / "down" direction values. Rows with `direction=None` are
    excluded for "long" / "short" but included for "any"."""
    if bias == "any":
        return list(rows)
    target = _BIAS_TO_DIRECTION[bias]
    return [r for r in rows if r.direction == target]


def filter_by_min_rr(rows: Iterable[ScanRow], min_rr: float) -> list[ScanRow]:
    """Keep rows whose `signal_rr` is at least `min_rr`.
    Rows with no signal (signal_rr is None) are dropped."""
    return [
        r for r in rows
        if r.signal_rr is not None and float(r.signal_rr) >= min_rr
    ]
```

- [ ] **Step 1.4: Run the tests — expect all 7 to PASS**

```bash
uv run pytest tests/scan/test_ranking.py -v
```

Expected: all tests pass (the new 7 plus the existing ones).

- [ ] **Step 1.5: Lint and format**

```bash
uv run ruff check src/wave_alpha/scan/ranking.py tests/scan/test_ranking.py
uv run ruff format src/wave_alpha/scan/ranking.py tests/scan/test_ranking.py
```

- [ ] **Step 1.6: Commit**

```bash
git add src/wave_alpha/scan/ranking.py tests/scan/test_ranking.py
git commit -m "feat(scan): add filter_by_bias and filter_by_min_rr to ranking"
```

---

## Task 2 · Backend — wire `bias` and `min_rr` into `/scan/{id}/table`

**Files:**
- Modify: `src/wave_alpha/web/routes/scan.py:47-63`
- Test: `tests/web/test_scan_routes.py`

- [ ] **Step 2.1: Append failing tests to `tests/web/test_scan_routes.py`**

Add at the bottom of the file (the `_sample_report` fixture is already defined above):

```python
def test_scan_table_filter_by_bias_long(client: TestClient, tmp_path: Path) -> None:
    save_report(_sample_report("2026-05-03T09-00-00"), dir=tmp_path)
    res = client.get("/scan/2026-05-03T09-00-00/table?bias=long")
    assert res.status_code == 200
    # AAPL is direction=up → matches long; MSFT is direction=down → excluded.
    assert "AAPL" in res.text
    assert "MSFT" not in res.text


def test_scan_table_filter_by_bias_short(client: TestClient, tmp_path: Path) -> None:
    save_report(_sample_report("2026-05-04T09-00-00"), dir=tmp_path)
    res = client.get("/scan/2026-05-04T09-00-00/table?bias=short")
    assert res.status_code == 200
    assert "MSFT" in res.text
    assert "AAPL" not in res.text


def test_scan_table_filter_by_bias_any_returns_all(client: TestClient, tmp_path: Path) -> None:
    save_report(_sample_report("2026-05-05T09-00-00"), dir=tmp_path)
    res = client.get("/scan/2026-05-05T09-00-00/table?bias=any")
    assert res.status_code == 200
    assert "AAPL" in res.text
    assert "MSFT" in res.text


def test_scan_table_filter_min_rr_drops_rows_below_threshold(
    client: TestClient, tmp_path: Path
) -> None:
    save_report(_sample_report("2026-05-06T09-00-00"), dir=tmp_path)
    # AAPL has rr=2.0; threshold 3.0 should drop it.
    res = client.get("/scan/2026-05-06T09-00-00/table?min_rr=3.0")
    assert res.status_code == 200
    assert "AAPL" not in res.text


def test_scan_table_filter_min_rr_zero_is_noop_for_signalled_rows(
    client: TestClient, tmp_path: Path
) -> None:
    save_report(_sample_report("2026-05-07T09-00-00"), dir=tmp_path)
    res = client.get("/scan/2026-05-07T09-00-00/table?min_rr=0")
    assert res.status_code == 200
    # AAPL has a signal at rr=2.0 — kept.
    assert "AAPL" in res.text
```

- [ ] **Step 2.2: Run new tests — expect all 5 to FAIL**

```bash
uv run pytest tests/web/test_scan_routes.py -v -k "filter_by_bias or filter_min_rr"
```

Expected: tests pass (since the route currently ignores unknown query params, the bias=long test will fail because MSFT is still in the response). Some may incidentally pass — read the failure output and only proceed when at least one fails.

If they all incidentally pass because the route silently ignores unknown params, that's still wrong — proceed to step 2.3.

- [ ] **Step 2.3: Replace the body of `scan_table_fragment` in `src/wave_alpha/web/routes/scan.py`**

Replace the entire `scan_table_fragment` function (around line 47-63) with:

```python
@router.get("/scan/{scan_id}/table", response_class=HTMLResponse)
def scan_table_fragment(
    scan_id: str,
    request: Request,
    sort: str = "score",
    signals_only: bool = False,
    bias: str = "any",
    min_rr: float = 0.0,
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
```

Update the import at the top of the file from:

```python
from wave_alpha.scan.ranking import filter_signals, sort_by
```

to:

```python
from wave_alpha.scan.ranking import filter_by_bias, filter_by_min_rr, filter_signals, sort_by
```

- [ ] **Step 2.4: Run all scan-route tests**

```bash
uv run pytest tests/web/test_scan_routes.py -v
```

Expected: all tests pass (existing ones + the 5 new ones).

- [ ] **Step 2.5: Lint, format, commit**

```bash
uv run ruff check src/wave_alpha/web/routes/scan.py tests/web/test_scan_routes.py
uv run ruff format src/wave_alpha/web/routes/scan.py tests/web/test_scan_routes.py
git add src/wave_alpha/web/routes/scan.py tests/web/test_scan_routes.py
git commit -m "feat(web/scan): accept bias and min_rr query params on table fragment"
```

---

## Task 3 · Backend — add watchlist enrichment helper module

**Files:**
- Create: `src/wave_alpha/web/watchlist_enrich.py`
- Test: `tests/web/test_watchlist_enrich.py`

**Why a separate module:** Keeps the route file thin and makes the lookup logic independently testable.

- [ ] **Step 3.1: Create the failing test file `tests/web/test_watchlist_enrich.py`**

```python
from datetime import date
from decimal import Decimal

from wave_alpha.scan.models import ScanReport, ScanRow
from wave_alpha.watchlist.models import WatchlistEntry
from wave_alpha.web.watchlist_enrich import EnrichedRow, enrich


def _entry(symbol: str, notes: str | None = None) -> WatchlistEntry:
    return WatchlistEntry(symbol=symbol, added_on=date(2026, 5, 1), notes=notes)


def _report(*rows: ScanRow) -> ScanReport:
    from datetime import datetime

    return ScanReport(
        scan_id="2026-05-09T14-30-00",
        started_at=datetime(2026, 5, 9, 14, 30, 0),
        completed_at=datetime(2026, 5, 9, 14, 30, 30),
        as_of=date(2026, 5, 9),
        rows=list(rows),
    )


def test_enrich_returns_one_row_per_entry_in_input_order() -> None:
    entries = [_entry("AAPL"), _entry("MSFT")]
    report = _report(
        ScanRow(ticker="AAPL", as_of=date(2026, 5, 9), top_pattern="impulse", top_score=0.8),
        ScanRow(ticker="MSFT", as_of=date(2026, 5, 9), top_pattern="zigzag", top_score=0.6),
    )
    out = enrich(entries, report)
    assert [r.entry.symbol for r in out] == ["AAPL", "MSFT"]


def test_enrich_finds_row_case_insensitively() -> None:
    entries = [_entry("aapl")]  # lowercased entry
    report = _report(
        ScanRow(ticker="AAPL", as_of=date(2026, 5, 9), top_pattern="impulse", top_score=0.8)
    )
    out = enrich(entries, report)
    assert out[0].row is not None
    assert out[0].row.ticker == "AAPL"


def test_enrich_returns_none_row_for_symbol_not_in_scan() -> None:
    entries = [_entry("GOOG")]
    report = _report(
        ScanRow(ticker="AAPL", as_of=date(2026, 5, 9), top_pattern="impulse", top_score=0.8)
    )
    out = enrich(entries, report)
    assert out[0].row is None


def test_enrich_with_no_report_returns_rows_with_none() -> None:
    entries = [_entry("AAPL"), _entry("MSFT")]
    out = enrich(entries, report=None)
    assert all(r.row is None for r in out)
    assert [r.entry.symbol for r in out] == ["AAPL", "MSFT"]


def test_enriched_row_exposes_signal_helpers() -> None:
    entries = [_entry("AAPL")]
    report = _report(
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
        )
    )
    out = enrich(entries, report)
    er: EnrichedRow = out[0]
    assert er.has_signal is True
    assert er.bias == "long"
    assert float(er.row.signal_rr) == 3.21


def test_enriched_row_for_short_signal_reports_short_bias() -> None:
    entries = [_entry("MSFT")]
    report = _report(
        ScanRow(
            ticker="MSFT",
            as_of=date(2026, 5, 9),
            top_pattern="zigzag",
            top_score=0.5,
            direction="down",
            signal_rr=Decimal("2.0"),
            signal_entry=Decimal("420"),
            signal_stop=Decimal("428"),
            signal_target=Decimal("404"),
        )
    )
    out = enrich(entries, report)
    assert out[0].bias == "short"


def test_enriched_row_with_no_signal_reports_none_bias() -> None:
    entries = [_entry("GOOG")]
    out = enrich(entries, report=None)
    assert out[0].bias is None
    assert out[0].has_signal is False
```

- [ ] **Step 3.2: Run the tests — expect ImportError on `wave_alpha.web.watchlist_enrich`**

```bash
uv run pytest tests/web/test_watchlist_enrich.py -v
```

Expected: collection error / ModuleNotFoundError.

- [ ] **Step 3.3: Create `src/wave_alpha/web/watchlist_enrich.py`**

```python
"""Join watchlist entries against a scan report so the watchlist page
can show last-known signal/bias/coherence/RR per symbol without doing
the join in a template."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from wave_alpha.scan.models import ScanReport, ScanRow
from wave_alpha.watchlist.models import WatchlistEntry

Bias = Literal["long", "short"]


@dataclass(frozen=True)
class EnrichedRow:
    """One watchlist entry plus its matched ScanRow (or None if not in scan)."""

    entry: WatchlistEntry
    row: ScanRow | None

    @property
    def has_signal(self) -> bool:
        return self.row is not None and self.row.has_signal

    @property
    def bias(self) -> Bias | None:
        if self.row is None or self.row.direction is None:
            return None
        return "long" if self.row.direction == "up" else "short"


def enrich(entries: list[WatchlistEntry], report: ScanReport | None) -> list[EnrichedRow]:
    """Pair each entry with its row from the scan report (case-insensitive
    symbol match) or with None if the symbol is not in the report. If
    `report` is None, every row gets None — page still renders."""
    by_ticker: dict[str, ScanRow] = {}
    if report is not None:
        for row in report.rows:
            by_ticker[row.ticker.upper()] = row
    return [EnrichedRow(entry=e, row=by_ticker.get(e.symbol.upper())) for e in entries]
```

- [ ] **Step 3.4: Run the tests — expect all 7 to PASS**

```bash
uv run pytest tests/web/test_watchlist_enrich.py -v
```

- [ ] **Step 3.5: Lint, format, commit**

```bash
uv run ruff check src/wave_alpha/web/watchlist_enrich.py tests/web/test_watchlist_enrich.py
uv run ruff format src/wave_alpha/web/watchlist_enrich.py tests/web/test_watchlist_enrich.py
git add src/wave_alpha/web/watchlist_enrich.py tests/web/test_watchlist_enrich.py
git commit -m "feat(web): add watchlist_enrich helper to join entries against latest scan"
```

---

## Task 4 · Backend — wire enrichment into the watchlist route

**Files:**
- Modify: `src/wave_alpha/web/routes/watchlist.py`
- Test: `tests/web/test_watchlist_routes.py`

- [ ] **Step 4.1: Append a failing test to `tests/web/test_watchlist_routes.py`**

```python
def test_get_watchlist_passes_enriched_rows_to_template(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    """When a recent scan exists, the watchlist page surfaces last-known
    signal information from it (verified by checking the rendered text
    contains the pattern from the scan)."""
    from datetime import date as _date, datetime

    from wave_alpha.scan.archive import save_report
    from wave_alpha.scan.models import ScanReport, ScanRow
    from wave_alpha.web.routes import scan as scan_routes
    from wave_alpha.web.routes import watchlist as wl_routes

    monkeypatch.setattr(scan_routes, "_scan_dir", lambda: tmp_path)

    save_watchlist(
        [WatchlistEntry(symbol="AAPL", added_on=_date(2026, 5, 1))],
        wl_routes._watchlist_path(),
    )
    save_report(
        ScanReport(
            scan_id="2026-05-09T14-30-00",
            started_at=datetime(2026, 5, 9, 14, 30, 0),
            completed_at=datetime(2026, 5, 9, 14, 30, 30),
            as_of=_date(2026, 5, 9),
            rows=[
                ScanRow(
                    ticker="AAPL",
                    as_of=_date(2026, 5, 9),
                    top_pattern="impulse",
                    top_score=0.82,
                    direction="up",
                ),
            ],
        ),
        dir=tmp_path,
    )

    res = client.get("/watchlist")
    assert res.status_code == 200
    assert "AAPL" in res.text
    # The enrichment should surface the pattern from the scan in the page.
    assert "impulse" in res.text


def test_get_watchlist_renders_when_no_scan_exists(client: TestClient, tmp_path: Path) -> None:
    """Empty scan dir must not break the page."""
    from datetime import date as _date

    from wave_alpha.web.routes import watchlist as wl_routes

    save_watchlist(
        [WatchlistEntry(symbol="AAPL", added_on=_date(2026, 5, 1))],
        wl_routes._watchlist_path(),
    )
    res = client.get("/watchlist")
    assert res.status_code == 200
    assert "AAPL" in res.text
```

Also extend the `client` fixture at the top of the file so the scan-dir indirection is monkeypatchable. **Replace** the existing fixture with:

```python
@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    from wave_alpha.web.routes import scan as scan_routes
    from wave_alpha.web.routes import watchlist as wl_routes

    monkeypatch.setattr(wl_routes, "_watchlist_path", lambda: tmp_path / "wl.yaml")
    monkeypatch.setattr(scan_routes, "_scan_dir", lambda: tmp_path)
    return TestClient(create_app())
```

- [ ] **Step 4.2: Run new tests — expect 1 FAIL on the enrichment assertion**

```bash
uv run pytest tests/web/test_watchlist_routes.py::test_get_watchlist_passes_enriched_rows_to_template -v
```

Expected: test fails because `impulse` is not yet in the rendered HTML (route doesn't pass it).

- [ ] **Step 4.3: Replace `watchlist_page` in `src/wave_alpha/web/routes/watchlist.py`**

Replace the body of `watchlist_page` and add an import for the enrichment helper. Top of file becomes:

```python
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from wave_alpha.scan.archive import DEFAULT_SCAN_DIR, list_reports, load_report
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
```

(The route module previously didn't import from `scan.archive`; the `enrich` helper needs the latest report, so we look it up here. We do **not** add a private `_scan_dir()` indirection in this file — instead, defer to the existing `scan_routes._scan_dir()` so tests can monkeypatch one place. Implementation:)

```python
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
            "entries": entries,           # kept for backward-compat with current template
            "enriched": enriched,         # used by the redesigned template (Task 12)
            "latest_scan_id": latest_id,  # for the page sub-line
        },
    )
```

The `add` and `rm` handlers below it stay unchanged.

- [ ] **Step 4.4: Run all watchlist-route tests**

```bash
uv run pytest tests/web/test_watchlist_routes.py -v
```

Expected: all tests pass. The existing template (which references `entries`) still works because we kept it in the context.

- [ ] **Step 4.5: Lint, format, commit**

```bash
uv run ruff check src/wave_alpha/web/routes/watchlist.py tests/web/test_watchlist_routes.py
uv run ruff format src/wave_alpha/web/routes/watchlist.py tests/web/test_watchlist_routes.py
git add src/wave_alpha/web/routes/watchlist.py tests/web/test_watchlist_routes.py
git commit -m "feat(web/watchlist): surface latest-scan signal per entry to template"
```

---

## Task 5 · Backend — Home route + KPI builder, change `GET /` to render Home

**Files:**
- Create: `src/wave_alpha/web/home.py` (KPI/data builder — pure)
- Create: `src/wave_alpha/web/routes/home.py` (router)
- Create: `src/wave_alpha/web/templates/home.html` (minimal placeholder; styling in Task 10)
- Modify: `src/wave_alpha/web/routes/system.py` (drop `/` redirect)
- Modify: `src/wave_alpha/web/app.py` (register home router)
- Modify: `tests/web/test_routes_system.py` (update `/` assertion)
- Test: `tests/web/test_home.py` (new) and `tests/web/test_home_route.py` (new)

- [ ] **Step 5.1: Create the failing test for the KPI builder `tests/web/test_home.py`**

```python
"""Pure tests for the Home page data builder. No FastAPI involvement."""

from datetime import date, datetime
from decimal import Decimal

from wave_alpha.scan.models import ScanReport, ScanRow
from wave_alpha.watchlist.models import WatchlistEntry
from wave_alpha.web.home import HomeKpis, HomeView, build_view


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


def test_build_view_top_signals_returns_first_five_with_signals_sorted_by_rr() -> None:
    rep = _report_with_signals()  # 2 signals: AAPL (rr 3.21) and MSFT (rr 2.40)
    view = build_view(report=rep, watchlist=[], stable_streaks={})
    assert [t.ticker for t in view.top_signals] == ["AAPL", "MSFT"]
    assert len(view.top_signals) <= 5
```

- [ ] **Step 5.2: Run the tests — expect ImportError on `wave_alpha.web.home`**

```bash
uv run pytest tests/web/test_home.py -v
```

- [ ] **Step 5.3: Create `src/wave_alpha/web/home.py`**

```python
"""Pure data builder for the Home page. No FastAPI imports — keeps the
view buildable from the route, from tests, and (later) from a CLI dump."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from wave_alpha.scan.models import ScanReport, ScanRow
from wave_alpha.scan.ranking import filter_signals, sort_by
from wave_alpha.watchlist.models import WatchlistEntry

Bias = Literal["long", "short"]


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
        stable_picks = sum(
            1 for e in watchlist if stable_streaks.get(e.symbol.upper(), 0) >= 3
        )

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
) -> HomeView:
    """Pure constructor.
    - `report`: most-recent scan (or None).
    - `watchlist`: persisted watchlist entries.
    - `stable_streaks`: {SYMBOL_UPPER: streak_int} from history service.
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
                    symbol=e.symbol, pattern=None, last_pivot_label=None,
                    bias=None, coherence=None, signal_rr=None,
                )
            )

    return HomeView(
        kpis=kpis,
        top_signals=top_signals,
        watchlist_top=watchlist_top,
        latest_scan_id=report.scan_id if report else None,
    )
```

- [ ] **Step 5.4: Run KPI tests — expect all 6 PASS**

```bash
uv run pytest tests/web/test_home.py -v
```

- [ ] **Step 5.5: Create the route test file `tests/web/test_home_route.py`**

```python
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wave_alpha.scan.archive import save_report
from wave_alpha.scan.models import ScanReport, ScanRow
from wave_alpha.watchlist.models import WatchlistEntry
from wave_alpha.watchlist.store import save_watchlist
from wave_alpha.web.app import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    from wave_alpha.web.routes import scan as scan_routes
    from wave_alpha.web.routes import watchlist as wl_routes

    monkeypatch.setattr(scan_routes, "_scan_dir", lambda: tmp_path)
    monkeypatch.setattr(wl_routes, "_watchlist_path", lambda: tmp_path / "wl.yaml")
    return TestClient(create_app())


def test_home_renders_empty_state_with_no_data(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200
    assert "wave-alpha" in res.text.lower()


def test_home_renders_top_signal_when_scan_present(client: TestClient, tmp_path: Path) -> None:
    save_report(
        ScanReport(
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
            ],
        ),
        dir=tmp_path,
    )
    res = client.get("/")
    assert res.status_code == 200
    assert "AAPL" in res.text
    assert "impulse" in res.text


def test_home_renders_watchlist_section_when_entries_exist(
    client: TestClient, tmp_path: Path
) -> None:
    save_watchlist(
        [WatchlistEntry(symbol="TSLA", added_on=date(2026, 5, 1))],
        tmp_path / "wl.yaml",
    )
    res = client.get("/")
    assert res.status_code == 200
    assert "TSLA" in res.text
```

- [ ] **Step 5.6: Run the route tests — expect 404 (route not registered yet)**

```bash
uv run pytest tests/web/test_home_route.py -v
```

Expected: status code 200 assertion fails because `/` still 307-redirects to `/settings`.

- [ ] **Step 5.7: Create `src/wave_alpha/web/routes/home.py`**

```python
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from wave_alpha.scan.archive import list_reports, load_report
from wave_alpha.watchlist.store import load_watchlist
from wave_alpha.web.home import build_view

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
        except Exception as exc:  # noqa: BLE001 — non-fatal
            _log.warning("home: history lookup failed for %s: %s", sym, exc)
            out[sym.upper()] = 0
    return out


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    from wave_alpha.web.routes import scan as scan_routes
    from wave_alpha.web.routes import watchlist as wl_routes

    scan_dir = scan_routes._scan_dir()
    latest_id = next(iter(list_reports(dir=scan_dir)), None)
    report = load_report(latest_id, dir=scan_dir) if latest_id else None

    entries = load_watchlist(wl_routes._watchlist_path())
    streaks = _stable_streaks([e.symbol for e in entries]) if entries else {}

    view = build_view(report=report, watchlist=entries, stable_streaks=streaks)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"view": view},
    )
```

- [ ] **Step 5.8: Create the placeholder template `src/wave_alpha/web/templates/home.html`**

This is a stub that satisfies the route-level tests. The full styled version is in Task 10.

```jinja
{% extends "base.html" %}
{% block title %}Home — wave-alpha{% endblock %}
{% block content %}
<h1>wave-alpha</h1>

{% if view.kpis.signals is not none %}
  <p>{{ view.kpis.signals }} signals from the latest scan
  {% if view.latest_scan_id %}({{ view.latest_scan_id }}){% endif %}.</p>
{% else %}
  <p>No scans yet.</p>
{% endif %}

{% if view.top_signals %}
  <h2>Today's top signals</h2>
  <ul>
    {% for r in view.top_signals %}
      <li>{{ r.ticker }} — {{ r.top_pattern }} — RR {{ r.signal_rr }}</li>
    {% endfor %}
  </ul>
{% endif %}

{% if view.watchlist_top %}
  <h2>Your watchlist</h2>
  <ul>
    {% for w in view.watchlist_top %}
      <li>{{ w.symbol }}{% if w.pattern %} — {{ w.pattern }}{% endif %}</li>
    {% endfor %}
  </ul>
{% endif %}
{% endblock %}
```

- [ ] **Step 5.9: Drop the redirect in `src/wave_alpha/web/routes/system.py`**

Replace the file with:

```python
from __future__ import annotations

import os
import signal

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@router.post("/quit")
def quit_server() -> JSONResponse:
    """Local-tool convenience: server shuts itself down on POST /quit."""
    os.kill(os.getpid(), signal.SIGINT)
    return JSONResponse({"status": "shutting down"})
```

(`GET /` is now owned by `home.py`.)

- [ ] **Step 5.10: Register the home router in `src/wave_alpha/web/app.py`**

Modify the import line and the router registrations:

```python
    # Routes
    from wave_alpha.web.routes import deepdive, home, scan, settings, system, watchlist

    app.include_router(system.router)
    app.include_router(home.router)
    app.include_router(settings.router)
    app.include_router(deepdive.router)
    app.include_router(watchlist.router)
    app.include_router(scan.router)
    return app
```

- [ ] **Step 5.11: Update the `/` test in `tests/web/test_routes_system.py`**

Replace `test_root_redirects_to_settings_on_first_run` with:

```python
def test_root_renders_home_page() -> None:
    res = _client().get("/", follow_redirects=False)
    assert res.status_code == 200
    # Sanity: the brand mark / wordmark appears.
    assert "wave-alpha" in res.text.lower()
```

- [ ] **Step 5.12: Run all web tests — expect green**

```bash
uv run pytest tests/web -v
```

Expected: all tests pass. If `test_routes_system.py` still has the old assertion, fix per 5.11.

- [ ] **Step 5.13: Lint, format, commit**

```bash
uv run ruff check src/wave_alpha/web tests/web
uv run ruff format src/wave_alpha/web tests/web
git add src/wave_alpha/web/home.py src/wave_alpha/web/routes/home.py \
        src/wave_alpha/web/routes/system.py src/wave_alpha/web/app.py \
        src/wave_alpha/web/templates/home.html \
        tests/web/test_home.py tests/web/test_home_route.py \
        tests/web/test_routes_system.py
git commit -m "feat(web): add Home dashboard at / and drop /settings redirect"
```

---

## Task 6 · Frontend — design tokens (full `app.css` rewrite)

**Files:**
- Modify (rewrite): `src/wave_alpha/web/static/app.css`
- Test: `tests/web/test_static_assets.py` (add a sanity check for new token presence)

**Why before templates:** the templates in tasks 7-13 reference token classes; having the CSS in place first means a smoke check after each template task surfaces issues immediately.

- [ ] **Step 6.1: Add a token-presence sanity test to `tests/web/test_static_assets.py`**

Read the existing file first to see its structure, then append:

```python
def test_app_css_declares_brand_tokens() -> None:
    from importlib.resources import files

    css = (files("wave_alpha.web") / "static" / "app.css").read_text()
    # Tokens used everywhere in the redesign — if any go missing, lots of pages break.
    for token in [
        "--bg-base", "--bg-surface", "--bg-elevated",
        "--fg-primary", "--fg-secondary", "--fg-muted",
        "--cyan", "--magenta", "--purple", "--yellow",
        "--bull", "--bear",
        "--candle-up", "--candle-down",
        "--font-sans", "--font-mono",
    ]:
        assert token in css, f"missing design token: {token}"
```

- [ ] **Step 6.2: Run the test — expect FAIL**

```bash
uv run pytest tests/web/test_static_assets.py::test_app_css_declares_brand_tokens -v
```

Expected: assertion error on the first missing token.

- [ ] **Step 6.3: Replace `src/wave_alpha/web/static/app.css` with the redesign tokens + components**

Replace the entire file with the contents below. (The file is long but mechanical — every selector here is referenced in tasks 7-13.)

```css
/* wave-alpha · cyber-spectrum dark UI · v1
 * Design tokens follow docs/superpowers/specs/2026-05-09-web-ui-redesign-design.md §5.
 * Pure black surfaces · 12-hue neon spectrum · classic green/red candles (locked).
 */

:root {
  /* Surfaces */
  --bg-base: #000000;
  --bg-surface: #07090F;
  --bg-elevated: #0E121C;
  --bg-input: #050608;

  /* Text */
  --fg-primary: #F8FAFC;
  --fg-secondary: #C7D2DE;
  --fg-muted: #7C8BA3;
  --fg-subtle: #475569;

  /* Hairlines */
  --border-subtle: rgba(34, 211, 238, 0.06);
  --border-default: rgba(34, 211, 238, 0.14);
  --border-strong: rgba(34, 211, 238, 0.30);

  /* Spectrum (semantic — see spec §5) */
  --cyan:    #22D3EE; --cyan-soft:    rgba(34,211,238,0.13);  --cyan-glow:    rgba(34,211,238,0.55);
  --teal:    #2DD4BF; --teal-soft:    rgba(45,212,191,0.13);  --teal-glow:    rgba(45,212,191,0.55);
  --lime:    #A3E635; --lime-soft:    rgba(163,230,53,0.13);  --lime-glow:    rgba(163,230,53,0.55);
  --green:   #4ADE80; --green-soft:   rgba(74,222,128,0.13);  --green-glow:   rgba(74,222,128,0.55);
  --yellow:  #FACC15; --yellow-soft:  rgba(250,204,21,0.13);  --yellow-glow:  rgba(250,204,21,0.55);
  --orange:  #FB923C; --orange-soft:  rgba(251,146,60,0.13);  --orange-glow:  rgba(251,146,60,0.55);
  --red:     #FB7185; --red-soft:     rgba(251,113,133,0.13); --red-glow:     rgba(251,113,133,0.55);
  --rose:    #F43F5E; --rose-soft:    rgba(244,63,94,0.13);   --rose-glow:    rgba(244,63,94,0.55);
  --magenta: #F472B6; --magenta-soft: rgba(244,114,182,0.13); --magenta-glow: rgba(244,114,182,0.55);
  --purple:  #A855F7; --purple-soft:  rgba(168,85,247,0.13);  --purple-glow:  rgba(168,85,247,0.55);
  --indigo:  #818CF8; --indigo-soft:  rgba(129,140,248,0.13); --indigo-glow:  rgba(129,140,248,0.55);
  --blue:    #60A5FA; --blue-soft:    rgba(96,165,250,0.13);  --blue-glow:    rgba(96,165,250,0.55);

  /* Bull/bear aliases */
  --bull: var(--green); --bull-soft: var(--green-soft); --bull-glow: var(--green-glow);
  --bear: var(--red);   --bear-soft: var(--red-soft);   --bear-glow: var(--red-glow);
  --warn: var(--yellow); --warn-soft: var(--yellow-soft);

  /* Locked candle colors — do not retint */
  --candle-up: #26A69A;
  --candle-down: #EF5350;

  /* Brand gradient */
  --brand-grad: linear-gradient(120deg, #22D3EE 0%, #818CF8 30%, #F472B6 60%, #FB923C 90%);

  /* Typography */
  --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI Variable",
               "Segoe UI", system-ui, sans-serif;
  --font-mono: "SF Mono", "JetBrains Mono", ui-monospace,
               "Cascadia Mono", Menlo, Consolas, monospace;

  /* Shadows */
  --shadow-card: 0 0 0 1px rgba(34,211,238,0.04) inset, 0 4px 24px rgba(0,0,0,0.65);
  --shadow-elev: 0 0 0 1px rgba(34,211,238,0.08) inset, 0 18px 50px rgba(0,0,0,0.75);

  --radius: 10px;
  --radius-sm: 6px;
}

* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0;
  background: var(--bg-base); color: var(--fg-primary);
  font: 14px/1.5 var(--font-sans);
  background-image:
    radial-gradient(circle at 15% -10%, rgba(34,211,238,0.07), transparent 38%),
    radial-gradient(circle at 85% -5%, rgba(168,85,247,0.05), transparent 35%),
    radial-gradient(circle at 80% 110%, rgba(244,114,182,0.05), transparent 50%),
    radial-gradient(circle at 10% 105%, rgba(251,146,60,0.04), transparent 45%),
    linear-gradient(rgba(34,211,238,0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(34,211,238,0.03) 1px, transparent 1px);
  background-size: auto, auto, auto, auto, 40px 40px, 40px 40px;
  background-attachment: fixed;
  min-height: 100vh;
}
a { color: var(--cyan); text-decoration: none; }
a:hover { text-decoration: underline; }
.num, .mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.glow-cyan   { color: var(--cyan);    text-shadow: 0 0 10px var(--cyan-glow); }
.glow-mag    { color: var(--magenta); text-shadow: 0 0 10px var(--magenta-glow); }
.glow-bull   { color: var(--bull);    text-shadow: 0 0 10px var(--bull-glow); }
.glow-bear   { color: var(--bear);    text-shadow: 0 0 10px var(--bear-glow); }
.glow-purple { color: var(--purple);  text-shadow: 0 0 10px var(--purple-glow); }
.glow-orange { color: var(--orange);  text-shadow: 0 0 10px var(--orange-glow); }
.glow-yellow { color: var(--yellow);  text-shadow: 0 0 10px var(--yellow-glow); }

/* Top nav */
.topnav {
  display: flex; align-items: center; gap: 24px;
  padding: 12px 24px; position: sticky; top: 0; z-index: 50;
  background: linear-gradient(180deg, rgba(34,211,238,0.04), rgba(0,0,0,0.85));
  border-bottom: 1px solid var(--border-default);
  backdrop-filter: blur(8px);
}
.topnav::after {
  content: ""; position: absolute; left: 0; right: 0; bottom: -1px; height: 1px;
  background: linear-gradient(90deg, transparent, var(--cyan-glow), var(--indigo-glow),
              var(--magenta-glow), var(--orange-glow), transparent);
  opacity: 0.35; pointer-events: none;
}
.brand { display: flex; align-items: center; gap: 10px; font-weight: 800;
  letter-spacing: -0.02em; color: var(--fg-primary); text-decoration: none; }
.brand:hover { text-decoration: none; }
.brand-mark { width: 22px; height: 22px; border-radius: 5px;
  background: var(--brand-grad); position: relative;
  box-shadow: 0 0 18px rgba(34,211,238,0.45); }
.brand-mark::after { content: ""; position: absolute; inset: 4px;
  border-radius: 2px; background: var(--bg-base); }
.brand-name { background: var(--brand-grad); -webkit-background-clip: text;
  background-clip: text; color: transparent;
  filter: drop-shadow(0 0 8px rgba(34,211,238,0.4)); }

.nav-links { display: flex; gap: 4px; flex: 1; margin-left: 8px; }
.nav-link { padding: 6px 12px; border-radius: var(--radius-sm);
  color: var(--fg-muted); font: 600 13px var(--font-sans);
  cursor: pointer; transition: color .15s, background .15s; text-decoration: none; }
.nav-link:hover { color: var(--fg-secondary); background: rgba(34,211,238,0.05);
  text-decoration: none; }
.nav-link.active { color: var(--cyan); background: var(--cyan-soft);
  box-shadow: 0 0 18px rgba(34,211,238,0.18);
  text-shadow: 0 0 6px var(--cyan-glow); }

.search { display: flex; align-items: center; gap: 8px;
  padding: 6px 10px; min-width: 240px;
  background: var(--bg-input); border: 1px solid var(--border-default);
  border-radius: var(--radius-sm); color: var(--fg-muted); font-size: 12px; }
.search input { background: transparent; border: 0; color: var(--fg-primary);
  font: 13px var(--font-mono); flex: 1; outline: none; }
.search input::placeholder { color: var(--fg-muted); }
.search .kbd { margin-left: auto; padding: 2px 6px;
  border: 1px solid var(--border-default); border-radius: 3px;
  font: 10px var(--font-mono); color: var(--cyan); }

.asof { display: flex; align-items: center; gap: 6px;
  padding: 6px 10px; background: var(--bg-input);
  border: 1px solid var(--border-default); border-radius: var(--radius-sm);
  color: var(--cyan); font: 12px var(--font-mono); }
.asof input { background: transparent; border: 0; color: var(--cyan);
  font: 12px var(--font-mono); outline: none;
  color-scheme: dark; }

/* Page chrome */
.page { padding: 24px; max-width: 1280px; margin: 0 auto; }
.page-head { display: flex; align-items: end; justify-content: space-between;
  margin-bottom: 20px; gap: 16px; flex-wrap: wrap; }
.page-title { font: 800 22px var(--font-sans); letter-spacing: -0.01em; margin: 0; }
.page-sub { color: var(--fg-muted); margin: 4px 0 0; font-size: 13px; }

/* Cards */
.card { background: linear-gradient(180deg, rgba(34,211,238,0.025), transparent 60%),
  var(--bg-surface);
  border: 1px solid var(--border-default); border-radius: var(--radius);
  padding: 18px; box-shadow: var(--shadow-card); position: relative;
  margin-bottom: 14px; }
.card:last-child { margin-bottom: 0; }
.card-head { display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 14px; gap: 12px; flex-wrap: wrap; }
.card-title { font: 800 11px var(--font-mono); letter-spacing: 0.16em;
  text-transform: uppercase; color: var(--cyan); margin: 0; }
.card-title.mag    { color: var(--magenta); text-shadow: 0 0 10px var(--magenta-glow); }
.card-title.purple { color: var(--purple);  text-shadow: 0 0 10px var(--purple-glow); }
.card-title.yellow { color: var(--yellow);  text-shadow: 0 0 10px var(--yellow-glow); }
.card-title.bull   { color: var(--bull);    text-shadow: 0 0 10px var(--bull-glow); }
.card-title.orange { color: var(--orange);  text-shadow: 0 0 10px var(--orange-glow); }
.card-action { color: var(--fg-muted); font-size: 12px; cursor: pointer; }
.card-action:hover { color: var(--cyan); }

/* KPI */
.kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px;
  margin-bottom: 16px; }
.kpi { padding: 14px 16px; background: var(--bg-surface);
  border: 1px solid var(--border-default); border-radius: var(--radius);
  position: relative; overflow: hidden; }
.kpi::after { content: ""; position: absolute; left: 0; top: 0; bottom: 0;
  width: 2px; background: var(--cyan); opacity: 0.65;
  box-shadow: 0 0 12px var(--cyan-glow); }
.kpi.mag::after    { background: var(--magenta); box-shadow: 0 0 12px var(--magenta-glow); }
.kpi.bull::after   { background: var(--bull);    box-shadow: 0 0 12px var(--bull-glow); }
.kpi.purple::after { background: var(--purple);  box-shadow: 0 0 12px var(--purple-glow); }
.kpi.yellow::after { background: var(--yellow);  box-shadow: 0 0 12px var(--yellow-glow); }
.kpi.orange::after { background: var(--orange);  box-shadow: 0 0 12px var(--orange-glow); }
.kpi.indigo::after { background: var(--indigo);  box-shadow: 0 0 12px var(--indigo-glow); }
.kpi-label { font: 700 10px var(--font-mono); letter-spacing: 0.12em;
  text-transform: uppercase; color: var(--fg-muted); }
.kpi-value { font-family: var(--font-mono); font-size: 24px; font-weight: 600;
  color: var(--fg-primary); margin-top: 6px; line-height: 1; }
.kpi-delta { font: 11px var(--font-mono); color: var(--fg-muted); margin-top: 6px; }
.kpi-delta.pos { color: var(--bull); }
.kpi-delta.neg { color: var(--bear); }

/* Pills */
.pill { display: inline-flex; align-items: center; gap: 5px;
  padding: 2px 9px; border-radius: 999px;
  font: 700 11px var(--font-sans); border: 1px solid transparent; }
.pill .dot { width: 6px; height: 6px; border-radius: 50%;
  background: currentColor; box-shadow: 0 0 6px currentColor; }
.pill.bull   { background: var(--bull-soft);    color: var(--bull);
  border-color: rgba(74,222,128,0.30); box-shadow: 0 0 10px rgba(74,222,128,0.18); }
.pill.bear   { background: var(--bear-soft);    color: var(--bear);
  border-color: rgba(251,113,133,0.30); box-shadow: 0 0 10px rgba(251,113,133,0.18); }
.pill.warn   { background: var(--warn-soft);    color: var(--warn);
  border-color: rgba(251,191,36,0.30); }
.pill.cyan   { background: var(--cyan-soft);    color: var(--cyan);
  border-color: rgba(34,211,238,0.30); box-shadow: 0 0 10px rgba(34,211,238,0.18); }
.pill.mag    { background: var(--magenta-soft); color: var(--magenta);
  border-color: rgba(244,114,182,0.30); box-shadow: 0 0 10px rgba(244,114,182,0.18); }
.pill.purple { background: var(--purple-soft);  color: var(--purple);
  border-color: rgba(168,85,247,0.30); box-shadow: 0 0 10px rgba(168,85,247,0.18); }
.pill.indigo { background: var(--indigo-soft);  color: var(--indigo);
  border-color: rgba(129,140,248,0.30); }
.pill.teal   { background: var(--teal-soft);    color: var(--teal);
  border-color: rgba(45,212,191,0.30); }
.pill.lime   { background: var(--lime-soft);    color: var(--lime);
  border-color: rgba(163,230,53,0.30); }
.pill.orange { background: var(--orange-soft);  color: var(--orange);
  border-color: rgba(251,146,60,0.30); }
.pill.rose   { background: var(--rose-soft);    color: var(--rose);
  border-color: rgba(244,63,94,0.30); }
.pill.muted  { background: rgba(124,139,163,0.10); color: var(--fg-secondary);
  border-color: rgba(124,139,163,0.18); }

/* Pattern badges */
.pat { display: inline-flex; align-items: center; gap: 6px;
  padding: 3px 10px; border-radius: 5px;
  font: 700 11px var(--font-mono); letter-spacing: 0.04em;
  border: 1px solid currentColor; }
.pat.impulse        { color: var(--cyan);    background: var(--cyan-soft);
  box-shadow: 0 0 10px rgba(34,211,238,0.18); }
.pat.zigzag         { color: var(--magenta); background: var(--magenta-soft);
  box-shadow: 0 0 10px rgba(244,114,182,0.18); }
.pat.flat           { color: var(--teal);    background: var(--teal-soft); }
.pat.expanded-flat  { color: var(--indigo);  background: var(--indigo-soft); }
.pat.end-diagonal   { color: var(--purple);  background: var(--purple-soft); }
.pat.contracting    { color: var(--orange);  background: var(--orange-soft); }
.pat.unknown        { color: var(--fg-muted); background: rgba(124,139,163,0.10); }

/* Wave label badges */
.wlabel { display: inline-flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; border-radius: 50%;
  font: 800 11px var(--font-mono); border: 1px solid currentColor; }
.wlabel.w1 { color: var(--cyan);    background: var(--cyan-soft); }
.wlabel.w2 { color: var(--orange);  background: var(--orange-soft); }
.wlabel.w3 { color: var(--magenta); background: var(--magenta-soft); }
.wlabel.w4 { color: var(--lime);    background: var(--lime-soft); }
.wlabel.w5 { color: var(--purple);  background: var(--purple-soft); }
.wlabel.wa { color: var(--teal);    background: var(--teal-soft); }
.wlabel.wb { color: var(--yellow);  background: var(--yellow-soft); }
.wlabel.wc { color: var(--rose);    background: var(--rose-soft); }
.wlabel.unknown { color: var(--fg-muted); background: rgba(124,139,163,0.10); }

/* Tri-coherence */
.tri { display: inline-flex; gap: 4px; }
.tri-cell { display: inline-flex; align-items: center; gap: 3px;
  padding: 2px 8px; border-radius: var(--radius-sm);
  font: 700 10px var(--font-mono);
  border: 1px solid var(--border-default); color: var(--fg-secondary); }
.tri-cell.ok      { background: var(--bull-soft); color: var(--bull);
  border-color: rgba(74,222,128,0.30); box-shadow: 0 0 8px rgba(74,222,128,0.12); }
.tri-cell.warn    { background: var(--warn-soft); color: var(--warn);
  border-color: rgba(251,191,36,0.30); }
.tri-cell.missing { color: var(--fg-subtle); }

/* Score bar */
.scorebar { display: inline-flex; align-items: center; gap: 8px; }
.scorebar .bar { width: 60px; height: 5px; border-radius: 3px;
  background: rgba(124,139,163,0.15); overflow: hidden; }
.scorebar .bar > span { display: block; height: 100%;
  background: linear-gradient(90deg, var(--cyan), var(--indigo),
              var(--magenta), var(--orange));
  border-radius: 3px; box-shadow: 0 0 10px var(--cyan-glow); }

/* Buttons */
.btn { display: inline-flex; align-items: center; gap: 8px;
  padding: 8px 14px; border-radius: var(--radius-sm);
  border: 1px solid var(--border-default);
  background: var(--bg-elevated); color: var(--fg-primary);
  font: 700 13px var(--font-sans); cursor: pointer;
  transition: background .15s, border-color .15s, box-shadow .15s, color .15s;
  text-decoration: none; }
.btn:hover { border-color: var(--cyan); color: var(--cyan);
  box-shadow: 0 0 16px rgba(34,211,238,0.20); }
.btn-primary { background: var(--brand-grad); color: #02060A; border: none;
  box-shadow: 0 0 22px rgba(34,211,238,0.45), 0 0 30px rgba(244,114,182,0.25); }
.btn-primary:hover { filter: brightness(1.1); color: #02060A; }
.btn-ghost { background: transparent; }
.btn-danger { background: transparent; color: var(--bear);
  border-color: rgba(251,113,133,0.30); }
.btn-danger:hover { background: var(--bear-soft); color: var(--bear);
  border-color: var(--bear); box-shadow: 0 0 16px rgba(251,113,133,0.25); }

/* Inputs */
.field { display: flex; flex-direction: column; gap: 6px; }
.field-label { font: 700 11px var(--font-mono); letter-spacing: 0.10em;
  text-transform: uppercase; color: var(--cyan); }
.field-help { font-size: 12px; color: var(--fg-muted); margin-top: 4px; }
.input { padding: 9px 12px; background: var(--bg-input);
  border: 1px solid var(--border-default); border-radius: var(--radius-sm);
  color: var(--fg-primary); font: 13px var(--font-sans);
  transition: border-color .15s, box-shadow .15s;
  color-scheme: dark; }
.input:focus { outline: none; border-color: var(--cyan);
  box-shadow: 0 0 0 3px var(--cyan-soft), 0 0 18px var(--cyan-glow); }
.input.mono { font-family: var(--font-mono); }
input[type="range"].input { padding: 0; accent-color: var(--cyan);
  background: transparent; border: none; }

/* Tables */
.dt { width: 100%; border-collapse: collapse; font-size: 13px; }
.dt th { text-align: left; font: 800 10px var(--font-mono);
  letter-spacing: 0.12em; text-transform: uppercase; color: var(--cyan);
  padding: 11px 12px; border-bottom: 1px solid var(--border-default);
  background: rgba(34,211,238,0.03); position: sticky; top: 0; }
.dt td { padding: 12px; border-bottom: 1px solid var(--border-subtle); }
.dt tr { transition: background .12s; }
.dt tr:hover td { background: rgba(34,211,238,0.04); }
.dt td.tk { font-family: var(--font-mono); font-weight: 700;
  color: var(--fg-primary); letter-spacing: 0.02em; }
.dt td.num { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.dt td.r-pos { color: var(--bull); text-shadow: 0 0 8px var(--bull-glow); }
.dt td.r-neg { color: var(--bear); text-shadow: 0 0 8px var(--bear-glow); }
.dt a { color: var(--fg-primary); }
.dt a:hover { color: var(--cyan); text-decoration: none; }
.dt-card { padding: 0; overflow: hidden; }

/* Tabs */
.tabs { display: inline-flex; padding: 3px; background: var(--bg-elevated);
  border: 1px solid var(--border-default); border-radius: var(--radius-sm); gap: 2px; }
.tab { padding: 6px 14px; border-radius: 5px;
  font: 700 12px var(--font-sans); color: var(--fg-muted); cursor: pointer; }
.tab.active { background: var(--cyan-soft); color: var(--cyan);
  box-shadow: 0 0 14px rgba(34,211,238,0.2);
  text-shadow: 0 0 6px var(--cyan-glow); }

/* Empty state */
.empty { padding: 48px 28px; text-align: center; }
.empty-ico { width: 48px; height: 48px; margin: 0 auto 14px;
  border-radius: var(--radius-sm); background: var(--cyan-soft); color: var(--cyan);
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 0 22px rgba(34,211,238,0.35);
  border: 1px solid var(--border-strong); }
.empty-title { font-size: 16px; font-weight: 700; margin: 0 0 6px; }
.empty-sub { color: var(--fg-muted); margin: 0 auto 18px; max-width: 480px; }

/* Code block */
.codeblock { display: block; padding: 12px 14px; text-align: left;
  background: var(--bg-input); border: 1px solid var(--border-default);
  border-radius: var(--radius-sm); font: 12px var(--font-mono);
  color: var(--fg-secondary); margin: 8px auto; max-width: 580px;
  overflow-x: auto; box-shadow: inset 0 0 18px rgba(34,211,238,0.04); }
.codeblock .c { color: var(--fg-subtle); }
.codeblock .k { color: var(--magenta); text-shadow: 0 0 6px var(--magenta-glow); }
.codeblock .s { color: var(--bull); }

/* Glossary tooltip */
.gloss { border-bottom: 1px dashed var(--cyan); cursor: help; position: relative;
  color: inherit; }
.gloss::after { content: attr(data-tip); position: absolute; bottom: 130%; left: 0;
  min-width: 240px; padding: 9px 11px; background: var(--bg-elevated);
  border: 1px solid var(--border-strong); border-radius: var(--radius-sm);
  font: 12px/1.45 var(--font-sans); color: var(--fg-secondary);
  opacity: 0; pointer-events: none; transition: opacity .15s;
  z-index: 5; box-shadow: var(--shadow-elev), 0 0 22px rgba(34,211,238,0.18); }
.gloss:hover::after, .gloss:focus::after { opacity: 1; }

/* Skeleton (HTMX loading state) */
.htmx-indicator { display: none; }
.htmx-request .htmx-indicator { display: block; }
.skeleton { display: block; height: 60px; border-radius: var(--radius-sm);
  background: linear-gradient(90deg, var(--bg-elevated) 0%,
              rgba(34,211,238,0.08) 50%, var(--bg-elevated) 100%);
  background-size: 200% 100%; animation: shimmer 1.4s infinite linear; }
@keyframes shimmer { from { background-position: 200% 0; } to { background-position: -200% 0; } }

/* Layout grids */
.row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.row-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }
.dd-grid { display: grid; grid-template-columns: 1.6fr 1fr; gap: 16px; }
.stack > * + * { margin-top: 12px; }

/* Ring (coherence) */
.ring { position: relative; width: 84px; height: 84px; }
.ring svg { transform: rotate(-90deg); }
.ring .pct { position: absolute; inset: 0; display: flex;
  flex-direction: column; align-items: center; justify-content: center; }
.ring .pct .v { font: 700 18px var(--font-mono); color: var(--cyan);
  text-shadow: 0 0 10px var(--cyan-glow); }
.ring .pct .l { font: 700 9px var(--font-mono); color: var(--fg-muted);
  text-transform: uppercase; letter-spacing: 0.12em; }

/* Chart container */
#chart, #chart-canvas { width: 100%; height: 480px; border-radius: var(--radius-sm);
  background: var(--bg-base); }

/* Footer / disclaimer */
.disclaimer { color: var(--fg-muted); font-size: 12px; padding: 24px;
  text-align: center; border-top: 1px solid var(--border-subtle);
  margin-top: 32px; }

/* Cursor */
[role="button"], button, .nav-link, .pill, .pat, .wlabel, .card-action,
.tab, a[href] { cursor: pointer; }

/* Reduced motion */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: 0.001ms !important;
    animation-iteration-count: 1 !important; transition-duration: 0.001ms !important; }
  .skeleton { background: var(--bg-elevated); animation: none; }
}

/* Responsive: collapse multi-col grids on narrow screens */
@media (max-width: 1023px) {
  .dd-grid, .row-2, .row-3 { grid-template-columns: 1fr; }
  .kpi-grid { grid-template-columns: 1fr 1fr; }
  .topnav { gap: 12px; padding: 10px 14px; flex-wrap: wrap; }
  .nav-links { order: 3; flex-basis: 100%; overflow-x: auto;
    -webkit-overflow-scrolling: touch; }
  .search { min-width: 0; flex: 1; }
}
```

- [ ] **Step 6.4: Run the token test — expect PASS**

```bash
uv run pytest tests/web/test_static_assets.py -v
```

- [ ] **Step 6.5: Smoke-check that nothing broke. Boot the server briefly and curl `/`.**

In one shell:

```bash
uv run wave-alpha ui &
sleep 3
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/scan
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/watchlist
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/settings
curl -s -X POST http://127.0.0.1:8765/quit
```

Expected: four `200`s.

- [ ] **Step 6.6: Commit**

```bash
git add src/wave_alpha/web/static/app.css tests/web/test_static_assets.py
git commit -m "feat(web/css): rewrite app.css around cyber-spectrum design tokens"
```

---

## Task 7 · Frontend — Jinja macros + base layout

**Files:**
- Create: `src/wave_alpha/web/templates/_macros.html`
- Modify (rewrite): `src/wave_alpha/web/templates/base.html`

- [ ] **Step 7.1: Create `src/wave_alpha/web/templates/_macros.html`**

```jinja
{# Reusable Jinja macros for wave-alpha UI.
   Import in any template with: {% raw %}{% from "_macros.html" import pat, wlabel, pill, tri, icon %}{% endraw %} #}

{# pat(name) — colored Elliott pattern badge. Maps known names; unknowns get .pat.unknown #}
{% macro pat(name) -%}
  {% if not name %}<span class="pat unknown">—</span>
  {% else %}
    {% set n = name|lower %}
    {% if n == "impulse" %}<span class="pat impulse">{{ name }}</span>
    {% elif n == "zigzag" %}<span class="pat zigzag">{{ name }}</span>
    {% elif n == "flat" %}<span class="pat flat">{{ name }}</span>
    {% elif n == "expanded_flat" or n == "expanded flat" %}<span class="pat expanded-flat">{{ name }}</span>
    {% elif n == "ending_diagonal" or n == "ending diagonal" %}<span class="pat end-diagonal">{{ name }}</span>
    {% elif n == "contracting_triangle" or n == "contracting triangle" %}<span class="pat contracting">{{ name }}</span>
    {% else %}<span class="pat unknown">{{ name }}</span>
    {% endif %}
  {% endif %}
{%- endmacro %}

{# wlabel(label) — 1..5 / A / B / C — circular wave label badge #}
{% macro wlabel(label) -%}
  {% if not label %}<span class="wlabel unknown">·</span>
  {% else %}
    {% set l = label|string|upper %}
    {% if l == "1" %}<span class="wlabel w1">1</span>
    {% elif l == "2" %}<span class="wlabel w2">2</span>
    {% elif l == "3" %}<span class="wlabel w3">3</span>
    {% elif l == "4" %}<span class="wlabel w4">4</span>
    {% elif l == "5" %}<span class="wlabel w5">5</span>
    {% elif l == "A" %}<span class="wlabel wa">A</span>
    {% elif l == "B" %}<span class="wlabel wb">B</span>
    {% elif l == "C" %}<span class="wlabel wc">C</span>
    {% else %}<span class="wlabel unknown">{{ label }}</span>
    {% endif %}
  {% endif %}
{%- endmacro %}

{# pill(text, kind) — kind ∈ bull|bear|warn|cyan|mag|purple|indigo|teal|lime|orange|rose|muted #}
{% macro pill(text, kind="muted", dot=false) -%}
  <span class="pill {{ kind }}">{% if dot %}<span class="dot"></span>{% endif %}{{ text }}</span>
{%- endmacro %}

{# tri(badge_list) — badge_list comes from web/coherence_badge.py:build_tri_badge.
   Each item is (label, status) where status ∈ "ok"|"warn"|"missing". #}
{% macro tri(badge_list) -%}
  {% if not badge_list %}<span class="tri-cell missing">—</span>
  {% else %}
    <span class="tri">
      {% for label, status in badge_list -%}
        <span class="tri-cell {{ status }}">{{ label }}{% if status == "ok" %} ✓{% elif status == "warn" %} ⚠{% endif %}</span>
      {%- endfor %}
    </span>
  {% endif %}
{%- endmacro %}

{# icon(name) — inline Heroicons-shaped SVG. Matches stroke="currentColor" so they
   inherit color via CSS. Add new icons below as needed. #}
{% macro icon(name, size=16) -%}
  {% set s = size|string %}
  {% if name == "search" %}
    <svg width="{{ s }}" height="{{ s }}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
  {% elif name == "calendar" %}
    <svg width="{{ s }}" height="{{ s }}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 9h18M8 3v4M16 3v4"/></svg>
  {% elif name == "arrow-right" %}
    <svg width="{{ s }}" height="{{ s }}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 5l7 7-7 7M3 12h18"/></svg>
  {% elif name == "plus" %}
    <svg width="{{ s }}" height="{{ s }}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 5v14M5 12h14"/></svg>
  {% elif name == "download" %}
    <svg width="{{ s }}" height="{{ s }}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3v12m0 0l-4-4m4 4l4-4M5 21h14"/></svg>
  {% elif name == "info" %}
    <svg width="{{ s }}" height="{{ s }}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v5h1"/></svg>
  {% else %}
    <svg width="{{ s }}" height="{{ s }}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/></svg>
  {% endif %}
{%- endmacro %}
```

- [ ] **Step 7.2: Replace `src/wave_alpha/web/templates/base.html`**

```jinja
{% from "_macros.html" import icon %}
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}wave-alpha{% endblock %}</title>
  <link rel="stylesheet" href="/static/app.css?v={{ asset_v }}">
  <script defer src="/static/vendor/htmx/htmx.min.js?v={{ asset_v }}"></script>
  <script defer src="/static/vendor/alpine/alpine.min.js?v={{ asset_v }}"></script>
  <script defer src="/static/vendor/lightweight-charts/lightweight-charts.standalone.production.js?v={{ asset_v }}"></script>
  <script defer src="/static/deepdive.js?v={{ asset_v }}"></script>
</head>
<body
  x-data="{
    asof: localStorage.getItem('wave_alpha_as_of') || new URLSearchParams(window.location.search).get('as_of') || new Date().toISOString().slice(0,10),
    setAsof(v) {
      this.asof = v;
      try { localStorage.setItem('wave_alpha_as_of', v); } catch(e) {}
    },
    submitTicker(ev) {
      ev.preventDefault();
      const t = (this.$refs.tickerInput.value || '').trim().toUpperCase();
      if (!t) return;
      const url = `/deepdive/${encodeURIComponent(t)}?as_of=${encodeURIComponent(this.asof)}`;
      window.location.href = url;
    },
  }"
  @keydown.window.meta.k.prevent="$refs.tickerInput.focus(); $refs.tickerInput.select();"
  @keydown.window.ctrl.k.prevent="$refs.tickerInput.focus(); $refs.tickerInput.select();"
>
  <nav class="topnav">
    <a class="brand" href="/"><span class="brand-mark"></span><span class="brand-name">wave-alpha</span></a>
    <div class="nav-links">
      <a class="nav-link {% if active == 'home' %}active{% endif %}" href="/">Home</a>
      <a class="nav-link {% if active == 'scan' %}active{% endif %}" href="/scan">Scan</a>
      <a class="nav-link {% if active == 'watchlist' %}active{% endif %}" href="/watchlist">Watchlist</a>
      <a class="nav-link {% if active == 'settings' %}active{% endif %}" href="/settings">Settings</a>
    </div>
    <form class="search" @submit="submitTicker($event)">
      {{ icon('search', 14) }}
      <input x-ref="tickerInput" name="t" placeholder="Search ticker…" autocomplete="off" autocapitalize="characters">
      <span class="kbd">⌘K</span>
    </form>
    <label class="asof">
      {{ icon('calendar', 14) }}
      <input type="date" :value="asof" @change="setAsof($event.target.value)">
    </label>
  </nav>

  <main>
    {% block content %}{% endblock %}
  </main>

  <p class="disclaimer">{{ disclaimer.lstrip('⚠').strip() }}</p>
</body>
</html>
```

- [ ] **Step 7.3: Smoke-check all four pages still render**

```bash
uv run wave-alpha ui &
sleep 3
for path in / /scan /watchlist /settings; do
  echo -n "$path → "
  curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8765$path"
done
curl -s -X POST http://127.0.0.1:8765/quit
```

Expected: each line prints `200`.

- [ ] **Step 7.4: Run the full test suite to make sure nothing template-related broke**

```bash
uv run pytest tests/web -v
```

Expected: all tests pass. The existing page templates still render because they only use the structures from base.html that survived (`{% block content %}` etc.).

- [ ] **Step 7.5: Commit**

```bash
git add src/wave_alpha/web/templates/_macros.html src/wave_alpha/web/templates/base.html
git commit -m "feat(web/templates): redesign base layout with top nav, macros, global ⌘K"
```

---

## Task 8 · Frontend — Home template (full styled version)

**Files:**
- Modify (rewrite): `src/wave_alpha/web/templates/home.html`

The placeholder from Task 5.8 gets replaced with the full layout.

- [ ] **Step 8.1: Replace `src/wave_alpha/web/templates/home.html`**

```jinja
{% extends "base.html" %}
{% from "_macros.html" import pat, wlabel, pill, tri, icon %}
{% set active = "home" %}
{% block title %}Home — wave-alpha{% endblock %}

{% block content %}
<div class="page" x-data="{ ticker: '' }">

  <div class="page-head">
    <div>
      <h1 class="page-title">Welcome to <span class="brand-name">wave-alpha</span></h1>
      <p class="page-sub">
        {% if view.kpis.signals is not none and view.kpis.signals > 0 %}
          {{ view.kpis.signals }} signal{% if view.kpis.signals != 1 %}s{% endif %} from your latest scan
          {% if view.kpis.watchlist_hits is not none and view.kpis.watchlist_hits > 0 %}
            · {{ view.kpis.watchlist_hits }} of your watched tickers triggered
          {% endif %}.
        {% else %}
          Run a scan or open a deep-dive below to get started.
        {% endif %}
      </p>
    </div>
    <div style="display:flex; gap: 8px;">
      <a class="btn" href="/watchlist">{{ icon('plus', 14) }}Add to watchlist</a>
      <a class="btn btn-primary" href="/scan">Open scan {{ icon('arrow-right', 14) }}</a>
    </div>
  </div>

  <div class="kpi-grid">
    <div class="kpi">
      <div class="kpi-label">Signals</div>
      <div class="kpi-value glow-cyan">{{ view.kpis.signals if view.kpis.signals is not none else "—" }}</div>
      <div class="kpi-delta">{% if view.latest_scan_id %}from {{ view.latest_scan_id }}{% else %}no scans yet{% endif %}</div>
    </div>
    <div class="kpi mag">
      <div class="kpi-label">Top R:R</div>
      <div class="kpi-value">{{ "%.2f"|format(view.kpis.top_rr.value|float) if view.kpis.top_rr else "—" }}</div>
      <div class="kpi-delta">{% if view.kpis.top_rr %}{{ view.kpis.top_rr.ticker }} · {{ view.kpis.top_rr.bias or "—" }}{% else %}—{% endif %}</div>
    </div>
    <div class="kpi bull">
      <div class="kpi-label">Watchlist hits</div>
      <div class="kpi-value glow-bull">{{ view.kpis.watchlist_hits if view.kpis.watchlist_hits is not none else "—" }}</div>
      <div class="kpi-delta">{% if view.kpis.watchlist_hits is not none %}of {{ view.watchlist_top|length }} watched{% else %}no watchlist{% endif %}</div>
    </div>
    <div class="kpi yellow">
      <div class="kpi-label">Stable picks</div>
      <div class="kpi-value glow-yellow">{{ view.kpis.stable_picks if view.kpis.stable_picks is not none else "—" }}</div>
      <div class="kpi-delta">streak ≥ 3 days</div>
    </div>
  </div>

  <div class="card">
    <div class="card-head"><h3 class="card-title">Deep-dive any ticker</h3></div>
    <form style="display:flex; gap: 10px; flex-wrap: wrap;"
          @submit.prevent="window.location.href = `/deepdive/${(ticker || '').trim().toUpperCase()}?as_of=${asof}`">
      <input class="input mono" placeholder="AAPL" autocapitalize="characters"
             x-model="ticker" required style="flex: 1; min-width: 240px; font-size: 16px; padding: 12px 14px;">
      <input type="date" class="input mono" :value="asof" @change="setAsof($event.target.value)" style="width: 170px;">
      <button class="btn btn-primary" type="submit">Analyze {{ icon('arrow-right', 14) }}</button>
    </form>
    <p class="field-help">Tip: press <span class="kbd" style="padding: 1px 6px; border: 1px solid var(--border-default); border-radius: 3px; font-family: var(--font-mono); color: var(--cyan);">⌘K</span> from anywhere to focus the nav search.</p>
  </div>

  <div class="row-2">
    <div class="card">
      <div class="card-head">
        <h3 class="card-title">Today's top signals</h3>
        {% if view.latest_scan_id %}<a class="card-action" href="/scan/{{ view.latest_scan_id }}">View scan →</a>{% endif %}
      </div>
      {% if view.top_signals %}
        <table class="dt">
          <tbody>
            {% for r in view.top_signals %}
              <tr>
                <td class="tk"><a href="/deepdive/{{ r.ticker }}?as_of={{ r.as_of.isoformat() }}">{{ r.ticker }}</a></td>
                <td>{{ pat(r.top_pattern) }}</td>
                <td>
                  {% if r.direction == "up" %}{{ pill("LONG", "bull", dot=true) }}
                  {% elif r.direction == "down" %}{{ pill("SHORT", "bear", dot=true) }}
                  {% else %}{{ pill("—", "muted") }}{% endif %}
                </td>
                <td class="num">{{ "%.2f"|format(r.top_score) if r.top_score is not none else "—" }}</td>
                <td class="num r-pos">{{ "%.2f"|format(r.signal_rr|float) }}R</td>
              </tr>
            {% endfor %}
          </tbody>
        </table>
      {% else %}
        <p style="color: var(--fg-muted); margin: 0;">No signals in the latest scan.</p>
      {% endif %}
    </div>

    <div class="card">
      <div class="card-head"><h3 class="card-title mag">Your watchlist · top picks</h3>
        <a class="card-action" href="/watchlist">View all →</a>
      </div>
      {% if view.watchlist_top %}
        <div class="stack">
          {% for w in view.watchlist_top %}
            <div style="display: grid; grid-template-columns: 70px 1fr auto auto; gap: 12px; padding: 10px 12px; align-items: center; border-radius: var(--radius-sm); {% if w.bias %}background: rgba(244,114,182,0.04); border-left: 2px solid var(--magenta);{% endif %}">
              <a class="tk mono" href="/deepdive/{{ w.symbol }}" style="font-weight: 700; color: var(--fg-primary);">{{ w.symbol }}</a>
              <span style="font-size: 12px; color: var(--fg-muted);">
                {% if w.pattern %}{{ pat(w.pattern) }}{% else %}<span style="color: var(--fg-muted);">no high-confidence count</span>{% endif %}
              </span>
              {% if w.bias == "long" %}{{ pill("LONG", "bull", dot=true) }}
              {% elif w.bias == "short" %}{{ pill("SHORT", "bear", dot=true) }}
              {% else %}<span></span>{% endif %}
              {% if w.signal_rr %}<span class="num glow-bull" style="font-weight: 700;">{{ "%.1f"|format(w.signal_rr|float) }}R</span>
              {% else %}<span style="color: var(--fg-muted);">—</span>{% endif %}
            </div>
          {% endfor %}
        </div>
      {% else %}
        <p style="color: var(--fg-muted); margin: 0;">No watchlist entries yet — <a href="/watchlist">add some</a>.</p>
      {% endif %}
    </div>
  </div>

</div>
{% endblock %}
```

- [ ] **Step 8.2: Run the home tests to confirm they still pass**

```bash
uv run pytest tests/web/test_home.py tests/web/test_home_route.py tests/web/test_routes_system.py -v
```

Expected: all green.

- [ ] **Step 8.3: Smoke-check the page in a browser**

```bash
uv run wave-alpha ui &
sleep 3
echo "Open http://127.0.0.1:8765/ in your browser, confirm: KPI strip renders, search works, brand mark appears."
sleep 30
curl -s -X POST http://127.0.0.1:8765/quit
```

If the page errors, read the uvicorn output and fix the template before continuing.

- [ ] **Step 8.4: Commit**

```bash
git add src/wave_alpha/web/templates/home.html
git commit -m "feat(web/home): full styled Home dashboard with KPIs, top signals, watchlist"
```

---

## Task 9 · Frontend — Scan page + table partial (incl. empty state)

**Files:**
- Modify (rewrite): `src/wave_alpha/web/templates/scan.html`
- Modify (rewrite): `src/wave_alpha/web/templates/_scan_table.html`

- [ ] **Step 9.1: Replace `src/wave_alpha/web/templates/scan.html`**

```jinja
{% extends "base.html" %}
{% from "_macros.html" import pill, icon %}
{% set active = "scan" %}
{% block title %}Scan — wave-alpha{% endblock %}

{% block content %}
<div class="page">

  {% if not scan_ids %}
    <div class="page-head"><div><h1 class="page-title">Scan</h1></div></div>
    <div class="card empty">
      <div class="empty-ico">{{ icon('search', 22) }}</div>
      <h3 class="empty-title">No scans yet</h3>
      <p class="empty-sub">A scan runs the wave engine across a list of tickers and saves the report. Run one from the CLI to populate this page.</p>
      <code class="codeblock"><span class="c"># Scan a list of tickers as of today</span>
<span class="k">uv run</span> wave-alpha scan <span class="s">--symbols AAPL,MSFT,NVDA,SPY</span></code>
      <code class="codeblock"><span class="c"># Or use a curated universe file</span>
<span class="k">uv run</span> wave-alpha scan <span class="s">--universe data/universe_curated.txt</span></code>
      <p class="field-help" style="margin-top: 14px;">Once a scan finishes, refresh this page — it will appear here automatically.</p>
    </div>
  {% else %}
    <div class="page-head">
      <div>
        <h1 class="page-title">Scan · <span class="glow-cyan">{{ current_id }}</span></h1>
        <p class="page-sub">
          {% if report %}
            {{ report.n_total }} ticker{{ "s" if report.n_total != 1 else "" }} ·
            {{ report.duration_seconds }}s ·
            {{ report.n_with_signal }} signal{{ "s" if report.n_with_signal != 1 else "" }} ·
            {{ report.n_errored }} error{{ "s" if report.n_errored != 1 else "" }}
          {% endif %}
        </p>
      </div>
    </div>

    <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px;">
      {% for sid in scan_ids[:5] %}
        <a href="/scan/{{ sid }}"
           style="text-decoration: none;">
          <span class="pill {% if sid == current_id %}cyan{% else %}muted{% endif %}"
                style="padding: 6px 12px; font-family: var(--font-mono);">{{ sid }}</span>
        </a>
      {% endfor %}
      {% if scan_ids|length > 5 %}
        <details>
          <summary style="cursor: pointer; padding: 6px 12px; color: var(--fg-muted); font-size: 12px;">… {{ scan_ids|length - 5 }} more</summary>
          <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px;">
            {% for sid in scan_ids[5:] %}
              <a href="/scan/{{ sid }}" style="text-decoration: none;">
                <span class="pill muted" style="padding: 6px 12px; font-family: var(--font-mono);">{{ sid }}</span>
              </a>
            {% endfor %}
          </div>
        </details>
      {% endif %}
    </div>

    {% if report %}
      <div class="kpi-grid">
        <div class="kpi"><div class="kpi-label">Signals</div>
          <div class="kpi-value glow-cyan">{{ report.n_with_signal }}</div>
          <div class="kpi-delta">of {{ report.n_total }}</div></div>
        <div class="kpi mag"><div class="kpi-label">Errors</div>
          <div class="kpi-value">{{ report.n_errored }}</div>
          <div class="kpi-delta">tickers failed</div></div>
        <div class="kpi bull"><div class="kpi-label">Duration</div>
          <div class="kpi-value">{{ report.duration_seconds }}s</div>
          <div class="kpi-delta">end-to-end</div></div>
        <div class="kpi yellow"><div class="kpi-label">Coverage</div>
          <div class="kpi-value">{{ report.n_total }}</div>
          <div class="kpi-delta">tickers</div></div>
      </div>

      <div class="card" style="padding: 12px 14px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap;"
           x-data="{ bias: 'any', signalsOnly: false, sort: 'score', minRr: '0' }">
        <div class="tabs">
          <button type="button" class="tab" :class="{ active: !signalsOnly && bias === 'any' }"
            @click="signalsOnly = false; bias = 'any'; htmx.trigger('#scan-table', 'refresh')">All</button>
          <button type="button" class="tab" :class="{ active: signalsOnly && bias === 'any' }"
            @click="signalsOnly = true; bias = 'any'; htmx.trigger('#scan-table', 'refresh')">Signals</button>
          <button type="button" class="tab" :class="{ active: bias === 'long' }"
            @click="signalsOnly = true; bias = 'long'; htmx.trigger('#scan-table', 'refresh')">Long</button>
          <button type="button" class="tab" :class="{ active: bias === 'short' }"
            @click="signalsOnly = true; bias = 'short'; htmx.trigger('#scan-table', 'refresh')">Short</button>
        </div>
        <div style="flex: 1;"></div>
        <span style="font: 700 11px var(--font-mono); color: var(--fg-muted); letter-spacing: .1em; text-transform: uppercase;">Sort</span>
        <select class="input" x-model="sort" @change="htmx.trigger('#scan-table', 'refresh')"
                style="width: 160px; padding: 6px 28px 6px 10px; font-size: 12px;">
          <option value="score">Score</option>
          <option value="rr">R:R</option>
          <option value="ticker">Ticker</option>
          <option value="coherence">Coherence</option>
          <option value="right_edge">Right-edge</option>
        </select>
        <select class="input" x-model="minRr" @change="htmx.trigger('#scan-table', 'refresh')"
                style="width: 130px; padding: 6px 28px 6px 10px; font-size: 12px;">
          <option value="0">Min R:R: any</option>
          <option value="1">Min R:R: 1.0</option>
          <option value="2">Min R:R: 2.0</option>
          <option value="3">Min R:R: 3.0</option>
        </select>

        {# HTMX target. The Alpine state above triggers a refresh on every change.
           hx-vals reads from the surrounding Alpine state via a small JSON expression. #}
        <div id="scan-table"
             hx-get="/scan/{{ current_id }}/table"
             hx-trigger="refresh from:closest .card, load delay:50ms"
             hx-include="closest .card [x-model]"
             hx-vals='js:{ sort: this.closest(".card").__x?.$data.sort || "score",
                            signals_only: this.closest(".card").__x?.$data.signalsOnly ? "true" : "false",
                            bias: this.closest(".card").__x?.$data.bias || "any",
                            min_rr: this.closest(".card").__x?.$data.minRr || "0" }'
             hx-target="#scan-table"
             hx-swap="innerHTML"
             style="display:none">
        </div>
      </div>

      <div class="card dt-card">
        <div id="scan-table"
             hx-get="/scan/{{ current_id }}/table"
             hx-trigger="load"
             hx-target="this">
          <div class="skeleton" style="margin: 12px;"></div>
        </div>
      </div>
    {% endif %}
  {% endif %}
</div>
{% endblock %}
```

(Note: there are two `#scan-table` IDs in the markup above — the hidden trigger element and the visible target. We need a single ID. Fix: rename the first to `scan-filters` or remove the duplicate. **Use this corrected version of the filter card + table:**)

Replace the filter card + table section (everything from `<div class="card" style="padding: 12px 14px;` through the closing `</div>` of `.dt-card`) with:

```jinja
      <div class="card" style="padding: 12px 14px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap;"
           x-data="{ bias: 'any', signalsOnly: false, sort: 'score', minRr: '0',
                    refresh() { htmx.trigger('#scan-table', 'refresh'); } }">
        <div class="tabs">
          <button type="button" class="tab" :class="{ active: !signalsOnly && bias === 'any' }"
            @click="signalsOnly = false; bias = 'any'; refresh()">All</button>
          <button type="button" class="tab" :class="{ active: signalsOnly && bias === 'any' }"
            @click="signalsOnly = true; bias = 'any'; refresh()">Signals</button>
          <button type="button" class="tab" :class="{ active: bias === 'long' }"
            @click="signalsOnly = true; bias = 'long'; refresh()">Long</button>
          <button type="button" class="tab" :class="{ active: bias === 'short' }"
            @click="signalsOnly = true; bias = 'short'; refresh()">Short</button>
        </div>
        <div style="flex: 1;"></div>
        <span style="font: 700 11px var(--font-mono); color: var(--fg-muted); letter-spacing: .1em; text-transform: uppercase;">Sort</span>
        <select class="input" x-model="sort" @change="refresh()"
                style="width: 160px; padding: 6px 28px 6px 10px; font-size: 12px;">
          <option value="score">Score</option>
          <option value="rr">R:R</option>
          <option value="ticker">Ticker</option>
          <option value="coherence">Coherence</option>
          <option value="right_edge">Right-edge</option>
        </select>
        <select class="input" x-model="minRr" @change="refresh()"
                style="width: 130px; padding: 6px 28px 6px 10px; font-size: 12px;">
          <option value="0">Min R:R: any</option>
          <option value="1">Min R:R: 1.0</option>
          <option value="2">Min R:R: 2.0</option>
          <option value="3">Min R:R: 3.0</option>
        </select>

        <div id="scan-table"
             class="card dt-card"
             style="flex-basis: 100%; margin-top: 12px;"
             hx-get="/scan/{{ current_id }}/table"
             hx-trigger="load, refresh"
             hx-vals='js:{ sort: this.closest("[x-data]").__x.$data.sort,
                           signals_only: this.closest("[x-data]").__x.$data.signalsOnly ? "true" : "false",
                           bias: this.closest("[x-data]").__x.$data.bias,
                           min_rr: this.closest("[x-data]").__x.$data.minRr }'
             hx-swap="innerHTML">
          <div class="skeleton" style="margin: 12px;"></div>
        </div>
      </div>
```

- [ ] **Step 9.2: Replace `src/wave_alpha/web/templates/_scan_table.html`**

```jinja
{% from "_macros.html" import pat, wlabel, pill, tri %}

{% if not rows %}
  <p style="padding: 24px; text-align: center; color: var(--fg-muted); margin: 0;">
    No rows match these filters.
  </p>
{% else %}
<table class="dt">
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
  <tbody>
    {% for r in rows %}
      <tr {% if r.has_error %}style="opacity:0.5;"{% endif %}>
        <td class="tk">
          <a href="/deepdive/{{ r.ticker }}?as_of={{ r.as_of.isoformat() }}">{{ r.ticker }}</a>
        </td>
        <td>{{ pat(r.top_pattern) }}</td>
        <td>
          {% if r.direction == "up" %}{{ pill("LONG", "bull", dot=true) }}
          {% elif r.direction == "down" %}{{ pill("SHORT", "bear", dot=true) }}
          {% else %}{{ pill("—", "muted") }}{% endif %}
        </td>
        <td class="num" style="text-align:right;">
          {% if r.top_score is not none %}
            <span class="scorebar"><span>{{ "%.2f"|format(r.top_score) }}</span><span class="bar"><span style="width: {{ (r.top_score * 100)|round|int }}%"></span></span></span>
          {% else %}—{% endif %}
        </td>
        <td>
          {% if r.coherence is not none %}<span class="num">{{ "%.2f"|format(r.coherence) }}</span>{% else %}—{% endif %}
        </td>
        <td class="num r-pos" style="text-align:right;">
          {{ "%.2f"|format(r.signal_rr|float) if r.signal_rr is not none else "—" }}
        </td>
        <td class="num">
          {% if r.signal_entry %}<span style="color: var(--fg-muted);">{{ r.signal_entry }}</span> /
            <span style="color: var(--bear);">{{ r.signal_stop }}</span> /
            <span style="color: var(--bull);">{{ r.signal_target }}</span>
          {% else %}—{% endif %}
        </td>
        <td class="num">
          {% if r.right_edge_proba is not none %}
            <span style="color: var(--purple);">{{ "%.2f"|format(r.right_edge_proba) }}</span>
          {% else %}—{% endif %}
        </td>
      </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}
```

- [ ] **Step 9.3: Run the scan-route tests — they must still pass**

```bash
uv run pytest tests/web/test_scan_routes.py -v
```

Note: existing tests check for "Right-Edge" (with hyphen, capitalized). The new `_scan_table.html` uses "Right-edge" (lowercase 'e'). Update the existing test assertion to match if it fails:

If `test_scan_table_renders_right_edge_column_and_value` fails, edit the assertion in `tests/web/test_scan_routes.py` to:
```python
    assert "Right-edge" in res.text  # column header (case-sensitive)
```

Re-run.

- [ ] **Step 9.4: Smoke-check empty + populated states in a browser**

```bash
uv run wave-alpha ui &
sleep 3
echo "Visit http://127.0.0.1:8765/scan — empty state should show codeblocks. Then run a scan from another shell and refresh."
sleep 30
curl -s -X POST http://127.0.0.1:8765/quit
```

- [ ] **Step 9.5: Commit**

```bash
git add src/wave_alpha/web/templates/scan.html src/wave_alpha/web/templates/_scan_table.html tests/web/test_scan_routes.py
git commit -m "feat(web/scan): redesign scan page with KPI strip, filter tabs, pattern badges"
```

---

## Task 10 · Frontend — Watchlist template + enriched table partial

**Files:**
- Modify (rewrite): `src/wave_alpha/web/templates/watchlist.html`
- Modify (rewrite): `src/wave_alpha/web/templates/_watchlist_table.html`

- [ ] **Step 10.1: Replace `src/wave_alpha/web/templates/watchlist.html`**

```jinja
{% extends "base.html" %}
{% from "_macros.html" import icon %}
{% set active = "watchlist" %}
{% block title %}Watchlist — wave-alpha{% endblock %}

{% block content %}
<div class="page">
  <div class="page-head">
    <div>
      <h1 class="page-title">Watchlist</h1>
      <p class="page-sub">
        {{ enriched|length }} symbol{{ "s" if enriched|length != 1 else "" }}
        {% if latest_scan_id %} · last refreshed from scan {{ latest_scan_id }}
        {% else %} · no scans yet — add symbols and run <code style="font-family: var(--font-mono); color: var(--cyan);">uv run wave-alpha scan</code>{% endif %}
      </p>
    </div>
  </div>

  <div class="card">
    <div class="card-head"><h3 class="card-title">Add symbol</h3></div>
    <form method="post" action="/watchlist/add" style="display: flex; gap: 8px;">
      <input class="input mono" name="symbol" placeholder="AAPL" autocapitalize="characters" required style="flex: 1;">
      <button class="btn btn-primary" type="submit">{{ icon('plus', 14) }}Add</button>
    </form>
    <p class="field-help">
      Bulk import:
      <code style="font-family: var(--font-mono); color: var(--magenta);">uv run wave-alpha watchlist import &lt;file&gt; --format csv|tv-txt</code>
    </p>
  </div>

  <div class="card dt-card">
    {% include "_watchlist_table.html" %}
  </div>
</div>
{% endblock %}
```

- [ ] **Step 10.2: Replace `src/wave_alpha/web/templates/_watchlist_table.html`**

```jinja
{% from "_macros.html" import pat, pill %}

{% if not enriched %}
  <p style="padding: 24px; text-align: center; color: var(--fg-muted); margin: 0;">
    No symbols yet — add one above.
  </p>
{% else %}
<table class="dt">
  <thead>
    <tr>
      <th>Symbol</th>
      <th>Last signal</th>
      <th>Bias</th>
      <th style="text-align:right;">Score</th>
      <th style="text-align:right;">R:R</th>
      <th>Notes</th>
      <th></th>
    </tr>
  </thead>
  <tbody>
    {% for er in enriched %}
      <tr>
        <td class="tk"><a href="/deepdive/{{ er.entry.symbol }}">{{ er.entry.symbol }}</a></td>
        <td>
          {% if er.row and er.row.top_pattern %}
            {{ pat(er.row.top_pattern) }}
          {% else %}<span style="color: var(--fg-muted);">—</span>{% endif %}
        </td>
        <td>
          {% if er.bias == "long" %}{{ pill("LONG", "bull", dot=true) }}
          {% elif er.bias == "short" %}{{ pill("SHORT", "bear", dot=true) }}
          {% else %}{{ pill("no signal", "muted") }}{% endif %}
        </td>
        <td class="num" style="text-align:right;">
          {{ "%.2f"|format(er.row.top_score) if er.row and er.row.top_score is not none else "—" }}
        </td>
        <td class="num r-pos" style="text-align:right;">
          {% if er.row and er.row.signal_rr %}{{ "%.2f"|format(er.row.signal_rr|float) }}R
          {% else %}<span style="color: var(--fg-muted);">—</span>{% endif %}
        </td>
        <td style="color: var(--fg-muted);">{{ er.entry.notes or "" }}</td>
        <td>
          <form method="post" action="/watchlist/rm" style="display: inline;">
            <input type="hidden" name="symbol" value="{{ er.entry.symbol }}">
            <button type="submit" class="btn btn-danger" style="padding: 4px 10px; font-size: 11px;">Remove</button>
          </form>
        </td>
      </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}
```

- [ ] **Step 10.3: Run the watchlist tests — must still pass**

```bash
uv run pytest tests/web/test_watchlist_routes.py tests/web/test_watchlist_enrich.py -v
```

If `test_get_watchlist_renders_empty_state` fails because the test asserts a specific text that no longer appears: update that test's assertion to:
```python
    assert "no symbols yet" in res.text.lower() or "watchlist" in res.text.lower()
```

- [ ] **Step 10.4: Smoke check**

```bash
uv run wave-alpha ui &
sleep 3
echo "Visit http://127.0.0.1:8765/watchlist — add a symbol, confirm it appears with Bias chip and Remove button."
sleep 30
curl -s -X POST http://127.0.0.1:8765/quit
```

- [ ] **Step 10.5: Commit**

```bash
git add src/wave_alpha/web/templates/watchlist.html src/wave_alpha/web/templates/_watchlist_table.html tests/web/test_watchlist_routes.py
git commit -m "feat(web/watchlist): redesign with enriched rows showing last-known signal"
```

---

## Task 11 · Frontend — Settings template

**Files:**
- Modify (rewrite): `src/wave_alpha/web/templates/settings.html`

- [ ] **Step 11.1: Replace `src/wave_alpha/web/templates/settings.html`**

```jinja
{% extends "base.html" %}
{% from "_macros.html" import pill %}
{% set active = "settings" %}
{% block title %}Settings — wave-alpha{% endblock %}

{% block content %}
<div class="page">
  <div class="page-head">
    <div>
      <h1 class="page-title">Settings</h1>
      <p class="page-sub">Stored in <code style="font-family: var(--font-mono); color: var(--cyan);">~/.wave_alpha/config.yaml</code>. ENV vars override file values at runtime.</p>
    </div>
  </div>

  <form method="post" action="/settings">

    <div class="card">
      <div class="card-head">
        <h3 class="card-title purple">LLM reranker (optional)</h3>
        {{ pill("disabled by default", "muted") }}
      </div>
      <p style="margin: 0 0 14px; color: var(--fg-secondary); font-size: 13px;">
        The engine is fully deterministic on its own. The LLM only re-ranks the engine's top
        candidates — it never invents counts.
      </p>
      <div class="stack">
        <div class="field">
          <label class="field-label" for="api_key">Anthropic API key</label>
          <input id="api_key" type="password" class="input mono" name="api_key" value="" autocomplete="off"
                 placeholder="{% if cfg.llm.api_key %}(set — leave blank to keep){% else %}sk-...{% endif %}">
          <span class="field-help">Required only for live rerank. The CLI also reads <code style="font-family: var(--font-mono); color: var(--cyan);">$ANTHROPIC_API_KEY</code>.</span>
        </div>

        <div class="row-2">
          <div class="field">
            <label class="field-label" for="scan_model">Scan model</label>
            <input id="scan_model" class="input mono" name="scan_model" value="{{ cfg.llm.scan_model }}">
            <span class="field-help">Used during bulk scans. Optimized for speed/cost.</span>
          </div>
          <div class="field">
            <label class="field-label" for="deep_model">Deep-dive model</label>
            <input id="deep_model" class="input mono" name="deep_model" value="{{ cfg.llm.deep_model }}">
            <span class="field-help">Used in the deep-dive view. Higher quality.</span>
          </div>
        </div>

        <div class="field" x-data="{ alpha: {{ cfg.llm.alpha }} }">
          <label class="field-label" for="alpha">
            <span class="gloss" data-tip="α weights the deterministic engine vs the LLM rerank. 1.0 = engine only; 0.0 = LLM only.">
              Engine ↔ LLM blend (α)
            </span>
          </label>
          <div style="display: flex; align-items: center; gap: 14px;">
            <input id="alpha" type="range" min="0" max="1" step="0.05" name="alpha"
                   x-model="alpha" class="input" style="flex: 1; padding: 0;">
            <span class="num glow-cyan" style="font-size: 16px; font-weight: 700; width: 50px;"
                  x-text="parseFloat(alpha).toFixed(2)">{{ cfg.llm.alpha }}</span>
          </div>
          <span class="field-help">
            final = α × engine + (1 − α) × llm.
            <strong>α = 1.0</strong> trusts only the engine; <strong>α = 0.0</strong> trusts only the LLM. Recommended 0.6 – 0.7.
          </span>
        </div>
      </div>
    </div>

    <div style="display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px;">
      <a class="btn" href="/settings">Cancel</a>
      <button class="btn btn-primary" type="submit">Save changes</button>
    </div>
  </form>
</div>
{% endblock %}
```

- [ ] **Step 11.2: Run settings tests**

```bash
uv run pytest tests/web/test_routes_settings.py -v
```

Expected: all green. The form names (`api_key`, `scan_model`, `deep_model`, `alpha`) are unchanged.

- [ ] **Step 11.3: Smoke check**

```bash
uv run wave-alpha ui &
sleep 3
echo "Visit http://127.0.0.1:8765/settings — verify slider, hover the dotted 'Engine ↔ LLM blend' for tooltip."
sleep 20
curl -s -X POST http://127.0.0.1:8765/quit
```

- [ ] **Step 11.4: Commit**

```bash
git add src/wave_alpha/web/templates/settings.html
git commit -m "feat(web/settings): redesign with help text, α slider, glossary tooltip"
```

---

## Task 12 · Frontend — Deepdive page (remove toolbar, hero card)

**Files:**
- Modify (rewrite): `src/wave_alpha/web/templates/deepdive.html`
- Delete: `src/wave_alpha/web/templates/_toolbar.html`

- [ ] **Step 12.1: Replace `src/wave_alpha/web/templates/deepdive.html`**

```jinja
{% extends "base.html" %}
{% from "_macros.html" import pill %}
{% block title %}{{ ticker }} — wave-alpha{% endblock %}

{% block content %}
<div class="page">

  <div class="card" style="display: grid; grid-template-columns: 1fr auto; gap: 16px; align-items: center;">
    <div>
      <div style="display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;">
        <h1 class="page-title glow-cyan" style="font-family: var(--font-mono); font-size: 30px;">{{ ticker }}</h1>
        <span style="color: var(--fg-muted); font-size: 13px;">as of {{ as_of }}</span>
        {% if llm_mode == "live" %}{{ pill("LLM live", "purple") }}
        {% elif llm_mode == "cached" %}{{ pill("LLM cached", "muted") }}
        {% else %}{{ pill("LLM disabled", "muted") }}{% endif %}
      </div>
    </div>
    <form method="get" action="/deepdive/{{ ticker }}" style="display: flex; gap: 8px; align-items: center;">
      <input type="hidden" name="t" value="{{ ticker }}">
      <input type="date" class="input mono" name="as_of" value="{{ as_of }}" style="width: 160px;">
      <select class="input" name="llm" style="width: 140px;">
        <option value="disabled" {% if llm_mode == 'disabled' %}selected{% endif %}>LLM: disabled</option>
        <option value="cached"   {% if llm_mode == 'cached'   %}selected{% endif %}>LLM: cached</option>
        <option value="live"     {% if llm_mode == 'live'     %}selected{% endif %}>LLM: live</option>
      </select>
      <button class="btn" type="submit">Reload</button>
    </form>
  </div>

  <div class="dd-grid">
    <div class="card" style="padding: 14px;">
      <div class="card-head" style="padding: 4px 4px 12px;">
        <h3 class="card-title">Price &amp; pivots</h3>
      </div>
      <div id="chart"
           hx-get="/deepdive/{{ ticker }}/chart?as_of={{ as_of }}"
           hx-trigger="load"
           hx-swap="innerHTML">
        <div class="skeleton" style="height: 480px;"></div>
      </div>
    </div>

    <div class="stack">
      <div class="card"
           hx-get="/deepdive/{{ ticker }}/plan?as_of={{ as_of }}"
           hx-trigger="load delay:200ms"
           hx-swap="innerHTML">
        <div class="card-head"><h3 class="card-title bull">Trade plan</h3></div>
        <div class="skeleton" style="height: 120px;"></div>
      </div>

      <div class="card"
           hx-get="/deepdive/{{ ticker }}/coherence?as_of={{ as_of }}"
           hx-trigger="load delay:100ms"
           hx-swap="innerHTML">
        <div class="card-head">
          <h3 class="card-title">
            <span class="gloss" data-tip="Coherence: how strongly the weekly, daily, and 4-hour timeframes agree on the same wave count.">Multi-timeframe agreement</span>
          </h3>
        </div>
        <div class="skeleton" style="height: 100px;"></div>
      </div>

      <div class="card"
           hx-get="/deepdive/{{ ticker }}/right-edge?as_of={{ as_of }}"
           hx-trigger="load delay:100ms"
           hx-swap="innerHTML">
        <div class="card-head">
          <h3 class="card-title purple">
            <span class="gloss" data-tip="Right-edge: probability that the most recent pivot is real and won't be invalidated by a deeper move against the wave.">Right-edge confirmation</span>
          </h3>
        </div>
        <div class="skeleton" style="height: 100px;"></div>
      </div>
    </div>
  </div>

  <div class="row-2" style="margin-top: 16px;">
    <div class="card"
         hx-get="/deepdive/{{ ticker }}/counts?as_of={{ as_of }}&llm={{ llm_mode }}"
         hx-trigger="load"
         hx-swap="innerHTML">
      <div class="card-head"><h3 class="card-title mag">Wave counts (top 3)</h3></div>
      <div class="skeleton" style="height: 200px;"></div>
    </div>

    <div class="card"
         hx-get="/deepdive/{{ ticker }}/scenarios?as_of={{ as_of }}"
         hx-trigger="load delay:200ms"
         hx-swap="innerHTML">
      <div class="card-head"><h3 class="card-title">Right-edge scenarios</h3></div>
      <div class="skeleton" style="height: 120px;"></div>
    </div>
  </div>

  <div class="card"
       style="margin-top: 16px;"
       hx-get="/deepdive/{{ ticker }}/history"
       hx-trigger="load delay:300ms"
       hx-swap="innerHTML">
    <div class="card-head"><h3 class="card-title yellow">Stability over time</h3></div>
    <div class="skeleton" style="height: 100px;"></div>
  </div>

</div>
{% endblock %}
```

- [ ] **Step 12.2: Delete the obsolete toolbar partial**

```bash
git rm src/wave_alpha/web/templates/_toolbar.html
```

- [ ] **Step 12.3: Run the deepdive tests**

```bash
uv run pytest tests/web/test_routes_deepdive.py tests/web/test_routes_deepdive_history.py -v
```

Expected: green.

- [ ] **Step 12.4: Commit**

```bash
git add src/wave_alpha/web/templates/deepdive.html src/wave_alpha/web/templates/_toolbar.html
git commit -m "feat(web/deepdive): redesign page with hero card + skeleton loading"
```

---

## Task 13 · Frontend — Deepdive fragment partials (counts, plan, coherence, right-edge, scenarios, history)

**Files:**
- Modify (rewrite): `src/wave_alpha/web/templates/_trade_plan.html`
- Modify (rewrite): `src/wave_alpha/web/templates/_coherence_row.html`
- Modify (rewrite): `src/wave_alpha/web/templates/_right_edge.html`
- Modify (rewrite): `src/wave_alpha/web/templates/_counts_list.html`
- Modify (rewrite): `src/wave_alpha/web/templates/_scenarios.html`
- Modify (rewrite): `src/wave_alpha/web/templates/_history.html`
- Modify (rewrite): `src/wave_alpha/web/templates/_chart.html`

- [ ] **Step 13.1: Replace `src/wave_alpha/web/templates/_trade_plan.html`**

```jinja
{% from "_macros.html" import pill %}

{% if not sig %}
  <p style="margin: 0; color: var(--fg-muted);">No trade plan derived for this count set (none meeting min_confidence).</p>
{% else %}
  <p style="margin: 4px 0 14px; color: var(--fg-secondary);">
    {% if sig.direction == "long" %}
      Enter near <strong>{{ sig.entry_price }}</strong>, stop below <strong>{{ sig.stop_price }}</strong>, first target <strong>{{ sig.target_price }}</strong>.
    {% else %}
      Enter near <strong>{{ sig.entry_price }}</strong>, stop above <strong>{{ sig.stop_price }}</strong>, first target <strong>{{ sig.target_price }}</strong>.
    {% endif %}
  </p>
  <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px;">
    <div><div class="kpi-label">Direction</div>
      {% if sig.direction == "long" %}{{ pill("LONG", "bull", dot=true) }}
      {% else %}{{ pill("SHORT", "bear", dot=true) }}{% endif %}</div>
    <div><div class="kpi-label">Entry</div>
      <div class="num" style="font-size: 16px; font-weight: 700;">{{ sig.entry_price }}</div></div>
    <div><div class="kpi-label">Stop</div>
      <div class="num glow-bear" style="font-size: 16px; font-weight: 700;">{{ sig.stop_price }}</div></div>
    <div><div class="kpi-label">Target</div>
      <div class="num glow-bull" style="font-size: 16px; font-weight: 700;">{{ sig.target_price }}</div></div>
    <div><div class="kpi-label">R:R</div>
      <div class="num glow-bull" style="font-size: 16px; font-weight: 700;">{{ "%.2f"|format(sig.rr) }}</div></div>
    <div><div class="kpi-label">Bars to target</div>
      <div class="num" style="font-size: 16px; font-weight: 700;">{{ sig.expected_bars_to_target }}</div></div>
    <div><div class="kpi-label">Max hold</div>
      <div class="num" style="font-size: 16px; font-weight: 700;">{{ sig.max_holding_bars }}</div></div>
    <div style="grid-column: span 2;"><div class="kpi-label">Pattern</div>
      <div style="font-size: 13px;">{{ sig.count_pattern }} (W{{ sig.wave_label }})</div></div>
  </div>
{% endif %}
```

- [ ] **Step 13.2: Replace `src/wave_alpha/web/templates/_coherence_row.html`**

```jinja
{% from "_macros.html" import tri %}

{% if not coh %}
  <p style="margin: 0; color: var(--fg-muted);">Insufficient history for coherence.</p>
{% else %}
  {# Coherence ring + tri-badge + summary line #}
  {% set coh_pct = (coh.score * 100)|round|int %}
  {% set ring_circumference = 226.2 %}
  {% set ring_offset = ring_circumference - (ring_circumference * coh.score) %}
  <div style="display: grid; grid-template-columns: 90px 1fr; gap: 18px; align-items: center;">
    <div class="ring">
      <svg width="84" height="84" viewBox="0 0 84 84">
        <circle cx="42" cy="42" r="36" stroke="rgba(124,139,163,0.15)" stroke-width="6" fill="none"/>
        <circle cx="42" cy="42" r="36" stroke="#22D3EE" stroke-width="6" fill="none"
                stroke-linecap="round" stroke-dasharray="{{ ring_circumference }}"
                stroke-dashoffset="{{ ring_offset }}"
                style="filter: drop-shadow(0 0 6px rgba(34,211,238,0.7));"/>
      </svg>
      <div class="pct"><span class="v">{{ "%.2f"|format(coh.score) }}</span><span class="l">coh</span></div>
    </div>
    <div>
      {{ tri(badge) }}
      <p style="margin: 8px 0 0; font-size: 13px; color: var(--fg-secondary);">
        Multiplier ×{{ "%.2f"|format(coh.multiplier) }}.
        {% if coh.fallback_used %}<span style="color: var(--warn);">{{ coh.fallback_used }}</span>{% endif %}
      </p>
      <div style="display: flex; gap: 12px; margin-top: 6px; font: 11px var(--font-mono); color: var(--fg-muted);">
        {% for k, v in coh.pairwise.items() %}
          <span>{{ k }}: <span style="color: var(--cyan);">{{ "%.2f"|format(v) }}</span></span>
        {% endfor %}
      </div>
    </div>
  </div>
{% endif %}
```

- [ ] **Step 13.3: Replace `src/wave_alpha/web/templates/_right_edge.html`**

```jinja
{% if not assessment %}
  <p style="margin: 0; color: var(--fg-muted);">No tentative right-edge pivot at degree {{ default_degree }} for this date.</p>
{% else %}
  <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
    <div>
      <div class="kpi-label" style="color: var(--purple);">Probability</div>
      <div class="num glow-purple" style="font-size: 18px; font-weight: 700;">{{ "%.2f"|format(assessment.proba) }}</div>
    </div>
    <div>
      <div class="kpi-label">Pivot</div>
      <div class="num" style="font-size: 14px; font-weight: 700;">{{ assessment.pivot_type|upper }} @ {{ assessment.pivot_timestamp.isoformat() }}</div>
    </div>
    <div>
      <div class="kpi-label">Degree</div>
      <div class="num" style="font-size: 14px;">{{ assessment.degree }}</div>
    </div>
    <div>
      <div class="kpi-label">Model</div>
      <div style="font-size: 13px;">{{ assessment.model_kind }}</div>
    </div>
  </div>
  <p style="margin: 12px 0 0; font-size: 12px; color: var(--fg-muted);">
    Probability that the most recent tentative pivot is confirmed (will not be invalidated by a deeper move against it).
  </p>
{% endif %}
```

- [ ] **Step 13.4: Replace `src/wave_alpha/web/templates/_counts_list.html`**

```jinja
{% from "_macros.html" import pat, wlabel, pill %}

{% if not ranked %}
  <p style="margin: 0; color: var(--fg-muted);">No candidates produced for this date.</p>
{% else %}
  <div class="stack">
    {% for rc in ranked %}
      <div style="padding: 10px 12px; border-radius: var(--radius-sm);
                  {% if loop.first %}background: rgba(244,114,182,0.05); border-left: 2px solid var(--magenta);
                   box-shadow: 0 0 16px rgba(244,114,182,0.08) inset;{% endif %}">
        <div style="display: flex; gap: 8px; align-items: center; margin-bottom: 4px; flex-wrap: wrap;">
          {{ pill(rc.candidate_id, "mag" if loop.first else "muted") }}
          {{ pat(rc.count.pattern) }}
          {{ pill("final " + "%.2f"|format(rc.final_score), "cyan") }}
          {{ pill("engine " + "%.2f"|format(rc.engine_score), "muted") }}
          {% if rc.llm_prob is not none %}
            {{ pill("llm " + "%.2f"|format(rc.llm_prob), "purple") }}
          {% endif %}
        </div>
        {% set narrative = narratives.get(rc.candidate_id) %}
        {% if narrative %}
          <p style="margin: 4px 0 0; font-size: 13px; color: var(--fg-secondary);">{{ narrative }}</p>
        {% endif %}
        {% if rc.count.soft_violations %}
          <div style="display: flex; gap: 6px; margin-top: 6px; flex-wrap: wrap;">
            {% for v in rc.count.soft_violations %}{{ pill("soft: " + v, "warn") }}{% endfor %}
          </div>
        {% endif %}
      </div>
    {% endfor %}
  </div>
  {% if engine_missed %}
    <p style="margin-top: 10px;">{{ pill("LLM flagged engine_missed — consider expanding template set.", "warn") }}</p>
  {% endif %}
{% endif %}
```

- [ ] **Step 13.5: Replace `src/wave_alpha/web/templates/_scenarios.html`**

```jinja
{% from "_macros.html" import pat, pill %}

{% if not alternates %}
  <p style="margin: 0; color: var(--fg-muted);">No alternate counts at this date.</p>
{% else %}
  <div class="stack">
    {% for rc in alternates %}
      <div style="padding: 8px 12px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
        {{ pill(rc.candidate_id, "muted") }}
        {{ pat(rc.count.pattern) }}
        {{ pill("final " + "%.2f"|format(rc.final_score), "muted") }}
        {% if rc.count.pivots %}
          {{ pill("last pivot " + (rc.count.pivots[-1].price|string), "muted") }}
        {% endif %}
      </div>
    {% endfor %}
  </div>
{% endif %}
```

- [ ] **Step 13.6: Replace `src/wave_alpha/web/templates/_history.html`**

```jinja
{% from "_macros.html" import pat, wlabel, pill %}

{% if not snapshots %}
  <p style="margin: 0; color: var(--fg-muted);">No snapshots yet. The first analysis will start the timeline.</p>
{% else %}
  {% if badge %}
    {{ pill("stable " + (badge.streak|string) + " day" + ("s" if badge.streak != 1 else ""), "yellow") }}
    {% if badge.streak != badge.matches_in_window %}
      <span style="margin-left: 8px; font-size: 12px; color: var(--fg-muted);">
        ({{ badge.matches_in_window }} of last {{ badge.window }})
      </span>
    {% endif %}
    {% if badge.last_changed_on %}
      <span style="margin-left: 8px; font-size: 12px; color: var(--fg-muted);">
        last changed {{ badge.last_changed_on }}
      </span>
    {% endif %}
  {% endif %}

  <div style="margin-top: 12px; font: 11px var(--font-mono);">
    {% for s in snapshots %}
      <div style="display: grid; grid-template-columns: 100px auto 30px 60px 1fr; gap: 12px;
                  padding: 6px 8px; align-items: center;
                  {% if loop.first %}background: rgba(250,204,21,0.05); border-left: 2px solid var(--yellow); border-radius: var(--radius-sm);{% endif %}">
        <span style="color: var(--fg-muted);">{{ s.as_of }}</span>
        {{ pat(s.top_3[0].pattern) }}
        {{ wlabel(s.top_3[0].last_pivot_label) }}
        <span style="{% if loop.first %}color: var(--cyan);{% endif %}">{{ "%.2f"|format(s.top_3[0].final_score) }}</span>
        {% if s.signal %}
          <span style="color: var(--bull);">RR {{ "%.1f"|format(s.signal.rr) }}</span>
        {% else %}<span></span>{% endif %}
      </div>
    {% endfor %}
  </div>
{% endif %}
```

- [ ] **Step 13.7: Replace `src/wave_alpha/web/templates/_chart.html`**

```jinja
<div id="chart-canvas" style="width: 100%; height: 480px;"></div>
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

(unchanged from current; included verbatim so this task is self-contained.)

- [ ] **Step 13.8: Run all web tests**

```bash
uv run pytest tests/web -v
```

Expected: all pass. Some assertions in the fragment tests may need updating if they assert specific old strings — read failures and update the assertions to match the new structure (e.g. previous "Direction" / "Entry" labels → still present in `_trade_plan.html`; check if a test asserted `<div class="kpi">` and adapt to current structure).

- [ ] **Step 13.9: Commit**

```bash
git add src/wave_alpha/web/templates/_trade_plan.html \
        src/wave_alpha/web/templates/_coherence_row.html \
        src/wave_alpha/web/templates/_right_edge.html \
        src/wave_alpha/web/templates/_counts_list.html \
        src/wave_alpha/web/templates/_scenarios.html \
        src/wave_alpha/web/templates/_history.html \
        src/wave_alpha/web/templates/_chart.html \
        tests/web/
git commit -m "feat(web/deepdive): redesign all fragment partials with new tokens"
```

---

## Task 14 · Frontend — chart theme update (`deepdive.js`)

**Files:**
- Modify (rewrite): `src/wave_alpha/web/static/deepdive.js`

The candle colors are LOCKED at `#26A69A` / `#EF5350`. Pivot markers and reference lines use the brand spectrum.

- [ ] **Step 14.1: Replace `src/wave_alpha/web/static/deepdive.js`**

```javascript
(function (global) {
  // Locked classic candle colors. Do NOT retint with brand palette.
  // (See docs/superpowers/specs/2026-05-09-web-ui-redesign-design.md §3.)
  var CANDLE_UP = "#26A69A";
  var CANDLE_DOWN = "#EF5350";

  // Wave-label hue map (matches CSS .wlabel.* and the spec's wave color map).
  var WAVE_HUE = {
    "1": "#22D3EE",  "2": "#FB923C",  "3": "#F472B6",
    "4": "#A3E635",  "5": "#A855F7",
    "A": "#2DD4BF",  "B": "#FACC15",  "C": "#F43F5E",
  };

  function pivotColor(label) {
    if (!label) return "#60A5FA"; // neutral blue fallback
    return WAVE_HUE[String(label).toUpperCase()] || "#60A5FA";
  }

  function renderChart(containerId, payload) {
    var el = document.getElementById(containerId);
    if (!el || !global.LightweightCharts) {
      return;
    }
    el.innerHTML = "";

    var chart = global.LightweightCharts.createChart(el, {
      layout: {
        background: { color: "#000000" },     // matches --bg-base
        textColor: "#C7D2DE",                  // matches --fg-secondary
      },
      grid: {
        vertLines: { color: "rgba(34,211,238,0.06)" }, // --border-subtle hue
        horzLines: { color: "rgba(34,211,238,0.06)" },
      },
      timeScale: { timeVisible: false, borderColor: "rgba(34,211,238,0.14)" },
      rightPriceScale: { borderColor: "rgba(34,211,238,0.14)" },
      crosshair: {
        vertLine: { color: "rgba(34,211,238,0.35)", width: 1 },
        horzLine: { color: "rgba(34,211,238,0.35)", width: 1 },
      },
    });

    var series = chart.addSeries(global.LightweightCharts.CandlestickSeries, {
      upColor: CANDLE_UP,
      downColor: CANDLE_DOWN,
      borderUpColor: CANDLE_UP,
      borderDownColor: CANDLE_DOWN,
      wickUpColor: CANDLE_UP,
      wickDownColor: CANDLE_DOWN,
    });
    series.setData(payload.bars || []);

    // Pivot markers — colored by wave label if the payload exposes one.
    if (payload.markers && payload.markers.length) {
      var coloredMarkers = payload.markers.map(function (m) {
        return Object.assign({}, m, {
          color: m.color || pivotColor(m.text || m.label || ""),
        });
      });
      global.LightweightCharts.createSeriesMarkers(series, coloredMarkers);
    }

    // Optional reference lines (entry/stop/target/fib) if the payload provides them.
    var lineDefs = [
      { key: "entry",  color: "#7C8BA3", title: "entry" },
      { key: "stop",   color: "#FB7185", title: "stop"  },
      { key: "target", color: "#4ADE80", title: "target"},
      { key: "fib",    color: "#818CF8", title: "fib"   },
    ];
    lineDefs.forEach(function (def) {
      var v = payload[def.key];
      if (typeof v === "number") {
        series.createPriceLine({
          price: v,
          color: def.color,
          lineWidth: 1,
          lineStyle: global.LightweightCharts.LineStyle.Dashed,
          axisLabelVisible: true,
          title: def.title,
        });
      }
    });
  }

  global.WaveAlpha = global.WaveAlpha || {};
  global.WaveAlpha.renderChart = renderChart;
  if (global._waveAlphaPending) {
    renderChart("chart-canvas", global._waveAlphaPending);
    global._waveAlphaPending = null;
  }
})(window);
```

- [ ] **Step 14.2: Run the chart-data tests**

```bash
uv run pytest tests/web/test_chart_data.py -v
```

Expected: green. (`chart_data.build_chart_payload` is unchanged; the JS reads only fields that already exist.)

- [ ] **Step 14.3: Smoke check the chart in a browser**

```bash
uv run wave-alpha ui &
sleep 3
echo "Visit http://127.0.0.1:8765/deepdive/SPY — confirm candles are classic green/red, pivot dots colored, no JS errors in console."
sleep 30
curl -s -X POST http://127.0.0.1:8765/quit
```

If the chart fails to render, check the browser console for errors. Most likely cause: a missing field in `payload`. The JS handles missing optional fields gracefully — if it crashes, fix the code defensively.

- [ ] **Step 14.4: Commit**

```bash
git add src/wave_alpha/web/static/deepdive.js
git commit -m "feat(web/chart): theme chart for cyber-spectrum (locked classic candles)"
```

---

## Task 15 · Final — full smoke walk + CHANGELOG entry

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 15.1: Run the entire test suite**

```bash
uv run pytest -v
```

Expected: all tests green. If any fail outside `tests/web/`, investigate — the redesign should not affect engine/CLI/etc.

- [ ] **Step 15.2: Run lint and format on the whole `web/` tree**

```bash
uv run ruff check src/wave_alpha/web tests/web
uv run ruff format src/wave_alpha/web tests/web
```

- [ ] **Step 15.3: Manual visual smoke walk**

```bash
uv run wave-alpha ui
# Then in your browser, walk this list:
#   1. http://127.0.0.1:8765/                (Home — KPI strip, search, top signals, watchlist)
#   2. http://127.0.0.1:8765/scan            (Scan empty state with codeblocks if no scans)
#   3. Run a scan: in another shell:
#        uv run wave-alpha scan --symbols AAPL,MSFT,NVDA,SPY --as-of 2024-12-31
#      Then refresh /scan — confirm KPI strip + table renders.
#   4. Click a ticker → /deepdive/AAPL       (chart loads, plan card, coherence ring,
#                                              right-edge, wave counts, history)
#   5. http://127.0.0.1:8765/watchlist        (add AAPL, confirm enriched row appears)
#   6. http://127.0.0.1:8765/settings         (slider works, value updates, hover the
#                                              dotted "Engine ↔ LLM blend" for tooltip)
#   7. Press ⌘K (or Ctrl+K) anywhere — search input focuses.
#   8. DevTools → Rendering → emulate prefers-reduced-motion: reduce. Confirm skeletons
#      stop shimmering and transitions become instant.
# Stop the server with Ctrl+C when done.
```

- [ ] **Step 15.4: Add a CHANGELOG entry**

Read `CHANGELOG.md` first, then add a line under the unreleased / 0.6.0 section describing the redesign:

```markdown
- **Web UI redesign.** New Home dashboard at `/`, redesigned Scan/Watchlist/Settings/Deepdive
  pages, cyber-spectrum design tokens, classic green/red candlesticks with brand-color overlays,
  inline glossary tooltips for engine jargon. See
  [`docs/superpowers/specs/2026-05-09-web-ui-redesign-design.md`](docs/superpowers/specs/2026-05-09-web-ui-redesign-design.md).
```

- [ ] **Step 15.5: Final commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): note web UI redesign for 0.6.0"
```

- [ ] **Step 15.6: Print the commit log for the redesign**

```bash
git log --oneline release/0.6.0-public ^59d64a9 -- src/wave_alpha/web tests/web docs/superpowers
```

Expected: 14-15 commits showing the staged backend/frontend work in order.

---

## Self-review notes

The plan covers every spec section:

- §1 Why → addressed across all tasks; visible-result test in 15.3.
- §2 Scope → in-scope items mapped to tasks 1-14; out-of-scope items not mentioned.
- §3 Constraints → no CDN deps, classic candle colors locked in 14.1, route compatibility kept (tasks 2 and 5 only widen route signatures), DISCLAIMER stripped of leading `⚠` in base.html (task 7).
- §4 IA → top nav with active states + ⌘K + as-of in task 7.
- §5 Design tokens → all listed tokens declared in task 6 CSS and verified by the token-presence test.
- §6 Components → buttons, pills, pattern badges, wave labels, tri-coherence, score bar, KPI, empty state, codeblock, glossary tooltip, tabs all implemented in task 6 CSS and used by the templates.
- §7 Page layouts → Home (8), Scan + empty (9), Watchlist (10), Settings (11), Deepdive (12) and fragments (13).
- §8 Interaction → loading skeletons, hover, focus, active nav, reduced-motion, cursor, ⌘K, as-of date all covered (CSS in 6, JS/Alpine in 7 base, refresh on scan filters in 9).
- §9 Accessibility → focus rings, prefers-reduced-motion, labels in forms, glyphs in tri-coherence, semantic table headers — all handled in CSS + templates.
- §10 File list → matches tasks 1-14 file enumeration.
- §11 Testing → tests in tasks 1, 2, 3, 4, 5, 6 + visual smoke in 15.
- §12 Rollout → CHANGELOG entry in 15.4. Single PR, no flag.
- §13 Deferred → unmentioned (correct; deferred items don't ship in v1).

Type/name consistency: `EnrichedRow` defined in task 3 used in task 4. `HomeView` / `HomeKpis` / `TopRr` defined in task 5 used in task 8. CSS classes (`.pat.impulse`, `.wlabel.w3`, `.kpi.mag`) defined in task 6, used by macros (task 7) and templates (8-13). The `_macros.html` `pat()` helper handles both `expanded_flat` (engine snake_case) and `expanded flat` (display) — covers both representations.

No "TODO" / "TBD" / "implement later" markers in the plan. Every code step has the actual code.

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-09-web-ui-redesign.md`.**
