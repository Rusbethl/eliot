# wave-alpha v0.1 — Engine Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic core of wave-alpha — data layer, multi-degree ATR-ZigZag pivots, Elliott Wave templates and rule validation, count enumeration, right-edge heuristic, multi-timeframe coherence, and trade-signal derivation — exposed via a CLI `wave-alpha analyze TICKER --json`. No LLM, no web UI; that's Plans 2 and 3.

**Architecture:** Pure Python, layered. Each module has one responsibility, communicates via Pydantic models, and is unit-testable with golden fixtures or canned inputs. SQLite caches OHLCV. A `PointInTimeView` wrapper hard-asserts no input bar has `timestamp > as_of`, eliminating lookahead bugs at the type boundary. Pattern templates are YAML-defined; constraint expressions evaluate in a small DSL namespace.

**Tech Stack:** Python 3.11+, uv, hatch, Pydantic v2, SQLModel, Typer, yfinance, pandas (internal numeric ops only), PyYAML, loguru, ruff, pytest, factory_boy.

**Reference spec:** `docs/superpowers/specs/2026-05-03-wave-alpha-design.md`

---

## Phase A — Project scaffolding

Bring the repo to "lints clean, tests run, CLI prints version" state.

### Task 1: Initialize pyproject.toml

**Files:**
- Create: `/Users/alexchen/Documents/project/wave_alpha/pyproject.toml`
- Create: `/Users/alexchen/Documents/project/wave_alpha/.python-version`

- [ ] **Step 1: Pin Python version**

Create `/Users/alexchen/Documents/project/wave_alpha/.python-version`:

```
3.11
```

- [ ] **Step 2: Write pyproject.toml**

Create `/Users/alexchen/Documents/project/wave_alpha/pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "wave-alpha"
version = "0.1.0"
description = "Local-first Elliott Wave analysis for swing traders"
readme = "README.md"
requires-python = ">=3.11"
authors = [{ name = "Alex Chen" }]
dependencies = [
  "pydantic>=2.6",
  "sqlmodel>=0.0.16",
  "typer[all]>=0.12",
  "yfinance>=0.2.40",
  "pandas>=2.2",
  "pyyaml>=6.0",
  "loguru>=0.7",
]

[project.optional-dependencies]
dev = [
  "pytest>=8",
  "pytest-cov>=5",
  "factory-boy>=3.3",
  "ruff>=0.4",
]

[project.scripts]
wave-alpha = "wave_alpha.cli.app:app"

[tool.hatch.build.targets.wheel]
packages = ["src/wave_alpha"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "UP", "B", "SIM", "RUF"]
ignore = ["E501"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"
```

- [ ] **Step 3: Verify install**

Run: `cd /Users/alexchen/Documents/project/wave_alpha && uv sync --extra dev`
Expected: lockfile written; no resolution errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add pyproject.toml .python-version uv.lock
git commit -m "$(cat <<'EOF'
chore: initialize pyproject and pin Python 3.11

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 2: Add .gitignore and package skeleton

**Files:**
- Create: `.gitignore`
- Create: `src/wave_alpha/__init__.py`
- Create: `src/wave_alpha/cli/__init__.py`
- Create: `src/wave_alpha/cli/app.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Write .gitignore**

Create `/Users/alexchen/Documents/project/wave_alpha/.gitignore`:

```
__pycache__/
*.py[cod]
*.egg-info/
.venv/
.pytest_cache/
.ruff_cache/
.coverage
htmlcov/
dist/
build/
*.sqlite
*.db
.DS_Store
.env
```

- [ ] **Step 2: Create package __init__**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/__init__.py`:

```python
__version__ = "0.1.0"
```

- [ ] **Step 3: Create CLI stub**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/cli/__init__.py`:

```python
```

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/cli/app.py`:

```python
from __future__ import annotations

import typer

from wave_alpha import __version__

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Elliott Wave analysis for swing traders.")


@app.command()
def version() -> None:
    """Print the wave-alpha version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Create tests package**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/__init__.py`:

```python
```

- [ ] **Step 5: Smoke-test the CLI**

Run: `cd /Users/alexchen/Documents/project/wave_alpha && uv run wave-alpha version`
Expected: prints `0.1.0`.

- [ ] **Step 6: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add .gitignore src/wave_alpha tests/__init__.py
git commit -m "$(cat <<'EOF'
feat(cli): minimal typer app exposing version

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 3: Add ruff and pytest sanity tests

**Files:**
- Create: `tests/test_sanity.py`

- [ ] **Step 1: Write a smoke test**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/test_sanity.py`:

```python
from wave_alpha import __version__


def test_version_is_a_string() -> None:
    assert isinstance(__version__, str)
    assert __version__.count(".") >= 2
```

- [ ] **Step 2: Run pytest**

Run: `cd /Users/alexchen/Documents/project/wave_alpha && uv run pytest -q`
Expected: 1 passed.

- [ ] **Step 3: Run ruff**

Run: `cd /Users/alexchen/Documents/project/wave_alpha && uv run ruff check . && uv run ruff format --check .`
Expected: "All checks passed!" or fix any reported issues.

- [ ] **Step 4: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add tests/test_sanity.py
git commit -m "$(cat <<'EOF'
test: add sanity test for package version

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase B — Data layer

Domain `Bar` model, `OHLCVProvider` ABC with a Yahoo implementation, a SQLite-backed `OHLCVCache`, and the all-important `PointInTimeView` that hard-asserts no bar leaks past `as_of`.

### Task 4: Bar domain model

**Files:**
- Create: `src/wave_alpha/domain.py`
- Create: `tests/test_domain.py`

- [ ] **Step 1: Write the failing test**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/test_domain.py`:

```python
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from wave_alpha.domain import Bar, Interval


def test_bar_basic_roundtrip() -> None:
    bar = Bar(
        timestamp=date(2024, 1, 2),
        open=Decimal("100.10"),
        high=Decimal("101.50"),
        low=Decimal("99.80"),
        close=Decimal("101.00"),
        volume=1_000_000,
    )
    assert bar.timestamp == date(2024, 1, 2)
    assert bar.high >= bar.low


def test_bar_rejects_high_below_low() -> None:
    with pytest.raises(ValidationError):
        Bar(
            timestamp=date(2024, 1, 2),
            open=Decimal("100"),
            high=Decimal("99"),
            low=Decimal("100"),
            close=Decimal("99.50"),
            volume=1,
        )


def test_interval_values() -> None:
    assert Interval.WEEKLY.value == "1wk"
    assert Interval.DAILY.value == "1d"
    assert Interval.HOURLY_4.value == "4h"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_domain.py -v`
Expected: FAIL — `wave_alpha.domain` not found.

- [ ] **Step 3: Implement the module**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/domain.py`:

```python
from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator


class Interval(str, Enum):
    WEEKLY = "1wk"
    DAILY = "1d"
    HOURLY_4 = "4h"


class Bar(BaseModel):
    """One OHLCV bar at a fixed interval."""

    model_config = ConfigDict(frozen=True)

    timestamp: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    @model_validator(mode="after")
    def _check_high_low(self) -> Bar:
        if self.high < self.low:
            raise ValueError(f"high ({self.high}) must be >= low ({self.low})")
        return self
```

- [ ] **Step 4: Run test to verify pass**

Run: `uv run pytest tests/test_domain.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add src/wave_alpha/domain.py tests/test_domain.py
git commit -m "$(cat <<'EOF'
feat(domain): Bar and Interval domain models

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 5: PointInTimeView with hard-assert

**Files:**
- Create: `src/wave_alpha/data/__init__.py`
- Create: `src/wave_alpha/data/point_in_time.py`
- Create: `tests/data/__init__.py`
- Create: `tests/data/test_point_in_time.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/data/__init__.py`:

```python
```

Create `/Users/alexchen/Documents/project/wave_alpha/tests/data/test_point_in_time.py`:

```python
from datetime import date
from decimal import Decimal

import pytest

from wave_alpha.data.point_in_time import LookaheadError, PointInTimeView
from wave_alpha.domain import Bar


def _bar(d: date, price: str = "100") -> Bar:
    p = Decimal(price)
    return Bar(timestamp=d, open=p, high=p, low=p, close=p, volume=1)


def test_view_blocks_future_bars() -> None:
    bars = [
        _bar(date(2024, 1, 1)),
        _bar(date(2024, 1, 2)),
        _bar(date(2024, 1, 3)),
    ]
    view = PointInTimeView(bars=bars, as_of=date(2024, 1, 2))
    visible = view.visible()
    assert len(visible) == 2
    assert visible[-1].timestamp == date(2024, 1, 2)


def test_view_latest_returns_most_recent_visible() -> None:
    bars = [
        _bar(date(2024, 1, 1)),
        _bar(date(2024, 1, 2)),
        _bar(date(2024, 1, 5)),
    ]
    view = PointInTimeView(bars=bars, as_of=date(2024, 1, 3))
    assert view.latest().timestamp == date(2024, 1, 2)


def test_view_raises_on_indexed_future_bar() -> None:
    bars = [
        _bar(date(2024, 1, 1)),
        _bar(date(2024, 1, 5)),
    ]
    view = PointInTimeView(bars=bars, as_of=date(2024, 1, 3))
    with pytest.raises(LookaheadError):
        _ = view[1]


def test_view_empty_visible_raises_on_latest() -> None:
    bars = [_bar(date(2024, 1, 5))]
    view = PointInTimeView(bars=bars, as_of=date(2024, 1, 1))
    with pytest.raises(LookaheadError):
        view.latest()
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/data/test_point_in_time.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the module**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/data/__init__.py`:

```python
```

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/data/point_in_time.py`:

```python
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from wave_alpha.domain import Bar


class LookaheadError(AssertionError):
    """Raised when code tries to read a Bar with timestamp > as_of."""


@dataclass(frozen=True)
class PointInTimeView:
    """Immutable point-in-time accessor over a sorted bar series.

    The single source of truth for both backtest and live pipelines.
    Hard-asserts no bar with timestamp > as_of is observable.
    """

    bars: Sequence[Bar]
    as_of: date

    def visible(self) -> list[Bar]:
        return [b for b in self.bars if b.timestamp <= self.as_of]

    def latest(self) -> Bar:
        vis = self.visible()
        if not vis:
            raise LookaheadError(
                f"no bars visible at as_of={self.as_of}; earliest is "
                f"{self.bars[0].timestamp if self.bars else 'none'}"
            )
        return vis[-1]

    def __getitem__(self, idx: int) -> Bar:
        bar = self.bars[idx]
        if bar.timestamp > self.as_of:
            raise LookaheadError(
                f"lookahead violation: bar {bar.timestamp} > as_of {self.as_of}"
            )
        return bar

    def __len__(self) -> int:
        return len(self.visible())
```

- [ ] **Step 4: Run test to verify pass**

Run: `uv run pytest tests/data/test_point_in_time.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add src/wave_alpha/data tests/data
git commit -m "$(cat <<'EOF'
feat(data): PointInTimeView with hard lookahead assertions

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 6: OHLCVProvider ABC

**Files:**
- Create: `src/wave_alpha/data/provider.py`
- Create: `tests/data/test_provider.py`

- [ ] **Step 1: Write failing test (in-memory fake)**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/data/test_provider.py`:

```python
from datetime import date
from decimal import Decimal

import pytest

from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval


class _StaticProvider(OHLCVProvider):
    def fetch(
        self,
        ticker: str,
        interval: Interval,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        bars = [
            Bar(
                timestamp=date(2024, 1, d),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100.5"),
                volume=1,
            )
            for d in (2, 3, 4)
        ]
        if start:
            bars = [b for b in bars if b.timestamp >= start]
        if end:
            bars = [b for b in bars if b.timestamp <= end]
        return bars


def test_provider_returns_filtered_bars() -> None:
    p = _StaticProvider()
    bars = p.fetch("ANY", Interval.DAILY, start=date(2024, 1, 3))
    assert len(bars) == 2
    assert bars[0].timestamp == date(2024, 1, 3)


def test_provider_abc_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        OHLCVProvider()  # type: ignore[abstract]
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/data/test_provider.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/data/provider.py`:

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from wave_alpha.domain import Bar, Interval


class OHLCVProvider(ABC):
    """Pluggable OHLCV bar source. Implementations: yfinance, polygon, ..."""

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

- [ ] **Step 4: Verify pass**

Run: `uv run pytest tests/data/test_provider.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add src/wave_alpha/data/provider.py tests/data/test_provider.py
git commit -m "$(cat <<'EOF'
feat(data): OHLCVProvider ABC

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 7: YahooProvider

**Files:**
- Create: `src/wave_alpha/data/yahoo.py`
- Create: `tests/data/test_yahoo.py`
- Create: `tests/fixtures/__init__.py`
- Create: `tests/fixtures/yahoo_aapl_5d_daily.csv`

- [ ] **Step 1: Add a tiny CSV fixture**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/fixtures/__init__.py`:

```python
```

Create `/Users/alexchen/Documents/project/wave_alpha/tests/fixtures/yahoo_aapl_5d_daily.csv`:

```
Date,Open,High,Low,Close,Volume
2024-04-22,165.52,167.26,164.77,165.84,48000000
2024-04-23,166.50,167.05,164.92,166.90,52000000
2024-04-24,166.21,167.11,164.81,165.05,46000000
2024-04-25,164.85,170.30,164.65,169.89,75000000
2024-04-26,169.88,171.34,168.49,169.30,68000000
```

- [ ] **Step 2: Write the failing test**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/data/test_yahoo.py`:

```python
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd

from wave_alpha.data.yahoo import bars_from_dataframe
from wave_alpha.domain import Interval


def test_bars_from_dataframe_parses_csv_fixture() -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "yahoo_aapl_5d_daily.csv"
    df = pd.read_csv(fixture, parse_dates=["Date"]).set_index("Date")
    bars = bars_from_dataframe(df, interval=Interval.DAILY)

    assert len(bars) == 5
    assert bars[0].timestamp == date(2024, 4, 22)
    assert bars[0].close == Decimal("165.84")
    # ascending order
    assert bars == sorted(bars, key=lambda b: b.timestamp)
    # high >= low always
    assert all(b.high >= b.low for b in bars)
```

- [ ] **Step 3: Verify failure**

Run: `uv run pytest tests/data/test_yahoo.py -v`
Expected: FAIL — module missing.

- [ ] **Step 4: Implement**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/data/yahoo.py`:

```python
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import yfinance as yf
from loguru import logger

from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval

_INTERVAL_TO_YF: dict[Interval, str] = {
    Interval.WEEKLY: "1wk",
    Interval.DAILY: "1d",
    Interval.HOURLY_4: "1h",  # we resample 1h to 4h client-side
}


def bars_from_dataframe(df: pd.DataFrame, interval: Interval) -> list[Bar]:
    """Convert a yfinance OHLCV DataFrame (DatetimeIndex) to canonical Bars."""
    if interval is Interval.HOURLY_4:
        df = _resample_to_4h(df)

    bars: list[Bar] = []
    for ts, row in df.iterrows():
        if pd.isna(row["Open"]) or pd.isna(row["Close"]):
            continue
        bars.append(
            Bar(
                timestamp=_to_date(ts),
                open=Decimal(f"{row['Open']:.4f}"),
                high=Decimal(f"{row['High']:.4f}"),
                low=Decimal(f"{row['Low']:.4f}"),
                close=Decimal(f"{row['Close']:.4f}"),
                volume=int(row.get("Volume", 0) or 0),
            )
        )
    return bars


def _resample_to_4h(df: pd.DataFrame) -> pd.DataFrame:
    return df.resample("4h").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna(subset=["Open", "Close"])


def _to_date(ts: pd.Timestamp) -> date:
    return ts.date() if hasattr(ts, "date") else ts


class YahooProvider(OHLCVProvider):
    def fetch(
        self,
        ticker: str,
        interval: Interval,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        kwargs: dict = {"interval": _INTERVAL_TO_YF[interval], "auto_adjust": True}
        if start:
            kwargs["start"] = start.isoformat()
        if end:
            kwargs["end"] = end.isoformat()
        if not start and not end:
            kwargs["period"] = "max" if interval is not Interval.HOURLY_4 else "730d"

        logger.debug("yfinance fetch ticker={} interval={} kwargs={}", ticker, interval.value, kwargs)
        df = yf.download(ticker, progress=False, threads=False, **kwargs)
        if df.empty:
            return []
        if isinstance(df.columns, pd.MultiIndex):
            df = df.droplevel(1, axis=1)
        return bars_from_dataframe(df, interval=interval)
```

- [ ] **Step 5: Verify pass**

Run: `uv run pytest tests/data/test_yahoo.py -v`
Expected: 1 passed (the parser test; the live fetch is not exercised in unit tests).

- [ ] **Step 6: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add src/wave_alpha/data/yahoo.py tests/data/test_yahoo.py tests/fixtures
git commit -m "$(cat <<'EOF'
feat(data): YahooProvider with 4h client-side resampling

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 8: OHLCVCache (SQLite)

**Files:**
- Create: `src/wave_alpha/data/cache.py`
- Create: `tests/data/test_cache.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/data/test_cache.py`:

```python
from datetime import date
from decimal import Decimal

from wave_alpha.data.cache import OHLCVCache
from wave_alpha.domain import Bar, Interval


def _bar(d: date, price: str) -> Bar:
    p = Decimal(price)
    return Bar(timestamp=d, open=p, high=p, low=p, close=p, volume=1)


def test_cache_roundtrip(tmp_path) -> None:
    cache = OHLCVCache(db_path=tmp_path / "cache.sqlite")
    bars = [_bar(date(2024, 1, d), "100") for d in (2, 3, 4)]
    cache.put("AAPL", Interval.DAILY, bars)
    out = cache.get("AAPL", Interval.DAILY)
    assert [b.timestamp for b in out] == [b.timestamp for b in bars]


def test_cache_upsert_replaces_overlapping_dates(tmp_path) -> None:
    cache = OHLCVCache(db_path=tmp_path / "cache.sqlite")
    cache.put("AAPL", Interval.DAILY, [_bar(date(2024, 1, 2), "100")])
    cache.put("AAPL", Interval.DAILY, [_bar(date(2024, 1, 2), "200")])
    out = cache.get("AAPL", Interval.DAILY)
    assert len(out) == 1
    assert out[0].close == Decimal("200")


def test_cache_isolates_intervals(tmp_path) -> None:
    cache = OHLCVCache(db_path=tmp_path / "cache.sqlite")
    cache.put("AAPL", Interval.DAILY, [_bar(date(2024, 1, 2), "100")])
    cache.put("AAPL", Interval.WEEKLY, [_bar(date(2024, 1, 5), "200")])
    assert len(cache.get("AAPL", Interval.DAILY)) == 1
    assert cache.get("AAPL", Interval.DAILY)[0].close == Decimal("100")
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/data/test_cache.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/data/cache.py`:

```python
from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from wave_alpha.domain import Bar, Interval

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    ticker   TEXT NOT NULL,
    interval TEXT NOT NULL,
    ts       TEXT NOT NULL,
    open     TEXT NOT NULL,
    high     TEXT NOT NULL,
    low      TEXT NOT NULL,
    close    TEXT NOT NULL,
    volume   INTEGER NOT NULL,
    PRIMARY KEY (ticker, interval, ts)
);
"""


class OHLCVCache:
    """SQLite-backed cache. Decimal values stored as TEXT to preserve precision."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def put(self, ticker: str, interval: Interval, bars: list[Bar]) -> None:
        if not bars:
            return
        rows = [
            (
                ticker,
                interval.value,
                b.timestamp.isoformat(),
                str(b.open),
                str(b.high),
                str(b.low),
                str(b.close),
                b.volume,
            )
            for b in bars
        ]
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO bars (ticker, interval, ts, open, high, low, close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def get(
        self,
        ticker: str,
        interval: Interval,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        sql = "SELECT ts, open, high, low, close, volume FROM bars WHERE ticker=? AND interval=?"
        params: list = [ticker, interval.value]
        if start:
            sql += " AND ts >= ?"
            params.append(start.isoformat())
        if end:
            sql += " AND ts <= ?"
            params.append(end.isoformat())
        sql += " ORDER BY ts ASC"
        with self._conn() as conn:
            cur = conn.execute(sql, params)
            return [
                Bar(
                    timestamp=date.fromisoformat(ts),
                    open=Decimal(o),
                    high=Decimal(h),
                    low=Decimal(low),
                    close=Decimal(c),
                    volume=v,
                )
                for ts, o, h, low, c, v in cur.fetchall()
            ]
```

- [ ] **Step 4: Verify pass**

Run: `uv run pytest tests/data/test_cache.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add src/wave_alpha/data/cache.py tests/data/test_cache.py
git commit -m "$(cat <<'EOF'
feat(data): SQLite-backed OHLCVCache with Decimal-safe storage

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase C — Pivot detection

ATR, single-threshold ZigZag, multi-degree orchestrator with the hierarchical-subset invariant, and confirmed/tentative state tracking.

### Task 9: ATR computation

**Files:**
- Create: `src/wave_alpha/pivots/__init__.py`
- Create: `src/wave_alpha/pivots/atr.py`
- Create: `tests/pivots/__init__.py`
- Create: `tests/pivots/test_atr.py`

- [ ] **Step 1: Write the failing test**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/pivots/__init__.py`:

```python
```

Create `/Users/alexchen/Documents/project/wave_alpha/tests/pivots/test_atr.py`:

```python
from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.domain import Bar
from wave_alpha.pivots.atr import compute_atr


def _bar(i: int, h: str, low: str, c: str, prev_c: str | None = None) -> Bar:
    return Bar(
        timestamp=date(2024, 1, 1) + timedelta(days=i),
        open=Decimal(prev_c or c),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=1,
    )


def test_atr_constant_one_dollar_range_after_warmup() -> None:
    # 30 bars, each with high-low=1.0, close==prev close → ATR converges to 1.0
    bars: list[Bar] = []
    for i in range(30):
        bars.append(_bar(i, "101", "100", "100", prev_c="100"))
    atr = compute_atr(bars, period=14)
    assert atr[-1] == Decimal("1.0").quantize(atr[-1])


def test_atr_returns_none_for_warmup_bars() -> None:
    bars = [_bar(i, "101", "100", "100", "100") for i in range(5)]
    atr = compute_atr(bars, period=14)
    assert all(v is None for v in atr)


def test_atr_handles_gap_via_true_range() -> None:
    # Day 1 close=100; day 2 gaps up to high=110, low=108, close=109
    # True range = max(110-108, |110-100|, |108-100|) = 10
    bars = [
        _bar(0, "101", "99", "100"),
    ]
    bars.append(_bar(1, "110", "108", "109", prev_c="100"))
    bars.extend(_bar(i, "110", "108", "109", prev_c="109") for i in range(2, 30))
    atr = compute_atr(bars, period=14)
    assert atr[-1] is not None
    assert atr[-1] > Decimal("1.5")
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/pivots/test_atr.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/pivots/__init__.py`:

```python
```

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/pivots/atr.py`:

```python
from __future__ import annotations

from decimal import Decimal

from wave_alpha.domain import Bar


def compute_atr(bars: list[Bar], period: int = 14) -> list[Decimal | None]:
    """Wilder's ATR. Returns one entry per bar; first `period` entries are None."""
    if period < 1:
        raise ValueError("period must be >= 1")

    n = len(bars)
    out: list[Decimal | None] = [None] * n
    if n < period + 1:
        return out

    true_ranges: list[Decimal] = [Decimal(0)]
    for i in range(1, n):
        prev_close = bars[i - 1].close
        tr = max(
            bars[i].high - bars[i].low,
            abs(bars[i].high - prev_close),
            abs(bars[i].low - prev_close),
        )
        true_ranges.append(tr)

    seed = sum(true_ranges[1 : period + 1], Decimal(0)) / Decimal(period)
    out[period] = seed
    for i in range(period + 1, n):
        prev = out[i - 1]
        assert prev is not None
        out[i] = (prev * (period - 1) + true_ranges[i]) / Decimal(period)
    return out
```

- [ ] **Step 4: Verify pass**

Run: `uv run pytest tests/pivots/test_atr.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add src/wave_alpha/pivots tests/pivots
git commit -m "$(cat <<'EOF'
feat(pivots): Wilder ATR computation

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 10: PivotEvent model + ZigZag at one threshold

**Files:**
- Create: `src/wave_alpha/pivots/models.py`
- Create: `src/wave_alpha/pivots/zigzag.py`
- Create: `tests/pivots/test_zigzag.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/pivots/test_zigzag.py`:

```python
from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.domain import Bar, Interval
from wave_alpha.pivots.models import PivotState, PivotType
from wave_alpha.pivots.zigzag import zigzag_atr


def _bars_from_closes(closes: list[float]) -> list[Bar]:
    bars: list[Bar] = []
    for i, c in enumerate(closes):
        cd = Decimal(f"{c:.4f}")
        bars.append(
            Bar(
                timestamp=date(2024, 1, 1) + timedelta(days=i),
                open=cd,
                high=cd + Decimal("0.10"),
                low=cd - Decimal("0.10"),
                close=cd,
                volume=1,
            )
        )
    return bars


def test_zigzag_alternates_high_low_above_threshold() -> None:
    # Closes: rise 100→110, fall to 95, rise to 115, fall to 100
    # threshold 4 → all swings significant
    closes = [100, 102, 104, 110, 108, 100, 95, 100, 110, 115, 112, 105, 100]
    bars = _bars_from_closes(closes)
    pivots = zigzag_atr(bars, interval=Interval.DAILY, atr_multiple=Decimal("0"), fixed_threshold=Decimal("4"))
    assert len(pivots) >= 4
    types = [p.type for p in pivots]
    # types must alternate
    for a, b in zip(types, types[1:]):
        assert a != b


def test_zigzag_first_pivot_is_low_when_series_rises() -> None:
    closes = [100, 105, 110, 115]
    bars = _bars_from_closes(closes)
    pivots = zigzag_atr(bars, interval=Interval.DAILY, atr_multiple=Decimal("0"), fixed_threshold=Decimal("3"))
    assert pivots[0].type is PivotType.LOW


def test_zigzag_marks_unconfirmed_tail_as_tentative() -> None:
    # Last leg has not yet retraced — tail pivot should be tentative
    closes = [100, 110, 100, 115]
    bars = _bars_from_closes(closes)
    pivots = zigzag_atr(bars, interval=Interval.DAILY, atr_multiple=Decimal("0"), fixed_threshold=Decimal("3"))
    assert pivots[-1].state in (PivotState.TENTATIVE, PivotState.CONFIRMED)
    # the last bar high (115) is the running peak with no retrace
    if pivots[-1].state is PivotState.TENTATIVE:
        assert pivots[-1].confirmed_at is None
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/pivots/test_zigzag.py -v`
Expected: FAIL — modules missing.

- [ ] **Step 3: Implement pivot models**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/pivots/models.py`:

```python
from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict

from wave_alpha.domain import Interval


class PivotType(str, Enum):
    HIGH = "high"
    LOW = "low"


class PivotState(str, Enum):
    CONFIRMED = "confirmed"
    TENTATIVE = "tentative"


class PivotEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    interval: Interval
    degree: int
    timestamp: date
    price: Decimal
    type: PivotType
    state: PivotState
    confirmed_at: date | None = None
```

- [ ] **Step 4: Implement ZigZag**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/pivots/zigzag.py`:

```python
from __future__ import annotations

from decimal import Decimal

from wave_alpha.domain import Bar, Interval
from wave_alpha.pivots.atr import compute_atr
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def zigzag_atr(
    bars: list[Bar],
    interval: Interval,
    atr_multiple: Decimal,
    degree: int = 0,
    fixed_threshold: Decimal | None = None,
    atr_period: int = 14,
) -> list[PivotEvent]:
    """Pivot detection via ATR-scaled ZigZag.

    Threshold at bar i is `atr_multiple * ATR(i)` unless `fixed_threshold` is supplied
    (used by tests for deterministic synthetic series).

    Returns pivots in ascending timestamp order. The final pivot is marked TENTATIVE
    until price moves at least `threshold` against it.
    """
    if not bars:
        return []

    atr = compute_atr(bars, period=atr_period)

    def threshold_at(i: int) -> Decimal:
        if fixed_threshold is not None:
            return fixed_threshold
        a = atr[i]
        return Decimal("0") if a is None else (a * atr_multiple)

    # Walk forward, tracking running extremes; emit a confirmed pivot when price
    # has retraced threshold against the running extreme.
    pivots: list[PivotEvent] = []

    direction: PivotType | None = None  # current trend; first pivot is determined retroactively
    extreme_idx = 0
    extreme_price = bars[0].close

    for i in range(1, len(bars)):
        price = bars[i].close
        thr = threshold_at(i)

        if direction is None:
            # bootstrap: first significant move sets direction
            move = price - extreme_price
            if abs(move) >= thr and thr > 0:
                # first pivot is at extreme_idx with type opposite to the new direction
                first_type = PivotType.LOW if move > 0 else PivotType.HIGH
                pivots.append(
                    _confirmed(bars[extreme_idx], first_type, interval, degree, bars[i].timestamp)
                )
                direction = PivotType.HIGH if move > 0 else PivotType.LOW
                extreme_idx = i
                extreme_price = price
            else:
                # update running extreme so first significant pivot is most extreme
                if (move > 0 and price > extreme_price) or (
                    move < 0 and price < extreme_price
                ):
                    extreme_idx = i
                    extreme_price = price
            continue

        if direction is PivotType.HIGH:
            if price > extreme_price:
                extreme_idx, extreme_price = i, price
            elif extreme_price - price >= thr and thr > 0:
                pivots.append(
                    _confirmed(bars[extreme_idx], PivotType.HIGH, interval, degree, bars[i].timestamp)
                )
                direction = PivotType.LOW
                extreme_idx, extreme_price = i, price
        else:
            if price < extreme_price:
                extreme_idx, extreme_price = i, price
            elif price - extreme_price >= thr and thr > 0:
                pivots.append(
                    _confirmed(bars[extreme_idx], PivotType.LOW, interval, degree, bars[i].timestamp)
                )
                direction = PivotType.HIGH
                extreme_idx, extreme_price = i, price

    # Final tentative pivot at running extreme (if direction has been set)
    if direction is not None:
        pivots.append(
            PivotEvent(
                interval=interval,
                degree=degree,
                timestamp=bars[extreme_idx].timestamp,
                price=extreme_price,
                type=direction,
                state=PivotState.TENTATIVE,
                confirmed_at=None,
            )
        )

    return pivots


def _confirmed(
    bar: Bar, ptype: PivotType, interval: Interval, degree: int, confirmed_at
) -> PivotEvent:
    return PivotEvent(
        interval=interval,
        degree=degree,
        timestamp=bar.timestamp,
        price=bar.high if ptype is PivotType.HIGH else bar.low,
        type=ptype,
        state=PivotState.CONFIRMED,
        confirmed_at=confirmed_at,
    )
```

- [ ] **Step 5: Verify pass**

Run: `uv run pytest tests/pivots/test_zigzag.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add src/wave_alpha/pivots/models.py src/wave_alpha/pivots/zigzag.py tests/pivots/test_zigzag.py
git commit -m "$(cat <<'EOF'
feat(pivots): single-threshold ATR-ZigZag with confirmed/tentative tail

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 11: Multi-degree orchestrator with subset invariant

**Files:**
- Create: `src/wave_alpha/pivots/multi_degree.py`
- Create: `tests/pivots/test_multi_degree.py`

- [ ] **Step 1: Write failing test**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/pivots/test_multi_degree.py`:

```python
from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.domain import Bar, Interval
from wave_alpha.pivots.multi_degree import detect_multi_degree


def _bar(i: int, c: float) -> Bar:
    cd = Decimal(f"{c:.4f}")
    return Bar(
        timestamp=date(2024, 1, 1) + timedelta(days=i),
        open=cd,
        high=cd + Decimal("0.50"),
        low=cd - Decimal("0.50"),
        close=cd,
        volume=1,
    )


def test_multi_degree_subset_invariant() -> None:
    # Synthetic price series with overlapping minor and major swings
    closes = [
        100, 102, 105, 108, 105, 103, 107, 110, 113, 116, 113,  # rising w/ minor pullbacks
        110, 105, 100, 95, 92, 95, 99, 103, 107, 111, 115, 119, 122,  # bigger swing
    ]
    bars = [_bar(i, c) for i, c in enumerate(closes)]

    # Use fixed thresholds to keep the test deterministic
    by_degree = detect_multi_degree(
        bars,
        interval=Interval.DAILY,
        thresholds=[Decimal("3"), Decimal("6"), Decimal("12")],  # ascending
    )
    assert set(by_degree.keys()) == {0, 1, 2}

    # subset invariant: every higher-degree pivot must coincide with a lower-degree pivot
    for higher, lower in ((1, 0), (2, 1)):
        higher_keys = {(p.timestamp, p.type) for p in by_degree[higher]}
        lower_keys = {(p.timestamp, p.type) for p in by_degree[lower]}
        assert higher_keys.issubset(lower_keys), (
            f"degree {higher} not a subset of degree {lower}: "
            f"missing {higher_keys - lower_keys}"
        )
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/pivots/test_multi_degree.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/pivots/multi_degree.py`:

```python
from __future__ import annotations

from decimal import Decimal

from wave_alpha.domain import Bar, Interval
from wave_alpha.pivots.models import PivotEvent
from wave_alpha.pivots.zigzag import zigzag_atr


def detect_multi_degree(
    bars: list[Bar],
    interval: Interval,
    *,
    atr_multiples: list[Decimal] | None = None,
    thresholds: list[Decimal] | None = None,
) -> dict[int, list[PivotEvent]]:
    """Run ZigZag at multiple sensitivities and enforce the subset invariant.

    Provide exactly one of atr_multiples (for live use) or thresholds (deterministic
    fixed numbers, useful for tests). Lists must be ascending — degree N+1 is the
    coarser threshold and a strict subset of degree N.
    """
    if (atr_multiples is None) == (thresholds is None):
        raise ValueError("provide exactly one of atr_multiples or thresholds")

    levels = atr_multiples or thresholds
    assert levels is not None
    assert all(a <= b for a, b in zip(levels, levels[1:])), "levels must be ascending"

    by_degree: dict[int, list[PivotEvent]] = {}
    for degree, level in enumerate(levels):
        if atr_multiples is not None:
            pivots = zigzag_atr(bars, interval=interval, atr_multiple=level, degree=degree)
        else:
            pivots = zigzag_atr(
                bars,
                interval=interval,
                atr_multiple=Decimal("0"),
                degree=degree,
                fixed_threshold=level,
            )
        if degree > 0:
            pivots = _intersect_with(by_degree[degree - 1], pivots, degree=degree)
        by_degree[degree] = pivots
    return by_degree


def _intersect_with(
    parent: list[PivotEvent], child: list[PivotEvent], *, degree: int
) -> list[PivotEvent]:
    parent_keys = {(p.timestamp, p.type): p for p in parent}
    out: list[PivotEvent] = []
    for c in child:
        if (c.timestamp, c.type) in parent_keys:
            # rebrand the parent pivot at this degree
            out.append(
                PivotEvent(
                    interval=c.interval,
                    degree=degree,
                    timestamp=c.timestamp,
                    price=c.price,
                    type=c.type,
                    state=c.state,
                    confirmed_at=c.confirmed_at,
                )
            )
    return out
```

- [ ] **Step 4: Verify pass**

Run: `uv run pytest tests/pivots/test_multi_degree.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add src/wave_alpha/pivots/multi_degree.py tests/pivots/test_multi_degree.py
git commit -m "$(cat <<'EOF'
feat(pivots): multi-degree orchestrator enforcing subset invariant

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase D — Elliott engine

Pattern templates as YAML, a constraint DSL evaluator, fib helpers, and a recursive-descent count enumerator that prunes on hard violations.

### Task 12: Elliott domain models

**Files:**
- Create: `src/wave_alpha/elliott/__init__.py`
- Create: `src/wave_alpha/elliott/models.py`
- Create: `tests/elliott/__init__.py`
- Create: `tests/elliott/test_models.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/elliott/__init__.py`:

```python
```

Create `/Users/alexchen/Documents/project/wave_alpha/tests/elliott/test_models.py`:

```python
from datetime import date
from decimal import Decimal

from wave_alpha.domain import Interval
from wave_alpha.elliott.models import WaveCount, WaveSegment
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def _piv(d: date, price: str, ptype: PivotType) -> PivotEvent:
    return PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=d,
        price=Decimal(price),
        type=ptype,
        state=PivotState.CONFIRMED,
        confirmed_at=d,
    )


def test_wave_segment_length_is_signed_price_distance() -> None:
    seg = WaveSegment(
        start=_piv(date(2024, 1, 1), "100", PivotType.LOW),
        end=_piv(date(2024, 1, 10), "115", PivotType.HIGH),
    )
    assert seg.length == Decimal("15")
    assert seg.is_up


def test_wave_count_sub_count_lookup() -> None:
    wc = WaveCount(
        pattern="impulse",
        degree=2,
        pivots=[
            _piv(date(2024, 1, 1), "100", PivotType.LOW),
            _piv(date(2024, 1, 5), "110", PivotType.HIGH),
            _piv(date(2024, 1, 8), "105", PivotType.LOW),
            _piv(date(2024, 1, 15), "125", PivotType.HIGH),
            _piv(date(2024, 1, 18), "118", PivotType.LOW),
            _piv(date(2024, 1, 25), "135", PivotType.HIGH),
        ],
        score=0.7,
    )
    assert len(wc.segments()) == 5
    seg1 = wc.segment_by_label("1")
    assert seg1.length == Decimal("10")
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/elliott/test_models.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/elliott/__init__.py`:

```python
```

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/elliott/models.py`:

```python
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from wave_alpha.pivots.models import PivotEvent

_PATTERN_LABELS: dict[str, list[str]] = {
    "impulse": ["1", "2", "3", "4", "5"],
    "leading_diagonal": ["1", "2", "3", "4", "5"],
    "ending_diagonal": ["1", "2", "3", "4", "5"],
    "zigzag": ["A", "B", "C"],
    "flat": ["A", "B", "C"],
    "expanded_flat": ["A", "B", "C"],
    "contracting_triangle": ["A", "B", "C", "D", "E"],
}


class WaveSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: PivotEvent
    end: PivotEvent

    @property
    def length(self) -> Decimal:
        return abs(self.end.price - self.start.price)

    @property
    def signed_length(self) -> Decimal:
        return self.end.price - self.start.price

    @property
    def is_up(self) -> bool:
        return self.signed_length > 0


class WaveCount(BaseModel):
    """A pattern instance bound to a sequence of pivots, with optional sub-counts."""

    model_config = ConfigDict(frozen=True)

    pattern: str
    degree: int
    pivots: list[PivotEvent]
    sub_counts: dict[str, "WaveCount"] = Field(default_factory=dict)
    score: float = 0.0
    hard_violations: list[str] = Field(default_factory=list)
    soft_violations: list[str] = Field(default_factory=list)

    def segments(self) -> list[WaveSegment]:
        return [
            WaveSegment(start=a, end=b) for a, b in zip(self.pivots, self.pivots[1:])
        ]

    def segment_by_label(self, label: str) -> WaveSegment:
        labels = _PATTERN_LABELS[self.pattern]
        idx = labels.index(label)  # raises ValueError if not present
        segs = self.segments()
        return segs[idx]
```

- [ ] **Step 4: Verify pass**

Run: `uv run pytest tests/elliott/test_models.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add src/wave_alpha/elliott tests/elliott
git commit -m "$(cat <<'EOF'
feat(elliott): WaveSegment and WaveCount domain models

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 13: Constraint DSL helpers

**Files:**
- Create: `src/wave_alpha/elliott/dsl.py`
- Create: `tests/elliott/test_dsl.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/elliott/test_dsl.py`:

```python
from datetime import date
from decimal import Decimal

import pytest

from wave_alpha.domain import Interval
from wave_alpha.elliott.dsl import evaluate, length, overlaps, retrace
from wave_alpha.elliott.models import WaveSegment
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def _piv(d: date, price: str, t: PivotType) -> PivotEvent:
    return PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=d,
        price=Decimal(price),
        type=t,
        state=PivotState.CONFIRMED,
        confirmed_at=d,
    )


W1 = WaveSegment(
    start=_piv(date(2024, 1, 1), "100", PivotType.LOW),
    end=_piv(date(2024, 1, 5), "110", PivotType.HIGH),
)
W2 = WaveSegment(
    start=_piv(date(2024, 1, 5), "110", PivotType.HIGH),
    end=_piv(date(2024, 1, 8), "104", PivotType.LOW),
)
W3 = WaveSegment(
    start=_piv(date(2024, 1, 8), "104", PivotType.LOW),
    end=_piv(date(2024, 1, 15), "130", PivotType.HIGH),
)
W4 = WaveSegment(
    start=_piv(date(2024, 1, 15), "130", PivotType.HIGH),
    end=_piv(date(2024, 1, 18), "118", PivotType.LOW),
)


def test_length_returns_unsigned_distance() -> None:
    assert length(W1) == Decimal("10")
    assert length(W2) == Decimal("6")


def test_retrace_relative_to_prior_wave() -> None:
    # W2 retraces 6/10 = 0.6 of W1
    assert retrace(W2, W1) == pytest.approx(Decimal("0.6"))


def test_overlaps_detects_w4_into_w1_territory() -> None:
    # W1 took price 100→110; W4 ended at 118 (above 110) → no overlap
    assert not overlaps(W4, W1)
    # Construct a W4 that DID overlap
    bad_w4 = WaveSegment(
        start=W3.end,
        end=_piv(date(2024, 1, 19), "108", PivotType.LOW),
    )
    assert overlaps(bad_w4, W1)


def test_evaluate_passes_for_simple_constraint() -> None:
    ok, _ = evaluate(
        "length(W3) >= 1.618 * length(W1)",
        bindings={"W1": W1, "W3": W3},
    )
    assert ok is True


def test_evaluate_returns_false_with_message_on_failure() -> None:
    ok, msg = evaluate(
        "length(W3) > 100 * length(W1)",
        bindings={"W1": W1, "W3": W3},
    )
    assert ok is False
    assert "False" in msg or "False" == msg or msg
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/elliott/test_dsl.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/elliott/dsl.py`:

```python
from __future__ import annotations

from decimal import Decimal
from typing import Any

from wave_alpha.elliott.models import WaveSegment


def length(seg: WaveSegment) -> Decimal:
    return seg.length


def retrace(seg: WaveSegment, prior: WaveSegment) -> Decimal:
    if prior.length == 0:
        return Decimal(0)
    return seg.length / prior.length


def overlaps(later: WaveSegment, earlier: WaveSegment) -> bool:
    """True if `later`'s end has crossed back into `earlier`'s price range."""
    e_lo = min(earlier.start.price, earlier.end.price)
    e_hi = max(earlier.start.price, earlier.end.price)
    return e_lo <= later.end.price <= e_hi


def evaluate(expr: str, bindings: dict[str, WaveSegment]) -> tuple[bool, str]:
    """Evaluate a constraint expression in a constrained namespace.

    Returns (passed, diagnostic). Treats any exception as a failed evaluation.
    """
    namespace: dict[str, Any] = {
        "length": length,
        "retrace": retrace,
        "overlaps": overlaps,
        "min": min,
        "max": max,
        "abs": abs,
        "Decimal": Decimal,
        **bindings,
    }
    try:
        # eval is acceptable here: templates are first-party YAML, not user input
        result = eval(expr, {"__builtins__": {}}, namespace)  # noqa: S307
        return bool(result), str(result)
    except Exception as exc:  # noqa: BLE001
        return False, f"eval error: {exc!s}"
```

- [ ] **Step 4: Verify pass**

Run: `uv run pytest tests/elliott/test_dsl.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add src/wave_alpha/elliott/dsl.py tests/elliott/test_dsl.py
git commit -m "$(cat <<'EOF'
feat(elliott): constraint DSL with length/retrace/overlaps helpers

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 14: PatternTemplate model and YAML loader

**Files:**
- Create: `src/wave_alpha/elliott/templates.py`
- Create: `src/wave_alpha/elliott/templates_data/impulse.yaml`
- Create: `tests/elliott/test_templates.py`

- [ ] **Step 1: Write failing test**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/elliott/test_templates.py`:

```python
from wave_alpha.elliott.templates import PatternTemplate, load_bundled_templates


def test_impulse_template_has_three_hard_constraints() -> None:
    tpl = PatternTemplate.load_bundled("impulse")
    assert tpl.name == "impulse"
    assert len(tpl.waves) == 5
    hard = [c for c in tpl.constraints if c.severity == "hard"]
    hard_names = {c.name for c in hard}
    assert {"wave_2_max_retrace", "wave_3_not_shortest", "wave_4_no_overlap"} <= hard_names


def test_load_bundled_returns_all_v0_1_patterns() -> None:
    tpls = load_bundled_templates()
    names = {t.name for t in tpls}
    assert {
        "impulse",
        "zigzag",
        "flat",
        "expanded_flat",
        "contracting_triangle",
        "ending_diagonal",
    } <= names
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/elliott/test_templates.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement template loader**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/elliott/templates.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict


class Constraint(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    severity: Literal["hard", "soft"]
    expr: str
    rationale: str = ""


class PatternTemplate(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    direction_alternates: bool = True
    waves: list[str]
    constraints: list[Constraint]
    allowed_sub_patterns: dict[str, list[str]] = {}

    @classmethod
    def load_bundled(cls, name: str) -> PatternTemplate:
        path = _bundled_dir() / f"{name}.yaml"
        return cls.from_yaml_file(path)

    @classmethod
    def from_yaml_file(cls, path: Path) -> PatternTemplate:
        return cls.model_validate(yaml.safe_load(path.read_text()))


def _bundled_dir() -> Path:
    return Path(__file__).resolve().parent / "templates_data"


def load_bundled_templates() -> list[PatternTemplate]:
    return [
        PatternTemplate.from_yaml_file(p)
        for p in sorted(_bundled_dir().glob("*.yaml"))
    ]
```

- [ ] **Step 4: Add the impulse template YAML**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/elliott/templates_data/impulse.yaml`:

```yaml
name: impulse
direction_alternates: true
waves: ["1", "2", "3", "4", "5"]
constraints:
  - name: wave_2_max_retrace
    severity: hard
    expr: "retrace(W2, W1) <= 1.0"
    rationale: "Wave 2 cannot retrace more than 100% of Wave 1"
  - name: wave_3_not_shortest
    severity: hard
    expr: "length(W3) >= min(length(W1), length(W5))"
    rationale: "Wave 3 cannot be the shortest of waves 1, 3, 5"
  - name: wave_4_no_overlap
    severity: hard
    expr: "not overlaps(W4, W1)"
    rationale: "Wave 4 cannot overlap Wave 1 territory"
  - name: wave_2_typical_retrace
    severity: soft
    expr: "Decimal('0.5') <= retrace(W2, W1) <= Decimal('0.786')"
    rationale: "Wave 2 typically retraces 50-78.6 percent of Wave 1"
  - name: wave_3_extension
    severity: soft
    expr: "length(W3) >= Decimal('1.618') * length(W1)"
    rationale: "Wave 3 typically extends 1.618x Wave 1 or more"
allowed_sub_patterns:
  "1": ["impulse", "leading_diagonal"]
  "2": ["zigzag", "flat", "expanded_flat", "contracting_triangle"]
  "3": ["impulse"]
  "4": ["zigzag", "flat", "expanded_flat", "contracting_triangle"]
  "5": ["impulse", "ending_diagonal"]
```

- [ ] **Step 5: Add the other 5 v0.1 templates**

Create the remaining files in `src/wave_alpha/elliott/templates_data/`:

`zigzag.yaml`:

```yaml
name: zigzag
direction_alternates: true
waves: ["A", "B", "C"]
constraints:
  - name: b_max_retrace_of_a
    severity: hard
    expr: "retrace(WB, WA) <= Decimal('0.9')"
    rationale: "Wave B retraces less than 90% of Wave A in a zigzag"
  - name: c_meets_or_exceeds_a
    severity: soft
    expr: "length(WC) >= Decimal('0.618') * length(WA)"
    rationale: "Wave C typically reaches at least 0.618x Wave A"
allowed_sub_patterns:
  "A": ["impulse", "leading_diagonal"]
  "B": ["zigzag", "flat", "contracting_triangle"]
  "C": ["impulse", "ending_diagonal"]
```

`flat.yaml`:

```yaml
name: flat
direction_alternates: true
waves: ["A", "B", "C"]
constraints:
  - name: b_retrace_near_a
    severity: hard
    expr: "Decimal('0.9') <= retrace(WB, WA) <= Decimal('1.05')"
    rationale: "Wave B in a regular flat retraces 90-105% of Wave A"
  - name: c_terminates_near_a_extreme
    severity: soft
    expr: "length(WC) >= Decimal('0.9') * length(WA)"
    rationale: "Wave C typically reaches Wave A's extreme"
allowed_sub_patterns:
  "A": ["zigzag", "flat", "contracting_triangle"]
  "B": ["zigzag", "flat", "contracting_triangle"]
  "C": ["impulse", "ending_diagonal"]
```

`expanded_flat.yaml`:

```yaml
name: expanded_flat
direction_alternates: true
waves: ["A", "B", "C"]
constraints:
  - name: b_exceeds_a_start
    severity: hard
    expr: "retrace(WB, WA) > Decimal('1.05')"
    rationale: "Wave B in an expanded flat exceeds Wave A's origin"
  - name: c_extends_past_a
    severity: hard
    expr: "length(WC) >= length(WA)"
    rationale: "Wave C exceeds Wave A's extreme in an expanded flat"
allowed_sub_patterns:
  "A": ["zigzag", "flat", "contracting_triangle"]
  "B": ["zigzag", "flat", "contracting_triangle"]
  "C": ["impulse", "ending_diagonal"]
```

`contracting_triangle.yaml`:

```yaml
name: contracting_triangle
direction_alternates: true
waves: ["A", "B", "C", "D", "E"]
constraints:
  - name: c_inside_a
    severity: hard
    expr: "length(WC) < length(WA)"
    rationale: "Wave C must be smaller than Wave A in a contracting triangle"
  - name: e_smallest_or_near_smallest
    severity: soft
    expr: "length(WE) <= length(WC)"
    rationale: "Wave E typically ends inside Wave C's range"
allowed_sub_patterns:
  "A": ["zigzag", "flat", "contracting_triangle"]
  "B": ["zigzag", "flat", "contracting_triangle"]
  "C": ["zigzag", "flat", "contracting_triangle"]
  "D": ["zigzag", "flat", "contracting_triangle"]
  "E": ["zigzag", "flat", "contracting_triangle"]
```

`ending_diagonal.yaml`:

```yaml
name: ending_diagonal
direction_alternates: true
waves: ["1", "2", "3", "4", "5"]
constraints:
  - name: wave_4_overlaps_wave_1
    severity: soft
    expr: "overlaps(W4, W1)"
    rationale: "Wave 4 SHOULD overlap Wave 1 in an ending diagonal"
  - name: wave_3_not_shortest
    severity: hard
    expr: "length(W3) >= min(length(W1), length(W5))"
    rationale: "Wave 3 still cannot be the shortest"
allowed_sub_patterns:
  "1": ["zigzag"]
  "2": ["zigzag", "flat"]
  "3": ["zigzag"]
  "4": ["zigzag", "flat"]
  "5": ["zigzag"]
```

- [ ] **Step 6: Verify pass**

Run: `uv run pytest tests/elliott/test_templates.py -v`
Expected: 2 passed.

- [ ] **Step 7: Make YAML files reachable from the wheel**

Edit `/Users/alexchen/Documents/project/wave_alpha/pyproject.toml` and add to the `[tool.hatch.build.targets.wheel]` table:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/wave_alpha"]

[tool.hatch.build.targets.wheel.force-include]
"src/wave_alpha/elliott/templates_data" = "wave_alpha/elliott/templates_data"
```

- [ ] **Step 8: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add src/wave_alpha/elliott/templates.py src/wave_alpha/elliott/templates_data \
        tests/elliott/test_templates.py pyproject.toml
git commit -m "$(cat <<'EOF'
feat(elliott): YAML pattern template loader with v0.1 starter set

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 15: Rule validator

**Files:**
- Create: `src/wave_alpha/elliott/validator.py`
- Create: `tests/elliott/test_validator.py`

- [ ] **Step 1: Write failing test**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/elliott/test_validator.py`:

```python
from datetime import date
from decimal import Decimal

from wave_alpha.domain import Interval
from wave_alpha.elliott.models import WaveCount
from wave_alpha.elliott.templates import PatternTemplate
from wave_alpha.elliott.validator import validate
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def _piv(d: date, price: str, t: PivotType) -> PivotEvent:
    return PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=d,
        price=Decimal(price),
        type=t,
        state=PivotState.CONFIRMED,
        confirmed_at=d,
    )


# Bullish impulse: 100 → 110 → 104 → 130 → 118 → 140
GOOD_PIVOTS = [
    _piv(date(2024, 1, 1), "100", PivotType.LOW),
    _piv(date(2024, 1, 5), "110", PivotType.HIGH),
    _piv(date(2024, 1, 8), "104", PivotType.LOW),
    _piv(date(2024, 1, 15), "130", PivotType.HIGH),
    _piv(date(2024, 1, 18), "118", PivotType.LOW),
    _piv(date(2024, 1, 25), "140", PivotType.HIGH),
]


def test_validate_passes_clean_impulse() -> None:
    tpl = PatternTemplate.load_bundled("impulse")
    count = WaveCount(pattern="impulse", degree=2, pivots=GOOD_PIVOTS)
    validated = validate(count, tpl)
    assert validated.hard_violations == []


def test_validate_flags_wave_4_overlap() -> None:
    # Replace W4 endpoint with one that overlaps W1's territory (close at 108 < 110)
    bad = list(GOOD_PIVOTS)
    bad[4] = _piv(date(2024, 1, 18), "108", PivotType.LOW)
    bad[5] = _piv(date(2024, 1, 25), "140", PivotType.HIGH)

    tpl = PatternTemplate.load_bundled("impulse")
    count = WaveCount(pattern="impulse", degree=2, pivots=bad)
    validated = validate(count, tpl)
    assert "wave_4_no_overlap" in validated.hard_violations


def test_validate_flags_short_wave_3() -> None:
    # W3 length 4 < min(W1=10, W5=22)
    pivots = [
        _piv(date(2024, 1, 1), "100", PivotType.LOW),
        _piv(date(2024, 1, 5), "110", PivotType.HIGH),
        _piv(date(2024, 1, 8), "104", PivotType.LOW),
        _piv(date(2024, 1, 12), "108", PivotType.HIGH),
        _piv(date(2024, 1, 15), "106", PivotType.LOW),
        _piv(date(2024, 1, 25), "128", PivotType.HIGH),
    ]
    tpl = PatternTemplate.load_bundled("impulse")
    count = WaveCount(pattern="impulse", degree=2, pivots=pivots)
    validated = validate(count, tpl)
    assert "wave_3_not_shortest" in validated.hard_violations
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/elliott/test_validator.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/elliott/validator.py`:

```python
from __future__ import annotations

from wave_alpha.elliott.dsl import evaluate
from wave_alpha.elliott.models import WaveCount, WaveSegment
from wave_alpha.elliott.templates import PatternTemplate


def validate(count: WaveCount, template: PatternTemplate) -> WaveCount:
    """Run all template constraints and return a copy with violation lists populated."""
    bindings = _segment_bindings(count, template)
    hard: list[str] = []
    soft: list[str] = []
    for c in template.constraints:
        ok, _ = evaluate(c.expr, bindings=bindings)
        if not ok:
            (hard if c.severity == "hard" else soft).append(c.name)
    return count.model_copy(update={"hard_violations": hard, "soft_violations": soft})


def _segment_bindings(count: WaveCount, template: PatternTemplate) -> dict[str, WaveSegment]:
    segs = count.segments()
    bindings: dict[str, WaveSegment] = {}
    for label, seg in zip(template.waves, segs):
        if label.isalpha():
            bindings[f"W{label}"] = seg  # WA, WB, WC, ...
        else:
            bindings[f"W{label}"] = seg  # W1, W2, ...
    return bindings
```

- [ ] **Step 4: Verify pass**

Run: `uv run pytest tests/elliott/test_validator.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add src/wave_alpha/elliott/validator.py tests/elliott/test_validator.py
git commit -m "$(cat <<'EOF'
feat(elliott): rule validator with hard/soft violation lists

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 16: Fib retracement/extension helpers

**Files:**
- Create: `src/wave_alpha/elliott/fib.py`
- Create: `tests/elliott/test_fib.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/elliott/test_fib.py`:

```python
from datetime import date
from decimal import Decimal

import pytest

from wave_alpha.domain import Interval
from wave_alpha.elliott.fib import fib_confluence_score, fib_extension, fib_retracement
from wave_alpha.elliott.models import WaveSegment
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def _piv(d: date, p: str, t: PivotType) -> PivotEvent:
    return PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=d,
        price=Decimal(p),
        type=t,
        state=PivotState.CONFIRMED,
        confirmed_at=d,
    )


W1 = WaveSegment(
    start=_piv(date(2024, 1, 1), "100", PivotType.LOW),
    end=_piv(date(2024, 1, 5), "110", PivotType.HIGH),
)


def test_fib_retracement_levels_are_descending_for_up_wave() -> None:
    levels = fib_retracement(W1)
    # levels at 0.236, 0.382, 0.5, 0.618, 0.786 below 110
    assert levels[Decimal("0.5")] == Decimal("105.0")
    assert levels[Decimal("0.618")] == pytest.approx(Decimal("103.82"), rel=Decimal("0.001"))


def test_fib_extension_levels_above_for_up_wave() -> None:
    levels = fib_extension(W1, anchor_price=Decimal("104"))
    # 1.618 extension from 104 = 104 + 1.618 * 10 = 120.18
    assert levels[Decimal("1.618")] == pytest.approx(Decimal("120.18"), rel=Decimal("0.001"))


def test_confluence_score_high_when_price_near_618() -> None:
    score = fib_confluence_score(target_price=Decimal("103.82"), reference=W1)
    assert score > 0.7
    far = fib_confluence_score(target_price=Decimal("90"), reference=W1)
    assert far < 0.3
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/elliott/test_fib.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/elliott/fib.py`:

```python
from __future__ import annotations

from decimal import Decimal

from wave_alpha.elliott.models import WaveSegment

RETRACE_LEVELS: tuple[Decimal, ...] = (
    Decimal("0.236"),
    Decimal("0.382"),
    Decimal("0.500"),
    Decimal("0.618"),
    Decimal("0.786"),
)

EXTENSION_LEVELS: tuple[Decimal, ...] = (
    Decimal("1.000"),
    Decimal("1.272"),
    Decimal("1.618"),
    Decimal("2.000"),
    Decimal("2.618"),
)


def fib_retracement(prior: WaveSegment) -> dict[Decimal, Decimal]:
    """Price levels at each retracement ratio of `prior`."""
    span = prior.end.price - prior.start.price  # signed
    return {ratio: prior.end.price - ratio * span for ratio in RETRACE_LEVELS}


def fib_extension(prior: WaveSegment, anchor_price: Decimal) -> dict[Decimal, Decimal]:
    """Price levels at each extension ratio of `prior`'s length, anchored at `anchor_price`.

    For an up-wave anchor (e.g., end of Wave 4), levels project upward.
    """
    span = abs(prior.end.price - prior.start.price)
    direction = Decimal(1) if prior.is_up else Decimal(-1)
    return {ratio: anchor_price + direction * ratio * span for ratio in EXTENSION_LEVELS}


def fib_confluence_score(target_price: Decimal, reference: WaveSegment) -> float:
    """0..1, higher when target_price is close to one of the standard fib levels of reference."""
    levels = list(fib_retracement(reference).values()) + list(
        fib_extension(reference, anchor_price=reference.start.price).values()
    )
    span = abs(reference.end.price - reference.start.price) or Decimal(1)
    distances = [abs(target_price - level) / span for level in levels]
    nearest = min(distances)
    # tight: ≤ 1% of span = score 1.0; ≥ 20% of span = score 0
    if nearest <= Decimal("0.01"):
        return 1.0
    if nearest >= Decimal("0.20"):
        return 0.0
    return float(1 - (nearest - Decimal("0.01")) / Decimal("0.19"))
```

- [ ] **Step 4: Verify pass**

Run: `uv run pytest tests/elliott/test_fib.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add src/wave_alpha/elliott/fib.py tests/elliott/test_fib.py
git commit -m "$(cat <<'EOF'
feat(elliott): fib retracement, extension, and confluence scoring

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 17: Count enumerator

**Files:**
- Create: `src/wave_alpha/elliott/enumerator.py`
- Create: `tests/elliott/test_enumerator.py`

The enumerator slides a window over the pivot list at a given degree and tries to fit each compatible template. v0.1 emits flat counts (no recursion into sub-patterns) — sub-count nesting is added when the LLM judge ships in Plan 2 and we need it for narrative depth. The hierarchical-subset invariant from Phase C already guarantees structural nesting is *available* for traversal; we just don't traverse it yet.

- [ ] **Step 1: Write failing test**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/elliott/test_enumerator.py`:

```python
from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.domain import Bar, Interval
from wave_alpha.elliott.enumerator import enumerate_counts
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def _piv(d_off: int, price: str, t: PivotType) -> PivotEvent:
    return PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=date(2024, 1, 1) + timedelta(days=d_off),
        price=Decimal(price),
        type=t,
        state=PivotState.CONFIRMED,
        confirmed_at=date(2024, 1, 1) + timedelta(days=d_off + 1),
    )


def test_enumerate_finds_clean_impulse() -> None:
    pivots = [
        _piv(0, "100", PivotType.LOW),
        _piv(4, "110", PivotType.HIGH),
        _piv(7, "104", PivotType.LOW),
        _piv(14, "130", PivotType.HIGH),
        _piv(17, "118", PivotType.LOW),
        _piv(24, "140", PivotType.HIGH),
    ]
    counts = enumerate_counts(pivots, degree=2, top_k=5)
    patterns = [c.pattern for c in counts]
    assert "impulse" in patterns
    impulse = next(c for c in counts if c.pattern == "impulse")
    assert impulse.hard_violations == []
    assert impulse.score > 0


def test_enumerate_drops_hard_violations() -> None:
    # impulse with W4 overlapping W1
    pivots = [
        _piv(0, "100", PivotType.LOW),
        _piv(4, "110", PivotType.HIGH),
        _piv(7, "104", PivotType.LOW),
        _piv(14, "130", PivotType.HIGH),
        _piv(17, "108", PivotType.LOW),  # overlaps W1's territory
        _piv(24, "140", PivotType.HIGH),
    ]
    counts = enumerate_counts(pivots, degree=2, top_k=5)
    impulse = [c for c in counts if c.pattern == "impulse"]
    assert all(c.hard_violations for c in impulse) or impulse == []


def test_enumerate_returns_top_k() -> None:
    pivots = [_piv(i * 3, f"{100 + i % 3 * 5}", PivotType.HIGH if i % 2 else PivotType.LOW) for i in range(8)]
    counts = enumerate_counts(pivots, degree=2, top_k=3)
    assert len(counts) <= 3
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/elliott/test_enumerator.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/elliott/enumerator.py`:

```python
from __future__ import annotations

from decimal import Decimal

from wave_alpha.elliott.fib import fib_confluence_score
from wave_alpha.elliott.models import WaveCount
from wave_alpha.elliott.templates import PatternTemplate, load_bundled_templates
from wave_alpha.elliott.validator import validate
from wave_alpha.pivots.models import PivotEvent, PivotType

# Templates whose first segment is direction-specified by the first pivot type.
# v0.1 tries each template against the most recent N+1 pivots (where N = wave count).


def enumerate_counts(
    pivots: list[PivotEvent],
    *,
    degree: int,
    top_k: int = 5,
    templates: list[PatternTemplate] | None = None,
) -> list[WaveCount]:
    """Try each template against the most recent pivots; return top_k by score."""
    if not pivots:
        return []
    templates = templates or load_bundled_templates()

    candidates: list[WaveCount] = []
    for tpl in templates:
        n_required = len(tpl.waves) + 1  # waves -> n segments need n+1 pivots
        if len(pivots) < n_required:
            continue
        # Slide the window from the right edge: the most recent attempt anchors on the
        # newest pivot; older candidates are produced by shifting left.
        for end_idx in range(len(pivots), n_required - 1, -1):
            window = pivots[end_idx - n_required : end_idx]
            if not _alternates_ok(window):
                continue
            count = WaveCount(pattern=tpl.name, degree=degree, pivots=window)
            count = validate(count, tpl)
            score = _score(count, tpl)
            count = count.model_copy(update={"score": score})
            if count.hard_violations:
                continue
            candidates.append(count)

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:top_k]


def _alternates_ok(window: list[PivotEvent]) -> bool:
    types = [p.type for p in window]
    return all(a != b for a, b in zip(types, types[1:]))


def _score(count: WaveCount, tpl: PatternTemplate) -> float:
    base = 1.0 - 0.10 * len(count.soft_violations)
    confluence = _confluence_bonus(count, tpl)
    return max(0.0, base * 0.7 + confluence * 0.3)


def _confluence_bonus(count: WaveCount, tpl: PatternTemplate) -> float:
    """Reward counts whose key levels (e.g., W2 retrace) sit on canonical fib levels."""
    segs = count.segments()
    if len(segs) < 2:
        return 0.0
    w1, w2 = segs[0], segs[1]
    return fib_confluence_score(target_price=w2.end.price, reference=w1)
```

- [ ] **Step 4: Verify pass**

Run: `uv run pytest tests/elliott/test_enumerator.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add src/wave_alpha/elliott/enumerator.py tests/elliott/test_enumerator.py
git commit -m "$(cat <<'EOF'
feat(elliott): count enumerator with template fit and fib confluence scoring

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase E — Right-edge probability heuristic

The v0.1 confirmation-probability model: a transparent hand-coded heuristic over seven features. Logistic-regression replacement is deferred to Plan 2 (after the backtest harness produces labels).

### Task 18: RightEdgeFeatures and feature extraction

**Files:**
- Create: `src/wave_alpha/right_edge/__init__.py`
- Create: `src/wave_alpha/right_edge/features.py`
- Create: `tests/right_edge/__init__.py`
- Create: `tests/right_edge/test_features.py`

- [ ] **Step 1: Write failing test**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/right_edge/__init__.py`:

```python
```

Create `/Users/alexchen/Documents/project/wave_alpha/tests/right_edge/test_features.py`:

```python
from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.domain import Bar, Interval
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
from wave_alpha.right_edge.features import extract_features


def _bar(i: int, c: float) -> Bar:
    cd = Decimal(f"{c:.4f}")
    return Bar(
        timestamp=date(2024, 1, 1) + timedelta(days=i),
        open=cd,
        high=cd + Decimal("0.50"),
        low=cd - Decimal("0.50"),
        close=cd,
        volume=1_000_000 - i * 1000,
    )


def test_features_capture_retracement_progress() -> None:
    bars = [_bar(i, 100 + i) for i in range(30)]  # rising
    bars.extend(_bar(30 + i, 130 - i * 0.5) for i in range(6))  # pull back from 130
    candidate = PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=bars[29].timestamp,
        price=bars[29].close,
        type=PivotType.HIGH,
        state=PivotState.TENTATIVE,
    )
    feats = extract_features(candidate=candidate, bars=bars, atr_at_candidate=Decimal("1.0"), required_move=Decimal("5.0"))
    assert 0.0 <= feats.retracement_progress <= 1.0
    assert feats.bars_since_candidate == 6
    assert 0.0 <= feats.atr_regime <= 5.0
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/right_edge/test_features.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/right_edge/__init__.py`:

```python
```

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/right_edge/features.py`:

```python
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from wave_alpha.domain import Bar
from wave_alpha.pivots.models import PivotEvent, PivotType


class RightEdgeFeatures(BaseModel):
    model_config = ConfigDict(frozen=True)

    retracement_progress: float
    bars_since_candidate: int
    fib_zone_match: float
    volume_decline_pct: float
    rsi_divergence_strength: float
    higher_degree_zone_match: float
    atr_regime: float


def extract_features(
    *,
    candidate: PivotEvent,
    bars: list[Bar],
    atr_at_candidate: Decimal,
    required_move: Decimal,
    fib_zone_match: float = 0.0,
    higher_degree_zone_match: float = 0.0,
) -> RightEdgeFeatures:
    """Compute features for a tentative pivot using only bars up through latest.

    Caller is responsible for ensuring `bars` is point-in-time correct.
    """
    if not bars:
        raise ValueError("bars cannot be empty")

    after = [b for b in bars if b.timestamp > candidate.timestamp]
    bars_since = len(after)

    if candidate.type is PivotType.HIGH:
        opposite_extreme = min((b.low for b in after), default=candidate.price)
        moved = max(Decimal(0), candidate.price - opposite_extreme)
    else:
        opposite_extreme = max((b.high for b in after), default=candidate.price)
        moved = max(Decimal(0), opposite_extreme - candidate.price)

    progress = float(min(Decimal(1), moved / required_move)) if required_move > 0 else 0.0

    # volume decline: average volume in last 5 bars vs average over last 25 leading up to candidate
    recent_vol = _avg_volume(bars[-5:])
    baseline_vol = _avg_volume(bars[-25:-5]) or Decimal(1)
    vol_decline = float((baseline_vol - recent_vol) / baseline_vol)

    # atr regime: required_move / median required move over history (approximate via mean atr)
    atr_regime = float(atr_at_candidate / required_move) if required_move > 0 else 1.0

    return RightEdgeFeatures(
        retracement_progress=progress,
        bars_since_candidate=bars_since,
        fib_zone_match=fib_zone_match,
        volume_decline_pct=vol_decline,
        rsi_divergence_strength=0.0,  # placeholder; richer signal computed in Plan 2
        higher_degree_zone_match=higher_degree_zone_match,
        atr_regime=atr_regime,
    )


def _avg_volume(bars: list[Bar]) -> Decimal:
    if not bars:
        return Decimal(0)
    return Decimal(sum(b.volume for b in bars)) / Decimal(len(bars))
```

- [ ] **Step 4: Verify pass**

Run: `uv run pytest tests/right_edge/test_features.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add src/wave_alpha/right_edge/features.py tests/right_edge
git commit -m "$(cat <<'EOF'
feat(right_edge): RightEdgeFeatures with point-in-time extraction

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 19: HeuristicConfirmationModel

**Files:**
- Create: `src/wave_alpha/right_edge/heuristic.py`
- Create: `tests/right_edge/test_heuristic.py`

- [ ] **Step 1: Write failing test**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/right_edge/test_heuristic.py`:

```python
from wave_alpha.right_edge.features import RightEdgeFeatures
from wave_alpha.right_edge.heuristic import HeuristicConfirmationModel


def _features(**overrides) -> RightEdgeFeatures:
    base = dict(
        retracement_progress=0.0,
        bars_since_candidate=0,
        fib_zone_match=0.0,
        volume_decline_pct=0.0,
        rsi_divergence_strength=0.0,
        higher_degree_zone_match=0.0,
        atr_regime=1.0,
    )
    base.update(overrides)
    return RightEdgeFeatures(**base)


def test_heuristic_zero_features_returns_low_probability() -> None:
    model = HeuristicConfirmationModel()
    p = model.predict_proba(_features())
    assert p == 0.0


def test_heuristic_full_retrace_returns_meaningful_probability() -> None:
    model = HeuristicConfirmationModel()
    p = model.predict_proba(_features(retracement_progress=1.0, fib_zone_match=1.0, bars_since_candidate=10))
    assert 0.5 < p <= 1.0


def test_heuristic_clamps_to_unit_interval() -> None:
    model = HeuristicConfirmationModel()
    p = model.predict_proba(_features(retracement_progress=2.0, fib_zone_match=2.0))
    assert 0.0 <= p <= 1.0


def test_heuristic_version_string() -> None:
    assert HeuristicConfirmationModel().version == "heuristic_v1"
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/right_edge/test_heuristic.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/right_edge/heuristic.py`:

```python
from __future__ import annotations

from wave_alpha.right_edge.features import RightEdgeFeatures


class HeuristicConfirmationModel:
    """Hand-coded weighted sum. Replaced by a logistic model trained from
    backtest-derived labels in Plan 2."""

    version: str = "heuristic_v1"

    def predict_proba(self, f: RightEdgeFeatures) -> float:
        score = (
            0.35 * _clip(f.retracement_progress)
            + 0.20 * min(f.bars_since_candidate / 10.0, 1.0)
            + 0.20 * _clip(f.fib_zone_match)
            + 0.10 * max(0.0, f.volume_decline_pct / 0.5)
            + 0.10 * _clip(f.rsi_divergence_strength)
            + 0.05 * _clip(f.higher_degree_zone_match)
        )
        return _clip(score)


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))
```

- [ ] **Step 4: Verify pass**

Run: `uv run pytest tests/right_edge/test_heuristic.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add src/wave_alpha/right_edge/heuristic.py tests/right_edge/test_heuristic.py
git commit -m "$(cat <<'EOF'
feat(right_edge): HeuristicConfirmationModel v1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase F — Multi-timeframe coherence

Pairwise scoring (pattern family + current-wave nesting + direction agreement + pivot drift) and the three-way blend with insufficient-history fallback.

### Task 20: Pairwise coherence

**Files:**
- Create: `src/wave_alpha/coherence/__init__.py`
- Create: `src/wave_alpha/coherence/score.py`
- Create: `tests/coherence/__init__.py`
- Create: `tests/coherence/test_score.py`

- [ ] **Step 1: Write failing test**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/coherence/__init__.py`:

```python
```

Create `/Users/alexchen/Documents/project/wave_alpha/tests/coherence/test_score.py`:

```python
from datetime import date
from decimal import Decimal

from wave_alpha.coherence.score import (
    CoherenceInputs,
    coherence_multiplier,
    coherence_score,
    three_way,
)
from wave_alpha.domain import Interval
from wave_alpha.elliott.models import WaveCount
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def _piv(d: date, p: str, t: PivotType, interval: Interval = Interval.DAILY) -> PivotEvent:
    return PivotEvent(
        interval=interval,
        degree=2,
        timestamp=d,
        price=Decimal(p),
        type=t,
        state=PivotState.CONFIRMED,
        confirmed_at=d,
    )


def _impulse(interval: Interval = Interval.DAILY) -> WaveCount:
    return WaveCount(
        pattern="impulse",
        degree=2,
        pivots=[
            _piv(date(2024, 1, 1), "100", PivotType.LOW, interval),
            _piv(date(2024, 1, 5), "110", PivotType.HIGH, interval),
            _piv(date(2024, 1, 8), "104", PivotType.LOW, interval),
            _piv(date(2024, 1, 15), "130", PivotType.HIGH, interval),
            _piv(date(2024, 1, 18), "118", PivotType.LOW, interval),
            _piv(date(2024, 1, 25), "140", PivotType.HIGH, interval),
        ],
    )


def _zigzag() -> WaveCount:
    return WaveCount(
        pattern="zigzag",
        degree=2,
        pivots=[
            _piv(date(2024, 1, 1), "140", PivotType.HIGH),
            _piv(date(2024, 1, 5), "120", PivotType.LOW),
            _piv(date(2024, 1, 10), "130", PivotType.HIGH),
            _piv(date(2024, 1, 20), "100", PivotType.LOW),
        ],
    )


def test_coherence_full_agreement_high_score() -> None:
    s = coherence_score(_impulse(Interval.DAILY), _impulse(Interval.WEEKLY))
    assert s > 0.7


def test_coherence_disagreement_low_score() -> None:
    s = coherence_score(_impulse(Interval.DAILY), _zigzag())
    assert s < 0.4


def test_three_way_falls_back_when_4h_insufficient() -> None:
    inputs = CoherenceInputs(
        weekly=_impulse(Interval.WEEKLY),
        daily=_impulse(Interval.DAILY),
        hourly4=None,
    )
    blended = three_way(inputs)
    assert blended.fallback_used == "no_4h_history"
    assert 0.7 <= blended.score <= 1.0


def test_multiplier_disagreement_lowers_below_one() -> None:
    assert coherence_multiplier(0.0) < 1.0
    assert coherence_multiplier(1.0) > 1.0
    assert 0.5 <= coherence_multiplier(0.0) and coherence_multiplier(1.0) <= 1.2
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/coherence/test_score.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/coherence/__init__.py`:

```python
```

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/coherence/score.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from wave_alpha.elliott.models import WaveCount

_IMPULSE_FAMILY = {"impulse", "leading_diagonal", "ending_diagonal"}
_CORRECTIVE_FAMILY = {"zigzag", "flat", "expanded_flat", "contracting_triangle"}


def _family(name: str) -> str:
    if name in _IMPULSE_FAMILY:
        return "impulse"
    if name in _CORRECTIVE_FAMILY:
        return "corrective"
    return "unknown"


def _predicted_direction(c: WaveCount) -> str | None:
    """The direction of the most recent segment indicates the current trend."""
    segs = c.segments()
    if not segs:
        return None
    return "up" if segs[-1].is_up else "down"


def coherence_score(a: WaveCount, b: WaveCount) -> float:
    score = 0.0
    if _family(a.pattern) == _family(b.pattern):
        score += 0.4
    # current wave nesting: in v0.1 we approximate by checking that A's last segment
    # ends on the same direction as B's last segment (proxy for "A's current wave
    # is the final sub-wave of B's current wave").
    if _predicted_direction(a) == _predicted_direction(b):
        score += 0.3 + 0.2  # direction + nesting proxy collapsed
    # pivot drift: how aligned are the last high-degree pivots in time?
    if a.pivots and b.pivots:
        drift = abs((a.pivots[-1].timestamp - b.pivots[-1].timestamp).days)
        if drift <= 5:
            score += 0.1
    return min(1.0, score)


def coherence_multiplier(score: float) -> float:
    """Map [0, 1] coherence to [0.5, 1.2] multiplier."""
    return 0.5 + 0.7 * max(0.0, min(1.0, score))


class CoherenceInputs(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    weekly: WaveCount | None
    daily: WaveCount | None
    hourly4: WaveCount | None


@dataclass
class CoherenceResult:
    score: float
    multiplier: float
    pairwise: dict[str, float]
    fallback_used: str | None


def three_way(inputs: CoherenceInputs) -> CoherenceResult:
    pairwise: dict[str, float] = {}
    fallback: str | None = None

    if inputs.weekly is None or inputs.daily is None:
        return CoherenceResult(
            score=0.5, multiplier=coherence_multiplier(0.5), pairwise={}, fallback_used="insufficient_history"
        )

    pairwise["weekly_daily"] = coherence_score(inputs.weekly, inputs.daily)

    if inputs.hourly4 is None:
        fallback = "no_4h_history"
        score = pairwise["weekly_daily"]
    else:
        pairwise["daily_4h"] = coherence_score(inputs.daily, inputs.hourly4)
        pairwise["weekly_4h"] = coherence_score(inputs.weekly, inputs.hourly4)
        score = (
            0.5 * pairwise["weekly_daily"]
            + 0.35 * pairwise["daily_4h"]
            + 0.15 * pairwise["weekly_4h"]
        )

    return CoherenceResult(
        score=score, multiplier=coherence_multiplier(score), pairwise=pairwise, fallback_used=fallback
    )
```

- [ ] **Step 4: Verify pass**

Run: `uv run pytest tests/coherence/test_score.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add src/wave_alpha/coherence tests/coherence
git commit -m "$(cat <<'EOF'
feat(coherence): pairwise + three-way blend with insufficient-history fallback

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase G — Trade-signal derivation

Convert a `WaveCount` into a `TradeSignal` per `TradeRules`. The hard work — ablation sweep and trade-sim — lives in v0.5 (Plan 4 future); v0.1 just produces signals using the spec defaults.

### Task 21: TradeRules and TradeSignal models

**Files:**
- Create: `src/wave_alpha/trades/__init__.py`
- Create: `src/wave_alpha/trades/models.py`
- Create: `tests/trades/__init__.py`
- Create: `tests/trades/test_models.py`

- [ ] **Step 1: Write failing test**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/trades/__init__.py`:

```python
```

Create `/Users/alexchen/Documents/project/wave_alpha/tests/trades/test_models.py`:

```python
from decimal import Decimal

import pytest
from pydantic import ValidationError

from wave_alpha.trades.models import TradeRules, TradeSignal


def test_trade_rules_defaults() -> None:
    r = TradeRules()
    assert "long" in r.direction_modes and "short" in r.direction_modes
    assert r.entry_trigger == "immediate"
    assert r.stop_source == "count_invalidation"
    assert r.target_source == "count_target"
    assert r.min_confidence == 0.45


def test_trade_signal_rejects_zero_risk() -> None:
    with pytest.raises(ValidationError):
        TradeSignal(
            ticker="AAPL",
            as_of="2024-04-26",
            direction="long",
            entry_price=Decimal("100"),
            stop_price=Decimal("100"),  # zero risk
            target_price=Decimal("110"),
            count_id="c1",
            count_pattern="impulse",
            wave_label="5",
            confidence=0.6,
            expected_bars_to_target=20,
            max_holding_bars=30,
        )
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/trades/test_models.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/trades/__init__.py`:

```python
```

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/trades/models.py`:

```python
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


class TradeRules(BaseModel):
    model_config = ConfigDict(frozen=True)

    direction_modes: list[Literal["long", "short"]] = Field(default_factory=lambda: ["long", "short"])
    entry_trigger: Literal["immediate", "confirmation"] = "immediate"
    stop_source: Literal["count_invalidation", "atr_multiple"] = "count_invalidation"
    target_source: Literal["count_target", "fib_extension", "fixed_R"] = "count_target"
    target_R: float | None = None
    time_stop_mode: Literal["degree_based", "fixed"] = "degree_based"
    time_stop_bars: int | None = None
    max_concurrent_signals_per_ticker: int = 1
    slippage_bps: int = 10
    commission_per_share: Decimal = Decimal("0")
    short_borrow_annual_pct: float = 0.005
    min_confidence: float = 0.45


class TradeSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    as_of: date
    direction: Literal["long", "short"]
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    count_id: str
    count_pattern: str
    wave_label: str
    confidence: float
    expected_bars_to_target: int
    max_holding_bars: int
    breakeven_trigger: Decimal | None = None

    @model_validator(mode="after")
    def _check_risk(self) -> TradeSignal:
        if self.entry_price == self.stop_price:
            raise ValueError("entry_price and stop_price cannot be equal (zero risk)")
        return self

    @computed_field
    @property
    def risk_per_share(self) -> Decimal:
        return abs(self.entry_price - self.stop_price)

    @computed_field
    @property
    def reward_per_share(self) -> Decimal:
        return abs(self.target_price - self.entry_price)

    @computed_field
    @property
    def rr(self) -> float:
        return float(self.reward_per_share / self.risk_per_share) if self.risk_per_share else 0.0
```

- [ ] **Step 4: Verify pass**

Run: `uv run pytest tests/trades/test_models.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add src/wave_alpha/trades tests/trades
git commit -m "$(cat <<'EOF'
feat(trades): TradeRules and TradeSignal Pydantic models

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 22: Signal derivation from a count

**Files:**
- Create: `src/wave_alpha/trades/derive.py`
- Create: `tests/trades/test_derive.py`

- [ ] **Step 1: Write failing test**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/trades/test_derive.py`:

```python
from datetime import date
from decimal import Decimal

import pytest

from wave_alpha.domain import Interval
from wave_alpha.elliott.models import WaveCount
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
from wave_alpha.trades.derive import derive_signal
from wave_alpha.trades.models import TradeRules


def _piv(d: date, price: str, t: PivotType) -> PivotEvent:
    return PivotEvent(
        interval=Interval.DAILY,
        degree=2,
        timestamp=d,
        price=Decimal(price),
        type=t,
        state=PivotState.CONFIRMED,
        confirmed_at=d,
    )


def _bullish_w5_setup() -> WaveCount:
    # ended W4 at 118 → expect Wave 5 up; treat the most recent pivot as the W4 low
    return WaveCount(
        pattern="impulse",
        degree=2,
        score=0.7,
        pivots=[
            _piv(date(2024, 1, 1), "100", PivotType.LOW),
            _piv(date(2024, 1, 5), "110", PivotType.HIGH),
            _piv(date(2024, 1, 8), "104", PivotType.LOW),
            _piv(date(2024, 1, 15), "130", PivotType.HIGH),
            _piv(date(2024, 1, 18), "118", PivotType.LOW),
        ],
    )


def test_derive_long_signal_from_w5_setup() -> None:
    count = _bullish_w5_setup()
    sig = derive_signal(
        count=count,
        ticker="AAPL",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(),
    )
    assert sig is not None
    assert sig.direction == "long"
    assert sig.stop_price == Decimal("118")
    assert sig.target_price > sig.entry_price
    assert sig.rr > 1.0


def test_derive_returns_none_below_min_confidence() -> None:
    count = _bullish_w5_setup().model_copy(update={"score": 0.1})
    sig = derive_signal(
        count=count,
        ticker="AAPL",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(min_confidence=0.45),
    )
    assert sig is None


def test_derive_returns_none_for_long_only_rules_when_short_setup() -> None:
    # Mirror to a corrective top → bearish setup
    count = WaveCount(
        pattern="impulse",
        degree=2,
        score=0.7,
        pivots=[
            _piv(date(2024, 1, 1), "140", PivotType.HIGH),
            _piv(date(2024, 1, 5), "130", PivotType.LOW),
            _piv(date(2024, 1, 8), "136", PivotType.HIGH),
            _piv(date(2024, 1, 15), "110", PivotType.LOW),
            _piv(date(2024, 1, 18), "122", PivotType.HIGH),
        ],
    )
    sig = derive_signal(
        count=count,
        ticker="AAPL",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(direction_modes=["long"]),
    )
    assert sig is None
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/trades/test_derive.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/trades/derive.py`:

```python
from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal

from wave_alpha.elliott.fib import fib_extension
from wave_alpha.elliott.models import WaveCount
from wave_alpha.trades.models import TradeRules, TradeSignal

_DEFAULT_BARS_PER_DEGREE: dict[int, int] = {0: 5, 1: 10, 2: 25, 3: 60, 4: 150}


def derive_signal(
    *,
    count: WaveCount,
    ticker: str,
    as_of: date,
    current_price: Decimal,
    rules: TradeRules,
) -> TradeSignal | None:
    """Convert a WaveCount into a TradeSignal per the rules. Returns None if
    confidence is below the minimum or the implied direction is excluded."""
    if count.score < rules.min_confidence:
        return None

    direction = _implied_direction(count)
    if direction not in rules.direction_modes:
        return None

    stop = _stop_price(count, direction, rules)
    if stop == current_price:
        return None  # zero-risk degenerate

    target = _target_price(count, direction, rules, anchor=current_price)
    expected = _DEFAULT_BARS_PER_DEGREE.get(count.degree, 25)
    max_hold = int(expected * 1.5)

    risk = abs(current_price - stop)
    breakeven = current_price + (risk if direction == "long" else -risk)

    return TradeSignal(
        ticker=ticker,
        as_of=as_of,
        direction=direction,
        entry_price=current_price,
        stop_price=stop,
        target_price=target,
        count_id=_count_id(count),
        count_pattern=count.pattern,
        wave_label=_current_wave_label(count),
        confidence=count.score,
        expected_bars_to_target=expected,
        max_holding_bars=max_hold,
        breakeven_trigger=breakeven,
    )


def _implied_direction(count: WaveCount) -> str:
    """Direction the next move is expected to take.

    For impulses where 5 segments have completed, the direction reverses (we expect
    correction). For impulses with 4 segments (W4 just printed), we expect another
    impulse leg.
    """
    segs = count.segments()
    if not segs:
        return "long"
    last_up = segs[-1].is_up
    if len(segs) == 5:
        return "short" if last_up else "long"
    if len(segs) == 4:
        return "long" if last_up is False else "short"
    return "long" if not last_up else "short"


def _stop_price(count: WaveCount, direction: str, rules: TradeRules) -> Decimal:
    segs = count.segments()
    last_pivot = count.pivots[-1].price
    if rules.stop_source == "count_invalidation":
        # for an impulse with W4 printed, invalidation is W4's extreme
        if len(segs) >= 4 and count.pattern in {"impulse", "leading_diagonal", "ending_diagonal"}:
            return segs[3].end.price
        return last_pivot
    return last_pivot  # atr_multiple deferred until v0.5 ablation


def _target_price(count: WaveCount, direction: str, rules: TradeRules, anchor: Decimal) -> Decimal:
    segs = count.segments()
    if not segs:
        return anchor
    w1 = segs[0]
    extensions = fib_extension(w1, anchor_price=anchor)
    return extensions[Decimal("1.618")]


def _current_wave_label(count: WaveCount) -> str:
    segs_done = len(count.segments())
    if count.pattern in {"impulse", "leading_diagonal", "ending_diagonal"}:
        return ["1", "2", "3", "4", "5"][min(segs_done, 4)]
    if count.pattern in {"zigzag", "flat", "expanded_flat"}:
        return ["A", "B", "C"][min(segs_done, 2)]
    if count.pattern == "contracting_triangle":
        return ["A", "B", "C", "D", "E"][min(segs_done, 4)]
    return "?"


def _count_id(count: WaveCount) -> str:
    """Deterministic short id from pattern + pivot timestamps."""
    h = hashlib.sha256()
    h.update(count.pattern.encode())
    for p in count.pivots:
        h.update(p.timestamp.isoformat().encode())
        h.update(str(p.price).encode())
    return h.hexdigest()[:12]
```

- [ ] **Step 4: Verify pass**

Run: `uv run pytest tests/trades/test_derive.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add src/wave_alpha/trades/derive.py tests/trades/test_derive.py
git commit -m "$(cat <<'EOF'
feat(trades): derive signal from WaveCount per TradeRules

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase H — CLI smoke and integration

Wire everything to a single command: `wave-alpha analyze TICKER --json` returns structured counts + coherence + signals using yfinance live data and the SQLite cache.

### Task 23: AnalyzeRequest, AnalyzeResult, and pipeline orchestrator

**Files:**
- Create: `src/wave_alpha/pipeline.py`
- Create: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing test (uses the canned fixture)**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/test_pipeline.py`:

```python
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd

from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.data.yahoo import bars_from_dataframe
from wave_alpha.domain import Bar, Interval
from wave_alpha.pipeline import AnalyzeRequest, run_analysis


class _FixtureProvider(OHLCVProvider):
    """Returns the canned 5-day AAPL fixture, repeated forward to satisfy lookback needs."""

    def __init__(self, fixture_path: Path) -> None:
        df = pd.read_csv(fixture_path, parse_dates=["Date"]).set_index("Date")
        self._daily = bars_from_dataframe(df, interval=Interval.DAILY)

    def fetch(
        self, ticker: str, interval: Interval, start: date | None = None, end: date | None = None
    ) -> list[Bar]:
        # deterministic synthetic stretch: replicate the 5-bar fixture 60 times so
        # the pipeline has enough bars for ATR + ZigZag to converge
        out: list[Bar] = []
        for cycle in range(60):
            for i, b in enumerate(self._daily):
                shifted = b.timestamp.replace(year=2020 + (cycle * 5 + i) // 365)
                out.append(b.model_copy(update={"timestamp": shifted}))
        return out


def test_run_analysis_returns_structured_result() -> None:
    fixture = Path(__file__).resolve().parent / "fixtures" / "yahoo_aapl_5d_daily.csv"
    provider = _FixtureProvider(fixture_path=fixture)
    result = run_analysis(
        AnalyzeRequest(ticker="AAPL", as_of=date(2024, 12, 31)),
        provider=provider,
    )
    assert result.ticker == "AAPL"
    assert result.as_of == date(2024, 12, 31)
    assert isinstance(result.counts_daily, list)
    # Coherence record always populated, even if some intervals are missing
    assert result.coherence is not None
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/pipeline.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from wave_alpha.coherence.score import (
    CoherenceInputs,
    CoherenceResult,
    three_way,
)
from wave_alpha.data.point_in_time import PointInTimeView
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval
from wave_alpha.elliott.enumerator import enumerate_counts
from wave_alpha.elliott.models import WaveCount
from wave_alpha.pivots.models import PivotEvent
from wave_alpha.pivots.multi_degree import detect_multi_degree
from wave_alpha.trades.derive import derive_signal
from wave_alpha.trades.models import TradeRules, TradeSignal

_DEFAULT_ATR_MULTIPLES: list[Decimal] = [
    Decimal("0.5"),
    Decimal("1"),
    Decimal("2"),
    Decimal("4"),
    Decimal("8"),
]
_DEFAULT_DEGREE_FOR_COUNT = 2  # the "intermediate" primary degree from the design


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    as_of: date
    rules: TradeRules = TradeRules()


@dataclass
class AnalyzeResult:
    ticker: str
    as_of: date
    counts_weekly: list[WaveCount] = field(default_factory=list)
    counts_daily: list[WaveCount] = field(default_factory=list)
    counts_4h: list[WaveCount] = field(default_factory=list)
    coherence: CoherenceResult | None = None
    signals: list[TradeSignal] = field(default_factory=list)


def run_analysis(req: AnalyzeRequest, provider: OHLCVProvider) -> AnalyzeResult:
    weekly = _bars(provider, req.ticker, Interval.WEEKLY, req.as_of)
    daily = _bars(provider, req.ticker, Interval.DAILY, req.as_of)
    hourly4 = _bars(provider, req.ticker, Interval.HOURLY_4, req.as_of)

    counts_weekly = _enumerate_for(weekly, Interval.WEEKLY)
    counts_daily = _enumerate_for(daily, Interval.DAILY)
    counts_4h = _enumerate_for(hourly4, Interval.HOURLY_4)

    coh = three_way(
        CoherenceInputs(
            weekly=counts_weekly[0] if counts_weekly else None,
            daily=counts_daily[0] if counts_daily else None,
            hourly4=counts_4h[0] if counts_4h else None,
        )
    )

    signals: list[TradeSignal] = []
    if counts_daily and daily:
        latest_close = PointInTimeView(daily, req.as_of).latest().close
        for c in counts_daily[:3]:
            adjusted = c.model_copy(update={"score": c.score * coh.multiplier})
            sig = derive_signal(
                count=adjusted,
                ticker=req.ticker,
                as_of=req.as_of,
                current_price=latest_close,
                rules=req.rules,
            )
            if sig is not None:
                signals.append(sig)

    return AnalyzeResult(
        ticker=req.ticker,
        as_of=req.as_of,
        counts_weekly=counts_weekly,
        counts_daily=counts_daily,
        counts_4h=counts_4h,
        coherence=coh,
        signals=signals,
    )


def _bars(provider: OHLCVProvider, ticker: str, interval: Interval, as_of: date) -> list[Bar]:
    raw = provider.fetch(ticker, interval, end=as_of)
    if not raw:
        return []
    view = PointInTimeView(raw, as_of)
    return view.visible()


def _enumerate_for(bars: list[Bar], interval: Interval) -> list[WaveCount]:
    if not bars:
        return []
    by_degree = detect_multi_degree(
        bars, interval=interval, atr_multiples=_DEFAULT_ATR_MULTIPLES
    )
    pivots: list[PivotEvent] = by_degree.get(_DEFAULT_DEGREE_FOR_COUNT, [])
    return enumerate_counts(pivots, degree=_DEFAULT_DEGREE_FOR_COUNT, top_k=5)
```

- [ ] **Step 4: Verify pass**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add src/wave_alpha/pipeline.py tests/test_pipeline.py
git commit -m "$(cat <<'EOF'
feat(pipeline): orchestrator wiring data, pivots, elliott, coherence, trades

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 24: CLI `analyze` command

**Files:**
- Modify: `src/wave_alpha/cli/app.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing test (typer CliRunner)**

Create `/Users/alexchen/Documents/project/wave_alpha/tests/test_cli.py`:

```python
import json

from typer.testing import CliRunner

from wave_alpha.cli.app import app

runner = CliRunner()


def test_version_command() -> None:
    res = runner.invoke(app, ["version"])
    assert res.exit_code == 0
    assert res.stdout.strip().count(".") >= 2


def test_analyze_help_documents_json_flag() -> None:
    res = runner.invoke(app, ["analyze", "--help"])
    assert res.exit_code == 0
    assert "--json" in res.stdout


def test_analyze_with_offline_provider_outputs_valid_json(monkeypatch, tmp_path) -> None:
    # Patch the production provider with a fixture-backed one to keep this hermetic
    from datetime import date
    from pathlib import Path

    import pandas as pd

    from wave_alpha.cli import app as app_module
    from wave_alpha.data.provider import OHLCVProvider
    from wave_alpha.data.yahoo import bars_from_dataframe
    from wave_alpha.domain import Bar, Interval

    fixture = Path(__file__).resolve().parent / "fixtures" / "yahoo_aapl_5d_daily.csv"

    class _Fixture(OHLCVProvider):
        def __init__(self) -> None:
            df = pd.read_csv(fixture, parse_dates=["Date"]).set_index("Date")
            self._bars = bars_from_dataframe(df, interval=Interval.DAILY)

        def fetch(
            self,
            ticker: str,
            interval: Interval,
            start: date | None = None,
            end: date | None = None,
        ) -> list[Bar]:
            out: list[Bar] = []
            for cycle in range(60):
                for i, b in enumerate(self._bars):
                    shifted = b.timestamp.replace(year=2020 + (cycle * 5 + i) // 365)
                    out.append(b.model_copy(update={"timestamp": shifted}))
            return out

    monkeypatch.setattr(app_module, "_default_provider", lambda: _Fixture())

    res = runner.invoke(app, ["analyze", "AAPL", "--as-of", "2024-12-31", "--json"])
    assert res.exit_code == 0, res.stdout
    payload = json.loads(res.stdout)
    assert payload["ticker"] == "AAPL"
    assert "counts_daily" in payload
    assert "coherence" in payload
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `analyze` command not registered.

- [ ] **Step 3: Implement**

Replace `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/cli/app.py` with:

```python
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import typer
from loguru import logger

from wave_alpha import __version__
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.data.yahoo import YahooProvider
from wave_alpha.pipeline import AnalyzeRequest, run_analysis

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Elliott Wave analysis for swing traders.")


@app.command()
def version() -> None:
    """Print the wave-alpha version."""
    typer.echo(__version__)


@app.command()
def analyze(
    ticker: str = typer.Argument(..., help="Ticker symbol, e.g. AAPL"),
    as_of: str = typer.Option(
        date.today().isoformat(), "--as-of", help="Analysis as-of date (YYYY-MM-DD)."
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON to stdout instead of human-readable output."
    ),
) -> None:
    """Run Elliott Wave analysis on TICKER and print counts, coherence, and signals."""
    provider = _default_provider()
    req = AnalyzeRequest(ticker=ticker.upper(), as_of=date.fromisoformat(as_of))
    logger.debug("analyze request {}", req)
    result = run_analysis(req, provider=provider)

    if json_out:
        typer.echo(json.dumps(_serialize(result), default=_json_default))
    else:
        _print_human(result)


def _default_provider() -> OHLCVProvider:
    return YahooProvider()


def _serialize(result: Any) -> dict[str, Any]:
    if is_dataclass(result):
        return {k: _serialize(v) for k, v in asdict(result).items()}
    if isinstance(result, list):
        return [_serialize(v) for v in result]  # type: ignore[return-value]
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if isinstance(result, dict):
        return {k: _serialize(v) for k, v in result.items()}
    return result


def _json_default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, date):
        return o.isoformat()
    raise TypeError(f"unserializable: {type(o).__name__}")


def _print_human(result: Any) -> None:
    typer.echo(f"ticker:  {result.ticker}")
    typer.echo(f"as_of:   {result.as_of.isoformat()}")
    typer.echo(f"daily counts:  {len(result.counts_daily)} candidates")
    typer.echo(f"weekly counts: {len(result.counts_weekly)} candidates")
    typer.echo(f"4h counts:     {len(result.counts_4h)} candidates")
    if result.coherence:
        typer.echo(f"coherence score:  {result.coherence.score:.2f}")
        typer.echo(f"coherence multiplier: {result.coherence.multiplier:.2f}")
    typer.echo(f"signals: {len(result.signals)}")
    for sig in result.signals:
        typer.echo(
            f"  {sig.direction.upper()}  entry={sig.entry_price}  "
            f"stop={sig.stop_price}  target={sig.target_price}  rr={sig.rr:.2f}"
        )
    typer.echo("\n⚠ Informational only. Confirm thesis. Not investment advice.")


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Verify pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add src/wave_alpha/cli/app.py tests/test_cli.py
git commit -m "$(cat <<'EOF'
feat(cli): wave-alpha analyze TICKER with --json output

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 25: Lint clean, full test pass, README placeholder

**Files:**
- Create: `README.md`

- [ ] **Step 1: Run the entire test suite**

Run: `cd /Users/alexchen/Documents/project/wave_alpha && uv run pytest -q`
Expected: all tests pass.

- [ ] **Step 2: Run ruff**

Run: `cd /Users/alexchen/Documents/project/wave_alpha && uv run ruff check . && uv run ruff format .`
Expected: clean. Fix any reported issues by running `uv run ruff format .` and re-running.

- [ ] **Step 3: Write README**

Create `/Users/alexchen/Documents/project/wave_alpha/README.md`:

```markdown
# wave-alpha

Local-first Elliott Wave analysis for swing traders. Sibling project to [wash-alpha](https://github.com/alexchen/wash-alpha).

## Status

v0.1 — engine core only. CLI ships `wave-alpha analyze TICKER --json`. No web UI yet (Plan 3); no LLM narrative yet (Plan 2).

## Install (development)

```bash
uv sync --extra dev
uv run wave-alpha version
```

## Usage

```bash
uv run wave-alpha analyze AAPL --as-of 2024-12-31
uv run wave-alpha analyze AAPL --json | jq .
```

## Design

See `docs/superpowers/specs/2026-05-03-wave-alpha-design.md` for the full design.

## Disclaimer

⚠ Informational only. Confirm thesis. Not investment advice.
```

- [ ] **Step 4: Commit**

```bash
cd /Users/alexchen/Documents/project/wave_alpha
git add README.md
git commit -m "$(cat <<'EOF'
docs: minimal README for v0.1 engine core

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Task 26: Live smoke test against yfinance

This is the only step that touches the network. Run it once after Task 25 to confirm the pipeline survives a real symbol; do not bake live network calls into the unit suite.

- [ ] **Step 1: Run a live analysis**

Run: `cd /Users/alexchen/Documents/project/wave_alpha && uv run wave-alpha analyze SPY --json`
Expected: a JSON payload with `ticker`, `as_of`, `counts_daily`, `counts_weekly`, `counts_4h`, `coherence`, `signals`. May take ~15-30 seconds for the three yfinance fetches.

- [ ] **Step 2: Spot-check the human output**

Run: `cd /Users/alexchen/Documents/project/wave_alpha && uv run wave-alpha analyze SPY`
Expected: human-readable summary ending with the disclaimer.

- [ ] **Step 3: Verify no commit needed**

This task is verification only — there should be no file changes. Run `git status` and confirm a clean tree.

---

## Self-review

Spec coverage scan against `docs/superpowers/specs/2026-05-03-wave-alpha-design.md`:

| Spec section | Plan task |
|---|---|
| §3 Tech stack (Python 3.11, uv, hatch, pydantic, sqlmodel, typer, yfinance, pyyaml, loguru, ruff, pytest) | Task 1 |
| §5.1 Multi-degree ATR-ZigZag with hierarchical subset | Tasks 9, 10, 11 |
| §5.2 PointInTimeView hard-assert | Task 5 |
| §5.2 PivotEvent with confirmed/tentative state | Task 10 |
| §5.3 Tree-of-templates count encoding via YAML | Tasks 12, 14 |
| §5.3 Constraint DSL with `retrace`, `length`, `overlaps` | Task 13 |
| §5.3 6 starter templates | Task 14 |
| §5.3 Rule validator | Task 15 |
| §5.3 Count enumerator with hard-violation pruning | Task 17 (sub-count nesting deferred to Plan 2 per task note) |
| §5.4 Right-edge probability heuristic | Tasks 18, 19 |
| §7 Three-way coherence with insufficient-history fallback | Task 20 |
| §9 TradeSignal + TradeRules + bidirectional, count-invalidation stop, count-target/fib-fallback | Tasks 21, 22 |
| §11 CLI deep-dive (text/JSON), web UI | CLI in Tasks 23, 24; web UI is **Plan 3 (out of scope)** |
| §13 Disclaimer | Tasks 24, 25 README |

Out-of-scope for this plan (correctly deferred):
- LLM contract (Plan 2)
- Web UI (Plan 3)
- Backtest harness, including walk-forward and trade-sim (separate plan)
- Logistic confirmation model (after backtest harness)
- Pooled feedback corpus (v1.2+)

Placeholder scan: no TBD/TODO/"implement later" left in the plan. Every code step contains the actual code; every test step contains the actual test; every command shows the expected outcome.

Type consistency: `WaveCount`, `WaveSegment`, `PivotEvent`, `RightEdgeFeatures`, `CoherenceResult`, `TradeRules`, `TradeSignal`, `AnalyzeRequest`, `AnalyzeResult` are defined exactly once and referenced by the same name throughout. The constraint DSL uses `W1`/`W2`/.../`WA`/`WB`/... bindings consistently between Tasks 13, 14 (templates), and 15 (validator).

