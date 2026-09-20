---
related:
  - docs/superpowers/plans/2026-05-09-pypi-public-release.md  # §Out of scope: deferred this spec
  - src/wave_alpha/data/provider.py
  - src/wave_alpha/data/cached.py
  - src/wave_alpha/data/yahoo.py
---

# Stooq OHLCV provider — design

## §1. Motivation

`wave-alpha` 0.6.0 ships with a single OHLCV upstream: `YahooProvider`
(yfinance). Every `analyze` / `scan` / `backtest` invocation depends on
Yahoo Finance's free endpoint being reachable. Yahoo throttles
aggressive callers, occasionally changes its undocumented response
shape, and has no SLA. For a public package picked up by anonymous
users, this is the largest production-fragility gap in the release.

Adding **Stooq** as a second upstream — a long-standing free CSV
endpoint with no API key requirement — gives every daily and weekly
fetch a fallback path. The 4h timeframe stays single-source (Stooq
has no intraday data) but the W/D pipeline gains resilience to
Yahoo outages and rate limits.

This spec wires Stooq behind a new `FallbackProvider` decorator,
extends `OHLCVProvider` with a `supported_intervals` declaration, and
re-points the two production wiring sites (`cli/app.py`,
`web/deps.py`) at the new fallback chain. No engine, template,
right-edge, LLM, or backtest behavior changes.

## §2. Goals and non-goals

**Goals**

- A daily or weekly fetch that fails on Yahoo (raises, or returns no
  bars) automatically retries against Stooq within the same call.
  Caller sees `list[Bar]` either way.
- 4h fetches stay Yahoo-only; `StooqProvider` is skipped by the
  fallback chain rather than raising.
- The provider abstraction (`OHLCVProvider`) gains an
  interval-support declaration that's backwards-compatible — existing
  concrete classes and test fakes work unchanged.
- New code is hermetic-testable: no test reaches the network by
  default. One opt-in network test verifies the real Stooq endpoint.
- No new third-party dependency. HTTP via `urllib.request` (stdlib),
  CSV parsing via Python's stdlib or pandas (already in tree).

**Non-goals**

- **Non-US tickers.** Stooq's symbol grammar for foreign exchanges
  (`.de`, `.uk`, `.jp`, etc.) needs research; this spec ships
  US-equity-only mapping (`AAPL` → `aapl.us`).
- **Cross-checking** Yahoo vs. Stooq for disagreement detection.
  We trust whichever upstream returns first. Disagreement audit is
  a separate, larger spec.
- **Provider attribution in cache.** The `bars` SQLite table stays
  schema-unchanged; no `source` column. Bars from Stooq and Yahoo
  are treated as interchangeable rows.
- **4h synthesis** from daily Stooq bars. Resampled bars would be
  fake (every 4h slot in a day would carry the same OHLC). The
  3-way coherence multiplier depends on independent timeframe
  evidence; fake 4h bars would corrupt it.
- **A `data.fallback_provider` config knob.** Always-on. The
  fallback is a no-op when Yahoo succeeds; there is no user-visible
  reason to disable it.
- **Weekly-anchor normalization.** Stooq weekly bars are Friday-
  anchored, Yahoo's resampled weekly are Monday-anchored. We accept
  this divergence (see §10) and document it.

## §3. Constraints

- **No new dependencies.** Stdlib `urllib.request` for HTTP; existing
  pandas for CSV parsing (or stdlib `csv` — see §6.3).
- **Lookahead safety preserved.** Stooq returns full history just
  like Yahoo; the same `PointInTimeView` slicing applies downstream.
  No new path bypasses the `as_of` boundary.
- **Privacy contract preserved.** No upstream call shape changes that
  would route OHLCV data anywhere new. Stooq receives only ticker
  symbol + interval.
- **Decimal precision.** Bars constructed by `StooqProvider` use the
  same 4-decimal-place quantization as `bars_from_dataframe` in
  `data/yahoo.py`.
- **Cache invariants intact.** The (ticker, interval, ts) primary key
  is unchanged. `INSERT OR REPLACE` semantics handle Yahoo→Stooq
  fallback writes the same way they handle Yahoo refetches.
- **Provider abstraction stays minimal.** One new class attribute on
  `OHLCVProvider`, no new methods, no signature changes to `fetch`.

## §4. Architecture

```
                CachedProvider                            [unchanged]
                      │
                      ▼
              FallbackProvider                            [NEW]
              [YahooProvider, StooqProvider]
                      │
        ┌─────────────┴─────────────┐
        ▼                           ▼
  YahooProvider                StooqProvider              [NEW]
  supports: {W,D,4h}           supports: {W,D}
```

`FallbackProvider.fetch(ticker, interval, start, end)` semantics:

1. Iterate providers in declaration order.
2. **Skip** any whose `supported_intervals` does not include
   `interval`. (For `interval=4h`, only Yahoo is tried.)
3. Call the candidate's `fetch(...)`. If it raises **or** returns
   `[]`, log at DEBUG and continue to the next candidate.
4. Return the first non-empty result.
5. If every candidate skips, fails, or returns `[]`, return `[]`.

Empty-vs-failure are treated identically as fallback triggers.
Rationale: Yahoo returning empty for a ticker it doesn't recognize
should not block Stooq — Stooq might know the ticker. Cost: a
wasted HTTP call to Stooq for genuinely-unknown tickers; acceptable.

`CachedProvider` is unchanged. It already calls
`upstream.fetch(...)`, writes back on non-empty, and falls back to
cached data on empty. Wrapping its upstream with `FallbackProvider`
slots in transparently.

## §5. Provider abstraction extension

`src/wave_alpha/data/provider.py`:

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from wave_alpha.domain import Bar, Interval


class OHLCVProvider(ABC):
    """Pluggable OHLCV bar source.

    Subclasses MAY override `supported_intervals` to declare a subset of
    `Interval`. The default declares full support, so existing concrete
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

The class attribute is a `frozenset` (immutable, set-comparable, hashable).
Default value covers `YahooProvider` and every existing test fake without
edit.

## §6. StooqProvider implementation

### §6.1 Module layout

```
src/wave_alpha/data/
  ├─ provider.py        # MODIFY — add supported_intervals attr
  ├─ yahoo.py           # unchanged
  ├─ stooq.py           # CREATE
  ├─ fallback.py        # CREATE
  ├─ cached.py          # unchanged
  └─ cache.py           # unchanged
```

### §6.2 Endpoint and symbol mapping

Stooq's free CSV endpoint:

```
https://stooq.com/q/d/l/?s={symbol}&i={d|w}
```

- `{symbol}` is the lowercase Stooq ticker, US-equity form: ticker
  with class-share dots replaced by `-`, plus `.us` suffix.
  - `AAPL` → `aapl.us`
  - `BRK.B` → `brk-b.us`
  - `MSFT` → `msft.us`
- `{i}` is `d` (daily) or `w` (weekly). No 4h variant.

Symbol mapping is a single function:

```python
def _to_stooq_symbol(ticker: str) -> str:
    return ticker.lower().replace(".", "-") + ".us"
```

### §6.3 CSV parsing

Stooq's CSV header is `Date,Open,High,Low,Close,Volume`. Date format
is `YYYY-MM-DD`. The endpoint returns descending-by-date OR ascending-
by-date depending on query params; we sort ascending after parsing
to be safe.

Empty-result sentinel: when no data is available (unknown ticker,
delisted), Stooq returns the literal text `No data` (HTTP 200, plain
text body). Detect by inspecting the first line for the header
prefix `Date,` — anything else means "no bars". Return `[]`.

Use `csv.DictReader` from stdlib for parsing (no pandas import in
this module — keeps the module light and aligned with "no new deps"
constraint, even though pandas is already a transitive dep).

Bar construction mirrors `bars_from_dataframe`:

```python
Bar(
    timestamp=date.fromisoformat(row["Date"]),
    open=Decimal(f"{float(row['Open']):.4f}"),
    high=Decimal(f"{float(row['High']):.4f}"),
    low=Decimal(f"{float(row['Low']):.4f}"),
    close=Decimal(f"{float(row['Close']):.4f}"),
    volume=int(row.get("Volume") or 0),
)
```

Skip rows with empty/`-` Open or Close (Stooq sometimes emits
sentinel rows for halted days).

### §6.4 HTTP transport

`urllib.request.urlopen(url, timeout=30)`. Decode bytes as UTF-8.
Wrap in a tiny indirection so tests can monkeypatch:

```python
def _http_get(url: str, *, timeout: float = 30.0) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode("utf-8")
```

User-Agent header: don't set one (Stooq's CSV endpoint accepts
default `Python-urllib/3.x`). If they ever block default UA, the
follow-up is a one-liner.

Errors (`URLError`, `HTTPError`, `socket.timeout`,
`UnicodeDecodeError`) propagate up; `FallbackProvider` catches them.
`StooqProvider` does not retry internally.

### §6.5 Class skeleton

```python
class StooqProvider(OHLCVProvider):
    supported_intervals = frozenset({Interval.DAILY, Interval.WEEKLY})

    _INTERVAL_TO_STOOQ: dict[Interval, str] = {
        Interval.DAILY: "d",
        Interval.WEEKLY: "w",
    }

    def fetch(
        self,
        ticker: str,
        interval: Interval,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Bar]:
        if interval not in self.supported_intervals:
            return []  # defensive — FallbackProvider should already skip us

        symbol = _to_stooq_symbol(ticker)
        url = f"https://stooq.com/q/d/l/?s={symbol}&i={self._INTERVAL_TO_STOOQ[interval]}"
        logger.debug("stooq fetch ticker={} interval={} url={}", ticker, interval.value, url)

        csv_text = _http_get(url)
        return _parse_stooq_csv(csv_text)
```

Window filtering is the `CachedProvider`'s responsibility; this
class returns the full upstream payload, matching `YahooProvider`'s
contract documented at `data/yahoo.py:62-69`.

## §7. FallbackProvider implementation

`src/wave_alpha/data/fallback.py`:

```python
from __future__ import annotations

from datetime import date
from typing import Sequence

from loguru import logger

from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval


class FallbackProvider(OHLCVProvider):
    """Tries each provider in order. Skips providers that don't
    declare support for the requested interval. Returns the first
    non-empty result; falls through on raise or empty.
    """

    def __init__(self, providers: Sequence[OHLCVProvider]) -> None:
        if not providers:
            raise ValueError("FallbackProvider requires at least one provider")
        self._providers = list(providers)

    @property
    def supported_intervals(self) -> frozenset[Interval]:
        # Union — supported if any inner provider supports it.
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

Notes:

- `supported_intervals` becomes a property to dynamically reflect
  the union of inner providers. The base class declares it as a
  class attribute with a default value; subclasses can override
  with either a class attribute or a property. Verified
  Python-attribute-resolution semantics: a property on the subclass
  shadows the class attribute on the base.
- `except Exception` is broad on purpose: every transport-layer
  failure mode (URLError, ConnectionError, TimeoutError, parser
  failures inside the provider) should fall through. The narrower
  alternative would be to enumerate exceptions per provider, which
  couples `FallbackProvider` to provider internals.

## §8. Wiring changes

### §8.1 `cli/app.py`

Current:

```python
def _default_provider() -> CachedProvider:
    return CachedProvider(upstream=YahooProvider(), db_path=_DEFAULT_DATA_DIR / "cache.sqlite")
```

After:

```python
from wave_alpha.data.fallback import FallbackProvider
from wave_alpha.data.stooq import StooqProvider

def _default_provider() -> CachedProvider:
    return CachedProvider(
        upstream=FallbackProvider([YahooProvider(), StooqProvider()]),
        db_path=_DEFAULT_DATA_DIR / "cache.sqlite",
    )
```

### §8.2 `web/deps.py`

Identical edit at line 38:

```python
from wave_alpha.data.fallback import FallbackProvider
from wave_alpha.data.stooq import StooqProvider

provider = CachedProvider(
    upstream=FallbackProvider([YahooProvider(), StooqProvider()]),
    db_path=data_dir / "cache.sqlite",
)
```

### §8.3 No other call sites change

`grep -rln "YahooProvider\|CachedProvider" src/wave_alpha tests`
shows the wiring is concentrated in:

- `src/wave_alpha/web/deps.py` (production)
- `src/wave_alpha/web/right_edge.py` (training reads — keep YahooProvider direct; right-edge training already injects providers explicitly and is opt-in CLI)
- `src/wave_alpha/web/routes/deepdive.py` (consumes `analyze()` from deps; no provider construction)
- `src/wave_alpha/cli/app.py` (production)
- `tests/data/test_*.py` (tests construct providers directly with fakes — unchanged)

The right-edge training path stays Yahoo-direct intentionally:
training consumes a curated universe with Yahoo coverage and a
deterministic provider for reproducible artifact generation. Adding
fallback there is YAGNI.

## §9. Testing strategy

### §9.1 Fixtures

```
tests/fixtures/
  ├─ stooq_aapl_5d_daily.csv         # CREATE — recorded ~5 daily bars
  └─ stooq_aapl_5w_weekly.csv        # CREATE — recorded ~5 weekly bars
```

Recording instructions live in test docstrings:

```
# To regenerate:
#   curl 'https://stooq.com/q/d/l/?s=aapl.us&i=d' | head -6 > tests/fixtures/stooq_aapl_5d_daily.csv
```

Fixture content shape (illustrative):

```csv
Date,Open,High,Low,Close,Volume
2024-04-22,165.52,167.26,164.77,165.84,48117420
2024-04-23,165.35,167.05,164.92,166.90,49537770
...
```

### §9.2 Test inventory

| File | Test | Type | What it asserts |
|------|------|------|------|
| `tests/data/test_stooq.py` | `test_parses_csv_fixture` | unit, fixture | Recorded daily CSV → 5 ascending Bars, Decimal precision |
| | `test_parses_weekly_fixture` | unit, fixture | Weekly CSV variant parses |
| | `test_symbol_mapping_us_equity` | unit | `AAPL → aapl.us`; `BRK.B → brk-b.us`; `MSFT → msft.us` |
| | `test_no_data_returns_empty` | unit, monkeypatched `_http_get` | `"No data"` body → `[]` |
| | `test_unsupported_interval_returns_empty` | unit | `interval=4h` → `[]` (defensive) |
| | `test_skips_halted_rows` | unit, monkeypatched | Rows with `-` for Open/Close are dropped |
| | `test_supported_intervals` | unit | `StooqProvider.supported_intervals == {DAILY, WEEKLY}` |
| `tests/data/test_fallback.py` | `test_uses_first_provider_when_succeeds` | unit, fakes | Yahoo returns bars; Stooq fake never called |
| | `test_falls_through_on_empty` | unit, fakes | Yahoo `[]` → Stooq called → Stooq's bars returned |
| | `test_falls_through_on_raise` | unit, fakes | Yahoo raises → Stooq called → no exception leaks |
| | `test_skips_unsupported_provider` | unit, fakes | 4h request: Stooq fake not called, Yahoo returns bars |
| | `test_all_empty_returns_empty` | unit, fakes | Both `[]` → `[]`, no exception |
| | `test_all_fail_returns_empty` | unit, fakes | Both raise → `[]`, no exception |
| | `test_supported_intervals_is_union` | unit | Union of inner providers' supported sets |
| | `test_empty_provider_list_raises` | unit | Constructor rejects empty list |
| `tests/data/test_cached_with_fallback.py` | `test_cached_provider_wraps_fallback` | integration, fakes | CachedProvider + FallbackProvider: cache hit/miss/stale paths preserved |
| `tests/data/test_stooq_network.py` | `test_real_stooq_fetch` | integration, marker `network` | Real fetch of `aapl.us` daily; skipped by default |

### §9.3 Network test discipline

- `pyproject.toml` `[tool.pytest.ini_options]` already has
  `addopts = "-ra --strict-markers"` and registers a `slow` marker.
- **Add** a `network` marker to that list:
  `"network: tests that hit live external endpoints (deselect with '-m \"not network\"')"`.
- **Modify** `addopts` to `"-ra --strict-markers -m 'not network'"`
  so the default `uv run pytest` invocation skips network tests
  (markers do not auto-skip; an explicit `-m` filter is the only
  way). Opt in with `uv run pytest -m network`.
- Mark the one network test with `@pytest.mark.network`.
- CI runs `uv run pytest` (default invocation), so the network test
  is skipped.

### §9.4 Existing-test impact

`tests/data/test_yahoo.py` and `tests/data/test_cached*.py`
construct providers directly with the existing classes. They
should pass unchanged. The new abstract attribute on
`OHLCVProvider` has a default, so any test fake that subclasses
`OHLCVProvider` without declaring `supported_intervals` continues
to work (defaults to "all intervals supported", which is
fallback-friendly).

## §10. Known divergences (acknowledged, not normalized)

### §10.1 Weekly bar anchor

- Yahoo's weekly bars (resampled from daily by yfinance) anchor on
  Sunday/Monday. Stooq's weekly bars anchor on Friday.
- For the same calendar week, the timestamp written to the cache
  differs by ~4 days depending on which provider supplied the bar.
- The `(ticker, interval, ts)` primary key means a Yahoo-supplied
  weekly bar and a Stooq-supplied weekly bar for the same week
  coexist as separate rows, not duplicates. They will be treated
  as 2 bars by downstream consumers.
- **Acceptable.** Weekly is used for high-degree pivot detection
  (multi-month windows) and coherence checks. Two adjacent rows
  that span a week instead of one is a low-amplitude perturbation
  on engine output. If empirically problematic (e.g., it shifts a
  scan ranking detectably), the follow-up is to normalize Stooq
  weekly bar timestamps to Monday in `_parse_stooq_csv`. Spec out
  of scope.

### §10.2 Volume scale differences

Stooq and Yahoo sometimes report volume at slightly different
scales (split-adjustment timing, holiday handling). The engine
does not consume volume — only OHLC. Acceptable.

### §10.3 Adjustment policy

- `YahooProvider` calls yfinance with `auto_adjust=True`: closes
  are split- and dividend-adjusted.
- Stooq's CSV endpoint returns split-adjusted but NOT
  dividend-adjusted prices (no flag to control).
- For Elliott pattern fitting on price action, dividend adjustment
  matters for high-yield names (REITs, utilities) over multi-year
  windows; for impulse/correction shapes within a year of a
  decision, the difference is sub-percent.
- **Acceptable for v1.** Documented as a divergence. If a
  high-yield name shows a Stooq-vs-Yahoo pattern divergence in
  practice, the follow-up is to apply manual dividend adjustment
  in StooqProvider — non-trivial and out of this spec's scope.

## §11. Risk and blast radius

| Surface | Change | Risk |
|---------|--------|------|
| `OHLCVProvider` | New class attr with default | Low — backwards compatible |
| `YahooProvider` | None | None |
| `CachedProvider` | None | None |
| `OHLCVCache` (SQLite) | None | None |
| New `StooqProvider` | New module | Low — isolated, hermetic-tested |
| New `FallbackProvider` | New module | Low — pure composition, hermetic-tested |
| `cli/app.py:_default_provider` | One line | Low — wraps existing call |
| `web/deps.py:_run_analysis_uncached` | One line | Low — wraps existing call |
| `pipeline.py`, `enumerator.py`, `coherence/`, `right_edge/`, `llm/`, `backtest/` | None | None |
| `web/templates`, `web/static` | None | None |
| Public API (`wave-alpha analyze`, `scan`, `ui`) | None | None |
| Cache schema | None | None |

GitNexus impact analysis (to be run during plan execution) on
`OHLCVProvider`, `CachedProvider.fetch`, and `_default_provider`
should confirm no caller signatures change.

## §12. Out of scope (explicit deferrals)

- Non-US ticker support (`.de`, `.uk`, `.jp`, etc.).
- `data.fallback_provider` config knob.
- Cross-provider disagreement detection / audit logging.
- Provider attribution column in the SQLite cache.
- Weekly anchor normalization for Stooq.
- Dividend adjustment for Stooq prices.
- Custom User-Agent header for the Stooq HTTP client.
- Right-edge training swap to use `FallbackProvider`.
- Internal retry logic inside `StooqProvider` (handled by the
  fallback chain, which is the simpler boundary).
- Asynchronous fallback (parallel calls + first-non-empty wins).
  The fallback path is exceptional; serial cost is negligible.
- A third upstream (Polygon, IEX, etc.). Two upstreams handle the
  SPOF concern. Adding a third multiplies the testing matrix
  without proportional benefit.

## §13. Acceptance criteria

- `uv run pytest` (default invocation, no markers) passes with the
  new tests.
- `uv run pytest -m network tests/data/test_stooq_network.py`
  passes against the live Stooq endpoint.
- `uv run ruff check .` passes.
- `uv run wave-alpha analyze AAPL --as-of 2024-12-31 --json` works
  unchanged (Yahoo path).
- Simulated Yahoo failure: with `YahooProvider.fetch` monkeypatched
  to raise, the same `analyze` call returns a result populated
  from Stooq.
- No new entries in `pyproject.toml [project] dependencies` or
  `[project.optional-dependencies]`.
- `CHANGELOG.md` `[Unreleased]` section gains an `Added` entry for
  the Stooq fallback.

---

**Status:** ready for `superpowers:writing-plans` to produce the
step-by-step implementation plan.
