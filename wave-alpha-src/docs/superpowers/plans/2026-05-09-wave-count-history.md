# Wave-Count History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist a per-symbol daily snapshot of the engine's top-3 ranked counts + signal + coherence to `~/.wave_alpha/history.sqlite`, and surface the recent timeline as a stability-badged strip on the `/deepdive/<ticker>` page.

**Architecture:** New `src/wave_alpha/history/` package — pure CRUD store, façade service, identity hash. Hooked from exactly two call sites: `web/routes/deepdive.py:counts_fragment` and `cli/app.py:analyze`. Pipeline untouched. SQLite at `~/.wave_alpha/history.sqlite`. Stability computed by hashing `(pattern, degree, pivot_sequence)` of the top-1 count.

**Tech Stack:** Python 3.11+, Pydantic v2 (frozen models), SQLite (stdlib `sqlite3`), FastAPI + Jinja2, htmx. Test stack: pytest + tmp_path.

**Spec:** `docs/superpowers/specs/2026-05-09-wave-count-history-design.md`

**Spec corrections discovered while writing this plan** (the plan's code overrides the spec on these):
- `TradeSignal` fields are `entry_price/stop_price/target_price` (not `entry/stop/target`). `SnapshotSignal` mirrors them.
- `CoherenceResult` has `score/multiplier/pairwise/fallback_used` — no per-timeframe labels. `SnapshotCoherence` mirrors that real shape.
- `db_path` is passed explicitly to service functions (rather than read from a module-level indirection) so both web and CLI can supply their own `_data_dir()` and tests can pass `tmp_path`.

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `src/wave_alpha/history/__init__.py` | Create | Package marker; re-exports `service` API |
| `src/wave_alpha/history/identity.py` | Create | `top_count_hash(WaveCount) -> str` |
| `src/wave_alpha/history/models.py` | Create | `Snapshot`, `SnapshotCount`, `SnapshotPivot`, `SnapshotSignal`, `SnapshotCoherence`, `StabilityBadge`, `RecentHistory` |
| `src/wave_alpha/history/store.py` | Create | SQLite CRUD: `upsert(snapshot, *, db_path)`, `recent(ticker, n, *, db_path)` |
| `src/wave_alpha/history/service.py` | Create | `record_snapshot`, `get_recent_with_badge`, `_build_snapshot`, `_compute_badge` |
| `src/wave_alpha/web/templates/_history.html` | Create | Jinja2 partial: stability badge + timeline rows |
| `src/wave_alpha/web/routes/deepdive.py` | Modify | Add `history_fragment` route; hook `record_snapshot` in `counts_fragment` |
| `src/wave_alpha/web/templates/deepdive.html` | Modify | Append `<section class="card">` for history (lazy-load) |
| `src/wave_alpha/web/static/app.css` | Modify | `.history-timeline`, `.history-row`, `.history-row.current`, `.stability-badge` |
| `src/wave_alpha/cli/app.py` | Modify | Hook `record_snapshot` after `run_analysis(...)` returns in `analyze` command |
| `tests/history/__init__.py` | Create | Test package marker |
| `tests/history/test_identity.py` | Create | Hash determinism + sensitivity tests |
| `tests/history/test_models.py` | Create | Round-trip via `model_dump_json` / `model_validate_json` |
| `tests/history/test_store.py` | Create | Upsert + recent against tmp_path SQLite |
| `tests/history/test_service.py` | Create | `record_snapshot` skip-empty / error-swallow / happy path |
| `tests/history/test_stability_badge.py` | Create | Table-driven `_compute_badge` cases |
| `tests/history/test_get_recent_with_badge.py` | Create | End-to-end read API over fixture DB |
| `tests/web/test_routes_deepdive_history.py` | Create | Integration test for `/deepdive/<ticker>/history` route + write hook |
| `tests/cli/test_analyze_history_hook.py` | Create | CLI `analyze` writes a snapshot |

---

## Task 1: Identity hash module

**Files:**
- Create: `src/wave_alpha/history/__init__.py`
- Create: `src/wave_alpha/history/identity.py`
- Create: `tests/history/__init__.py`
- Create: `tests/history/test_identity.py`

- [ ] **Step 1: Create empty package markers**

```bash
mkdir -p src/wave_alpha/history tests/history
touch src/wave_alpha/history/__init__.py tests/history/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `tests/history/test_identity.py`:

```python
from datetime import date
from decimal import Decimal

from wave_alpha.elliott.models import WaveCount
from wave_alpha.history.identity import top_count_hash
from wave_alpha.pivots.models import PivotEvent


def _pivot(d: str, p: str, degree: int = 0) -> PivotEvent:
    return PivotEvent(degree=degree, timestamp=date.fromisoformat(d), price=Decimal(p))


def _wc(pattern: str = "impulse", *, pivots: list[PivotEvent], degree: int = 1) -> WaveCount:
    return WaveCount(pattern=pattern, degree=degree, pivots=pivots, score=0.5)


def test_top_count_hash_is_deterministic() -> None:
    pivots = [_pivot("2024-01-01", "100"), _pivot("2024-02-01", "110")]
    h1 = top_count_hash(_wc(pivots=pivots))
    h2 = top_count_hash(_wc(pivots=pivots))
    assert h1 == h2
    assert len(h1) == 16  # 16-char hex prefix


def test_top_count_hash_changes_when_pivot_price_changes() -> None:
    base = [_pivot("2024-01-01", "100"), _pivot("2024-02-01", "110")]
    perturbed = [_pivot("2024-01-01", "100"), _pivot("2024-02-01", "111")]
    assert top_count_hash(_wc(pivots=base)) != top_count_hash(_wc(pivots=perturbed))


def test_top_count_hash_changes_when_pattern_changes() -> None:
    pivots = [_pivot("2024-01-01", "100"), _pivot("2024-02-01", "110")]
    assert top_count_hash(_wc("impulse", pivots=pivots)) != top_count_hash(
        _wc("zigzag", pivots=pivots)
    )


def test_top_count_hash_changes_when_pivot_timestamp_changes() -> None:
    base = [_pivot("2024-01-01", "100"), _pivot("2024-02-01", "110")]
    perturbed = [_pivot("2024-01-01", "100"), _pivot("2024-02-02", "110")]
    assert top_count_hash(_wc(pivots=base)) != top_count_hash(_wc(pivots=perturbed))


def test_top_count_hash_changes_when_degree_changes() -> None:
    pivots = [_pivot("2024-01-01", "100"), _pivot("2024-02-01", "110")]
    assert top_count_hash(_wc(pivots=pivots, degree=1)) != top_count_hash(
        _wc(pivots=pivots, degree=2)
    )
```

- [ ] **Step 3: Run test to verify it fails**

```bash
uv run pytest tests/history/test_identity.py -v
```

Expected: ImportError (`No module named 'wave_alpha.history.identity'`).

- [ ] **Step 4: Implement `identity.py`**

Create `src/wave_alpha/history/identity.py`:

```python
"""Stable identity hash for a WaveCount used by the history-stability badge.

The hash captures (pattern, degree, ordered pivot sequence). Any drift in
pivots — including a single bar shifting an extreme — yields a different
hash, so two snapshots with the same hash represent genuinely the same
count, not just the same template/label.
"""

from __future__ import annotations

import hashlib

from wave_alpha.elliott.models import WaveCount


def top_count_hash(c: WaveCount) -> str:
    key = (
        c.pattern,
        c.degree,
        tuple((p.timestamp.isoformat(), str(p.price)) for p in c.pivots),
    )
    return hashlib.sha256(repr(key).encode()).hexdigest()[:16]
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/history/test_identity.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Lint**

```bash
uv run ruff check src/wave_alpha/history/ tests/history/
uv run ruff format src/wave_alpha/history/ tests/history/
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/wave_alpha/history/__init__.py src/wave_alpha/history/identity.py \
        tests/history/__init__.py tests/history/test_identity.py
git commit -m "feat(history): top_count_hash for stability identity"
```

---

## Task 2: Snapshot data models

**Files:**
- Create: `src/wave_alpha/history/models.py`
- Create: `tests/history/test_models.py`

- [ ] **Step 1: Write the failing test**

Create `tests/history/test_models.py`:

```python
from datetime import date, datetime
from decimal import Decimal

from wave_alpha.history.models import (
    RecentHistory,
    Snapshot,
    SnapshotCoherence,
    SnapshotCount,
    SnapshotPivot,
    SnapshotSignal,
    StabilityBadge,
)


def _make_snapshot() -> Snapshot:
    return Snapshot(
        ticker="AAPL",
        as_of=date(2024, 12, 31),
        engine_version="0.5.11",
        top_3=[
            SnapshotCount(
                candidate_id="C1",
                pattern="impulse",
                degree=1,
                last_pivot_label="3",
                pivots=[
                    SnapshotPivot(timestamp=date(2024, 1, 1), price=Decimal("100")),
                    SnapshotPivot(timestamp=date(2024, 6, 1), price=Decimal("130")),
                ],
                engine_score=0.72,
                llm_prob=None,
                final_score=0.72,
            )
        ],
        signal=SnapshotSignal(
            direction="long",
            entry_price=Decimal("130.50"),
            stop_price=Decimal("125.00"),
            target_price=Decimal("145.00"),
            rr=2.6,
        ),
        coherence=SnapshotCoherence(
            score=0.81,
            multiplier=1.05,
            pairwise={"weekly_daily": 0.9},
            fallback_used=None,
        ),
        top_count_hash="a1b2c3d4e5f60718",
        created_at=datetime(2024, 12, 31, 16, 30, 0),
    )


def test_snapshot_round_trips_via_json() -> None:
    snap = _make_snapshot()
    raw = snap.model_dump_json()
    restored = Snapshot.model_validate_json(raw)
    assert restored == snap


def test_snapshot_extra_fields_in_json_are_ignored() -> None:
    """Forward-compat: payloads written by a future engine with extra
    fields must still load against the current model."""
    snap = _make_snapshot()
    raw = snap.model_dump_json()
    # inject a new top-level field not in the schema
    raw_obj = raw.replace('"ticker":"AAPL"', '"ticker":"AAPL","new_v2_field":42')
    restored = Snapshot.model_validate_json(raw_obj)
    assert restored.ticker == "AAPL"


def test_snapshot_is_frozen() -> None:
    snap = _make_snapshot()
    try:
        snap.ticker = "MSFT"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("expected Snapshot to be frozen (immutable)")


def test_stability_badge_and_recent_history_are_dataclasses() -> None:
    badge = StabilityBadge(streak=5, matches_in_window=9, window=14, last_changed_on=None)
    assert badge.streak == 5
    rh = RecentHistory(snapshots=[_make_snapshot()], badge=badge)
    assert len(rh.snapshots) == 1
    assert rh.badge is badge
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/history/test_models.py -v
```

Expected: ImportError (`No module named 'wave_alpha.history.models'`).

- [ ] **Step 3: Implement `models.py`**

Create `src/wave_alpha/history/models.py`:

```python
"""Pydantic models and frozen dataclasses for history snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class SnapshotPivot(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp: date
    price: Decimal


class SnapshotCount(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: str
    pattern: str
    degree: int
    last_pivot_label: str
    pivots: list[SnapshotPivot]
    engine_score: float
    llm_prob: float | None
    final_score: float


class SnapshotSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    direction: Literal["long", "short"]
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    rr: float


class SnapshotCoherence(BaseModel):
    model_config = ConfigDict(frozen=True)

    score: float
    multiplier: float
    pairwise: dict[str, float]
    fallback_used: str | None


class Snapshot(BaseModel):
    """A single (ticker, as_of) record. Pydantic ignores unknown fields by
    default, which is the forward-compat hook for v2 additive fields."""

    model_config = ConfigDict(frozen=True)

    ticker: str
    as_of: date
    engine_version: str
    top_3: list[SnapshotCount]
    signal: SnapshotSignal | None
    coherence: SnapshotCoherence | None
    top_count_hash: str
    created_at: datetime


@dataclass(frozen=True)
class StabilityBadge:
    streak: int
    matches_in_window: int
    window: int
    last_changed_on: date | None


@dataclass(frozen=True)
class RecentHistory:
    snapshots: list[Snapshot]
    badge: StabilityBadge | None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/history/test_models.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Lint**

```bash
uv run ruff check src/wave_alpha/history/models.py tests/history/test_models.py
uv run ruff format src/wave_alpha/history/models.py tests/history/test_models.py
```

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/history/models.py tests/history/test_models.py
git commit -m "feat(history): Snapshot, StabilityBadge, RecentHistory models"
```

---

## Task 3: SQLite store — upsert and recent

**Files:**
- Create: `src/wave_alpha/history/store.py`
- Create: `tests/history/test_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/history/test_store.py`:

```python
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from wave_alpha.history import store
from wave_alpha.history.models import (
    Snapshot,
    SnapshotCoherence,
    SnapshotCount,
    SnapshotPivot,
    SnapshotSignal,
)


def _snap(
    ticker: str = "AAPL",
    as_of: date = date(2024, 12, 31),
    *,
    top_hash: str = "h-default",
    created_at: datetime | None = None,
    engine_score: float = 0.7,
) -> Snapshot:
    return Snapshot(
        ticker=ticker,
        as_of=as_of,
        engine_version="0.5.11",
        top_3=[
            SnapshotCount(
                candidate_id="C1",
                pattern="impulse",
                degree=1,
                last_pivot_label="3",
                pivots=[SnapshotPivot(timestamp=as_of, price=Decimal("100"))],
                engine_score=engine_score,
                llm_prob=None,
                final_score=engine_score,
            )
        ],
        signal=SnapshotSignal(
            direction="long",
            entry_price=Decimal("100"),
            stop_price=Decimal("95"),
            target_price=Decimal("115"),
            rr=3.0,
        ),
        coherence=SnapshotCoherence(score=0.7, multiplier=1.0, pairwise={}, fallback_used=None),
        top_count_hash=top_hash,
        created_at=created_at or datetime(2024, 12, 31, 16, 0, 0),
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "history.sqlite"


def test_upsert_then_recent_round_trips(db_path: Path) -> None:
    snap = _snap()
    store.upsert(snap, db_path=db_path)
    rows = store.recent("AAPL", 10, db_path=db_path)
    assert len(rows) == 1
    assert rows[0].ticker == "AAPL"
    assert rows[0].as_of == date(2024, 12, 31)
    assert rows[0].top_count_hash == "h-default"
    # payload round-trip via Snapshot.model_validate_json works
    restored = Snapshot.model_validate_json(rows[0].payload_json)
    assert restored == snap


def test_upsert_overwrites_same_key(db_path: Path) -> None:
    first = _snap(engine_score=0.6, created_at=datetime(2024, 12, 31, 12, 0, 0))
    second = _snap(engine_score=0.9, created_at=datetime(2024, 12, 31, 18, 0, 0))
    store.upsert(first, db_path=db_path)
    store.upsert(second, db_path=db_path)
    rows = store.recent("AAPL", 10, db_path=db_path)
    assert len(rows) == 1
    restored = Snapshot.model_validate_json(rows[0].payload_json)
    assert restored.top_3[0].final_score == pytest.approx(0.9)


def test_recent_returns_newest_first(db_path: Path) -> None:
    store.upsert(_snap(as_of=date(2024, 12, 1), top_hash="h-old"), db_path=db_path)
    store.upsert(_snap(as_of=date(2024, 12, 31), top_hash="h-new"), db_path=db_path)
    store.upsert(_snap(as_of=date(2024, 12, 15), top_hash="h-mid"), db_path=db_path)
    rows = store.recent("AAPL", 10, db_path=db_path)
    assert [r.as_of for r in rows] == [date(2024, 12, 31), date(2024, 12, 15), date(2024, 12, 1)]


def test_recent_filters_by_ticker(db_path: Path) -> None:
    store.upsert(_snap(ticker="AAPL"), db_path=db_path)
    store.upsert(_snap(ticker="MSFT"), db_path=db_path)
    rows = store.recent("AAPL", 10, db_path=db_path)
    assert len(rows) == 1
    assert rows[0].ticker == "AAPL"


def test_recent_respects_limit(db_path: Path) -> None:
    for i in range(1, 8):
        store.upsert(_snap(as_of=date(2024, 12, i), top_hash=f"h-{i}"), db_path=db_path)
    rows = store.recent("AAPL", 3, db_path=db_path)
    assert len(rows) == 3
    assert [r.as_of for r in rows] == [date(2024, 12, 7), date(2024, 12, 6), date(2024, 12, 5)]


def test_recent_empty_for_unknown_ticker(db_path: Path) -> None:
    store.upsert(_snap(ticker="AAPL"), db_path=db_path)
    rows = store.recent("NVDA", 10, db_path=db_path)
    assert rows == []


def test_upsert_creates_parent_dir(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "more" / "history.sqlite"
    store.upsert(_snap(), db_path=nested)
    assert nested.exists()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/history/test_store.py -v
```

Expected: ImportError (`No module named 'wave_alpha.history.store'`).

- [ ] **Step 3: Implement `store.py`**

Create `src/wave_alpha/history/store.py`:

```python
"""SQLite persistence for history snapshots. Pure CRUD; no pipeline knowledge."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from wave_alpha.history.models import Snapshot

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    ticker          TEXT NOT NULL,
    as_of           TEXT NOT NULL,
    engine_version  TEXT NOT NULL,
    top_count_hash  TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (ticker, as_of)
);
"""

_INDEX = """
CREATE INDEX IF NOT EXISTS idx_snapshots_ticker_as_of
    ON snapshots(ticker, as_of DESC);
"""


@dataclass(frozen=True)
class StoredRow:
    ticker: str
    as_of: date
    engine_version: str
    top_count_hash: str
    payload_json: str
    created_at: datetime


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(_SCHEMA)
    conn.execute(_INDEX)
    return conn


def upsert(snapshot: Snapshot, *, db_path: Path) -> None:
    payload = snapshot.model_dump_json()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO snapshots
                (ticker, as_of, engine_version, top_count_hash, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, as_of) DO UPDATE SET
                engine_version = excluded.engine_version,
                top_count_hash = excluded.top_count_hash,
                payload_json   = excluded.payload_json,
                created_at     = excluded.created_at
            """,
            (
                snapshot.ticker,
                snapshot.as_of.isoformat(),
                snapshot.engine_version,
                snapshot.top_count_hash,
                payload,
                snapshot.created_at.isoformat(),
            ),
        )
        conn.commit()


def recent(ticker: str, n: int, *, db_path: Path) -> list[StoredRow]:
    if not db_path.exists():
        return []
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT ticker, as_of, engine_version, top_count_hash, payload_json, created_at
            FROM snapshots
            WHERE ticker = ?
            ORDER BY as_of DESC
            LIMIT ?
            """,
            (ticker, n),
        )
        rows = cursor.fetchall()
    return [
        StoredRow(
            ticker=r[0],
            as_of=date.fromisoformat(r[1]),
            engine_version=r[2],
            top_count_hash=r[3],
            payload_json=r[4],
            created_at=datetime.fromisoformat(r[5]),
        )
        for r in rows
    ]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/history/test_store.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Lint**

```bash
uv run ruff check src/wave_alpha/history/store.py tests/history/test_store.py
uv run ruff format src/wave_alpha/history/store.py tests/history/test_store.py
```

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/history/store.py tests/history/test_store.py
git commit -m "feat(history): SQLite store with upsert and recent"
```

---

## Task 4: Service — `_build_snapshot` and `record_snapshot`

**Files:**
- Create: `src/wave_alpha/history/service.py`
- Create: `tests/history/test_service.py`

- [ ] **Step 1: Write the failing test**

Create `tests/history/test_service.py`:

```python
import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from wave_alpha.coherence.score import CoherenceResult
from wave_alpha.elliott.models import WaveCount
from wave_alpha.history import service, store
from wave_alpha.history.models import Snapshot
from wave_alpha.pipeline import AnalyzeResult, RankedCount
from wave_alpha.pivots.models import PivotEvent
from wave_alpha.trades.models import TradeSignal


def _pivots() -> list[PivotEvent]:
    return [
        PivotEvent(degree=1, timestamp=date(2024, 1, 1), price=Decimal("100")),
        PivotEvent(degree=1, timestamp=date(2024, 2, 1), price=Decimal("110")),
        PivotEvent(degree=1, timestamp=date(2024, 3, 1), price=Decimal("105")),
        PivotEvent(degree=1, timestamp=date(2024, 6, 1), price=Decimal("130")),
    ]


def _wc(pattern: str = "impulse", score: float = 0.7) -> WaveCount:
    return WaveCount(pattern=pattern, degree=1, pivots=_pivots(), score=score)


def _ranked(score: float = 0.7) -> RankedCount:
    return RankedCount(
        candidate_id="C1",
        count=_wc(score=score),
        engine_score=score,
        llm_prob=None,
        final_score=score,
    )


def _signal() -> TradeSignal:
    return TradeSignal(
        ticker="AAPL",
        as_of=date(2024, 6, 1),
        direction="long",
        entry_price=Decimal("130.00"),
        stop_price=Decimal("125.00"),
        target_price=Decimal("145.00"),
        count_id="C1",
        count_pattern="impulse",
        wave_label="3",
        degree=1,
        confidence=0.7,
        expected_bars_to_target=20,
        max_holding_bars=60,
    )


def _coh(score: float = 0.8) -> CoherenceResult:
    return CoherenceResult(
        score=score, multiplier=1.05, pairwise={"weekly_daily": score}, fallback_used=None
    )


def _result(*, ranked: list[RankedCount] | None = None) -> AnalyzeResult:
    if ranked is None:
        ranked = [_ranked()]
    return AnalyzeResult(
        ticker="AAPL",
        as_of=date(2024, 6, 1),
        ranked_daily=ranked,
        signals=[_signal()] if ranked else [],
        coherence=_coh() if ranked else None,
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "history.sqlite"


def test_record_snapshot_writes_a_row(db_path: Path) -> None:
    service.record_snapshot(_result(), "AAPL", date(2024, 6, 1), db_path=db_path)
    rows = store.recent("AAPL", 10, db_path=db_path)
    assert len(rows) == 1
    snap = Snapshot.model_validate_json(rows[0].payload_json)
    assert snap.ticker == "AAPL"
    assert snap.as_of == date(2024, 6, 1)
    assert snap.top_3[0].pattern == "impulse"
    assert snap.signal is not None
    assert snap.signal.direction == "long"
    assert snap.coherence is not None
    assert snap.coherence.score == pytest.approx(0.8)
    assert len(snap.top_count_hash) == 16


def test_record_snapshot_skips_when_ranked_daily_empty(db_path: Path) -> None:
    empty = AnalyzeResult(ticker="AAPL", as_of=date(2024, 6, 1))
    service.record_snapshot(empty, "AAPL", date(2024, 6, 1), db_path=db_path)
    assert store.recent("AAPL", 10, db_path=db_path) == []


def test_record_snapshot_swallows_store_errors(
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(service.store, "upsert", boom)
    with caplog.at_level(logging.WARNING, logger="wave_alpha.history.service"):
        service.record_snapshot(_result(), "AAPL", date(2024, 6, 1), db_path=db_path)
    assert any("history snapshot failed" in rec.message for rec in caplog.records)


def test_record_snapshot_caps_top_3_at_three(db_path: Path) -> None:
    five = [_ranked(score=0.9 - 0.1 * i) for i in range(5)]
    service.record_snapshot(_result(ranked=five), "AAPL", date(2024, 6, 1), db_path=db_path)
    rows = store.recent("AAPL", 10, db_path=db_path)
    snap = Snapshot.model_validate_json(rows[0].payload_json)
    assert len(snap.top_3) == 3


def test_record_snapshot_idempotent_on_repeat(db_path: Path) -> None:
    service.record_snapshot(_result(), "AAPL", date(2024, 6, 1), db_path=db_path)
    service.record_snapshot(_result(), "AAPL", date(2024, 6, 1), db_path=db_path)
    rows = store.recent("AAPL", 10, db_path=db_path)
    assert len(rows) == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/history/test_service.py -v
```

Expected: ImportError (`No module named 'wave_alpha.history.service'`).

- [ ] **Step 3: Implement `service.py` (subset — just `_build_snapshot` and `record_snapshot`)**

Create `src/wave_alpha/history/service.py`:

```python
"""Façade for the history package. Only thing call sites should import."""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from wave_alpha import __version__
from wave_alpha.history import store
from wave_alpha.history.identity import top_count_hash
from wave_alpha.history.models import (
    Snapshot,
    SnapshotCoherence,
    SnapshotCount,
    SnapshotPivot,
    SnapshotSignal,
)

if TYPE_CHECKING:
    from wave_alpha.coherence.score import CoherenceResult
    from wave_alpha.elliott.models import WaveCount
    from wave_alpha.pipeline import AnalyzeResult, RankedCount
    from wave_alpha.trades.models import TradeSignal

logger = logging.getLogger(__name__)

# Module-level alias for monkeypatch in tests (e.g. patch service.store.upsert).
__all__ = ["record_snapshot", "store"]


_PATTERN_LABELS: dict[str, list[str]] = {
    "impulse": ["1", "2", "3", "4", "5"],
    "leading_diagonal": ["1", "2", "3", "4", "5"],
    "ending_diagonal": ["1", "2", "3", "4", "5"],
    "zigzag": ["A", "B", "C"],
    "flat": ["A", "B", "C"],
    "expanded_flat": ["A", "B", "C"],
    "contracting_triangle": ["A", "B", "C", "D", "E"],
}


def _last_pivot_label(count: WaveCount) -> str:
    labels = _PATTERN_LABELS.get(count.pattern, [])
    if not labels or not count.pivots:
        return ""
    # pivots include the starting pivot before label "1"; segments = pivots-1
    # so the last completed wave label is at index len(pivots)-2 (clamped).
    idx = max(0, min(len(labels) - 1, len(count.pivots) - 2))
    return labels[idx]


def _to_snapshot_count(rc: RankedCount) -> SnapshotCount:
    return SnapshotCount(
        candidate_id=rc.candidate_id,
        pattern=rc.count.pattern,
        degree=rc.count.degree,
        last_pivot_label=_last_pivot_label(rc.count),
        pivots=[SnapshotPivot(timestamp=p.timestamp, price=p.price) for p in rc.count.pivots],
        engine_score=rc.engine_score,
        llm_prob=rc.llm_prob,
        final_score=rc.final_score,
    )


def _to_snapshot_signal(sig: TradeSignal | None) -> SnapshotSignal | None:
    if sig is None:
        return None
    return SnapshotSignal(
        direction=sig.direction,
        entry_price=sig.entry_price,
        stop_price=sig.stop_price,
        target_price=sig.target_price,
        rr=sig.rr,
    )


def _to_snapshot_coherence(coh: CoherenceResult | None) -> SnapshotCoherence | None:
    if coh is None:
        return None
    return SnapshotCoherence(
        score=coh.score,
        multiplier=coh.multiplier,
        pairwise=dict(coh.pairwise),
        fallback_used=coh.fallback_used,
    )


def _build_snapshot(result: AnalyzeResult, ticker: str, as_of: date) -> Snapshot:
    top_3 = [_to_snapshot_count(rc) for rc in result.ranked_daily[:3]]
    signal = _to_snapshot_signal(result.signals[0] if result.signals else None)
    coherence = _to_snapshot_coherence(result.coherence)
    return Snapshot(
        ticker=ticker,
        as_of=as_of,
        engine_version=__version__,
        top_3=top_3,
        signal=signal,
        coherence=coherence,
        top_count_hash=top_count_hash(result.ranked_daily[0].count),
        created_at=datetime.now(),
    )


def record_snapshot(
    result: AnalyzeResult,
    ticker: str,
    as_of: date,
    *,
    db_path: Path,
) -> None:
    """Persist `result` as a snapshot. Side-effect only — never raises."""
    if not result.ranked_daily:
        return
    try:
        snapshot = _build_snapshot(result, ticker, as_of)
        store.upsert(snapshot, db_path=db_path)
    except Exception as exc:
        logger.warning("history snapshot failed for %s @ %s: %s", ticker, as_of, exc)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/history/test_service.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Lint**

```bash
uv run ruff check src/wave_alpha/history/service.py tests/history/test_service.py
uv run ruff format src/wave_alpha/history/service.py tests/history/test_service.py
```

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/history/service.py tests/history/test_service.py
git commit -m "feat(history): record_snapshot service (write path)"
```

---

## Task 5: Stability badge — `_compute_badge`

**Files:**
- Modify: `src/wave_alpha/history/service.py`
- Create: `tests/history/test_stability_badge.py`

- [ ] **Step 1: Write the failing test**

Create `tests/history/test_stability_badge.py`:

```python
from datetime import date

import pytest

from wave_alpha.history.service import _compute_badge
from wave_alpha.history.store import StoredRow


def _row(as_of_iso: str, top_hash: str) -> StoredRow:
    return StoredRow(
        ticker="AAPL",
        as_of=date.fromisoformat(as_of_iso),
        engine_version="0.5.11",
        top_count_hash=top_hash,
        payload_json="{}",
        created_at=date.fromisoformat(as_of_iso),  # type: ignore[arg-type]
    )


def test_single_row_returns_none() -> None:
    rows = [_row("2024-12-31", "h-A")]
    assert _compute_badge(rows, window=14) is None


def test_empty_rows_returns_none() -> None:
    assert _compute_badge([], window=14) is None


def test_all_same_hash() -> None:
    rows = [_row(f"2024-12-{i:02d}", "h-A") for i in range(5, 0, -1)]
    badge = _compute_badge(rows, window=14)
    assert badge is not None
    assert badge.streak == 5
    assert badge.matches_in_window == 5
    assert badge.window == 5
    assert badge.last_changed_on is None


def test_streak_breaks_at_first_difference() -> None:
    rows = [
        _row("2024-12-05", "h-A"),
        _row("2024-12-04", "h-A"),
        _row("2024-12-03", "h-A"),
        _row("2024-12-02", "h-B"),  # break
        _row("2024-12-01", "h-A"),
    ]
    badge = _compute_badge(rows, window=14)
    assert badge is not None
    assert badge.streak == 3
    assert badge.matches_in_window == 4  # h-A appears 4 times overall
    assert badge.last_changed_on == date(2024, 12, 2)


def test_flip_flop_sequence() -> None:
    rows = [
        _row("2024-12-05", "h-A"),
        _row("2024-12-04", "h-B"),
        _row("2024-12-03", "h-A"),
        _row("2024-12-02", "h-B"),
        _row("2024-12-01", "h-A"),
    ]
    badge = _compute_badge(rows, window=14)
    assert badge is not None
    assert badge.streak == 1
    assert badge.matches_in_window == 3
    assert badge.last_changed_on == date(2024, 12, 4)


def test_window_reflects_actual_rows_not_requested() -> None:
    rows = [_row("2024-12-05", "h-A"), _row("2024-12-04", "h-A")]
    badge = _compute_badge(rows, window=14)
    assert badge is not None
    assert badge.window == 2  # only 2 rows actually present
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/history/test_stability_badge.py -v
```

Expected: ImportError on `_compute_badge`.

- [ ] **Step 3: Add `_compute_badge` to `service.py`**

Append to `src/wave_alpha/history/service.py` (above `record_snapshot`, after the `_to_*` helpers):

```python
from wave_alpha.history.models import StabilityBadge  # noqa: E402  (grouped with other model imports)
from wave_alpha.history.store import StoredRow  # noqa: E402


def _compute_badge(rows: list[StoredRow], window: int) -> StabilityBadge | None:
    if len(rows) < 2:
        return None
    current = rows[0].top_count_hash
    streak = 1
    last_changed: date | None = None
    for r in rows[1:]:
        if r.top_count_hash == current:
            streak += 1
        else:
            last_changed = r.as_of
            break
    matches = sum(1 for r in rows if r.top_count_hash == current)
    return StabilityBadge(
        streak=streak,
        matches_in_window=matches,
        window=len(rows),
        last_changed_on=last_changed,
    )
```

Note on imports: move `StabilityBadge` and `StoredRow` into the top-level imports (delete the inline `noqa` lines once placed at the top). Keep imports grouped: stdlib → third-party → first-party. Final top-level import block in `service.py` after this task:

```python
from wave_alpha import __version__
from wave_alpha.history import store
from wave_alpha.history.identity import top_count_hash
from wave_alpha.history.models import (
    Snapshot,
    SnapshotCoherence,
    SnapshotCount,
    SnapshotPivot,
    SnapshotSignal,
    StabilityBadge,
)
from wave_alpha.history.store import StoredRow
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/history/test_stability_badge.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Lint**

```bash
uv run ruff check src/wave_alpha/history/service.py tests/history/test_stability_badge.py
uv run ruff format src/wave_alpha/history/service.py tests/history/test_stability_badge.py
```

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/history/service.py tests/history/test_stability_badge.py
git commit -m "feat(history): _compute_badge stability metrics"
```

---

## Task 6: Service — `get_recent_with_badge`

**Files:**
- Modify: `src/wave_alpha/history/service.py`
- Create: `tests/history/test_get_recent_with_badge.py`

- [ ] **Step 1: Write the failing test**

Create `tests/history/test_get_recent_with_badge.py`:

```python
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from wave_alpha.history import service, store
from wave_alpha.history.models import (
    Snapshot,
    SnapshotCoherence,
    SnapshotCount,
    SnapshotPivot,
    SnapshotSignal,
)


def _snap(*, as_of: date, top_hash: str, ticker: str = "AAPL") -> Snapshot:
    return Snapshot(
        ticker=ticker,
        as_of=as_of,
        engine_version="0.5.11",
        top_3=[
            SnapshotCount(
                candidate_id="C1",
                pattern="impulse",
                degree=1,
                last_pivot_label="3",
                pivots=[SnapshotPivot(timestamp=as_of, price=Decimal("100"))],
                engine_score=0.7,
                llm_prob=None,
                final_score=0.7,
            )
        ],
        signal=SnapshotSignal(
            direction="long",
            entry_price=Decimal("100"),
            stop_price=Decimal("95"),
            target_price=Decimal("115"),
            rr=3.0,
        ),
        coherence=SnapshotCoherence(score=0.7, multiplier=1.0, pairwise={}, fallback_used=None),
        top_count_hash=top_hash,
        created_at=datetime(as_of.year, as_of.month, as_of.day, 16, 0, 0),
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "history.sqlite"


def test_empty_history(db_path: Path) -> None:
    rh = service.get_recent_with_badge("AAPL", db_path=db_path)
    assert rh.snapshots == []
    assert rh.badge is None


def test_single_snapshot_no_badge(db_path: Path) -> None:
    store.upsert(_snap(as_of=date(2024, 12, 31), top_hash="h-A"), db_path=db_path)
    rh = service.get_recent_with_badge("AAPL", db_path=db_path)
    assert len(rh.snapshots) == 1
    assert rh.badge is None


def test_stable_top_pick_produces_full_streak_badge(db_path: Path) -> None:
    for i in range(5, 0, -1):
        store.upsert(_snap(as_of=date(2024, 12, i), top_hash="h-A"), db_path=db_path)
    rh = service.get_recent_with_badge("AAPL", db_path=db_path)
    assert len(rh.snapshots) == 5
    assert rh.snapshots[0].as_of == date(2024, 12, 5)
    assert rh.badge is not None
    assert rh.badge.streak == 5
    assert rh.badge.matches_in_window == 5
    assert rh.badge.last_changed_on is None


def test_default_window_is_14(db_path: Path) -> None:
    for i in range(20):
        store.upsert(
            _snap(as_of=date(2024, 1, 1).replace(day=i + 1), top_hash="h-A"), db_path=db_path
        )
    rh = service.get_recent_with_badge("AAPL", db_path=db_path)
    assert len(rh.snapshots) == 14
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/history/test_get_recent_with_badge.py -v
```

Expected: AttributeError on `service.get_recent_with_badge`.

- [ ] **Step 3: Add `get_recent_with_badge` to `service.py`**

Append to `src/wave_alpha/history/service.py`:

```python
from wave_alpha.history.models import RecentHistory  # add to top-level imports


def get_recent_with_badge(ticker: str, *, n: int = 14, db_path: Path) -> RecentHistory:
    rows = store.recent(ticker, n, db_path=db_path)
    snapshots = [Snapshot.model_validate_json(r.payload_json) for r in rows]
    badge = _compute_badge(rows, window=n)
    return RecentHistory(snapshots=snapshots, badge=badge)
```

(Move `RecentHistory` into the existing model import block at the top of the file.)

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/history/test_get_recent_with_badge.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Run the full history test suite**

```bash
uv run pytest tests/history/ -v
```

Expected: all green (~26 tests across the 6 files).

- [ ] **Step 6: Lint**

```bash
uv run ruff check src/wave_alpha/history/ tests/history/
uv run ruff format src/wave_alpha/history/ tests/history/
```

- [ ] **Step 7: Commit**

```bash
git add src/wave_alpha/history/service.py tests/history/test_get_recent_with_badge.py
git commit -m "feat(history): get_recent_with_badge read API"
```

---

## Task 7: Web `/deepdive/<ticker>/history` route + template

**Files:**
- Create: `src/wave_alpha/web/templates/_history.html`
- Modify: `src/wave_alpha/web/routes/deepdive.py`
- Create: `tests/web/test_routes_deepdive_history.py`

- [ ] **Step 1: Write the failing route test**

Create `tests/web/test_routes_deepdive_history.py`:

```python
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wave_alpha.history import service, store
from wave_alpha.history.models import (
    Snapshot,
    SnapshotCoherence,
    SnapshotCount,
    SnapshotPivot,
    SnapshotSignal,
)
from wave_alpha.web import deps
from wave_alpha.web.app import create_app


def _snap(*, as_of: date, top_hash: str, ticker: str = "AAPL") -> Snapshot:
    return Snapshot(
        ticker=ticker,
        as_of=as_of,
        engine_version="0.5.11",
        top_3=[
            SnapshotCount(
                candidate_id="C1",
                pattern="impulse",
                degree=1,
                last_pivot_label="3",
                pivots=[SnapshotPivot(timestamp=as_of, price=Decimal("100"))],
                engine_score=0.72,
                llm_prob=None,
                final_score=0.72,
            )
        ],
        signal=SnapshotSignal(
            direction="long",
            entry_price=Decimal("100"),
            stop_price=Decimal("95"),
            target_price=Decimal("115"),
            rr=3.0,
        ),
        coherence=SnapshotCoherence(score=0.7, multiplier=1.0, pairwise={}, fallback_used=None),
        top_count_hash=top_hash,
        created_at=datetime(as_of.year, as_of.month, as_of.day, 16, 0, 0),
    )


@pytest.fixture
def temp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(deps, "_data_dir", lambda: tmp_path)
    return tmp_path


def test_history_route_empty(temp_data_dir: Path) -> None:
    client = TestClient(create_app())
    r = client.get("/deepdive/AAPL/history")
    assert r.status_code == 200
    assert "No snapshots yet" in r.text


def test_history_route_renders_snapshots(temp_data_dir: Path) -> None:
    db = temp_data_dir / "history.sqlite"
    for i in range(5, 0, -1):
        store.upsert(_snap(as_of=date(2024, 12, i), top_hash="h-A"), db_path=db)
    client = TestClient(create_app())
    r = client.get("/deepdive/AAPL/history")
    assert r.status_code == 200
    assert "2024-12-05" in r.text
    assert "impulse" in r.text
    assert "stable for 5 days" in r.text


def test_history_route_lowercases_to_canonical_ticker(temp_data_dir: Path) -> None:
    db = temp_data_dir / "history.sqlite"
    store.upsert(_snap(as_of=date(2024, 12, 31), top_hash="h-A"), db_path=db)
    client = TestClient(create_app())
    r = client.get("/deepdive/aapl/history")
    assert r.status_code == 200
    assert "2024-12-31" in r.text
```

Confirming `service.get_recent_with_badge` integrates into the route happens here too — implicitly through the rendered output.

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/web/test_routes_deepdive_history.py -v
```

Expected: 404 on `/deepdive/AAPL/history` (route not registered yet).

- [ ] **Step 3: Create the template `src/wave_alpha/web/templates/_history.html`**

```html
{% if not snapshots %}
  <p class="muted">No snapshots yet. The first analysis will start the timeline.</p>
{% else %}
  {% if badge %}
    <div class="stability-badge">
      {% if badge.streak == badge.matches_in_window %}
        Top pick stable for {{ badge.streak }} day{% if badge.streak != 1 %}s{% endif %}
      {% else %}
        Top pick stable for {{ badge.streak }} day{% if badge.streak != 1 %}s{% endif %}
        ({{ badge.matches_in_window }} of last {{ badge.window }})
      {% endif %}
      {% if badge.last_changed_on %}
        <span class="muted">— last changed {{ badge.last_changed_on }}</span>
      {% endif %}
    </div>
  {% endif %}
  <ol class="history-timeline">
    {% for s in snapshots %}
      <li class="history-row{% if loop.first %} current{% endif %}">
        <span class="date">{{ s.as_of }}</span>
        <span class="pattern">{{ s.top_3[0].pattern }}</span>
        <span class="label">W{{ s.top_3[0].last_pivot_label }}</span>
        <span class="score">{{ "%.2f"|format(s.top_3[0].final_score) }}</span>
        {% if s.signal %}
          <span class="rr">RR {{ "%.1f"|format(s.signal.rr) }}</span>
        {% endif %}
      </li>
    {% endfor %}
  </ol>
{% endif %}
```

- [ ] **Step 4: Add the route to `src/wave_alpha/web/routes/deepdive.py`**

Append after the `scenarios_fragment` definition (around line 138):

```python
@router.get("/{ticker}/history", response_class=HTMLResponse)
def history_fragment(ticker: str, request: Request) -> HTMLResponse:
    from wave_alpha.history import service as history_service

    db_path = deps._data_dir() / "history.sqlite"
    recent = history_service.get_recent_with_badge(ticker.upper(), db_path=db_path)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="_history.html",
        context={"snapshots": recent.snapshots, "badge": recent.badge},
    )
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/web/test_routes_deepdive_history.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Lint**

```bash
uv run ruff check src/wave_alpha/web/routes/deepdive.py tests/web/test_routes_deepdive_history.py
uv run ruff format src/wave_alpha/web/routes/deepdive.py tests/web/test_routes_deepdive_history.py
```

- [ ] **Step 7: Commit**

```bash
git add src/wave_alpha/web/templates/_history.html \
        src/wave_alpha/web/routes/deepdive.py \
        tests/web/test_routes_deepdive_history.py
git commit -m "feat(web): /deepdive/<ticker>/history route + template"
```

---

## Task 8: Wire history section into `deepdive.html` + write hook in `counts_fragment`

**Files:**
- Modify: `src/wave_alpha/web/templates/deepdive.html`
- Modify: `src/wave_alpha/web/routes/deepdive.py`
- Modify: `tests/web/test_routes_deepdive_history.py` (add an integration test for the write hook)

- [ ] **Step 1: Add an end-to-end "visiting counts writes a snapshot" test**

Append to `tests/web/test_routes_deepdive_history.py`:

```python
def test_counts_fragment_writes_history_snapshot(
    temp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Visiting /deepdive/<ticker>/counts must populate the history DB."""
    from datetime import date as _date
    from decimal import Decimal as _Decimal

    from wave_alpha.coherence.score import CoherenceResult
    from wave_alpha.elliott.models import WaveCount
    from wave_alpha.pipeline import AnalyzeResult, RankedCount
    from wave_alpha.pivots.models import PivotEvent
    from wave_alpha.trades.models import TradeSignal
    from wave_alpha.web import deps as web_deps

    pivots = [
        PivotEvent(degree=1, timestamp=_date(2024, 1, 1), price=_Decimal("100")),
        PivotEvent(degree=1, timestamp=_date(2024, 6, 1), price=_Decimal("130")),
    ]
    fake = AnalyzeResult(
        ticker="AAPL",
        as_of=_date(2024, 6, 1),
        ranked_daily=[
            RankedCount(
                candidate_id="C1",
                count=WaveCount(pattern="impulse", degree=1, pivots=pivots, score=0.7),
                engine_score=0.7,
                llm_prob=None,
                final_score=0.7,
            )
        ],
        signals=[
            TradeSignal(
                ticker="AAPL",
                as_of=_date(2024, 6, 1),
                direction="long",
                entry_price=_Decimal("130"),
                stop_price=_Decimal("125"),
                target_price=_Decimal("145"),
                count_id="C1",
                count_pattern="impulse",
                wave_label="3",
                degree=1,
                confidence=0.7,
                expected_bars_to_target=20,
                max_holding_bars=60,
            )
        ],
        coherence=CoherenceResult(score=0.8, multiplier=1.05, pairwise={}, fallback_used=None),
    )
    monkeypatch.setattr(web_deps, "analyze", lambda *a, **k: fake)

    client = TestClient(create_app())
    r = client.get("/deepdive/AAPL/counts?as_of=2024-06-01")
    assert r.status_code == 200

    db = temp_data_dir / "history.sqlite"
    assert db.exists()
    rows = store.recent("AAPL", 10, db_path=db)
    assert len(rows) == 1
    assert rows[0].as_of == _date(2024, 6, 1)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/web/test_routes_deepdive_history.py::test_counts_fragment_writes_history_snapshot -v
```

Expected: AssertionError on `db.exists()` (no write hook yet).

- [ ] **Step 3: Add the write hook in `counts_fragment`**

In `src/wave_alpha/web/routes/deepdive.py`, the existing `counts_fragment`:

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
    # ↓ NEW: write history snapshot (best-effort; never raises)
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
```

(The two new lines are the `from … import` and the `record_snapshot(...)` call. Place them between the `result = deps.analyze(...)` line and the existing `narratives` block.)

- [ ] **Step 4: Add the History section to `deepdive.html`**

Append after the existing Scenarios `<section>` (which currently ends `loading scenarios…</section>`):

```html
  <section class="card"
           hx-get="/deepdive/{{ ticker }}/history"
           hx-trigger="load delay:300ms"
           hx-swap="innerHTML">
    <h2>History</h2>
    <span class="htmx-indicator">loading history…</span>
  </section>
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
uv run pytest tests/web/test_routes_deepdive_history.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Run the full web test suite to catch regressions**

```bash
uv run pytest tests/web/ -v
```

Expected: all green (existing routes unchanged in behavior).

- [ ] **Step 7: Lint**

```bash
uv run ruff check src/wave_alpha/web/ tests/web/
uv run ruff format src/wave_alpha/web/ tests/web/
```

- [ ] **Step 8: Commit**

```bash
git add src/wave_alpha/web/templates/deepdive.html \
        src/wave_alpha/web/routes/deepdive.py \
        tests/web/test_routes_deepdive_history.py
git commit -m "feat(web): wire history section + counts_fragment write hook"
```

---

## Task 9: CLI `analyze` write hook

**Files:**
- Modify: `src/wave_alpha/cli/app.py`
- Create: `tests/cli/test_analyze_history_hook.py`

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_analyze_history_hook.py`:

```python
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wave_alpha.cli import app as cli_app
from wave_alpha.coherence.score import CoherenceResult
from wave_alpha.elliott.models import WaveCount
from wave_alpha.history import store
from wave_alpha.pipeline import AnalyzeResult, RankedCount
from wave_alpha.pivots.models import PivotEvent
from wave_alpha.trades.models import TradeSignal


def _fake_result() -> AnalyzeResult:
    pivots = [
        PivotEvent(degree=1, timestamp=date(2024, 1, 1), price=Decimal("100")),
        PivotEvent(degree=1, timestamp=date(2024, 6, 1), price=Decimal("130")),
    ]
    return AnalyzeResult(
        ticker="AAPL",
        as_of=date(2024, 6, 1),
        ranked_daily=[
            RankedCount(
                candidate_id="C1",
                count=WaveCount(pattern="impulse", degree=1, pivots=pivots, score=0.7),
                engine_score=0.7,
                llm_prob=None,
                final_score=0.7,
            )
        ],
        signals=[
            TradeSignal(
                ticker="AAPL",
                as_of=date(2024, 6, 1),
                direction="long",
                entry_price=Decimal("130"),
                stop_price=Decimal("125"),
                target_price=Decimal("145"),
                count_id="C1",
                count_pattern="impulse",
                wave_label="3",
                degree=1,
                confidence=0.7,
                expected_bars_to_target=20,
                max_holding_bars=60,
            )
        ],
        coherence=CoherenceResult(score=0.8, multiplier=1.05, pairwise={}, fallback_used=None),
    )


def test_cli_analyze_writes_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_app, "_DEFAULT_DATA_DIR", tmp_path)
    monkeypatch.setattr(cli_app, "run_analysis", lambda *a, **k: _fake_result())
    monkeypatch.setattr(cli_app, "_default_provider", lambda: object())

    runner = CliRunner()
    result = runner.invoke(
        cli_app.app,
        ["analyze", "AAPL", "--as-of", "2024-06-01", "--json"],
    )
    assert result.exit_code == 0, result.output

    db = tmp_path / "history.sqlite"
    assert db.exists()
    rows = store.recent("AAPL", 10, db_path=db)
    assert len(rows) == 1
    assert rows[0].as_of == date(2024, 6, 1)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/cli/test_analyze_history_hook.py -v
```

Expected: AssertionError on `db.exists()`.

- [ ] **Step 3: Add the write hook to the CLI `analyze` command**

In `src/wave_alpha/cli/app.py`, the `analyze` command currently ends with the `if json_out: ... else: _print_human(result)` block. Add the hook after `result = run_analysis(...)` and before the output formatting:

```python
    result = run_analysis(
        req,
        provider=provider,
        llm_mode=llm_mode,
        llm_runner=runner,
        alpha=effective_alpha,
    )
    # Best-effort history snapshot (never raises)
    from wave_alpha.history import service as history_service
    history_service.record_snapshot(
        result, ticker.upper(), resolved_as_of, db_path=_DEFAULT_DATA_DIR / "history.sqlite"
    )
    if json_out:
        typer.echo(json.dumps(_serialize(result), default=_json_default))
    else:
        _print_human(result)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/cli/test_analyze_history_hook.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Run the full CLI test suite for regressions**

```bash
uv run pytest tests/cli/ -v
```

Expected: all green.

- [ ] **Step 6: Lint**

```bash
uv run ruff check src/wave_alpha/cli/app.py tests/cli/test_analyze_history_hook.py
uv run ruff format src/wave_alpha/cli/app.py tests/cli/test_analyze_history_hook.py
```

- [ ] **Step 7: Commit**

```bash
git add src/wave_alpha/cli/app.py tests/cli/test_analyze_history_hook.py
git commit -m "feat(cli): analyze writes history snapshot"
```

---

## Task 10: CSS for history section

**Files:**
- Modify: `src/wave_alpha/web/static/app.css`

- [ ] **Step 1: Inspect existing CSS to match conventions**

```bash
uv run head -n 60 src/wave_alpha/web/static/app.css
```

Identify the project's spacing/color tokens (CSS variables or hard-coded values) and the look of the `.coherence-row` / coherence-badge styles. Use the same idiom.

- [ ] **Step 2: Append history-section styles to `app.css`**

```css
/* History section */
.stability-badge {
  margin: 0 0 0.75rem 0;
  padding: 0.4rem 0.6rem;
  background: #f4f6f8;
  border-left: 3px solid #4a90e2;
  font-size: 0.95rem;
}
.stability-badge .muted {
  color: #888;
  font-size: 0.85rem;
  margin-left: 0.4rem;
}
.history-timeline {
  list-style: none;
  margin: 0;
  padding: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.92rem;
}
.history-row {
  display: grid;
  grid-template-columns: 7rem 7rem 3rem 4rem auto;
  gap: 1rem;
  padding: 0.3rem 0.5rem;
  border-bottom: 1px solid #eee;
}
.history-row.current {
  border-left: 3px solid #4a90e2;
  background: #fafbfc;
}
.history-row .date { color: #555; }
.history-row .pattern { font-weight: 600; }
.history-row .score { color: #222; }
.history-row .rr { color: #4a7a3c; }
```

(Adjust color values if `app.css` already defines variables — prefer those.)

- [ ] **Step 3: Smoke test the UI manually**

```bash
uv run wave-alpha ui &
# in another shell:
uv run wave-alpha analyze AAPL --as-of $(date +%Y-%m-%d)
# then visit http://127.0.0.1:8765/deepdive/AAPL
```

Verify:
1. Page loads without errors.
2. "History" card appears at the bottom.
3. Today's snapshot is visible with pattern, score, RR.
4. Stability badge renders if you've run analyze multiple days (won't on first run).
5. Browser console shows no 4xx/5xx errors against `/deepdive/AAPL/history`.

Stop the UI when done.

- [ ] **Step 4: Commit**

```bash
git add src/wave_alpha/web/static/app.css
git commit -m "feat(web): styles for history section and stability badge"
```

---

## Task 11: Final regression sweep

- [ ] **Step 1: Full test suite**

```bash
uv run pytest -v
```

Expected: all green.

- [ ] **Step 2: Full lint**

```bash
uv run ruff check .
uv run ruff format --check .
```

Expected: no errors.

- [ ] **Step 3: Lookahead-leak guard still green** (sanity — history shouldn't have touched it)

```bash
uv run pytest tests/backtest/test_lookahead_guard.py -v
```

Expected: pass.

- [ ] **Step 4: GitNexus reindex** (so the index reflects the new module)

```bash
npx gitnexus analyze
```

Expected: success message, new symbols indexed.

- [ ] **Step 5: Final summary commit (if any docs touched up)**

If you noticed any spec inconsistencies during implementation, edit the spec and commit. Otherwise skip.

```bash
git status   # confirm clean tree
```

---

## Self-Review Checklist

**Spec coverage:**
- §2 goals: persist auto, surface on deepdive, stability badge → Tasks 4, 7, 8, 5/6 ✓
- §5.1 module boundary → Tasks 1–6 ✓
- §5.2 hook points (web counts_fragment + CLI analyze, NOT pipeline) → Tasks 8, 9 ✓
- §6.1 Pydantic models → Task 2 ✓
- §6.2 SQLite schema → Task 3 ✓
- §6.3 hash definition → Task 1 ✓
- §7 write path (skip-empty, swallow errors, sync) → Task 4 ✓
- §8 read path + badge semantics → Tasks 5, 6 ✓
- §9 UI surface (route, partial, deepdive section, CSS) → Tasks 7, 8, 10 ✓
- §10 error handling (mkdir, no migrations, case canonical, frozen, extra=ignore) → Tasks 2, 3, 4, 7 ✓
- §11 testing strategy → covered across all tasks ✓
- §3 non-goals (no outcomes, no cron, no chart overlay, no scan/backtest writes) → respected throughout ✓

**Placeholder scan:** no TBD/TODO/"implement later"; every code step shows the actual code.

**Type consistency:**
- `Snapshot` field names match across Tasks 2, 3, 4, 6, 7, 8, 9 ✓
- `record_snapshot(result, ticker, as_of, *, db_path: Path)` used consistently in Tasks 4, 8, 9 ✓
- `get_recent_with_badge(ticker, *, n=14, db_path)` used consistently in Tasks 6, 7 ✓
- `StoredRow` shape consistent between Tasks 3 and 5 ✓
- `top_count_hash` always returns 16-char hex string from Task 1; asserted in Task 4 ✓
