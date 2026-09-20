# Watchlist + Scan Dashboard Implementation Plan (v0.5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Per user memory, controller skips per-task reviewer subagents on mechanical work; reviews at phase boundaries instead, plus one final cross-cutting review before merge.

**Goal:** Multi-ticker watchlist + scan dashboard. Users manage a watchlist (add, remove, list, import from CSV or TradingView TXT export), trigger a scan that runs the analyze pipeline against every watchlist ticker, see ranked results in a sortable web dashboard, and re-run from CLI for nightly batches. Per spec §11/§12 v0.3 row.

**Architecture:** A new `watchlist/` package owns persistence (one YAML file at `~/.wave_alpha/watchlist.yaml`) and import parsers. A new `scan/` package owns the orchestrator that fans out across tickers, deduplicates LLM cache hits, and aggregates `ScanRow` results. New web routes `/scan`, `/scan/run` (HTMX trigger), `/watchlist` (CRUD page). New CLI: `wave-alpha watchlist add|rm|ls|import` and `wave-alpha scan`. Result of a scan is persisted to `~/.wave_alpha/scans/<timestamp>.json` so the dashboard can reload without re-running.

**Tech Stack:** Python 3.11+ with existing `pipeline.run_analysis` seam, `prefs.config` patterns, FastAPI + Jinja + HTMX. No new runtime dependencies; reuses PyYAML (already in deps for config) for watchlist persistence and stdlib `json` for scan archives.

---

## Out of scope (deferred)

- Nightly cron / scheduled batch — ship the CLI command; cron wiring is user-side.
- Cache invalidation on new bar close — `CachedProvider` already invalidates per (ticker, interval, end-date); the scan command always uses today's date so the cache is always fresh on nightly run.
- Email / push notifications on scan completion — per spec, a v1.1 deliverable.
- Multi-process or async parallelism inside the scan — sequential is fine for ≤200 tickers; reassess once measured.
- `--llm-mode live` for scans — surfaces in CLI flag but is opt-in; default is `disabled` so scans cost $0.
- Sub-dashboards (sector slice, "new this week", history compare) — defer to v0.5.x.

---

## File structure

```
src/wave_alpha/
├── watchlist/
│   ├── __init__.py             # public re-exports
│   ├── models.py               # WatchlistEntry (frozen Pydantic)
│   ├── store.py                # load_watchlist, save_watchlist, add, remove
│   └── parsers.py              # parse_csv_symbols, parse_tv_txt_symbols
├── scan/
│   ├── __init__.py             # public re-exports
│   ├── models.py               # ScanRow, ScanReport (frozen Pydantic)
│   ├── orchestrator.py         # run_scan() — analyze each ticker, aggregate
│   ├── archive.py              # save_report, load_report, list_reports
│   └── ranking.py              # default sort + filter helpers (pure)
src/wave_alpha/web/routes/
├── scan.py                     # GET /scan, GET /scan/{id}, POST /scan/run
└── watchlist.py                # GET /watchlist, POST /watchlist/add|rm|import
src/wave_alpha/web/templates/
├── scan.html                   # main dashboard
├── _scan_table.html            # HTMX fragment for sortable table
├── _scan_row.html              # HTMX fragment for one row (deep-dive button)
├── _scan_status.html           # HTMX fragment for "running 4/50…" indicator
├── watchlist.html              # CRUD page
└── _watchlist_table.html       # HTMX fragment
src/wave_alpha/cli/app.py       # add `watchlist` + `scan` subcommands
tests/watchlist/
tests/scan/
tests/web/                      # add tests for new routes
tests/test_cli_watchlist.py
tests/test_cli_scan.py
```

Why this split: `watchlist/` is a tiny CRUD module — separate from `scan/` because the dashboard reads watchlist (deps), not the other way around. `scan/orchestrator.py` is the only place that imports the analyze pipeline; `archive.py` is filesystem-only; `ranking.py` is pure. `web/routes/scan.py` and `watchlist.py` only call those modules — no business logic in `web/`.

---

## Phase A — Watchlist persistence

### Task 1: WatchlistEntry model + store

**Files:**
- Create: `src/wave_alpha/watchlist/__init__.py`
- Create: `src/wave_alpha/watchlist/models.py`
- Create: `src/wave_alpha/watchlist/store.py`
- Create: `tests/watchlist/__init__.py`
- Create: `tests/watchlist/test_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/watchlist/test_store.py
from datetime import date
from pathlib import Path

import pytest

from wave_alpha.watchlist.models import WatchlistEntry
from wave_alpha.watchlist.store import (
    DEFAULT_WATCHLIST_PATH,
    add_entry,
    load_watchlist,
    remove_entry,
    save_watchlist,
)


def test_default_watchlist_path_under_wave_alpha_home() -> None:
    assert DEFAULT_WATCHLIST_PATH.name == "watchlist.yaml"
    assert ".wave_alpha" in str(DEFAULT_WATCHLIST_PATH)


def test_load_watchlist_returns_empty_when_missing(tmp_path: Path) -> None:
    p = tmp_path / "missing.yaml"
    assert load_watchlist(p) == []


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "wl.yaml"
    entries = [
        WatchlistEntry(symbol="AAPL", added_on=date(2026, 5, 1), notes="megacap"),
        WatchlistEntry(symbol="NVDA", added_on=date(2026, 5, 2)),
    ]
    save_watchlist(entries, p)
    loaded = load_watchlist(p)
    assert loaded == entries


def test_add_entry_dedupes_existing_symbol(tmp_path: Path) -> None:
    p = tmp_path / "wl.yaml"
    save_watchlist([WatchlistEntry(symbol="AAPL", added_on=date(2026, 5, 1))], p)
    # Adding AAPL again returns the existing entry, no duplicate written
    result = add_entry("AAPL", path=p, today=date(2026, 5, 5))
    assert result.symbol == "AAPL"
    assert result.added_on == date(2026, 5, 1)  # original date preserved
    assert load_watchlist(p) == [WatchlistEntry(symbol="AAPL", added_on=date(2026, 5, 1))]


def test_add_entry_normalizes_to_uppercase(tmp_path: Path) -> None:
    p = tmp_path / "wl.yaml"
    result = add_entry("aapl", path=p, today=date(2026, 5, 5))
    assert result.symbol == "AAPL"


def test_add_entry_rejects_invalid_symbol(tmp_path: Path) -> None:
    p = tmp_path / "wl.yaml"
    with pytest.raises(ValueError, match="invalid symbol"):
        add_entry("bad symbol", path=p, today=date(2026, 5, 5))


def test_remove_entry_returns_true_when_removed(tmp_path: Path) -> None:
    p = tmp_path / "wl.yaml"
    save_watchlist(
        [
            WatchlistEntry(symbol="AAPL", added_on=date(2026, 5, 1)),
            WatchlistEntry(symbol="MSFT", added_on=date(2026, 5, 2)),
        ],
        p,
    )
    assert remove_entry("AAPL", path=p) is True
    assert [e.symbol for e in load_watchlist(p)] == ["MSFT"]


def test_remove_entry_returns_false_when_not_present(tmp_path: Path) -> None:
    p = tmp_path / "wl.yaml"
    save_watchlist([WatchlistEntry(symbol="AAPL", added_on=date(2026, 5, 1))], p)
    assert remove_entry("MSFT", path=p) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/watchlist/test_store.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement model + store**

```python
# src/wave_alpha/watchlist/__init__.py
from wave_alpha.watchlist.models import WatchlistEntry
from wave_alpha.watchlist.store import (
    DEFAULT_WATCHLIST_PATH,
    add_entry,
    load_watchlist,
    remove_entry,
    save_watchlist,
)

__all__ = [
    "DEFAULT_WATCHLIST_PATH",
    "WatchlistEntry",
    "add_entry",
    "load_watchlist",
    "remove_entry",
    "save_watchlist",
]
```

```python
# src/wave_alpha/watchlist/models.py
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class WatchlistEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    added_on: date
    notes: str | None = None
```

```python
# src/wave_alpha/watchlist/store.py
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import yaml

from wave_alpha.watchlist.models import WatchlistEntry

DEFAULT_WATCHLIST_PATH = Path.home() / ".wave_alpha" / "watchlist.yaml"

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def load_watchlist(path: Path = DEFAULT_WATCHLIST_PATH) -> list[WatchlistEntry]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text()) or []
    return [WatchlistEntry.model_validate(item) for item in raw]


def save_watchlist(
    entries: list[WatchlistEntry], path: Path = DEFAULT_WATCHLIST_PATH
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [e.model_dump(mode="json") for e in entries]
    path.write_text(yaml.safe_dump(payload, sort_keys=False))


def _validate_symbol(raw: str) -> str:
    sym = raw.strip().upper()
    if not _SYMBOL_RE.match(sym):
        raise ValueError(f"invalid symbol: {raw!r}")
    return sym


def add_entry(
    symbol: str,
    *,
    path: Path = DEFAULT_WATCHLIST_PATH,
    today: date | None = None,
    notes: str | None = None,
) -> WatchlistEntry:
    """Idempotent add: existing symbol returns its current entry unchanged."""
    sym = _validate_symbol(symbol)
    entries = load_watchlist(path)
    for e in entries:
        if e.symbol == sym:
            return e
    new = WatchlistEntry(symbol=sym, added_on=today or date.today(), notes=notes)
    entries.append(new)
    save_watchlist(entries, path)
    return new


def remove_entry(symbol: str, *, path: Path = DEFAULT_WATCHLIST_PATH) -> bool:
    sym = _validate_symbol(symbol)
    entries = load_watchlist(path)
    survivors = [e for e in entries if e.symbol != sym]
    if len(survivors) == len(entries):
        return False
    save_watchlist(survivors, path)
    return True
```

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/watchlist/test_store.py -v`
Expected: 8/8 PASS

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/watchlist/ tests/watchlist/__init__.py tests/watchlist/test_store.py
git commit -m "$(cat <<'EOF'
feat(watchlist): WatchlistEntry model + YAML-backed store

Persists at ~/.wave_alpha/watchlist.yaml. add_entry is idempotent
(existing symbol returns the original entry, preserving added_on);
remove_entry returns bool so callers can distinguish "removed" from
"wasn't there".

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: CSV + TradingView TXT import parsers

**Files:**
- Create: `src/wave_alpha/watchlist/parsers.py`
- Create: `tests/watchlist/test_parsers.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/watchlist/test_parsers.py
from pathlib import Path

import pytest

from wave_alpha.watchlist.parsers import parse_csv_symbols, parse_tv_txt_symbols


def test_parse_csv_first_column_only(tmp_path: Path) -> None:
    f = tmp_path / "in.csv"
    f.write_text("Symbol,Notes\nAAPL,megacap\nMSFT,\nNVDA,gpu\n")
    assert parse_csv_symbols(f) == ["AAPL", "MSFT", "NVDA"]


def test_parse_csv_skips_header_row_when_first_cell_isnt_a_symbol(tmp_path: Path) -> None:
    f = tmp_path / "in.csv"
    f.write_text("Symbol\nAAPL\nMSFT\n")
    assert parse_csv_symbols(f) == ["AAPL", "MSFT"]


def test_parse_csv_handles_no_header(tmp_path: Path) -> None:
    f = tmp_path / "in.csv"
    f.write_text("AAPL\nMSFT\nNVDA\n")
    assert parse_csv_symbols(f) == ["AAPL", "MSFT", "NVDA"]


def test_parse_csv_dedupes_preserving_order(tmp_path: Path) -> None:
    f = tmp_path / "in.csv"
    f.write_text("AAPL\nMSFT\nAAPL\nNVDA\nMSFT\n")
    assert parse_csv_symbols(f) == ["AAPL", "MSFT", "NVDA"]


def test_parse_tv_txt_strips_exchange_prefix(tmp_path: Path) -> None:
    """TradingView export is one symbol per line, ###NASDAQ:AAPL### or
    NASDAQ:AAPL or just AAPL — all valid."""
    f = tmp_path / "tv.txt"
    f.write_text("###NASDAQ:AAPL###\nNASDAQ:MSFT\nNYSE:GE\nNVDA\n")
    assert parse_tv_txt_symbols(f) == ["AAPL", "MSFT", "GE", "NVDA"]


def test_parse_tv_txt_handles_groups_and_separators(tmp_path: Path) -> None:
    """TV exports use commas and section markers like ###Group###."""
    f = tmp_path / "tv.txt"
    f.write_text("###Mega Cap###,NASDAQ:AAPL,NASDAQ:MSFT,###Defensive###,NYSE:KO\n")
    assert parse_tv_txt_symbols(f) == ["AAPL", "MSFT", "KO"]


def test_parse_tv_txt_skips_invalid_tokens(tmp_path: Path) -> None:
    f = tmp_path / "tv.txt"
    f.write_text("AAPL,---,MSFT,###just a header###\n")
    assert parse_tv_txt_symbols(f) == ["AAPL", "MSFT"]


def test_parse_csv_invalid_symbol_skipped_with_no_error(tmp_path: Path) -> None:
    """Lenient — skip bad rows rather than reject the whole import."""
    f = tmp_path / "in.csv"
    f.write_text("AAPL\n123\nMSFT\n")
    result = parse_csv_symbols(f)
    assert "AAPL" in result and "MSFT" in result and "123" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: module missing.

- [ ] **Step 3: Implement parsers**

```python
# src/wave_alpha/watchlist/parsers.py
from __future__ import annotations

import csv
import re
from pathlib import Path

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_TV_PREFIX_RE = re.compile(r"^[A-Z]+:")  # NASDAQ:, NYSE:, BATS:, etc.
_TV_GROUP_RE = re.compile(r"^###.*###$")


def _normalize(token: str) -> str | None:
    """Strip TV-style exchange prefix; uppercase; validate. None if invalid."""
    s = token.strip()
    if not s or _TV_GROUP_RE.match(s):
        return None
    s = _TV_PREFIX_RE.sub("", s.upper())
    s = s.replace("###", "").strip()
    return s if _SYMBOL_RE.match(s) else None


def parse_csv_symbols(path: Path) -> list[str]:
    """Read first column of a CSV, normalize, dedupe.

    Lenient: skips invalid rows silently. Detects and skips a literal
    "Symbol" / "Ticker" header row.
    """
    out: list[str] = []
    seen: set[str] = set()
    with path.open(newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            cell = row[0].strip()
            if cell.lower() in {"symbol", "ticker"}:
                continue
            sym = _normalize(cell)
            if sym is None or sym in seen:
                continue
            seen.add(sym)
            out.append(sym)
    return out


def parse_tv_txt_symbols(path: Path) -> list[str]:
    """Parse a TradingView TXT watchlist export.

    Format: comma-separated tokens across one or more lines. Tokens are
    `EXCHANGE:SYMBOL`, bare `SYMBOL`, or `###Group Name###` section markers.
    Strips prefixes, ignores groups, dedupes.
    """
    out: list[str] = []
    seen: set[str] = set()
    text = path.read_text()
    # Split on commas AND newlines so multi-line files work
    for raw_token in re.split(r"[,\n]+", text):
        sym = _normalize(raw_token)
        if sym is None or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out
```

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/watchlist/test_parsers.py -v`
Expected: 8/8 PASS

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/watchlist/parsers.py tests/watchlist/test_parsers.py
git commit -m "$(cat <<'EOF'
feat(watchlist): CSV and TradingView TXT import parsers

parse_csv_symbols reads the first column, skips Symbol/Ticker header
rows, dedupes, lenient on invalid cells.

parse_tv_txt_symbols handles the TV export format: comma-separated
EXCHANGE:SYMBOL tokens, bare symbols, and ###Group### section markers
(skipped). Strips exchange prefixes (NASDAQ:, NYSE:, etc).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase A Review Boundary

After Tasks 1-2:
- Confirm `add_entry` honors `today` parameter (test isolation — tests can pass `today=date(2026, 5, 5)` for stable assertions).
- Confirm CSV parser handles BOM-prefixed exports (we don't, but TV exports don't BOM; flag as known limitation if a real export breaks).
- Confirm TV parser accepts both `\n`-separated and `,`-separated formats (it does — single regex split).

If clean → proceed to Phase B.

---

## Phase B — Watchlist CLI

### Task 3: `wave-alpha watchlist` CLI subcommand

**Files:**
- Modify: `src/wave_alpha/cli/app.py`
- Create: `tests/test_cli_watchlist.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_watchlist.py
from datetime import date
from pathlib import Path

from typer.testing import CliRunner

from wave_alpha.cli.app import app
from wave_alpha.watchlist.store import load_watchlist

runner = CliRunner()


def test_watchlist_help_documents_subcommands() -> None:
    res = runner.invoke(app, ["watchlist", "--help"])
    assert res.exit_code == 0
    for sub in ("add", "rm", "ls", "import"):
        assert sub in res.stdout


def test_watchlist_add_persists(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "wl.yaml"
    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_watchlist_path", lambda: p)
    res = runner.invoke(app, ["watchlist", "add", "AAPL"])
    assert res.exit_code == 0
    assert "AAPL" in res.stdout
    entries = load_watchlist(p)
    assert [e.symbol for e in entries] == ["AAPL"]


def test_watchlist_add_idempotent(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "wl.yaml"
    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_watchlist_path", lambda: p)
    runner.invoke(app, ["watchlist", "add", "AAPL"])
    runner.invoke(app, ["watchlist", "add", "AAPL"])
    assert len(load_watchlist(p)) == 1


def test_watchlist_ls_lists_in_order(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "wl.yaml"
    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_watchlist_path", lambda: p)
    runner.invoke(app, ["watchlist", "add", "AAPL"])
    runner.invoke(app, ["watchlist", "add", "MSFT"])
    res = runner.invoke(app, ["watchlist", "ls"])
    assert res.exit_code == 0
    out = res.stdout
    assert out.index("AAPL") < out.index("MSFT")


def test_watchlist_rm_removes(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "wl.yaml"
    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_watchlist_path", lambda: p)
    runner.invoke(app, ["watchlist", "add", "AAPL"])
    runner.invoke(app, ["watchlist", "add", "MSFT"])
    res = runner.invoke(app, ["watchlist", "rm", "AAPL"])
    assert res.exit_code == 0
    assert [e.symbol for e in load_watchlist(p)] == ["MSFT"]


def test_watchlist_import_csv(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "wl.yaml"
    csv_file = tmp_path / "in.csv"
    csv_file.write_text("Symbol\nAAPL\nMSFT\nNVDA\n")
    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_watchlist_path", lambda: p)
    res = runner.invoke(app, ["watchlist", "import", str(csv_file), "--format", "csv"])
    assert res.exit_code == 0, res.stdout
    assert "imported 3" in res.stdout
    assert sorted(e.symbol for e in load_watchlist(p)) == ["AAPL", "MSFT", "NVDA"]


def test_watchlist_import_tv_txt(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "wl.yaml"
    tv_file = tmp_path / "tv.txt"
    tv_file.write_text("###Mega###,NASDAQ:AAPL,NASDAQ:NVDA")
    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_watchlist_path", lambda: p)
    res = runner.invoke(app, ["watchlist", "import", str(tv_file), "--format", "tv-txt"])
    assert res.exit_code == 0
    assert "imported 2" in res.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: subcommand missing.

- [ ] **Step 3: Wire CLI**

In `src/wave_alpha/cli/app.py`, add:

```python
from wave_alpha.watchlist.store import (
    DEFAULT_WATCHLIST_PATH,
    add_entry as _wl_add,
    load_watchlist as _wl_load,
    remove_entry as _wl_remove,
)

watchlist_app = typer.Typer(no_args_is_help=True, help="Manage the watchlist.")
app.add_typer(watchlist_app, name="watchlist")


def _watchlist_path() -> Path:
    """Indirection so tests can monkeypatch the location."""
    return DEFAULT_WATCHLIST_PATH


@watchlist_app.command("add")
def watchlist_add(symbol: str = typer.Argument(...)) -> None:
    """Add a symbol to the watchlist (idempotent)."""
    e = _wl_add(symbol, path=_watchlist_path())
    typer.echo(f"{e.symbol}  added_on={e.added_on.isoformat()}")


@watchlist_app.command("rm")
def watchlist_rm(symbol: str = typer.Argument(...)) -> None:
    """Remove a symbol from the watchlist."""
    if _wl_remove(symbol, path=_watchlist_path()):
        typer.echo(f"removed {symbol.upper()}")
    else:
        typer.echo(f"{symbol.upper()} not in watchlist", err=True)
        raise typer.Exit(code=1)


@watchlist_app.command("ls")
def watchlist_ls() -> None:
    """List watchlist entries."""
    entries = _wl_load(_watchlist_path())
    if not entries:
        typer.echo("(empty — use `wave-alpha watchlist add SYMBOL`)")
        return
    for e in entries:
        notes = f"  # {e.notes}" if e.notes else ""
        typer.echo(f"{e.symbol:<8}  {e.added_on.isoformat()}{notes}")


@watchlist_app.command("import")
def watchlist_import(
    path: Path = typer.Argument(..., exists=True, readable=True),
    fmt: str = typer.Option(
        "csv", "--format", help="Source format: csv or tv-txt", case_sensitive=False
    ),
) -> None:
    """Import symbols from a CSV or TradingView TXT export."""
    from wave_alpha.watchlist.parsers import parse_csv_symbols, parse_tv_txt_symbols

    parser = {"csv": parse_csv_symbols, "tv-txt": parse_tv_txt_symbols}.get(fmt.lower())
    if parser is None:
        raise typer.BadParameter(f"unknown format: {fmt}")
    symbols = parser(path)
    added = 0
    for s in symbols:
        e_before = _wl_load(_watchlist_path())
        _wl_add(s, path=_watchlist_path())
        e_after = _wl_load(_watchlist_path())
        if len(e_after) > len(e_before):
            added += 1
    typer.echo(f"imported {added} new symbol(s) from {path.name}")
```

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/test_cli_watchlist.py -v`
Expected: 7/7 PASS

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/cli/app.py tests/test_cli_watchlist.py
git commit -m "$(cat <<'EOF'
feat(cli): wave-alpha watchlist add|rm|ls|import subcommands

Manages the YAML-backed watchlist at ~/.wave_alpha/watchlist.yaml.
Import accepts both CSV (first-column symbols, header detected) and
TradingView TXT exports (NASDAQ:AAPL with section markers).

Tests use a `_watchlist_path` indirection so monkeypatch can point at
tmp_path — same pattern as `_config_path` for prefs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase B Review Boundary

After Task 3:
- Confirm `_watchlist_path` indirection mirrors `_config_path` pattern (test isolation).
- Confirm import command counts NEW additions, not total file lines (idempotent semantics).

If clean → proceed to Phase C.

---

## Phase C — Scan orchestrator

### Task 4: ScanRow + ScanReport models

**Files:**
- Create: `src/wave_alpha/scan/__init__.py`
- Create: `src/wave_alpha/scan/models.py`
- Create: `tests/scan/__init__.py`
- Create: `tests/scan/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scan/test_models.py
from datetime import datetime, date
from decimal import Decimal

from wave_alpha.scan.models import ScanReport, ScanRow


def test_scan_row_construction() -> None:
    row = ScanRow(
        ticker="AAPL",
        as_of=date(2026, 5, 1),
        coherence=0.78,
        top_pattern="impulse",
        top_score=0.65,
        direction="up",
        signal_rr=Decimal("2.4"),
        signal_entry=Decimal("180"),
        signal_stop=Decimal("172"),
        signal_target=Decimal("199"),
        notes=None,
        error=None,
    )
    assert row.has_signal is True


def test_scan_row_no_signal_when_rr_missing() -> None:
    row = ScanRow(
        ticker="AAPL",
        as_of=date(2026, 5, 1),
        coherence=0.5,
        top_pattern="impulse",
        top_score=0.4,
        direction="up",
    )
    assert row.has_signal is False


def test_scan_row_error_state() -> None:
    row = ScanRow(
        ticker="ZZZ",
        as_of=date(2026, 5, 1),
        coherence=None,
        top_pattern=None,
        top_score=None,
        direction=None,
        error="no data from provider",
    )
    assert row.has_error is True


def test_scan_report_started_completed_invariant() -> None:
    started = datetime(2026, 5, 1, 9, 0, 0)
    completed = datetime(2026, 5, 1, 9, 0, 30)
    report = ScanReport(
        scan_id="2026-05-01T09-00-00",
        started_at=started,
        completed_at=completed,
        as_of=date(2026, 5, 1),
        rows=[],
    )
    assert report.duration_seconds == 30


def test_scan_report_summary_counts() -> None:
    report = ScanReport(
        scan_id="x",
        started_at=datetime(2026, 5, 1, 9, 0, 0),
        completed_at=datetime(2026, 5, 1, 9, 0, 30),
        as_of=date(2026, 5, 1),
        rows=[
            ScanRow(
                ticker="AAPL",
                as_of=date(2026, 5, 1),
                coherence=0.8,
                top_pattern="impulse",
                top_score=0.7,
                direction="up",
                signal_rr=Decimal("2.0"),
                signal_entry=Decimal("180"),
                signal_stop=Decimal("172"),
                signal_target=Decimal("196"),
            ),
            ScanRow(
                ticker="MSFT",
                as_of=date(2026, 5, 1),
                coherence=0.4,
                top_pattern="zigzag",
                top_score=0.5,
                direction="down",
            ),
            ScanRow(
                ticker="ZZZ",
                as_of=date(2026, 5, 1),
                coherence=None,
                top_pattern=None,
                top_score=None,
                direction=None,
                error="no data",
            ),
        ],
    )
    assert report.n_total == 3
    assert report.n_with_signal == 1
    assert report.n_errored == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: module missing.

- [ ] **Step 3: Implement models**

```python
# src/wave_alpha/scan/__init__.py
from wave_alpha.scan.models import ScanReport, ScanRow

__all__ = ["ScanReport", "ScanRow"]
```

```python
# src/wave_alpha/scan/models.py
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ScanRow(BaseModel):
    """One ticker's scan summary. None on optional fields means the engine
    couldn't produce that piece (no counts, no signal, etc); `error` is set
    only on hard failures (provider down, exception)."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    as_of: date
    coherence: float | None = None
    top_pattern: str | None = None
    top_score: float | None = None
    direction: Literal["up", "down"] | None = None
    signal_rr: Decimal | None = None
    signal_entry: Decimal | None = None
    signal_stop: Decimal | None = None
    signal_target: Decimal | None = None
    notes: str | None = None
    error: str | None = None

    @property
    def has_signal(self) -> bool:
        return self.signal_rr is not None

    @property
    def has_error(self) -> bool:
        return self.error is not None


class ScanReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    scan_id: str
    started_at: datetime
    completed_at: datetime
    as_of: date
    rows: list[ScanRow]

    @property
    def duration_seconds(self) -> int:
        return int((self.completed_at - self.started_at).total_seconds())

    @property
    def n_total(self) -> int:
        return len(self.rows)

    @property
    def n_with_signal(self) -> int:
        return sum(1 for r in self.rows if r.has_signal)

    @property
    def n_errored(self) -> int:
        return sum(1 for r in self.rows if r.has_error)
```

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/scan/test_models.py -v`
Expected: 5/5 PASS

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/scan/__init__.py src/wave_alpha/scan/models.py \
  tests/scan/__init__.py tests/scan/test_models.py
git commit -m "$(cat <<'EOF'
feat(scan): ScanRow + ScanReport frozen Pydantic models

ScanRow distinguishes "engine didn't produce" (None on field) from
"hard error" (error set). has_signal / has_error are read-only props.

ScanReport tracks scan_id, start/end timestamps, as_of, rows, and
exposes duration + summary counts.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Scan orchestrator

**Files:**
- Create: `src/wave_alpha/scan/orchestrator.py`
- Create: `tests/scan/test_orchestrator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scan/test_orchestrator.py
from datetime import date, timedelta
from decimal import Decimal

import pytest

from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval
from wave_alpha.scan.orchestrator import run_scan


class _RisingProvider:
    def fetch(self, ticker: str, interval: Interval, *, end: date) -> list[Bar]:
        bars = []
        d = date(2024, 1, 1)
        i = 0
        while d <= end:
            if d.weekday() < 5:
                p = 100 + i * 0.4
                bars.append(
                    Bar(
                        ticker=ticker,
                        interval=interval,
                        timestamp=d,
                        open=Decimal(str(p)),
                        high=Decimal(str(p + 0.5)),
                        low=Decimal(str(p - 0.5)),
                        close=Decimal(str(p)),
                        volume=1_000,
                    )
                )
                i += 1
            d = d + timedelta(days=1)
        return bars


class _BrokenProvider:
    def fetch(self, ticker: str, interval: Interval, *, end: date) -> list[Bar]:
        if ticker == "BAD":
            raise RuntimeError("provider down for BAD")
        return _RisingProvider().fetch(ticker, interval, end=end)


def test_run_scan_produces_one_row_per_ticker() -> None:
    report = run_scan(
        symbols=["AAPL", "MSFT", "NVDA"],
        as_of=date(2026, 5, 1),
        provider=_RisingProvider(),
    )
    assert {r.ticker for r in report.rows} == {"AAPL", "MSFT", "NVDA"}
    assert report.n_total == 3
    assert report.n_errored == 0


def test_run_scan_captures_errors_per_ticker() -> None:
    report = run_scan(
        symbols=["AAPL", "BAD", "MSFT"],
        as_of=date(2026, 5, 1),
        provider=_BrokenProvider(),
    )
    assert report.n_total == 3
    bad_row = next(r for r in report.rows if r.ticker == "BAD")
    assert bad_row.has_error
    assert "provider down" in (bad_row.error or "")
    # Other rows are unaffected
    assert all(not r.has_error for r in report.rows if r.ticker != "BAD")


def test_run_scan_uses_disabled_llm_by_default() -> None:
    """The orchestrator must NOT instantiate any LLM client by default."""
    import wave_alpha.llm.client as llm_client_mod

    sentinel = {"called": False}
    original_init = llm_client_mod.AnthropicClient.__init__

    def spy(self, *a, **kw):  # type: ignore[no-untyped-def]
        sentinel["called"] = True
        original_init(self, *a, **kw)

    try:
        llm_client_mod.AnthropicClient.__init__ = spy  # type: ignore[method-assign]
        run_scan(symbols=["AAPL"], as_of=date(2026, 5, 1), provider=_RisingProvider())
        assert sentinel["called"] is False
    finally:
        llm_client_mod.AnthropicClient.__init__ = original_init  # type: ignore[method-assign]


def test_run_scan_records_signal_when_engine_produces_one() -> None:
    report = run_scan(
        symbols=["AAPL"],
        as_of=date(2026, 5, 1),
        provider=_RisingProvider(),
    )
    row = report.rows[0]
    # On synthetic rising data, engine may or may not produce a signal —
    # if it does, the fields must be consistent.
    if row.has_signal:
        assert row.signal_entry is not None
        assert row.signal_stop is not None
        assert row.signal_target is not None
        assert row.signal_rr is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: module missing.

- [ ] **Step 3: Implement orchestrator**

```python
# src/wave_alpha/scan/orchestrator.py
from __future__ import annotations

from datetime import date, datetime
from typing import cast

from wave_alpha.coherence.score import _predicted_direction  # type: ignore[attr-defined]
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.llm.modes import LLMMode
from wave_alpha.llm.runner import LLMRunner
from wave_alpha.pipeline import AnalyzeRequest, run_analysis
from wave_alpha.scan.models import ScanReport, ScanRow


def run_scan(
    *,
    symbols: list[str],
    as_of: date,
    provider: OHLCVProvider,
    llm_mode: LLMMode = LLMMode.DISABLED,
    llm_runner: LLMRunner | None = None,
    alpha: float = 0.6,
) -> ScanReport:
    """Run analyze for each symbol; aggregate one ScanRow per ticker.

    Errors are captured per-ticker — one bad symbol does not abort the scan.
    LLM is disabled by default; pass llm_runner + LLMMode.LIVE to enable.
    """
    started = datetime.utcnow()
    rows: list[ScanRow] = []
    for sym in symbols:
        rows.append(_scan_one(sym, as_of, provider, llm_mode, llm_runner, alpha))
    completed = datetime.utcnow()
    return ScanReport(
        scan_id=started.strftime("%Y-%m-%dT%H-%M-%S"),
        started_at=started,
        completed_at=completed,
        as_of=as_of,
        rows=rows,
    )


def _scan_one(
    symbol: str,
    as_of: date,
    provider: OHLCVProvider,
    llm_mode: LLMMode,
    llm_runner: LLMRunner | None,
    alpha: float,
) -> ScanRow:
    try:
        req = AnalyzeRequest(ticker=symbol.upper(), as_of=as_of)
        result = run_analysis(
            req, provider=provider, llm_mode=llm_mode, llm_runner=llm_runner, alpha=alpha
        )
    except Exception as exc:  # noqa: BLE001 — capture per-ticker
        return ScanRow(ticker=symbol.upper(), as_of=as_of, error=str(exc))

    if not result.counts_daily:
        return ScanRow(
            ticker=symbol.upper(),
            as_of=as_of,
            coherence=result.coherence.score if result.coherence else None,
            notes="no daily counts",
        )

    top_count = result.counts_daily[0]
    direction = _predicted_direction(top_count)
    signal = result.signals[0] if result.signals else None
    return ScanRow(
        ticker=symbol.upper(),
        as_of=as_of,
        coherence=result.coherence.score if result.coherence else None,
        top_pattern=top_count.pattern,
        top_score=result.ranked_daily[0].final_score if result.ranked_daily else top_count.score,
        direction=cast("Literal['up', 'down'] | None", direction),
        signal_rr=signal.risk_per_share and (signal.reward_per_share / signal.risk_per_share)
        if signal
        else None,
        signal_entry=signal.entry_price if signal else None,
        signal_stop=signal.stop_price if signal else None,
        signal_target=signal.target_price if signal else None,
    )
```

Note on the `signal_rr` line: it currently computes RR from reward/risk. If `TradeSignal` already has `rr: float`, simplify to `Decimal(str(signal.rr))` — implementer should check `trades/models.py` and use the existing field if present (it is, per CLAUDE.md `TradeSignal.rr`).

Replace the orchestrator code with:
```python
        signal_rr=Decimal(str(signal.rr)) if signal else None,
```

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/scan/test_orchestrator.py -v`
Expected: 4/4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/scan/orchestrator.py tests/scan/test_orchestrator.py
git commit -m "$(cat <<'EOF'
feat(scan): run_scan orchestrator — one ScanRow per ticker

Walks the symbol list, runs analyze per ticker, captures errors
per-ticker (a bad symbol does not abort the scan). LLM-disabled by
default; opt-in via llm_mode + llm_runner.

ScanRow fields cover engine outputs (top pattern, score, direction,
coherence) and trade signal (entry/stop/target/rr) when present.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Archive (save/load/list reports)

**Files:**
- Create: `src/wave_alpha/scan/archive.py`
- Create: `tests/scan/test_archive.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scan/test_archive.py
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from wave_alpha.scan.archive import (
    DEFAULT_SCAN_DIR,
    list_reports,
    load_report,
    save_report,
)
from wave_alpha.scan.models import ScanReport, ScanRow


def _sample_report(scan_id: str = "2026-05-01T09-00-00") -> ScanReport:
    return ScanReport(
        scan_id=scan_id,
        started_at=datetime(2026, 5, 1, 9, 0, 0),
        completed_at=datetime(2026, 5, 1, 9, 0, 30),
        as_of=date(2026, 5, 1),
        rows=[
            ScanRow(
                ticker="AAPL",
                as_of=date(2026, 5, 1),
                coherence=0.8,
                top_pattern="impulse",
                top_score=0.7,
                direction="up",
                signal_rr=Decimal("2.0"),
                signal_entry=Decimal("180"),
                signal_stop=Decimal("172"),
                signal_target=Decimal("196"),
            ),
        ],
    )


def test_default_scan_dir_under_wave_alpha_home() -> None:
    assert ".wave_alpha" in str(DEFAULT_SCAN_DIR)
    assert DEFAULT_SCAN_DIR.name == "scans"


def test_save_then_load_report_roundtrip(tmp_path: Path) -> None:
    rpt = _sample_report()
    path = save_report(rpt, dir=tmp_path)
    assert path.exists()
    loaded = load_report(rpt.scan_id, dir=tmp_path)
    assert loaded == rpt


def test_list_reports_sorted_newest_first(tmp_path: Path) -> None:
    save_report(_sample_report("2026-05-01T09-00-00"), dir=tmp_path)
    save_report(_sample_report("2026-05-02T09-00-00"), dir=tmp_path)
    save_report(_sample_report("2026-05-01T10-00-00"), dir=tmp_path)
    ids = list_reports(dir=tmp_path)
    assert ids == [
        "2026-05-02T09-00-00",
        "2026-05-01T10-00-00",
        "2026-05-01T09-00-00",
    ]


def test_list_reports_empty_when_no_files(tmp_path: Path) -> None:
    assert list_reports(dir=tmp_path) == []


def test_load_report_missing_returns_none(tmp_path: Path) -> None:
    assert load_report("does-not-exist", dir=tmp_path) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: module missing.

- [ ] **Step 3: Implement archive**

```python
# src/wave_alpha/scan/archive.py
from __future__ import annotations

import json
from pathlib import Path

from wave_alpha.scan.models import ScanReport

DEFAULT_SCAN_DIR = Path.home() / ".wave_alpha" / "scans"


def save_report(report: ScanReport, *, dir: Path = DEFAULT_SCAN_DIR) -> Path:
    dir.mkdir(parents=True, exist_ok=True)
    path = dir / f"{report.scan_id}.json"
    path.write_text(json.dumps(report.model_dump(mode="json")))
    return path


def load_report(scan_id: str, *, dir: Path = DEFAULT_SCAN_DIR) -> ScanReport | None:
    path = dir / f"{scan_id}.json"
    if not path.exists():
        return None
    return ScanReport.model_validate_json(path.read_text())


def list_reports(*, dir: Path = DEFAULT_SCAN_DIR) -> list[str]:
    """Return scan_ids newest-first (lex sort works because IDs are ISO timestamps)."""
    if not dir.exists():
        return []
    return sorted(
        (p.stem for p in dir.glob("*.json")),
        reverse=True,
    )
```

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/scan/test_archive.py -v`
Expected: 5/5 PASS

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/scan/archive.py tests/scan/test_archive.py
git commit -m "$(cat <<'EOF'
feat(scan): JSON-backed scan report archive

save_report writes ~/.wave_alpha/scans/<scan_id>.json (Pydantic
model_dump_json). load_report returns None on missing. list_reports
returns scan_ids newest-first (ISO timestamp lex sort).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Ranking helpers

**Files:**
- Create: `src/wave_alpha/scan/ranking.py`
- Create: `tests/scan/test_ranking.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/scan/test_ranking.py
from datetime import date
from decimal import Decimal

from wave_alpha.scan.models import ScanRow
from wave_alpha.scan.ranking import filter_signals, sort_by


def _row(tk: str, score: float, rr: str | None = None) -> ScanRow:
    kwargs = dict(
        ticker=tk,
        as_of=date(2026, 5, 1),
        coherence=0.5,
        top_pattern="impulse",
        top_score=score,
        direction="up",
    )
    if rr:
        kwargs.update(
            signal_rr=Decimal(rr),
            signal_entry=Decimal("100"),
            signal_stop=Decimal("90"),
            signal_target=Decimal("130"),
        )
    return ScanRow(**kwargs)


def test_sort_by_score_descending_default() -> None:
    rows = [_row("A", 0.4), _row("B", 0.8), _row("C", 0.6)]
    out = sort_by(rows, "score")
    assert [r.ticker for r in out] == ["B", "C", "A"]


def test_sort_by_rr_puts_no_signal_last() -> None:
    rows = [
        _row("A", 0.4, rr="3.0"),
        _row("B", 0.8),  # no signal
        _row("C", 0.6, rr="1.5"),
    ]
    out = sort_by(rows, "rr")
    assert [r.ticker for r in out] == ["A", "C", "B"]


def test_sort_by_ticker_alpha() -> None:
    rows = [_row("NVDA", 0.5), _row("AAPL", 0.5), _row("MSFT", 0.5)]
    out = sort_by(rows, "ticker")
    assert [r.ticker for r in out] == ["AAPL", "MSFT", "NVDA"]


def test_filter_signals_keeps_only_rows_with_rr() -> None:
    rows = [_row("A", 0.4, rr="3.0"), _row("B", 0.8), _row("C", 0.6, rr="1.5")]
    out = filter_signals(rows)
    assert [r.ticker for r in out] == ["A", "C"]
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: module missing.

- [ ] **Step 3: Implement ranking**

```python
# src/wave_alpha/scan/ranking.py
from __future__ import annotations

from collections.abc import Iterable

from wave_alpha.scan.models import ScanRow

SortKey = str  # "score" | "rr" | "ticker" | "coherence"


def sort_by(rows: Iterable[ScanRow], key: SortKey, *, reverse: bool = True) -> list[ScanRow]:
    """Stable sort. Rows with None on the sort key go last (regardless of reverse)."""
    rows_list = list(rows)
    if key == "ticker":
        return sorted(rows_list, key=lambda r: r.ticker, reverse=False)

    def numeric(r: ScanRow) -> float | None:
        if key == "score":
            return r.top_score
        if key == "rr":
            return float(r.signal_rr) if r.signal_rr is not None else None
        if key == "coherence":
            return r.coherence
        return None

    none_rows = [r for r in rows_list if numeric(r) is None]
    val_rows = [r for r in rows_list if numeric(r) is not None]
    val_rows.sort(key=lambda r: numeric(r) or 0.0, reverse=reverse)
    return val_rows + none_rows


def filter_signals(rows: Iterable[ScanRow]) -> list[ScanRow]:
    """Keep only rows with a derived trade signal."""
    return [r for r in rows if r.has_signal]
```

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/scan/test_ranking.py -v`
Expected: 4/4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/scan/ranking.py tests/scan/test_ranking.py
git commit -m "$(cat <<'EOF'
feat(scan): pure-function ranking helpers (sort_by, filter_signals)

sort_by accepts "score" | "rr" | "ticker" | "coherence". Numeric sorts
push None-valued rows to the end regardless of direction. ticker sort
is alphabetical ascending (ignores reverse flag — UX choice).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase C Review Boundary

After Tasks 4-7:
- Confirm orchestrator captures broad exceptions per-ticker (already covered by `_BrokenProvider` test).
- Confirm `signal_rr` uses existing `TradeSignal.rr` field (verified by reading `trades/models.py`) — implementer must check.
- Confirm archive JSON roundtrips Decimal cleanly (model_dump(mode="json") handles it).

If clean → proceed to Phase D.

---

## Phase D — Scan CLI command

### Task 8: `wave-alpha scan` CLI subcommand

**Files:**
- Modify: `src/wave_alpha/cli/app.py`
- Create: `tests/test_cli_scan.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_scan.py
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from wave_alpha.cli.app import app
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval

runner = CliRunner()


class _RisingProvider:
    def fetch(self, ticker: str, interval: Interval, *, end: date) -> list[Bar]:
        bars = []
        d = date(2024, 1, 1)
        i = 0
        while d <= end:
            if d.weekday() < 5:
                p = 100 + i * 0.4
                bars.append(
                    Bar(
                        ticker=ticker,
                        interval=interval,
                        timestamp=d,
                        open=Decimal(str(p)),
                        high=Decimal(str(p + 0.5)),
                        low=Decimal(str(p - 0.5)),
                        close=Decimal(str(p)),
                        volume=1_000,
                    )
                )
                i += 1
            d = d + timedelta(days=1)
        return bars


def test_scan_help_documents_flags() -> None:
    res = runner.invoke(app, ["scan", "--help"])
    assert res.exit_code == 0
    assert "--watchlist" in res.stdout
    assert "--symbols" in res.stdout
    assert "--as-of" in res.stdout


def test_scan_with_inline_symbols(tmp_path: Path, monkeypatch) -> None:
    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_default_provider", lambda: _RisingProvider())
    monkeypatch.setattr(app_module, "_scan_dir", lambda: tmp_path)
    res = runner.invoke(
        app, ["scan", "--symbols", "AAPL,MSFT", "--as-of", "2026-05-01"]
    )
    assert res.exit_code == 0, res.stdout
    # One JSON file written
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert "AAPL" in res.stdout
    assert "MSFT" in res.stdout


def test_scan_uses_watchlist_by_default(tmp_path: Path, monkeypatch) -> None:
    wl_path = tmp_path / "wl.yaml"
    scans_dir = tmp_path / "scans"
    scans_dir.mkdir()
    from wave_alpha.cli import app as app_module
    from wave_alpha.watchlist.store import save_watchlist
    from wave_alpha.watchlist.models import WatchlistEntry

    save_watchlist([WatchlistEntry(symbol="AAPL", added_on=date(2026, 5, 1))], wl_path)
    monkeypatch.setattr(app_module, "_watchlist_path", lambda: wl_path)
    monkeypatch.setattr(app_module, "_scan_dir", lambda: scans_dir)
    monkeypatch.setattr(app_module, "_default_provider", lambda: _RisingProvider())
    res = runner.invoke(app, ["scan", "--as-of", "2026-05-01"])
    assert res.exit_code == 0, res.stdout
    assert "AAPL" in res.stdout


def test_scan_errors_when_no_symbols_and_empty_watchlist(tmp_path: Path, monkeypatch) -> None:
    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_watchlist_path", lambda: tmp_path / "missing.yaml")
    res = runner.invoke(app, ["scan", "--as-of", "2026-05-01"])
    assert res.exit_code != 0
    assert "no symbols" in res.stdout.lower() or "empty" in res.stdout.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: subcommand missing.

- [ ] **Step 3: Wire CLI**

In `src/wave_alpha/cli/app.py`, add:

```python
from wave_alpha.scan.archive import DEFAULT_SCAN_DIR, save_report
from wave_alpha.scan.orchestrator import run_scan as _run_scan


def _scan_dir() -> Path:
    """Indirection so tests can monkeypatch the location."""
    return DEFAULT_SCAN_DIR


@app.command()
def scan(
    symbols: str | None = typer.Option(
        None, "--symbols", help="Comma-separated symbols. Overrides --watchlist."
    ),
    watchlist: bool = typer.Option(
        True, "--watchlist/--no-watchlist", help="Use watchlist when --symbols not given."
    ),
    as_of: str | None = typer.Option(None, "--as-of", help="Scan as-of YYYY-MM-DD."),
    llm_mode: LLMMode = typer.Option(  # noqa: B008
        LLMMode.DISABLED, "--llm-mode", help="LLM mode (default: disabled = $0)."
    ),
) -> None:
    """Run a scan over multiple tickers and persist a report."""
    resolved_as_of = date.fromisoformat(as_of) if as_of else date.today()
    if symbols:
        sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    elif watchlist:
        sym_list = [e.symbol for e in _wl_load(_watchlist_path())]
    else:
        sym_list = []
    if not sym_list:
        typer.echo("no symbols to scan: pass --symbols or populate the watchlist", err=True)
        raise typer.Exit(code=1)

    cfg = load_config()
    provider = _default_provider()
    runner_obj = _build_runner(cfg, mode=llm_mode) if llm_mode is not LLMMode.DISABLED else None
    typer.echo(f"scanning {len(sym_list)} symbol(s) as_of={resolved_as_of}…")
    report = _run_scan(
        symbols=sym_list,
        as_of=resolved_as_of,
        provider=provider,
        llm_mode=llm_mode,
        llm_runner=runner_obj,
        alpha=cfg.llm.alpha,
    )
    out = save_report(report, dir=_scan_dir())
    typer.echo(f"wrote {out}  (signals={report.n_with_signal}/{report.n_total}, errors={report.n_errored})")
    for r in report.rows:
        if r.has_error:
            typer.echo(f"  ✗ {r.ticker}  {r.error}")
        elif r.has_signal:
            typer.echo(
                f"  ✓ {r.ticker}  {r.top_pattern}  rr={r.signal_rr:.2f}  "
                f"entry={r.signal_entry}  stop={r.signal_stop}  target={r.signal_target}"
            )
        else:
            score = f"{r.top_score:.2f}" if r.top_score is not None else "n/a"
            typer.echo(f"  • {r.ticker}  {r.top_pattern or '—'}  score={score}  (no signal)")
```

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/test_cli_scan.py -v`
Expected: 4/4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/cli/app.py tests/test_cli_scan.py
git commit -m "$(cat <<'EOF'
feat(cli): wave-alpha scan command — multi-ticker scan + persist

Defaults to scanning the watchlist; override with --symbols A,B,C.
Reuses the existing _build_runner so LLM modes work the same as
analyze. Persists a JSON report to ~/.wave_alpha/scans/<scan_id>.json
and prints a one-line summary per ticker.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase D Review Boundary

After Task 8:
- Confirm `--llm-mode disabled` is the default — running `wave-alpha scan` on a fresh box must not require an API key.
- Confirm error rows are surfaced inline (✗ prefix in output).

If clean → proceed to Phase E.

---

## Phase E — Web routes

### Task 9: Watchlist page (CRUD)

**Files:**
- Create: `src/wave_alpha/web/routes/watchlist.py`
- Create: `src/wave_alpha/web/templates/watchlist.html`
- Create: `src/wave_alpha/web/templates/_watchlist_table.html`
- Modify: `src/wave_alpha/web/app.py` (register router)
- Create: `tests/web/test_watchlist_routes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_watchlist_routes.py
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wave_alpha.watchlist.models import WatchlistEntry
from wave_alpha.watchlist.store import save_watchlist
from wave_alpha.web.app import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    from wave_alpha.web.routes import watchlist as wl_routes

    monkeypatch.setattr(wl_routes, "_watchlist_path", lambda: tmp_path / "wl.yaml")
    return TestClient(create_app())


def test_get_watchlist_renders_empty_state(client: TestClient) -> None:
    res = client.get("/watchlist")
    assert res.status_code == 200
    assert "watchlist" in res.text.lower()


def test_post_watchlist_add_persists_and_redirects(client: TestClient, tmp_path: Path) -> None:
    res = client.post("/watchlist/add", data={"symbol": "AAPL"}, follow_redirects=False)
    assert res.status_code == 303  # POST-redirect-GET
    assert res.headers["location"] == "/watchlist"
    from wave_alpha.web.routes import watchlist as wl_routes
    from wave_alpha.watchlist.store import load_watchlist

    entries = load_watchlist(wl_routes._watchlist_path())
    assert [e.symbol for e in entries] == ["AAPL"]


def test_post_watchlist_remove(client: TestClient, tmp_path: Path) -> None:
    from wave_alpha.web.routes import watchlist as wl_routes

    save_watchlist(
        [WatchlistEntry(symbol="AAPL", added_on=date(2026, 5, 1))],
        wl_routes._watchlist_path(),
    )
    res = client.post("/watchlist/rm", data={"symbol": "AAPL"}, follow_redirects=False)
    assert res.status_code == 303
    from wave_alpha.watchlist.store import load_watchlist

    assert load_watchlist(wl_routes._watchlist_path()) == []


def test_post_watchlist_add_rejects_invalid_symbol(client: TestClient) -> None:
    res = client.post("/watchlist/add", data={"symbol": "bad sym"}, follow_redirects=False)
    # 400 with error in response body
    assert res.status_code == 400
    assert "invalid" in res.text.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: route missing.

- [ ] **Step 3: Implement watchlist routes**

```python
# src/wave_alpha/web/routes/watchlist.py
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from wave_alpha.watchlist.store import (
    DEFAULT_WATCHLIST_PATH,
    add_entry,
    load_watchlist,
    remove_entry,
)

router = APIRouter()


def _watchlist_path() -> Path:
    """Indirection so tests can monkeypatch the location."""
    return DEFAULT_WATCHLIST_PATH


@router.get("/watchlist", response_class=HTMLResponse)
def watchlist_page(request: Request) -> HTMLResponse:
    entries = load_watchlist(_watchlist_path())
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="watchlist.html",
        context={"entries": entries},
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
```

```html
<!-- src/wave_alpha/web/templates/watchlist.html -->
{% extends "base.html" %}
{% block content %}
<div class="card">
  <h1>Watchlist</h1>
  <form method="post" action="/watchlist/add" class="row" style="gap: 8px; margin: 12px 0;">
    <input name="symbol" placeholder="AAPL" autocomplete="off" required>
    <button type="submit">Add</button>
  </form>
  {% include "_watchlist_table.html" %}
  <p style="color: var(--muted); font-size: 12px; margin-top: 16px;">
    Use <code>wave-alpha watchlist import &lt;file&gt; --format csv|tv-txt</code> from the CLI to bulk-import.
  </p>
</div>
{% endblock %}
```

```html
<!-- src/wave_alpha/web/templates/_watchlist_table.html -->
{% if entries %}
<table class="data-table">
  <thead><tr><th>Symbol</th><th>Added</th><th>Notes</th><th></th></tr></thead>
  <tbody>
  {% for e in entries %}
  <tr>
    <td><a href="/deepdive/{{ e.symbol }}">{{ e.symbol }}</a></td>
    <td>{{ e.added_on.isoformat() }}</td>
    <td>{{ e.notes or "" }}</td>
    <td>
      <form method="post" action="/watchlist/rm" style="display: inline;">
        <input type="hidden" name="symbol" value="{{ e.symbol }}">
        <button type="submit" class="btn-danger">Remove</button>
      </form>
    </td>
  </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<p style="color: var(--muted);">No symbols yet — add one above.</p>
{% endif %}
```

In `src/wave_alpha/web/app.py`, register the router:

```python
from wave_alpha.web.routes import deepdive, settings, system, watchlist
...
app.include_router(watchlist.router)
```

Add the `.btn-danger` and `.data-table` classes to `static/app.css`:

```css
.data-table { width: 100%; border-collapse: collapse; }
.data-table th, .data-table td { padding: 6px 8px; border-bottom: 1px solid var(--border); text-align: left; }
.btn-danger { background: var(--danger, #e53935); color: white; border: none; padding: 4px 10px; cursor: pointer; }
.btn-danger:hover { background: #c62828; }
```

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/web/test_watchlist_routes.py -v`
Expected: 4/4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/web/routes/watchlist.py src/wave_alpha/web/templates/watchlist.html \
  src/wave_alpha/web/templates/_watchlist_table.html src/wave_alpha/web/app.py \
  src/wave_alpha/web/static/app.css tests/web/test_watchlist_routes.py
git commit -m "$(cat <<'EOF'
feat(web): /watchlist page with add/remove forms

POST-redirect-GET pattern (303) for add/rm so refresh doesn't double-
submit. Symbols link to /deepdive/{symbol} for one-click drill-in.

Bulk import is intentionally CLI-only — keeps the web page focused and
avoids file-upload complexity for a power-user feature.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Scan dashboard page

**Files:**
- Create: `src/wave_alpha/web/routes/scan.py`
- Create: `src/wave_alpha/web/templates/scan.html`
- Create: `src/wave_alpha/web/templates/_scan_table.html`
- Modify: `src/wave_alpha/web/app.py` (register router)
- Create: `tests/web/test_scan_routes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/web/test_scan_routes.py
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wave_alpha.scan.archive import save_report
from wave_alpha.scan.models import ScanReport, ScanRow
from wave_alpha.web.app import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    from wave_alpha.web.routes import scan as scan_routes

    monkeypatch.setattr(scan_routes, "_scan_dir", lambda: tmp_path)
    return TestClient(create_app())


def _sample_report(scan_id: str = "2026-05-01T09-00-00") -> ScanReport:
    return ScanReport(
        scan_id=scan_id,
        started_at=datetime(2026, 5, 1, 9, 0, 0),
        completed_at=datetime(2026, 5, 1, 9, 0, 30),
        as_of=date(2026, 5, 1),
        rows=[
            ScanRow(
                ticker="AAPL",
                as_of=date(2026, 5, 1),
                coherence=0.8,
                top_pattern="impulse",
                top_score=0.7,
                direction="up",
                signal_rr=Decimal("2.0"),
                signal_entry=Decimal("180"),
                signal_stop=Decimal("172"),
                signal_target=Decimal("196"),
            ),
            ScanRow(
                ticker="MSFT",
                as_of=date(2026, 5, 1),
                coherence=0.5,
                top_pattern="zigzag",
                top_score=0.4,
                direction="down",
            ),
        ],
    )


def test_get_scan_dashboard_with_no_reports_renders_empty_state(client: TestClient) -> None:
    res = client.get("/scan")
    assert res.status_code == 200
    assert "no scan" in res.text.lower() or "empty" in res.text.lower()


def test_get_scan_dashboard_lists_recent_reports(client: TestClient, tmp_path: Path) -> None:
    save_report(_sample_report("2026-05-01T09-00-00"), dir=tmp_path)
    save_report(_sample_report("2026-05-02T09-00-00"), dir=tmp_path)
    res = client.get("/scan")
    assert res.status_code == 200
    # Newest first
    assert res.text.index("2026-05-02") < res.text.index("2026-05-01")


def test_get_scan_id_renders_table(client: TestClient, tmp_path: Path) -> None:
    save_report(_sample_report(), dir=tmp_path)
    res = client.get("/scan/2026-05-01T09-00-00")
    assert res.status_code == 200
    assert "AAPL" in res.text
    assert "MSFT" in res.text
    assert "impulse" in res.text


def test_get_scan_table_supports_sort_param(client: TestClient, tmp_path: Path) -> None:
    save_report(_sample_report(), dir=tmp_path)
    res = client.get("/scan/2026-05-01T09-00-00/table?sort=ticker")
    assert res.status_code == 200
    # AAPL before MSFT alphabetically
    assert res.text.index("AAPL") < res.text.index("MSFT")


def test_get_scan_table_filter_signals(client: TestClient, tmp_path: Path) -> None:
    save_report(_sample_report(), dir=tmp_path)
    res = client.get("/scan/2026-05-01T09-00-00/table?signals_only=true")
    # Only AAPL has a signal
    assert "AAPL" in res.text
    assert "MSFT" not in res.text


def test_get_unknown_scan_id_returns_404(client: TestClient) -> None:
    res = client.get("/scan/does-not-exist")
    assert res.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: routes missing.

- [ ] **Step 3: Implement scan routes**

```python
# src/wave_alpha/web/routes/scan.py
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from wave_alpha.scan.archive import DEFAULT_SCAN_DIR, list_reports, load_report
from wave_alpha.scan.ranking import filter_signals, sort_by

router = APIRouter()


def _scan_dir() -> Path:
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
) -> HTMLResponse:
    report = load_report(scan_id, dir=_scan_dir())
    if report is None:
        raise HTTPException(status_code=404, detail=f"scan {scan_id} not found")
    rows = report.rows
    if signals_only:
        rows = filter_signals(rows)
    rows = sort_by(rows, sort)
    return request.app.state.templates.TemplateResponse(
        request=request, name="_scan_table.html", context={"rows": rows}
    )
```

```html
<!-- src/wave_alpha/web/templates/scan.html -->
{% extends "base.html" %}
{% block content %}
<div class="card">
  <h1>Scan</h1>
  {% if not scan_ids %}
    <p style="color: var(--muted);">
      No scans yet. Run <code>wave-alpha scan</code> from the CLI to generate one.
    </p>
  {% else %}
    <div class="row" style="gap: 12px; align-items: center; margin-bottom: 12px;">
      <label>Scan:
        <select onchange="window.location.href = '/scan/' + this.value">
          {% for sid in scan_ids %}
          <option value="{{ sid }}" {% if sid == current_id %}selected{% endif %}>{{ sid }}</option>
          {% endfor %}
        </select>
      </label>
      {% if report %}
      <span style="color: var(--muted); font-size: 12px;">
        {{ report.n_with_signal }}/{{ report.n_total }} signals · {{ report.n_errored }} errors · {{ report.duration_seconds }}s
      </span>
      {% endif %}
    </div>
    {% if report %}
      <div class="row" style="gap: 12px; margin-bottom: 8px;">
        <button hx-get="/scan/{{ current_id }}/table?sort=score" hx-target="#scan-table">Sort: score</button>
        <button hx-get="/scan/{{ current_id }}/table?sort=rr" hx-target="#scan-table">Sort: R:R</button>
        <button hx-get="/scan/{{ current_id }}/table?sort=ticker" hx-target="#scan-table">Sort: ticker</button>
        <button hx-get="/scan/{{ current_id }}/table?signals_only=true" hx-target="#scan-table">Signals only</button>
      </div>
      <div id="scan-table">{% include "_scan_table.html" %}</div>
    {% endif %}
  {% endif %}
</div>
{% endblock %}
```

```html
<!-- src/wave_alpha/web/templates/_scan_table.html -->
<table class="data-table">
  <thead>
    <tr>
      <th>Ticker</th><th>Pattern</th><th>Direction</th>
      <th>Score</th><th>Coherence</th>
      <th>Entry</th><th>Stop</th><th>Target</th><th>R:R</th>
      <th></th>
    </tr>
  </thead>
  <tbody>
  {% for r in rows %}
  <tr {% if r.has_error %}style="opacity:0.5;"{% endif %}>
    <td><a href="/deepdive/{{ r.ticker }}?as_of={{ r.as_of.isoformat() }}">{{ r.ticker }}</a></td>
    <td>{{ r.top_pattern or "—" }}</td>
    <td>{{ r.direction or "—" }}</td>
    <td>{{ "%.2f"|format(r.top_score) if r.top_score is not none else "—" }}</td>
    <td>{{ "%.2f"|format(r.coherence) if r.coherence is not none else "—" }}</td>
    <td>{{ r.signal_entry or "" }}</td>
    <td>{{ r.signal_stop or "" }}</td>
    <td>{{ r.signal_target or "" }}</td>
    <td>{{ "%.2f"|format(r.signal_rr|float) if r.signal_rr is not none else "" }}</td>
    <td>{% if r.has_error %}<span style="color: var(--danger);">{{ r.error }}</span>{% endif %}</td>
  </tr>
  {% endfor %}
  </tbody>
</table>
```

In `src/wave_alpha/web/app.py`:

```python
from wave_alpha.web.routes import deepdive, scan, settings, system, watchlist
...
app.include_router(scan.router)
```

- [ ] **Step 4: Tests pass**

Run: `uv run pytest tests/web/test_scan_routes.py -v`
Expected: 6/6 PASS

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/web/routes/scan.py src/wave_alpha/web/templates/scan.html \
  src/wave_alpha/web/templates/_scan_table.html src/wave_alpha/web/app.py \
  tests/web/test_scan_routes.py
git commit -m "$(cat <<'EOF'
feat(web): /scan dashboard reads JSON archives

GET /scan lists recent scan IDs (newest first) with a selector.
GET /scan/{id} renders the most recent scan or the selected one with a
sortable table. GET /scan/{id}/table?sort=...&signals_only=... is the
HTMX fragment for in-place sort and filter.

Each ticker links to its deep-dive page with the scan's as_of date so
the "what was the system seeing" view stays consistent.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Navigation links on existing pages

**Files:**
- Modify: `src/wave_alpha/web/templates/base.html`

- [ ] **Step 1: Read base.html**

Run: `cat src/wave_alpha/web/templates/base.html` to find where the title or nav lives.

- [ ] **Step 2: Add nav strip**

Add to the `<header>` (or create one if not present) inside `base.html`:

```html
<nav style="padding: 8px 16px; border-bottom: 1px solid var(--border); display: flex; gap: 16px;">
  <a href="/scan">Scan</a>
  <a href="/watchlist">Watchlist</a>
  <a href="/settings">Settings</a>
</nav>
```

- [ ] **Step 3: Smoke test**

Run: `uv run wave-alpha ui --no-browser --port 8765 &` then `curl -s http://127.0.0.1:8765/watchlist | head -30 && curl -s http://127.0.0.1:8765/scan | head -30`. Verify both pages render.

Kill the server: `pkill -f 'wave-alpha ui'` (or `kill %1`).

- [ ] **Step 4: Commit**

```bash
git add src/wave_alpha/web/templates/base.html
git commit -m "$(cat <<'EOF'
feat(web): nav strip linking Scan, Watchlist, Settings

Single horizontal nav in the base template. /deepdive/{ticker} pages
inherit it through the {% extends %} chain without per-page changes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase E Review Boundary

After Tasks 9-11:
- Manual smoke: launch UI, click through Watchlist → add AAPL → click AAPL link → land on deep-dive. Click Scan → empty state visible. Run `wave-alpha scan --symbols AAPL --as-of 2024-12-15` from another terminal, refresh /scan → row appears.
- Confirm `_scan_dir` and `_watchlist_path` indirections used everywhere (greps OK).

If clean → proceed to Phase F.

---

## Phase F — Version bump + README + final review

### Task 12: Version bump + README

**Files:**
- Modify: `src/wave_alpha/__init__.py`
- Modify: `pyproject.toml`
- Modify: `README.md`

- [ ] **Step 1: Bump to 0.5.0**

Edit `src/wave_alpha/__init__.py`:
```python
__version__ = "0.5.0"
```

Edit `pyproject.toml` version field similarly.

- [ ] **Step 2: README addition**

Append to `README.md`:

```markdown
## Watchlist + Scan (v0.5)

Manage a watchlist and run multi-ticker scans:

```bash
# Add to watchlist
wave-alpha watchlist add AAPL
wave-alpha watchlist add MSFT

# Bulk import
wave-alpha watchlist import portfolio.csv --format csv
wave-alpha watchlist import tradingview.txt --format tv-txt

# Scan all watchlist tickers (default LLM mode = disabled, no API cost)
wave-alpha scan

# Scan an ad-hoc list
wave-alpha scan --symbols AAPL,MSFT,NVDA --as-of 2026-05-01

# Browse results in the web UI
wave-alpha ui
# → http://127.0.0.1:8765/scan
# → http://127.0.0.1:8765/watchlist
```

Scan reports are persisted as JSON to `~/.wave_alpha/scans/<scan_id>.json` so the dashboard can reload without re-running.

For a nightly batch, schedule `wave-alpha scan` from cron — the OHLCV cache (`CachedProvider`) refreshes per (ticker, interval, end-date) so each new bar close re-fetches automatically.
```

- [ ] **Step 3: Verify**

Run:
```bash
uv run wave-alpha version
uv run wave-alpha watchlist --help
uv run wave-alpha scan --help
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
```

Expected: `0.5.0`, both helps render, all tests green, ruff clean.

- [ ] **Step 4: Commit**

```bash
git add src/wave_alpha/__init__.py pyproject.toml README.md
git commit -m "$(cat <<'EOF'
chore(release): bump to v0.5.0; document watchlist + scan

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase F Review Boundary (final cross-cutting)

Controller dispatches one final code-quality reviewer subagent across the whole feature branch:
- No business logic in `web/routes/scan.py` or `web/routes/watchlist.py` (they call `scan/`, `watchlist/`, `archive`).
- No `import anthropic` in `scan/` or `watchlist/` modules.
- Tests cover the empty-state paths (no reports, no watchlist, error rows).
- Indirection helpers (`_scan_dir`, `_watchlist_path`) used consistently.
- `uv lock` updated and committed.

If review finds blockers → fix loop. Otherwise:
- Merge to main with `--no-ff`.
- Run `npx gitnexus analyze` post-merge per CLAUDE.md hook.

---

## Self-Review Checklist (controller fills this in before dispatch)

- [ ] Spec coverage: watchlist (manual + CSV + TV TXT import), scan dashboard with sort/filter, nightly-batch CLI, cache invalidation honored (via existing CachedProvider), JSON archive for replay. ✓
- [ ] Placeholder scan: every step has actual code, file paths, commands. ✓
- [ ] Type consistency: `WatchlistEntry` shape stable across store/parsers/CLI/web; `ScanRow` and `ScanReport` field names consistent across orchestrator, archive, ranking, web. ✓
- [ ] No phantom symbols: `TradeSignal.rr` field exists (verified — `signal_rr=Decimal(str(signal.rr))`), `_predicted_direction` reused across pipeline + scan, `LLMMode` + `LLMRunner` + `_build_runner` reused as-is. ✓
