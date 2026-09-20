# Stooq OHLCV Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Stooq OHLCV upstream behind a new `FallbackProvider` decorator so that `wave-alpha analyze` / `scan` keep working when Yahoo Finance is unavailable, throttled, or returns empty.

**Architecture:** Pure additive composition. A new `FallbackProvider` decorator wraps an ordered list of `OHLCVProvider`s and tries each in turn (skipping providers that don't support the requested interval, falling through on exception or empty result). `StooqProvider` is a new concrete provider that fetches CSV from `https://stooq.com/q/d/l/` over stdlib HTTP, supporting daily and weekly only. Two production wiring sites (`cli/app.py`, `web/deps.py`) get a one-line change to wrap their existing `YahooProvider` in `FallbackProvider([YahooProvider(), StooqProvider()])`. No engine, template, right-edge, LLM, or backtest logic changes.

**Tech Stack:** Python 3.11+, `urllib.request` (stdlib HTTP), stdlib `csv`, `decimal.Decimal`, `pydantic` v2 (existing `Bar` model), `pytest` with `--strict-markers`, `loguru` for debug logging, `uv` for dep management.

**Spec:** `docs/superpowers/specs/2026-05-10-stooq-provider-design.md`.

---

## File structure

| Path | Action | Responsibility |
|------|--------|----------------|
| `src/wave_alpha/data/provider.py` | MODIFY | Add `supported_intervals: frozenset[Interval]` class attribute with default = all |
| `src/wave_alpha/data/stooq.py` | CREATE | `StooqProvider`, symbol mapping, CSV parser, `_http_get` indirection |
| `src/wave_alpha/data/fallback.py` | CREATE | `FallbackProvider` — ordered chain, interval-aware skip, empty/raise fall-through |
| `src/wave_alpha/cli/app.py` | MODIFY | `_default_provider()`: wrap `YahooProvider()` in `FallbackProvider` |
| `src/wave_alpha/web/deps.py` | MODIFY | `_run_analysis_uncached()`: same wrapping change |
| `pyproject.toml` | MODIFY | Register `network` pytest marker; update `addopts` to skip it by default |
| `CHANGELOG.md` | MODIFY | `[Unreleased] / Added` entry for Stooq fallback |
| `tests/fixtures/stooq_aapl_5d_daily.csv` | CREATE | 5 daily bars for parser fixture test |
| `tests/fixtures/stooq_aapl_5w_weekly.csv` | CREATE | 5 weekly bars for parser fixture test |
| `tests/data/test_stooq.py` | CREATE | Unit tests for symbol mapping, CSV parsing, supported intervals |
| `tests/data/test_fallback.py` | CREATE | Unit tests for FallbackProvider chain semantics |
| `tests/data/test_cached_with_fallback.py` | CREATE | Integration test: CachedProvider wrapping FallbackProvider |
| `tests/data/test_stooq_network.py` | CREATE | Opt-in real-network test (`@pytest.mark.network`) |
| `tests/data/test_simulated_yahoo_failure.py` | CREATE | End-to-end: Yahoo monkeypatched to raise → result populated from Stooq |

The two new modules (`stooq.py`, `fallback.py`) are intentionally small (~70-100 lines each) and have one responsibility apiece. Tests are split per module so each test file has one subject under test.

---

## Task ordering rationale

1. Foundation first: `OHLCVProvider.supported_intervals` so later tasks can declare interval support.
2. Pure-function pieces of `StooqProvider` (symbol mapping, CSV parsing) before the class skeleton, so the class assembles tested primitives.
3. `FallbackProvider` is independent of `StooqProvider`'s internals — it can be tested with fakes, then verified against the real `StooqProvider` via the integration test.
4. Wiring last, after all pieces are tested in isolation.
5. Pytest config + network test + end-to-end simulation come at the end as the verification layer.
6. CHANGELOG and final scope check are housekeeping.

---

## Task 1: Extend `OHLCVProvider` with `supported_intervals`

**Files:**
- Modify: `src/wave_alpha/data/provider.py`
- Test: existing tests in `tests/data/test_yahoo.py`, `tests/data/test_cached.py` must continue to pass unchanged.

- [ ] **Step 1: Modify `provider.py`**

Replace the file with:

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from wave_alpha.domain import Bar, Interval


class OHLCVProvider(ABC):
    """Pluggable OHLCV bar source.

    Subclasses MAY override `supported_intervals` to declare a subset of
    `Interval`. The default declares full support so existing concrete
    providers and test fakes need no modification.
    """

    supported_intervals: frozenset[Interval] = frozenset(Interval)

    @abstractmethod
    def fetch(
        self,
        ticker: str,
        interval: Interval,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        """Return bars in ascending timestamp order, filtered to [start, end]."""
```

- [ ] **Step 2: Run existing data tests to verify no regressions**

Run: `uv run pytest tests/data/ -v`
Expected: PASS (every existing test still passes; the new attribute has a default that covers all subclasses).

- [ ] **Step 3: Run ruff**

Run: `uv run ruff check src/wave_alpha/data/provider.py`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add src/wave_alpha/data/provider.py
git commit -m "$(cat <<'EOF'
feat(data): add supported_intervals declaration to OHLCVProvider

Backward-compatible class attribute defaulting to all Intervals. Lets
FallbackProvider skip providers that don't support a requested interval
(e.g. Stooq has no 4h data).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Record Stooq fixture CSVs

**Files:**
- Create: `tests/fixtures/stooq_aapl_5d_daily.csv`
- Create: `tests/fixtures/stooq_aapl_5w_weekly.csv`

These are hand-curated to match Stooq's real CSV shape (`Date,Open,High,Low,Close,Volume`, ascending). Values are illustrative — tests only assert structural properties (count, ordering, Decimal precision, ascending-by-date), not exact prices.

- [ ] **Step 1: Create the daily fixture**

Path: `tests/fixtures/stooq_aapl_5d_daily.csv`
Content:

```csv
Date,Open,High,Low,Close,Volume
2024-04-22,165.5200,167.2600,164.7700,165.8400,48117420
2024-04-23,166.5000,167.0500,164.9200,166.9000,49537770
2024-04-24,166.2100,167.1100,164.8100,165.0500,46291800
2024-04-25,164.8500,170.3000,164.6500,169.8900,75404820
2024-04-26,169.8800,171.3400,168.4900,169.3000,68262500
```

- [ ] **Step 2: Create the weekly fixture**

Path: `tests/fixtures/stooq_aapl_5w_weekly.csv`

Stooq weekly bars are Friday-anchored. Five bars at week-end Fridays:

```csv
Date,Open,High,Low,Close,Volume
2024-03-29,170.7300,176.4200,168.5600,171.4800,250300000
2024-04-05,171.1900,178.3600,168.7900,169.5800,283100000
2024-04-12,168.7600,170.4500,165.6700,169.8200,290500000
2024-04-19,169.4600,170.6800,164.0100,165.0000,295200000
2024-04-26,165.5200,171.3400,164.6500,169.3000,348100000
```

- [ ] **Step 3: Verify the files were written**

Run: `wc -l tests/fixtures/stooq_aapl_5d_daily.csv tests/fixtures/stooq_aapl_5w_weekly.csv`
Expected: each file has 6 lines (1 header + 5 rows).

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/stooq_aapl_5d_daily.csv tests/fixtures/stooq_aapl_5w_weekly.csv
git commit -m "$(cat <<'EOF'
test(data): add Stooq CSV fixtures for parser tests

Five daily bars and five Friday-anchored weekly bars matching Stooq's
real CSV header. Hand-curated illustrative values; tests assert
structure, not specific prices.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `StooqProvider` symbol mapping

**Files:**
- Create: `src/wave_alpha/data/stooq.py` (initial scaffold — symbol mapping only)
- Create: `tests/data/test_stooq.py` (initial — symbol mapping tests only)

- [ ] **Step 1: Write the failing test**

Path: `tests/data/test_stooq.py`

```python
from __future__ import annotations

from wave_alpha.data.stooq import _to_stooq_symbol


def test_symbol_mapping_plain_us_equity() -> None:
    assert _to_stooq_symbol("AAPL") == "aapl.us"
    assert _to_stooq_symbol("MSFT") == "msft.us"
    assert _to_stooq_symbol("GOOGL") == "googl.us"


def test_symbol_mapping_class_share_uses_dash() -> None:
    """Stooq encodes class shares with `-` not `.` (e.g., BRK.B -> brk-b.us)."""
    assert _to_stooq_symbol("BRK.B") == "brk-b.us"
    assert _to_stooq_symbol("BF.B") == "bf-b.us"


def test_symbol_mapping_lowercases() -> None:
    assert _to_stooq_symbol("aapl") == "aapl.us"
    assert _to_stooq_symbol("Aapl") == "aapl.us"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/data/test_stooq.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'wave_alpha.data.stooq'`.

- [ ] **Step 3: Create the module with the symbol-mapping function**

Path: `src/wave_alpha/data/stooq.py`

```python
from __future__ import annotations


def _to_stooq_symbol(ticker: str) -> str:
    """Convert a US-equity ticker to Stooq's symbol grammar.

    `AAPL` -> `aapl.us`. Class shares use `-` instead of `.`
    (Stooq's convention): `BRK.B` -> `brk-b.us`.
    """
    return ticker.lower().replace(".", "-") + ".us"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/data/test_stooq.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/data/stooq.py tests/data/test_stooq.py
git commit -m "$(cat <<'EOF'
feat(data): add Stooq symbol mapping

US-equity tickers map to Stooq's grammar: lowercase with class-share
dots replaced by dashes plus `.us` suffix. Non-US symbols deferred.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `StooqProvider` CSV parser

**Files:**
- Modify: `src/wave_alpha/data/stooq.py` (add `_parse_stooq_csv`)
- Modify: `tests/data/test_stooq.py` (add parser tests)

- [ ] **Step 1: Append the failing tests**

Append to `tests/data/test_stooq.py`:

```python
from datetime import date
from decimal import Decimal
from pathlib import Path

from wave_alpha.data.stooq import _parse_stooq_csv


_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_parses_daily_fixture() -> None:
    csv_text = (_FIXTURES / "stooq_aapl_5d_daily.csv").read_text()
    bars = _parse_stooq_csv(csv_text)

    assert len(bars) == 5
    assert bars[0].timestamp == date(2024, 4, 22)
    assert bars[-1].timestamp == date(2024, 4, 26)
    # Ascending order
    assert bars == sorted(bars, key=lambda b: b.timestamp)
    # Decimal precision preserved
    assert bars[0].close == Decimal("165.8400")
    # high >= low always
    assert all(b.high >= b.low for b in bars)


def test_parses_weekly_fixture() -> None:
    csv_text = (_FIXTURES / "stooq_aapl_5w_weekly.csv").read_text()
    bars = _parse_stooq_csv(csv_text)

    assert len(bars) == 5
    assert bars[0].timestamp == date(2024, 3, 29)
    assert bars[-1].timestamp == date(2024, 4, 26)


def test_no_data_sentinel_returns_empty() -> None:
    """Stooq emits literal 'No data' for unknown/delisted tickers."""
    assert _parse_stooq_csv("No data") == []
    assert _parse_stooq_csv("No data\n") == []


def test_empty_string_returns_empty() -> None:
    assert _parse_stooq_csv("") == []


def test_skips_halted_rows_with_dash_sentinel() -> None:
    """Stooq emits '-' for OHLC values on halted/no-trade days."""
    csv_text = (
        "Date,Open,High,Low,Close,Volume\n"
        "2024-04-22,165.52,167.26,164.77,165.84,48117420\n"
        "2024-04-23,-,-,-,-,0\n"
        "2024-04-24,166.21,167.11,164.81,165.05,46291800\n"
    )
    bars = _parse_stooq_csv(csv_text)
    assert len(bars) == 2
    assert [b.timestamp for b in bars] == [date(2024, 4, 22), date(2024, 4, 24)]


def test_resorts_descending_input_to_ascending() -> None:
    """Stooq's response order varies; parser normalizes to ascending."""
    csv_text = (
        "Date,Open,High,Low,Close,Volume\n"
        "2024-04-24,166.21,167.11,164.81,165.05,46291800\n"
        "2024-04-23,166.50,167.05,164.92,166.90,49537770\n"
        "2024-04-22,165.52,167.26,164.77,165.84,48117420\n"
    )
    bars = _parse_stooq_csv(csv_text)
    assert [b.timestamp for b in bars] == [
        date(2024, 4, 22),
        date(2024, 4, 23),
        date(2024, 4, 24),
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/data/test_stooq.py -v`
Expected: 6 new tests FAIL with `ImportError: cannot import name '_parse_stooq_csv'`.

- [ ] **Step 3: Replace `stooq.py` with the symbol mapper plus the parser**

Replace the entire contents of `src/wave_alpha/data/stooq.py` with:

```python
from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from io import StringIO

from wave_alpha.domain import Bar


_HALT_SENTINELS = {"-", "", "null", "NaN"}


def _to_stooq_symbol(ticker: str) -> str:
    """Convert a US-equity ticker to Stooq's symbol grammar.

    `AAPL` -> `aapl.us`. Class shares use `-` instead of `.`
    (Stooq's convention): `BRK.B` -> `brk-b.us`.
    """
    return ticker.lower().replace(".", "-") + ".us"


def _parse_stooq_csv(csv_text: str) -> list[Bar]:
    """Parse Stooq's CSV body into ascending-by-date Bars.

    Returns `[]` for the literal "No data" sentinel, an empty body, or
    any input whose first line isn't the expected `Date,...` header.
    Skips rows whose Open or Close is empty or `-` (halted-day sentinel).
    """
    csv_text = csv_text.strip()
    if not csv_text or not csv_text.startswith("Date,"):
        return []

    bars: list[Bar] = []
    for row in csv.DictReader(StringIO(csv_text)):
        raw_open = row.get("Open", "").strip()
        raw_close = row.get("Close", "").strip()
        if raw_open in _HALT_SENTINELS or raw_close in _HALT_SENTINELS:
            continue
        try:
            bars.append(
                Bar(
                    timestamp=date.fromisoformat(row["Date"]),
                    open=Decimal(f"{float(raw_open):.4f}"),
                    high=Decimal(f"{float(row['High']):.4f}"),
                    low=Decimal(f"{float(row['Low']):.4f}"),
                    close=Decimal(f"{float(raw_close):.4f}"),
                    volume=int(row.get("Volume") or 0),
                )
            )
        except (ValueError, KeyError):
            # Malformed row — skip rather than blow up the whole batch.
            continue

    bars.sort(key=lambda b: b.timestamp)
    return bars
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/data/test_stooq.py -v`
Expected: PASS (9 tests total — 3 from Task 3 + 6 new).

- [ ] **Step 5: Run ruff**

Run: `uv run ruff check src/wave_alpha/data/stooq.py tests/data/test_stooq.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/data/stooq.py tests/data/test_stooq.py
git commit -m "$(cat <<'EOF'
feat(data): add Stooq CSV parser

Parses Stooq's CSV body to ascending-by-date Bars. Handles the 'No data'
sentinel, empty bodies, halted-day '-' rows, and out-of-order inputs.
Decimal precision matches the existing yfinance parser (4dp).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `StooqProvider` class with HTTP fetch

**Files:**
- Modify: `src/wave_alpha/data/stooq.py` (add class + `_http_get`)
- Modify: `tests/data/test_stooq.py` (add class-level tests)

- [ ] **Step 1: Append the failing tests**

Append to `tests/data/test_stooq.py`:

```python
from wave_alpha.data.stooq import StooqProvider
from wave_alpha.domain import Interval


def test_supported_intervals_is_daily_and_weekly() -> None:
    assert StooqProvider.supported_intervals == frozenset(
        {Interval.DAILY, Interval.WEEKLY}
    )


def test_supported_intervals_excludes_4h() -> None:
    assert Interval.HOURLY_4 not in StooqProvider.supported_intervals


def test_fetch_4h_returns_empty_defensively(monkeypatch) -> None:
    """If a caller bypasses FallbackProvider's interval skip and asks Stooq
    for 4h directly, return [] rather than hit HTTP."""

    def boom(*args, **kwargs):
        raise AssertionError("HTTP must not be called for unsupported interval")

    monkeypatch.setattr("wave_alpha.data.stooq._http_get", boom)
    bars = StooqProvider().fetch("AAPL", Interval.HOURLY_4)
    assert bars == []


def test_fetch_daily_calls_correct_url(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_http(url: str, *, timeout: float = 30.0) -> str:
        captured["url"] = url
        return "Date,Open,High,Low,Close,Volume\n2024-04-22,165.52,167.26,164.77,165.84,48117420\n"

    monkeypatch.setattr("wave_alpha.data.stooq._http_get", fake_http)
    bars = StooqProvider().fetch("AAPL", Interval.DAILY)

    assert captured["url"] == "https://stooq.com/q/d/l/?s=aapl.us&i=d"
    assert len(bars) == 1
    assert bars[0].timestamp == date(2024, 4, 22)


def test_fetch_weekly_calls_correct_url(monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_http(url: str, *, timeout: float = 30.0) -> str:
        captured["url"] = url
        return "Date,Open,High,Low,Close,Volume\n2024-04-26,165.52,171.34,164.65,169.30,348100000\n"

    monkeypatch.setattr("wave_alpha.data.stooq._http_get", fake_http)
    bars = StooqProvider().fetch("AAPL", Interval.WEEKLY)

    assert captured["url"] == "https://stooq.com/q/d/l/?s=aapl.us&i=w"
    assert len(bars) == 1


def test_fetch_propagates_http_errors(monkeypatch) -> None:
    """StooqProvider does not retry; transport errors bubble up so
    FallbackProvider can catch them."""
    from urllib.error import URLError

    def fake_http(url: str, *, timeout: float = 30.0) -> str:
        raise URLError("connection refused")

    monkeypatch.setattr("wave_alpha.data.stooq._http_get", fake_http)
    import pytest

    with pytest.raises(URLError):
        StooqProvider().fetch("AAPL", Interval.DAILY)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/data/test_stooq.py -v`
Expected: 6 new tests FAIL with `ImportError: cannot import name 'StooqProvider'`.

- [ ] **Step 3: Replace `stooq.py` with the full module**

Replace the entire contents of `src/wave_alpha/data/stooq.py` with:

```python
from __future__ import annotations

import csv
import urllib.request
from datetime import date
from decimal import Decimal
from io import StringIO

from loguru import logger

from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval


_HALT_SENTINELS = {"-", "", "null", "NaN"}

_INTERVAL_TO_STOOQ: dict[Interval, str] = {
    Interval.DAILY: "d",
    Interval.WEEKLY: "w",
}


def _to_stooq_symbol(ticker: str) -> str:
    """Convert a US-equity ticker to Stooq's symbol grammar.

    `AAPL` -> `aapl.us`. Class shares use `-` instead of `.`
    (Stooq's convention): `BRK.B` -> `brk-b.us`.
    """
    return ticker.lower().replace(".", "-") + ".us"


def _http_get(url: str, *, timeout: float = 30.0) -> str:
    """Fetch `url` as UTF-8 text. Indirection lets tests monkeypatch."""
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def _parse_stooq_csv(csv_text: str) -> list[Bar]:
    """Parse Stooq's CSV body into ascending-by-date Bars.

    Returns `[]` for the literal "No data" sentinel, an empty body, or
    any input whose first line isn't the expected `Date,...` header.
    Skips rows whose Open or Close is empty or `-` (halted-day sentinel).
    """
    csv_text = csv_text.strip()
    if not csv_text or not csv_text.startswith("Date,"):
        return []

    bars: list[Bar] = []
    for row in csv.DictReader(StringIO(csv_text)):
        raw_open = row.get("Open", "").strip()
        raw_close = row.get("Close", "").strip()
        if raw_open in _HALT_SENTINELS or raw_close in _HALT_SENTINELS:
            continue
        try:
            bars.append(
                Bar(
                    timestamp=date.fromisoformat(row["Date"]),
                    open=Decimal(f"{float(raw_open):.4f}"),
                    high=Decimal(f"{float(row['High']):.4f}"),
                    low=Decimal(f"{float(row['Low']):.4f}"),
                    close=Decimal(f"{float(raw_close):.4f}"),
                    volume=int(row.get("Volume") or 0),
                )
            )
        except (ValueError, KeyError):
            # Malformed row — skip rather than blow up the whole batch.
            continue

    bars.sort(key=lambda b: b.timestamp)
    return bars


class StooqProvider(OHLCVProvider):
    """Stooq CSV-endpoint OHLCV provider. US equities only.

    Stooq has no intraday data; `supported_intervals` excludes 4h.
    Window filtering is the caller's (CachedProvider's) responsibility.
    """

    supported_intervals = frozenset({Interval.DAILY, Interval.WEEKLY})

    def fetch(
        self,
        ticker: str,
        interval: Interval,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        if interval not in self.supported_intervals:
            return []

        symbol = _to_stooq_symbol(ticker)
        url = f"https://stooq.com/q/d/l/?s={symbol}&i={_INTERVAL_TO_STOOQ[interval]}"
        logger.debug(
            "stooq fetch ticker={} interval={} url={}", ticker, interval.value, url
        )

        csv_text = _http_get(url)
        return _parse_stooq_csv(csv_text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/data/test_stooq.py -v`
Expected: PASS (15 tests).

- [ ] **Step 5: Run ruff**

Run: `uv run ruff check src/wave_alpha/data/stooq.py tests/data/test_stooq.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/data/stooq.py tests/data/test_stooq.py
git commit -m "$(cat <<'EOF'
feat(data): add StooqProvider class with HTTP fetch

Composes the symbol mapper and CSV parser behind an OHLCVProvider with
supported_intervals={DAILY, WEEKLY}. Uses stdlib urllib.request through
a small _http_get indirection that tests monkeypatch. No retries inside
the provider; transport errors propagate for FallbackProvider to catch.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `FallbackProvider`

**Files:**
- Create: `src/wave_alpha/data/fallback.py`
- Create: `tests/data/test_fallback.py`

- [ ] **Step 1: Write the failing tests**

Path: `tests/data/test_fallback.py`

```python
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from wave_alpha.data.fallback import FallbackProvider
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval


class _FakeProvider(OHLCVProvider):
    """Configurable fake. Records calls and either returns bars or raises."""

    def __init__(
        self,
        *,
        bars: list[Bar] | None = None,
        raises: Exception | None = None,
        supports: frozenset[Interval] | None = None,
    ) -> None:
        self._bars = bars or []
        self._raises = raises
        if supports is not None:
            # Per-instance override (descriptor-friendly).
            self.supported_intervals = supports
        self.calls = 0

    def fetch(
        self,
        ticker: str,
        interval: Interval,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return list(self._bars)


def _bar(d: date) -> Bar:
    p = Decimal("100")
    return Bar(timestamp=d, open=p, high=p, low=p, close=p, volume=1)


def test_uses_first_provider_when_succeeds() -> None:
    primary = _FakeProvider(bars=[_bar(date(2024, 1, 2))])
    secondary = _FakeProvider(bars=[_bar(date(2024, 1, 3))])
    chain = FallbackProvider([primary, secondary])

    bars = chain.fetch("AAPL", Interval.DAILY)

    assert len(bars) == 1
    assert bars[0].timestamp == date(2024, 1, 2)
    assert primary.calls == 1
    assert secondary.calls == 0


def test_falls_through_on_empty() -> None:
    primary = _FakeProvider(bars=[])
    secondary = _FakeProvider(bars=[_bar(date(2024, 1, 3))])
    chain = FallbackProvider([primary, secondary])

    bars = chain.fetch("AAPL", Interval.DAILY)

    assert len(bars) == 1
    assert bars[0].timestamp == date(2024, 1, 3)
    assert primary.calls == 1
    assert secondary.calls == 1


def test_falls_through_on_raise() -> None:
    primary = _FakeProvider(raises=RuntimeError("yfinance died"))
    secondary = _FakeProvider(bars=[_bar(date(2024, 1, 3))])
    chain = FallbackProvider([primary, secondary])

    bars = chain.fetch("AAPL", Interval.DAILY)

    assert len(bars) == 1
    assert bars[0].timestamp == date(2024, 1, 3)
    assert primary.calls == 1
    assert secondary.calls == 1


def test_skips_unsupported_provider() -> None:
    """Provider that doesn't declare support for the requested interval is
    skipped without being called."""
    only_wd = _FakeProvider(
        bars=[_bar(date(2024, 1, 5))],
        supports=frozenset({Interval.DAILY, Interval.WEEKLY}),
    )
    full = _FakeProvider(bars=[_bar(date(2024, 1, 6))])
    chain = FallbackProvider([only_wd, full])

    bars = chain.fetch("AAPL", Interval.HOURLY_4)

    assert len(bars) == 1
    assert bars[0].timestamp == date(2024, 1, 6)
    assert only_wd.calls == 0, "unsupported provider must not be called"
    assert full.calls == 1


def test_all_empty_returns_empty() -> None:
    primary = _FakeProvider(bars=[])
    secondary = _FakeProvider(bars=[])
    chain = FallbackProvider([primary, secondary])

    assert chain.fetch("AAPL", Interval.DAILY) == []
    assert primary.calls == 1
    assert secondary.calls == 1


def test_all_fail_returns_empty() -> None:
    primary = _FakeProvider(raises=RuntimeError("a"))
    secondary = _FakeProvider(raises=ConnectionError("b"))
    chain = FallbackProvider([primary, secondary])

    # All providers raised; FallbackProvider must NOT propagate.
    assert chain.fetch("AAPL", Interval.DAILY) == []
    assert primary.calls == 1
    assert secondary.calls == 1


def test_supported_intervals_is_union() -> None:
    only_d = _FakeProvider(supports=frozenset({Interval.DAILY}))
    only_w = _FakeProvider(supports=frozenset({Interval.WEEKLY}))
    chain = FallbackProvider([only_d, only_w])

    assert chain.supported_intervals == frozenset({Interval.DAILY, Interval.WEEKLY})


def test_empty_provider_list_raises() -> None:
    with pytest.raises(ValueError):
        FallbackProvider([])


def test_passes_ticker_interval_start_end_to_providers() -> None:
    """Sanity: arguments forwarded unchanged."""
    captured: dict = {}

    class _Recording(OHLCVProvider):
        def fetch(self, ticker, interval, start=None, end=None):
            captured.update(
                ticker=ticker, interval=interval, start=start, end=end
            )
            return [_bar(date(2024, 1, 2))]

    chain = FallbackProvider([_Recording()])
    chain.fetch("MSFT", Interval.WEEKLY, start=date(2020, 1, 1), end=date(2024, 1, 1))

    assert captured == {
        "ticker": "MSFT",
        "interval": Interval.WEEKLY,
        "start": date(2020, 1, 1),
        "end": date(2024, 1, 1),
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/data/test_fallback.py -v`
Expected: all FAIL with `ModuleNotFoundError: No module named 'wave_alpha.data.fallback'`.

- [ ] **Step 3: Implement `FallbackProvider`**

Path: `src/wave_alpha/data/fallback.py`

```python
from __future__ import annotations

from datetime import date
from typing import Sequence

from loguru import logger

from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval


class FallbackProvider(OHLCVProvider):
    """Tries each provider in order. Skips providers whose
    `supported_intervals` does not include the requested interval.
    Returns the first non-empty result; falls through on raise or empty.
    Returns `[]` if every provider skips, fails, or yields empty.
    """

    def __init__(self, providers: Sequence[OHLCVProvider]) -> None:
        if not providers:
            raise ValueError("FallbackProvider requires at least one provider")
        self._providers = list(providers)

    @property
    def supported_intervals(self) -> frozenset[Interval]:
        result: frozenset[Interval] = frozenset()
        for p in self._providers:
            result = result | p.supported_intervals
        return result

    def fetch(
        self,
        ticker: str,
        interval: Interval,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        for provider in self._providers:
            if interval not in provider.supported_intervals:
                logger.debug(
                    "fallback skip provider={} interval={} (unsupported)",
                    type(provider).__name__,
                    interval.value,
                )
                continue
            try:
                bars = provider.fetch(ticker, interval, start=start, end=end)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "fallback provider={} raised: {}; trying next",
                    type(provider).__name__,
                    exc,
                )
                continue
            if bars:
                return bars
            logger.debug(
                "fallback provider={} returned empty; trying next",
                type(provider).__name__,
            )
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/data/test_fallback.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Run ruff**

Run: `uv run ruff check src/wave_alpha/data/fallback.py tests/data/test_fallback.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/data/fallback.py tests/data/test_fallback.py
git commit -m "$(cat <<'EOF'
feat(data): add FallbackProvider chain decorator

Tries providers in order, skipping any whose supported_intervals
exclude the requested interval, and falling through on exception or
empty result. Returns [] if every provider skips, fails, or yields
empty (no exception ever propagates).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Integration test — `CachedProvider` wrapping `FallbackProvider`

**Files:**
- Create: `tests/data/test_cached_with_fallback.py`

This task adds no production code — it verifies the existing `CachedProvider` semantics (hit / miss / stale-tail / persist) all work when its `upstream` is a `FallbackProvider` rather than a single concrete provider. Catches any subtle interaction (e.g., `FallbackProvider` returning `[]` propagating to `CachedProvider._cache.get` correctly).

- [ ] **Step 1: Write the test**

Path: `tests/data/test_cached_with_fallback.py`

```python
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from wave_alpha.data.cached import CachedProvider
from wave_alpha.data.fallback import FallbackProvider
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval


class _CountingProvider(OHLCVProvider):
    def __init__(
        self,
        *,
        bars: list[Bar] | None = None,
        raises: Exception | None = None,
        supports: frozenset[Interval] | None = None,
    ) -> None:
        self._bars = bars or []
        self._raises = raises
        if supports is not None:
            self.supported_intervals = supports
        self.calls = 0

    def fetch(self, ticker, interval, start=None, end=None):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return list(self._bars)


def _bar(d: date, price: str = "100") -> Bar:
    p = Decimal(price)
    return Bar(timestamp=d, open=p, high=p, low=p, close=p, volume=1)


def test_cached_caches_bars_supplied_by_secondary(tmp_path: Path) -> None:
    """Yahoo fails on first call → Stooq supplies → CachedProvider persists.
    Second call: Yahoo recovers but cache is fresh → no upstream call at all."""
    yahoo = _CountingProvider(raises=RuntimeError("rate-limited"))
    stooq = _CountingProvider(bars=[_bar(date(2024, 1, 2)), _bar(date(2024, 1, 3))])
    chain = FallbackProvider([yahoo, stooq])
    cached = CachedProvider(upstream=chain, db_path=tmp_path / "c.sqlite")

    first = cached.fetch("AAPL", Interval.DAILY)
    assert len(first) == 2
    assert yahoo.calls == 1
    assert stooq.calls == 1

    # Second call: cache fresh enough (end=None → any cache is fresh).
    second = cached.fetch("AAPL", Interval.DAILY)
    assert len(second) == 2
    assert yahoo.calls == 1, "no extra upstream call after cache populated"
    assert stooq.calls == 1


def test_cached_returns_cache_when_chain_empty(tmp_path: Path) -> None:
    """Chain returns [] → CachedProvider falls back to whatever cache holds."""
    yahoo = _CountingProvider(bars=[_bar(date(2024, 1, 2))])
    stooq = _CountingProvider(bars=[])
    chain = FallbackProvider([yahoo, stooq])
    cached = CachedProvider(upstream=chain, db_path=tmp_path / "c.sqlite")

    # Populate cache via Yahoo.
    cached.fetch("AAPL", Interval.DAILY)
    yahoo_baseline = yahoo.calls

    # Now both fail → chain returns [] → cache served.
    yahoo._raises = RuntimeError("died")
    yahoo._bars = []
    yahoo.calls = 0
    stooq.calls = 0

    # Force a stale read: pass an end date far in the future so freshness
    # check forces an upstream call.
    out = cached.fetch("AAPL", Interval.DAILY, end=date(2099, 1, 1))
    # Both providers consulted; both empty/failing → cache returned.
    assert len(out) == 1, "should have served the previously-cached bar"
    assert yahoo_baseline >= 1


def test_cached_skips_4h_for_stooq_only_chain(tmp_path: Path) -> None:
    """When only Stooq supports D/W, a 4h request cleanly produces []
    (Yahoo isn't in the chain in this hypothetical scenario)."""
    stooq_only = _CountingProvider(
        bars=[_bar(date(2024, 1, 2))],
        supports=frozenset({Interval.DAILY, Interval.WEEKLY}),
    )
    chain = FallbackProvider([stooq_only])
    cached = CachedProvider(upstream=chain, db_path=tmp_path / "c.sqlite")

    bars = cached.fetch("AAPL", Interval.HOURLY_4)
    assert bars == []
    assert stooq_only.calls == 0
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/data/test_cached_with_fallback.py -v`
Expected: PASS (3 tests).

- [ ] **Step 3: Run ruff**

Run: `uv run ruff check tests/data/test_cached_with_fallback.py`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add tests/data/test_cached_with_fallback.py
git commit -m "$(cat <<'EOF'
test(data): integration test for CachedProvider wrapping FallbackProvider

Verifies cache hit/miss/persist semantics work when upstream is the
fallback chain. Covers: secondary supplies bars (cache persists them),
chain empty falls back to cache, fully-skipped chain produces [].

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Wire `FallbackProvider` into production providers

**Files:**
- Modify: `src/wave_alpha/cli/app.py:614-622` (`_default_provider`)
- Modify: `src/wave_alpha/web/deps.py:34-57` (`_run_analysis_uncached`)

- [ ] **Step 1: Modify `cli/app.py`**

Add imports at the top with the other `wave_alpha.data.*` imports (around line 17-19, alongside `CachedProvider` and `YahooProvider`):

```python
from wave_alpha.data.fallback import FallbackProvider
from wave_alpha.data.stooq import StooqProvider
```

Replace the `_default_provider` body (currently `cli/app.py:622`):

```python
def _default_provider() -> OHLCVProvider:
    """Production provider stack: Yahoo (primary) + Stooq (fallback) wrapped
    in a SQLite-backed cache.

    The cache lives at `~/.wave_alpha/cache.sqlite` and persists OHLCV bars
    across CLI invocations so a `wave-alpha analyze AAPL` run only hits an
    upstream when the cache is cold for that `(ticker, interval)` pair.

    Stooq covers daily/weekly only; 4h fetches stay Yahoo-only.
    """
    _DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return CachedProvider(
        upstream=FallbackProvider([YahooProvider(), StooqProvider()]),
        db_path=_DEFAULT_DATA_DIR / "cache.sqlite",
    )
```

- [ ] **Step 2: Modify `web/deps.py`**

Add imports at the top (next to the existing `wave_alpha.data.*` imports, around line 7-8):

```python
from wave_alpha.data.fallback import FallbackProvider
from wave_alpha.data.stooq import StooqProvider
```

Replace the provider construction line (currently `web/deps.py:38`):

```python
    provider = CachedProvider(
        upstream=FallbackProvider([YahooProvider(), StooqProvider()]),
        db_path=data_dir / "cache.sqlite",
    )
```

- [ ] **Step 3: Run the full data + cli + web test suite**

Run: `uv run pytest tests/data/ tests/cli/ tests/web/ -v`
Expected: PASS. No tests should break — both call sites previously constructed `CachedProvider(upstream=YahooProvider(), …)` which is now `CachedProvider(upstream=FallbackProvider([YahooProvider(), StooqProvider()]), …)`. As long as Yahoo is reachable in any test path that exercises the production wiring (most use fakes), behavior is identical.

- [ ] **Step 4: Run ruff**

Run: `uv run ruff check src/wave_alpha/cli/app.py src/wave_alpha/web/deps.py`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/cli/app.py src/wave_alpha/web/deps.py
git commit -m "$(cat <<'EOF'
feat: wire Stooq fallback into CLI and web providers

cli/app.py:_default_provider and web/deps.py:_run_analysis_uncached
now construct CachedProvider(FallbackProvider([Yahoo, Stooq])). Yahoo
stays primary; Stooq covers daily/weekly when Yahoo fails or returns
empty. 4h stays Yahoo-only via interval-aware skip.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Register `network` pytest marker

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Modify `pyproject.toml`**

Find the `[tool.pytest.ini_options]` block. It currently reads:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"
markers = [
  "slow: tests that build wheels or hit subprocess (deselect with '-m \"not slow\"')",
]
```

Replace with:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers -m 'not network'"
markers = [
  "slow: tests that build wheels or hit subprocess (deselect with '-m \"not slow\"')",
  "network: tests that hit live external endpoints (opt in with '-m network')",
]
```

- [ ] **Step 2: Verify default invocation still works**

Run: `uv run pytest tests/data/ -v`
Expected: PASS. The `-m 'not network'` filter is harmless for tests with no `network` mark.

- [ ] **Step 3: Verify `-m network` selects no tests yet**

Run: `uv run pytest -m network -v`
Expected: collected 0 items, exit code 5 (no tests run). This is fine — Task 10 adds the first network test.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "$(cat <<'EOF'
test: register 'network' pytest marker

Default invocation now skips network-marked tests via addopts. Opt in
with `pytest -m network`. Prepares the way for the live-Stooq smoke
test in the next commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Live-network smoke test for `StooqProvider`

**Files:**
- Create: `tests/data/test_stooq_network.py`

- [ ] **Step 1: Write the test**

Path: `tests/data/test_stooq_network.py`

```python
"""Live-network smoke test for StooqProvider.

Skipped by default (the 'network' marker is excluded by pyproject.toml's
addopts). Opt in with:

    uv run pytest -m network tests/data/test_stooq_network.py -v
"""
from __future__ import annotations

import pytest

from wave_alpha.data.stooq import StooqProvider
from wave_alpha.domain import Interval


@pytest.mark.network
def test_real_stooq_daily_fetch_aapl() -> None:
    bars = StooqProvider().fetch("AAPL", Interval.DAILY)

    # Sanity bounds — Stooq's AAPL daily history goes back decades, so
    # we should easily see thousands of bars.
    assert len(bars) > 1000, f"expected >1000 daily bars, got {len(bars)}"

    # Ascending order.
    assert bars == sorted(bars, key=lambda b: b.timestamp)

    # Most recent bar should be within the last few months. We don't
    # pin an exact date because Stooq updates daily.
    from datetime import date, timedelta

    assert bars[-1].timestamp >= date.today() - timedelta(days=120), (
        f"last bar {bars[-1].timestamp} is older than 120 days; Stooq feed may be stale"
    )

    # Decimal precision is preserved.
    assert all(b.high >= b.low for b in bars)


@pytest.mark.network
def test_real_stooq_weekly_fetch_aapl() -> None:
    bars = StooqProvider().fetch("AAPL", Interval.WEEKLY)
    assert len(bars) > 200, f"expected >200 weekly bars, got {len(bars)}"
    assert bars == sorted(bars, key=lambda b: b.timestamp)
```

- [ ] **Step 2: Verify the test is skipped by default**

Run: `uv run pytest tests/data/test_stooq_network.py -v`
Expected: collected 2 items, deselected 2; exit code 5 (no tests selected). The `-m 'not network'` from `pyproject.toml` deselects them.

- [ ] **Step 3: Verify the test runs (and passes) with `-m network`**

Run: `uv run pytest -m network tests/data/test_stooq_network.py -v`
Expected: PASS (2 tests). Requires internet connectivity. If Stooq is unreachable, the test will fail with a transport error — that's the expected behavior; the test is a smoke probe of the live endpoint.

- [ ] **Step 4: Run ruff**

Run: `uv run ruff check tests/data/test_stooq_network.py`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add tests/data/test_stooq_network.py
git commit -m "$(cat <<'EOF'
test(data): add opt-in live-Stooq smoke test

Two tests behind @pytest.mark.network that verify a real fetch of
AAPL daily and weekly bars succeeds against stooq.com. Skipped by
default; run with `pytest -m network`. Catches breakage in Stooq's
CSV format or endpoint availability.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: End-to-end simulated Yahoo failure

**Files:**
- Create: `tests/data/test_simulated_yahoo_failure.py`

This test exercises the full production wiring (`CachedProvider(FallbackProvider([Yahoo, Stooq]))`) and verifies that when Yahoo's `fetch` is monkeypatched to raise, Stooq's contribution is what reaches the caller.

- [ ] **Step 1: Write the test**

Path: `tests/data/test_simulated_yahoo_failure.py`

```python
"""End-to-end simulation: Yahoo fails, Stooq supplies bars.

Exercises the full production stack: CachedProvider(FallbackProvider([Yahoo, Stooq])).
Stooq's HTTP transport is monkeypatched to a fixture so the test stays hermetic.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from wave_alpha.data.cached import CachedProvider
from wave_alpha.data.fallback import FallbackProvider
from wave_alpha.data.stooq import StooqProvider
from wave_alpha.data.yahoo import YahooProvider
from wave_alpha.domain import Interval


_FIXTURE_CSV = (
    Path(__file__).resolve().parents[1] / "fixtures" / "stooq_aapl_5d_daily.csv"
).read_text()


def test_yahoo_failure_falls_through_to_stooq(tmp_path: Path, monkeypatch) -> None:
    # Yahoo always raises (simulates rate-limit / network error).
    def yahoo_boom(self, ticker, interval, start=None, end=None):
        raise RuntimeError("yfinance unavailable")

    monkeypatch.setattr(YahooProvider, "fetch", yahoo_boom)

    # Stooq is real but its HTTP layer is replaced by the fixture body.
    monkeypatch.setattr(
        "wave_alpha.data.stooq._http_get",
        lambda url, *, timeout=30.0: _FIXTURE_CSV,
    )

    provider = CachedProvider(
        upstream=FallbackProvider([YahooProvider(), StooqProvider()]),
        db_path=tmp_path / "cache.sqlite",
    )

    bars = provider.fetch("AAPL", Interval.DAILY)

    assert len(bars) == 5
    assert bars[0].timestamp == date(2024, 4, 22)
    assert bars[-1].timestamp == date(2024, 4, 26)


def test_yahoo_failure_4h_returns_empty_no_stooq_call(tmp_path: Path, monkeypatch) -> None:
    """4h request: Yahoo fails, Stooq is interval-skipped, result is []."""

    def yahoo_boom(self, ticker, interval, start=None, end=None):
        raise RuntimeError("yfinance unavailable")

    monkeypatch.setattr(YahooProvider, "fetch", yahoo_boom)

    stooq_calls: list[str] = []

    def stooq_http(url, *, timeout=30.0):
        stooq_calls.append(url)
        return _FIXTURE_CSV  # would-be data, but we expect this never to run

    monkeypatch.setattr("wave_alpha.data.stooq._http_get", stooq_http)

    provider = CachedProvider(
        upstream=FallbackProvider([YahooProvider(), StooqProvider()]),
        db_path=tmp_path / "cache.sqlite",
    )

    bars = provider.fetch("AAPL", Interval.HOURLY_4)
    assert bars == []
    assert stooq_calls == [], "Stooq must not be called for 4h requests"
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/data/test_simulated_yahoo_failure.py -v`
Expected: PASS (2 tests).

- [ ] **Step 3: Run ruff**

Run: `uv run ruff check tests/data/test_simulated_yahoo_failure.py`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add tests/data/test_simulated_yahoo_failure.py
git commit -m "$(cat <<'EOF'
test(data): end-to-end simulated Yahoo failure

Two tests exercising the full production stack (CachedProvider +
FallbackProvider + Yahoo + Stooq) with Yahoo monkeypatched to raise.
Verifies (a) daily/weekly fall through to Stooq's bars and (b) 4h
correctly returns [] without consulting Stooq (interval-skipped).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Update `CHANGELOG.md`

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Edit the `[Unreleased]` block**

Find the existing `[Unreleased] / Added` section. It currently lists trust popovers and the `/about` page. Append a third bullet at the end of the `Added` block:

```markdown
- **Stooq OHLCV fallback** — `wave-alpha analyze` and `scan` now silently
  fall through to Stooq for daily/weekly bars when Yahoo Finance returns
  empty or raises. 4h bars stay Yahoo-only. New `FallbackProvider` chain
  in `data/fallback.py` and `StooqProvider` in `data/stooq.py`. No new
  dependencies (stdlib `urllib.request`, stdlib `csv`); no API key
  required by Stooq. See `docs/superpowers/specs/2026-05-10-stooq-provider-design.md`.
```

- [ ] **Step 2: Verify the file is well-formed**

Run: `head -25 CHANGELOG.md`
Expected: the new bullet appears under `[Unreleased] / Added`.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "$(cat <<'EOF'
docs(changelog): note Stooq OHLCV fallback under [Unreleased]

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Final verification + GitNexus reindex

**Files:** none modified.

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest`
Expected: all tests pass; collection includes the new test files; `-m 'not network'` from `pyproject.toml` deselects the 2 network tests.

- [ ] **Step 2: Run ruff over the whole repo**

Run: `uv run ruff check .`
Expected: no errors.

- [ ] **Step 3: Verify the CLI still works**

Run: `uv run wave-alpha analyze AAPL --as-of 2024-12-31 --json | head -20`
Expected: a JSON document begins to print (the analysis result). The provider stack is now `CachedProvider(FallbackProvider([Yahoo, Stooq]))` but in steady state with a populated cache or healthy Yahoo, behavior is identical to before.

- [ ] **Step 4: Reindex GitNexus**

The codebase added two new modules and modified five existing ones. Refresh the index so subsequent impact analysis is accurate.

Run: `npx gitnexus analyze`
Expected: completes without errors. Index now reflects `StooqProvider`, `FallbackProvider`, and the new wiring.

- [ ] **Step 5: Run GitNexus change detection**

Use the `gitnexus_detect_changes` tool to verify only the expected symbols changed. Expected output should reference, at minimum:

- New: `StooqProvider`, `FallbackProvider`, `_to_stooq_symbol`, `_parse_stooq_csv`, `_http_get`
- Modified: `OHLCVProvider` (new class attribute), `_default_provider` (cli), `_run_analysis_uncached` (web)
- No unexpected changes to engine internals (`pipeline.py`, `enumerator.py`, `coherence/`, `right_edge/`, `llm/`, `backtest/`).

If anything outside this surface shows as changed, investigate before merging.

- [ ] **Step 6: Run GitNexus impact on `OHLCVProvider`**

Use the `gitnexus_impact` tool with target `OHLCVProvider`, direction `upstream`, to confirm the class-attribute addition does not raise risk for any caller. Expected: LOW risk (additive class attribute with default value; no method signatures changed).

- [ ] **Step 7: Final commit (if anything was missed)**

If steps 1-6 surfaced any small fix (lint, missed test name, etc.), commit it now with a descriptive message. Otherwise skip.

```bash
git status
# If clean, plan complete. Otherwise:
# git add <files>
# git commit -m "fix(stooq): <what>"
```

---

## Self-review notes

**Spec coverage:**

| Spec section | Plan task |
|---|---|
| §4 Architecture (FallbackProvider + interval-aware skip) | Task 6 |
| §5 OHLCVProvider extension | Task 1 |
| §6.1 Module layout | Tasks 1, 3, 4, 5, 6 (incremental) |
| §6.2 Endpoint and symbol mapping | Task 3 |
| §6.3 CSV parsing (incl. halted rows, "No data") | Task 4 |
| §6.4 HTTP transport | Task 5 |
| §6.5 Class skeleton | Task 5 |
| §7 FallbackProvider | Task 6 |
| §8.1 cli/app.py wiring | Task 8 |
| §8.2 web/deps.py wiring | Task 8 |
| §8.3 Right-edge stays Yahoo-direct | Implicit — task 8 explicitly does not modify `right_edge.py`; verified by Step 5 of Task 13's GitNexus check |
| §9.1 Fixtures | Task 2 |
| §9.2 Test inventory (13 tests) | Tasks 3-7, 10-11 (collectively cover all rows) |
| §9.3 Network test discipline | Tasks 9 + 10 |
| §9.4 Existing-test impact | Verified across Tasks 1, 8, 13 |
| §10 Known divergences | Documented in spec; no plan task needed |
| §11 Risk/blast radius | Verified by Task 13 step 5 (gitnexus_detect_changes) |
| §13 Acceptance criteria | All verified across Tasks 8 (CLI), 9 (network discipline), 12 (CHANGELOG), 13 (full test + ruff + CLI smoke) |

**Type / name consistency:** `_to_stooq_symbol`, `_parse_stooq_csv`, `_http_get`, `_INTERVAL_TO_STOOQ`, `_HALT_SENTINELS`, `StooqProvider.supported_intervals`, `FallbackProvider._providers` are referenced consistently across tasks 3-8. `Interval.HOURLY_4` (not `H4` or `FOUR_HOUR`) matches the existing enum in `domain.py`.

**Placeholder scan:** every code block contains complete, runnable code. No "TBD" / "TODO" / "implement later" anywhere. Every test has an assert; every commit message is concrete.

**Order dependencies:** Task 1 → 5, 6 (provider abstraction first). Task 2 → 4 (fixtures before parser tests use them). Task 3 → 4 → 5 (incremental construction of `stooq.py`). Task 6 is independent of 3-5 (uses fakes, not StooqProvider). Task 7 needs 6. Task 8 needs 5, 6. Task 9 → 10 (marker registered before marker-using test). Task 11 needs 8 (full wiring). Task 12 is independent. Task 13 is the closer.
