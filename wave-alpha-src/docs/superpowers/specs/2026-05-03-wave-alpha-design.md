---
title: wave-alpha — design
date: 2026-05-03
status: approved (brainstorm complete; pending implementation plan)
author: Alex Chen
---

# wave-alpha — design

A local-first, OSS Python tool that produces Elliott Wave counts on swing-trader watchlists, with multi-timeframe coherence, rule-validated alternates, LLM-narrated reasoning, and trade-signal derivation. Family-cohesive with `wash-alpha`: same tech DNA, same trader audience, different angle.

## Table of contents

1. [Motivation](#1-motivation)
2. [Goals and non-goals](#2-goals-and-non-goals)
3. [Tech stack](#3-tech-stack)
4. [System architecture](#4-system-architecture)
5. [EW engine internals](#5-ew-engine-internals)
6. [LLM contract](#6-llm-contract)
7. [Multi-timeframe coherence](#7-multi-timeframe-coherence)
8. [Backtest harness](#8-backtest-harness)
9. [Trade-signal contract](#9-trade-signal-contract)
10. [Feedback loop and continuous improvement](#10-feedback-loop-and-continuous-improvement)
11. [v0.1 UI](#11-v01-ui)
12. [Roadmap and phasing](#12-roadmap-and-phasing)
13. [Privacy contract](#13-privacy-contract)
14. [Open questions and deferred decisions](#14-open-questions-and-deferred-decisions)

---

## 1. Motivation

Swing traders using TradingView have access to manual Elliott Wave drawing tools and a handful of community "auto Elliott" Pine indicators that are essentially dressed-up ZigZag pivot detectors. None of the existing tools provide:

- Multi-timeframe coherence validation (weekly, daily, 4-hour read against each other)
- Rule-compliant alternate counts ranked by probability
- LLM-narrated reasoning ("we're in Wave 3 because…")
- Backtest-validated forward-outcome accuracy
- A trade-signal contract derived directly from the count's structural levels

Bloomberg and Elliott Wave International offer pieces of this for institutional prices. Retail tools either don't exist, are subscription-locked SaaS, or are cloud-only. `wave-alpha` is the local-first, OSS, BYO-API-key alternative — same family as `wash-alpha`.

**Primary user:** swing trader running a 20-50 ticker personal watchlist, daily/weekly bars, BYO Anthropic API key.

## 2. Goals and non-goals

**Goals**

- Produce rule-compliant Elliott Wave counts on equity OHLCV data with multi-TF coherence as the differentiator
- Surface alternates with calibrated probabilities, not a single confident-but-fragile answer
- Tie counts to actionable trade signals (entry, stop, target, holding window)
- Validate accuracy via a backtest harness that is honest about lookahead and regime sensitivity
- Improve over time via versioned learned models trained on backtest-derived labels and (optionally) opt-in pooled trade outcomes
- Stay local-first, BYO key, no telemetry, no cloud dependency for the core flow

**Non-goals**

- Not a charting platform — chart rendering uses TradingView Lightweight Charts (MIT)
- Not a real-time / day-trading tool — daily and weekly are first-class; 4h is supplementary; sub-hour is out of scope
- Not a portfolio manager — `wash-alpha` covers tax/portfolio
- Not an Elliott Wave tutor — assumes user knows the basics
- Not a SaaS — no auth, no billing, no multi-tenant DB
- Not a "right answer" oracle — the marketing claim is "high-coherence rule-compliant setups," not "AI knows the count"

## 3. Tech stack

Mirrors `wash-alpha` to minimize cognitive load.

- **Python 3.11+** (pinned in `.python-version`)
- **Package manager:** `uv` — `uv run <cmd>`, never activate venv manually
- **Build backend:** `hatch` via `pyproject.toml`
- **CLI framework:** `typer[all]` (bundles `rich` and `click`)
- **Web UI:** FastAPI + Jinja2 + HTMX + Alpine
- **Charts:** TradingView Lightweight Charts (MIT, vendored under `web/static/`)
- **Data models:** `pydantic` v2
- **ORM/storage:** `sqlmodel` over SQLite at `~/.wave_alpha/db.sqlite`
- **Date arithmetic:** stdlib `datetime`; bar dates stored as `YYYY-MM-DD` strings
- **Config:** `pyyaml` for `templates/*.yaml`, `~/.wave_alpha/config.yaml`
- **Logging:** `loguru`
- **Linter/formatter:** `ruff`
- **Tests:** `pytest`, `pytest-cov`, `factory_boy`
- **Data:** `yfinance` (default), with an `OHLCVProvider` ABC so users can swap in Polygon / Tiingo / IBKR for paid intraday
- **LLM:** `anthropic` SDK (BYO key); Haiku 4.5 for scan path, Sonnet 4.6 for deep-dive; prompt caching enabled

**Where we deviate from wash-alpha:** we add the `anthropic` SDK and `lightweight-charts` static asset; we do not import wash-alpha — these are independent projects.

## 4. System architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Web UI (FastAPI + HTMX + Lightweight Charts)                    │
│   /deepdive/{ticker}      ← v0.1: single-ticker deep-dive page   │
│   /scan                   ← v0.3: watchlist scan dashboard       │
│   /watchlist              ← v0.3: ticker list management         │
│   /settings               ← v0.1: API key, model, prefs          │
└──────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────┐
│  scan/orchestrator (v0.3+)                                        │
│   per ticker:                                                     │
│     1. fetch OHLCV (cached) at weekly + daily + 4h                │
│     2. detect pivots (multi-degree ATR-ZigZag)                    │
│     3. enumerate counts (templates + rule validation)             │
│     4. compute coherence (3-way)                                  │
│     5. LLM judge (Haiku, text, cached prompt)                     │
│     6. derive trade signals                                       │
│     7. cache by (ticker, latest_bar_date, count_set_hash)         │
└──────────────────────────────────────────────────────────────────┘
        │            │              │              │
   ┌────▼─────┐ ┌────▼─────┐ ┌─────▼─────┐ ┌──────▼──────┐
   │  data/   │ │  pivots/ │ │ elliott/  │ │    llm/     │
   │ yfinance │ │ ATR-     │ │ templates │ │ Anthropic   │
   │ + cache  │ │ ZigZag   │ │ + rules   │ │ judge       │
   │ (SQLite) │ │ multi-d  │ │ + fib     │ │ + cache     │
   └──────────┘ └──────────┘ └───────────┘ └─────────────┘
        │            │              │
        └────────────┴──────────────┘
                     │
                     ▼
            ┌────────────────┐
            │   SQLite at    │
            │ ~/.wave_alpha/ │
            │   db.sqlite    │
            └────────────────┘
```

**Top-level package layout:**

```
wave_alpha/
├── cli/                # typer app: deep-dive (v0.1), scan (v0.3), backtest (v0.2)
├── data/               # OHLCVProvider ABC, YahooProvider, OHLCVCache
├── pivots/             # multi-degree ATR-ZigZag, point-in-time view, repainting policy
├── elliott/            # template registry, rule validator, fib confluence, count enumerator
│   └── templates/      # YAML pattern definitions: impulse, zigzag, flat, ...
├── coherence/          # multi-TF coherence scoring (weekly × daily × 4h)
├── llm/                # Anthropic client wrapper, prompt builders, response parser, cache
├── trades/             # TradeSignal, TradeRules, signal derivation, sim engine (v0.5)
├── backtest/           # walk-forward harness, point-in-time guards, metrics (v0.2+)
├── feedback/           # local outcome capture, personalization (v0.4+), pool client (v1.2+)
├── models/             # right-edge confirmation models: heuristic, logistic, registry
├── web/                # FastAPI app: routes, templates, static assets
└── prefs/              # API key, model choice, timeframe defaults, opt-in flags
```

**Key architectural principle: "computed core, narrated edge."**

The deterministic core (pivots, rule validation, fib, scoring, coherence) runs without an LLM. Counts and trade signals are produced by pure Python that's testable with golden fixtures. The LLM is a reranking-and-narrating layer on top — it never invents counts, never invents pivots, never selects from outside the engine's enumeration. This keeps the core unit-testable, makes LLM costs proportional to user engagement (not every-ticker-every-day), and allows the tool to function in degraded mode without an API key (counts and rankings without narrative).

## 5. EW engine internals

### 5.1 Pivot detection — multi-degree ATR-ZigZag

Pivots are the atoms of EW. We use ATR-adaptive ZigZag at five degrees of sensitivity, with a strict subset invariant: pivots at degree N+1 are a subset of pivots at degree N.

```
degrees = [0.5, 1, 2, 4, 8]  # ATR multipliers
atr     = ATR(close, period=20)

for k in degrees:
    threshold(t) = k * atr(t)
    pivots[k]    = atr_zigzag(ohlcv, threshold)
    # invariant: pivots[k+1] ⊆ pivots[k]
```

The hierarchical subset invariant gives us nesting "for free" — a degree-N wave naturally contains the degree-(N-1) sub-waves that lie between its endpoints.

**Why ATR-adaptive instead of fixed %:** the watchlist universe spans low-vol names (KO, JNJ, ~0.8% ATR) and high-vol names (NVDA, COIN, ~5%+ ATR). Fixed thresholds either spam pivots on JNJ or miss real moves on NVDA.

**Rejected alternatives:**
- Williams Fractal: 5-bar local check, no swing magnitude → too noisy.
- RDP polyline simplification: ignores volatility regime.
- Hybrid voting: introduces tunables without clear benefit.

### 5.2 Right-edge handling — strict backtest, tentative live

Every ZigZag pivot has an "honesty problem": it's only confirmed once price moves K×ATR in the opposite direction. The most recent K×ATR worth of price action contains pivots that may or may not exist.

**Two pipelines:**

- **Backtest pipeline:** strict point-in-time. A `PointInTimeView(bars, as_of)` wrapper hard-asserts no input bar has `timestamp > as_of`. Pivots used in evaluation at time `t` are only those whose `confirmed_at <= t`. Lookahead is impossible by construction.
- **Live pipeline:** displays both confirmed pivots (solid) and tentative pivots (dashed, with a confirmation probability from the right-edge model). Right-edge candidate counts surface as scenario branches, each with a probability and an explicit invalidation level.

**Pivot record schema:**

```python
class PivotEvent(BaseModel):
    ticker: str
    interval: Literal["1wk", "1d", "4h"]
    degree: int                                  # 0..4
    timestamp: date
    price: Decimal
    type: Literal["high", "low"]
    state: Literal["confirmed", "tentative"]
    confirmed_at: date | None                    # bar where K*ATR threshold was first crossed
```

The single accessor `pivots_visible_at(ticker, interval, t)` is the source of truth for both pipelines, eliminating drift.

### 5.3 Count encoding — tree of pattern templates

A count is a tree of pattern instances. Each node is a template (impulse, zigzag, flat, …) bound to pivots. Sub-patterns nest at each wave slot.

**Template registry as YAML:**

```yaml
# elliott/templates/impulse.yaml
name: impulse
direction_alternates: true
waves: [1, 2, 3, 4, 5]
constraints:
  - {name: wave_2_max_retrace,        severity: hard,
     expr: "retrace(W2, W1) <= 1.0",
     rationale: "Wave 2 cannot retrace more than 100% of Wave 1"}
  - {name: wave_3_not_shortest,       severity: hard,
     expr: "length(W3) >= min(length(W1), length(W5))",
     rationale: "Wave 3 cannot be the shortest of waves 1, 3, 5"}
  - {name: wave_4_no_overlap,         severity: hard,
     expr: "not overlaps(W4_extreme, W1_extreme)",
     rationale: "Wave 4 cannot overlap Wave 1 territory"}
  - {name: wave_2_typical_retrace,    severity: soft,
     expr: "0.5 <= retrace(W2, W1) <= 0.786"}
  - {name: wave_3_extension,          severity: soft,
     expr: "length(W3) >= 1.618 * length(W1)"}
allowed_sub_patterns:
  "1": [impulse, leading_diagonal]
  "2": [zigzag, flat, expanded_flat, contracting_triangle, double_three]
  "3": [impulse]
  "4": [zigzag, flat, expanded_flat, contracting_triangle, double_three]
  "5": [impulse, ending_diagonal]
```

**v0.1 template set:** impulse, zigzag, flat, expanded_flat, contracting_triangle, ending_diagonal — covers ~85% of real-world setups. Leading diagonals, running flats, double zigzags, triple threes added in later versions as backtest reveals coverage gaps.

**Constraint DSL:** Python expressions evaluated in a constrained namespace exposing helpers (`retrace`, `length`, `overlaps`, `extreme`). Templates are first-party code, so eval-as-DSL is acceptable; the namespace is defined once in `elliott/dsl.py`.

**Count schema:**

```python
class WaveCount(BaseModel):
    pattern: str                                 # template name
    degree: int                                  # 0..4 from §5.1
    pivots: list[PivotRef]                       # one per wave slot + start
    sub_counts: dict[str, "WaveCount"]           # nested counts by wave slot
    score: float                                 # template fit × fib confluence × sub-fit
    hard_violations: list[str]                   # dropped if non-empty
    soft_violations: list[str]                   # downgrades score
```

**Enumeration:** recursive descent. At each degree, try each compatible template, fit pivots from the next-lower degree into sub-pattern slots, score, drop on hard violations, prune to top-K=5 per degree before recursing.

### 5.4 Right-edge probability model — phased

Probability that a tentative pivot becomes confirmed, given data visible at time t.

**Features:**

```python
class RightEdgeFeatures(BaseModel):
    retracement_progress: float                  # 0..1, fraction of K*ATR moved
    bars_since_candidate: int
    fib_zone_match: float                        # 0..1, proximity to typical fib retrace
    volume_decline_pct: float                    # volume slope into the candidate
    rsi_divergence_strength: float
    higher_degree_zone_match: float
    atr_regime: float                            # current ATR / 6mo median ATR
```

**Model phasing:**

- **v0.1 — heuristic:** transparent hand-coded weighted sum. No training data needed.
- **v0.2+ (post-backtest harness) — logistic regression:** trained on backtest-derived labels (each historical tentative pivot in the point-in-time pivot timeline → confirmed/invalidated). Time-aware splits. Calibrated via Platt scaling. Promoted only if it beats heuristic on a held-out window.
- **vN if needed — gradient-boosted trees:** only if logistic plateaus below the bar.

The LLM never computes the probability — it only narrates the number. Probabilities must be reproducible bit-for-bit for backtest stability.

## 6. LLM contract

The LLM is a judge inside the engine's candidate set, not a primary detector.

**Two paths:**

- **Scan path (v0.3+):** Haiku 4.5, text-only, `temperature=0`. Inputs: top-5 engine-ranked candidates as structured text + key metrics. Output: probability vector + 1-line per-count narrative + `engine_missed` flag. Cost target: pennies per ticker per day.
- **Deep-dive path (v0.1):** Sonnet 4.6, text-only, `temperature=0`. Top-10 candidates + scenario branches. Output: 3-paragraph narrative for top-2; 1-paragraph for alternates; invalidation levels; fib targets; multi-TF commentary; "what would change my mind" criteria.
- **Optional vision verify (deep-dive only):** single-shot Sonnet 4.6 vision on a rendered chart PNG, on-demand. Verifies label placement; flags discrepancies. Result is a verification badge.

**Determinism for backtest:**

- `temperature=0`, pinned model ID (e.g., `claude-haiku-4-5-20251001`)
- Cache all LLM responses by `(model_id, prompt_hash)` in SQLite
- Backtest CLI accepts `--llm-mode {live, cached, disabled}`:
  - `live` — call API, cache results
  - `cached` — cache-only; fail on miss (used for paper-reproducible runs)
  - `disabled` — skip LLM stage; α treated as 1.0 (engine-only baseline)
- The `--llm-mode disabled` baseline run is **mandatory**: it shows whether the LLM is adding value or just adding cost.

**Prompt caching:**

The system prompt (template registry summary, JSON schema for candidate input, output format) is static across all calls. A cache breakpoint after the static block saves 80%+ of input tokens on the scan path. Implemented per the `claude-api` skill at code time.

**Final ranking blend:**

```
final_score = coherence_multiplier(coh) × (α × engine_score + (1 - α) × llm_prob)
```

`α ∈ [0, 1]` set by backtest. Initial default `α = 0.6` (engine-dominant). Blend tuned per regime if necessary.

## 7. Multi-timeframe coherence

Three-way: weekly + daily + 4-hour. Coherence acts as a confidence multiplier on the final ranking, never as a hard veto (right-edge timing lag would create false negatives).

**Independent counts per interval:**

```python
weekly_counts  = engine.run(ticker, interval="1wk")
daily_counts   = engine.run(ticker, interval="1d")
hourly4_counts = engine.run(ticker, interval="4h")  # resampled from 1h
```

**Pairwise coherence:**

```python
def coherence_score(a: WaveCount, b: WaveCount) -> float:
    score = 0.0
    if pattern_family(a) == pattern_family(b):                    score += 0.4
    if a_current_nests_into_b_current(a, b):                      score += 0.3
    if predicted_next_direction(a) == predicted_next_direction(b):score += 0.2
    if abs(latest_high_degree_pivot_drift(a, b)) <= 5:            score += 0.1
    return score

coh_3 = 0.5 * coh_wd + 0.35 * coh_d4 + 0.15 * coh_w4
mult = 0.5 + 0.7 * coh_3                                          # range [0.5, 1.2]
```

**Insufficient-history fallback:** if 4h history < threshold (recent IPOs, far-back backtest bars), drop to the 2-way `coh_wd` and surface a `weekly_history_insufficient` or `4h_history_insufficient` badge.

**Backtest scope caveat:** yfinance caps `interval="1h"` history at ~730 days. Two backtest scopes published:

- **Long-history (daily + weekly only):** ~10 years.
- **Recent-window (daily + weekly + 4h):** ~2 years.

Reporting both lets us empirically validate whether 4h adds accuracy or is feature creep.

## 8. Backtest harness

The single most consequential engineering deliverable. If the harness leaks future information, every accuracy claim is fiction.

**Three layers, walk-forward:**

- **Pivot layer (v0.2):** confirmation hit rate per degree, Brier score, calibration error.
- **Count layer (v0.2):** top-1 / top-3 direction hit rate over forward N bars; per-coherence-bucket accuracy; per-regime-year breakdown.
- **Trade layer (v0.5):** simulated entries/exits per `TradeRules`; hit rate, avg R, expectancy, profit factor, max drawdown, MFE/MAE.

**Cadence:** walk-forward daily (sub-sampled if compute requires), one re-fit/re-calibrate of any learned model per window.

**Lookahead guards:** `PointInTimeView(bars, as_of)` hard-asserts no input has `timestamp > as_of`. Synthetic-leak unit tests plant violations and verify the assertion fires. Every feature-extraction function takes `as_of` and goes through the wrapper.

**Universe:** curated 50 tickers in `data/universe_curated.txt`, spanning sectors (tech, defensive, cyclical, financial, energy, biotech), vol regimes, market caps, and IPO ages. User can supply their own list via `--universe`. For S&P 500 benchmark claims (future), use `data/sp500_membership.csv` with `date_added` / `date_removed` for survivorship correction.

**Compute envelope (rough):**

- 50 tickers × ~1500 trading days × 5y lookback × walk-forward = ~7,500 evaluations per layer
- Per evaluation: pivot detect (5 degrees) + count enumerate + LLM call ≈ 200ms + LLM latency
- First run cold cache: ~1-2 hours wall-clock, ~$5-15 in Haiku spend on the count layer
- Subsequent runs hit the LLM cache: ~20 minutes

**Metric output (sample shape):**

```
PIVOT LAYER
  per-degree confirmation hit rate:  d0=0.78  d1=0.71  d2=0.65  d3=0.59  d4=0.52
  Brier score:                       0.18
  reliability calibration error:     0.04 (10-bin)

COUNT LAYER
  top-1 direction hit rate (20 bars):  0.58 ± 0.02
  top-3 direction hit rate:            0.74 ± 0.02
  by coherence bucket:
    full agreement (>0.7):  0.71 (12% of cases)
    partial (0.4-0.7):      0.55 (54%)
    disagreement (<0.4):    0.39 (34%)
  by regime year:
    2018: 0.59  2019: 0.61  2020: 0.51  2021: 0.63  2022: 0.49  2023: 0.60  2024: 0.62

TRADE LAYER (v0.5)
  hit rate:        0.55
  avg R:           0.31
  expectancy:      0.17 R
  profit factor:   1.42
  max drawdown:   -0.18 R
```

The "by regime year" row is the most important diagnostic — it reveals whether the system survives 2020-style vol spikes and 2022-style trending bears.

## 9. Trade-signal contract

A directional bet derived from a count's structural levels.

```python
class TradeSignal(BaseModel):
    ticker: str
    as_of: date
    direction: Literal["long", "short"]
    entry_price: Decimal
    stop_price: Decimal                          # = count's invalidation level
    target_price: Decimal                        # = count target OR fib extension fallback
    count_id: str
    count_pattern: str                           # "impulse", "ending_diagonal", ...
    wave_label: str                              # "5", "3", "C", ...
    confidence: float                            # final blended score from §6 + §7
    risk_per_share: Decimal
    reward_per_share: Decimal
    rr: float
    expected_bars_to_target: int                 # degree-derived from historical median × 1.5
    max_holding_bars: int
    breakeven_trigger: Decimal | None            # move stop to breakeven at +1R

class TradeRules(BaseModel):
    direction_modes: list[Literal["long", "short"]] = ["long", "short"]
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
```

**Defaults rationale:**

- **Bidirectional** — EW is structurally bidirectional; long-only halves the signal set.
- **Immediate entry** — the count IS the signal; confirmation gates dilute the timing edge. Validated by ablation in v0.5.
- **Count-invalidation stop** — semantically clean; structural kill switch matches the analytical claim.
- **Count target with fib fallback** — counts that pin a price use it; counts that don't fall back to standard fib extensions.
- **Degree-derived time-stop** — a degree-3 wave takes weeks; a degree-1 wave takes days; one-size-fits-all is wrong.

**Ablation grid (v0.5):**

```
entry_trigger:  [immediate, confirmation]
stop_source:    [count_invalidation, atr_multiple_2]
target_source:  [count_target, fib_extension, fixed_2R]
min_confidence: [0.35, 0.45, 0.55, 0.65]
```

Per cell: hit rate, avg R, expectancy, profit factor, sample size. Winners ship as defaults; full table published in docs.

## 10. Feedback loop and continuous improvement

EW is interpretive enough that what actually predicts outcomes is empirical, not theoretical. Without a feedback loop, launch-day accuracy is forever accuracy.

**Three feedback channels with different weights:**

| Channel | Local | Pooled (opt-in) | Weight in shared corpus |
|---|---|---|---|
| Auto trade outcome (broker CSV import) | ✅ | ✅ (anonymized) | 1.0 |
| Manual trade outcome (user-entered) | ✅ | ✅ (anonymized) | 0.6 |
| Subjective thumbs on a count | ✅ | ❌ never | 0 (local personalization only) |
| Manual count override | ✅ | ❌ never | 0 (used as adversarial probe locally) |

**Pooled corpus contract:** `realized_R`, `exit_reason`, `bars_held`, ticker, count pattern, wave label, direction, confidence, coherence at signal time, **stop/target distances as ATR ratios** (never absolute prices), anonymous rotatable install hash. Never sent: user ID, broker, position size, dollar P&L, free-text notes, other holdings, subjective ratings.

**Personalization layer (v0.4+):** local-only score adjustment based on this user's hit rate by `(pattern, direction, sector)` once user has ≥30 outcomes.

**Versioned models (v1.3+):** every retrain produces a versioned artifact. Promotion gated by Q8 backtest beat-rate (e.g., +2% hit rate on held-out window). Old versions archived; users can pin or rollback. Audit log records every promotion.

**Online learning is rejected** — would create a moving target that breaks backtest reproducibility. Batch monthly retrains only.

**Withdrawal:** single CLI command `wave-alpha contributions withdraw` deletes all records bearing the install hash from the server. Idempotent.

## 11. v0.1 UI

Single-ticker deep-dive page only. No watchlist, no scanner, no backtest UI in v0.1.

**Entry point:** `wave-alpha deep-dive AAPL` opens the default browser to `http://localhost:8765/deepdive/AAPL`.

**Page composition (single column, stacked sections, HTMX fragments):**

1. **Toolbar** — ticker selector, period (1y/2y/5y/lifetime), degree slider, data freshness indicator.
2. **Chart** (`/deepdive/{ticker}/chart`) — Lightweight Chart with EW overlay; solid markers/segments for confirmed pivots, dashed for tentative; fib retracement and extension lines; "↗ Open in TradingView" deep-link.
3. **Multi-TF coherence row** (`/deepdive/{ticker}/coherence`) — `W ✓ D ✓ 4h ⚠` tri-badge, score, one-paragraph LLM explanation of agreement/disagreement.
4. **Counts list** (`/deepdive/{ticker}/counts`) — top-3 candidates with probability, soft-violation notes, sub-count validity, inline LLM narrative per count.
5. **Trade plan card** (`/deepdive/{ticker}/plan?count_id=N`) — auto-derived from top-1; "show alt #2's plan" toggle; entry, stop, target, R:R, expected bars, max hold, breakeven trigger.
6. **Right-edge scenarios** (`/deepdive/{ticker}/scenarios`) — 1-3 viable counts at the right edge with probabilities and explicit invalidation levels.
7. **Vision verify button** (`/deepdive/{ticker}/verify?count_id=N`) — opt-in single-shot Sonnet vision call on a rendered chart PNG; result shown as a verification badge on the targeted count.
8. **Disclaimer** — "Informational only. Confirm thesis. Not investment advice."

**Lazy load order:** chart and coherence row paint immediately (sub-second). Counts list streams in (engine + LLM Sonnet text call, a few seconds). Trade plan and scenarios render once counts arrive.

**Settings page** (`/settings`):
- Anthropic API key entry (stored in `~/.wave_alpha/config.yaml`)
- Model selection (Haiku / Sonnet)
- Default period and degree
- Toggle for "track outcomes locally" (on by default)
- Pool contribution toggle (off by default; v1.2+ is when this becomes functional)

## 12. Roadmap and phasing

| Version | Scope | Validates |
|---|---|---|
| **v0.1** | Single-ticker deep-dive web UI; full pipeline (engine + LLM + 3-way coherence + trade plan + vision verify); local OHLCV cache; LLM response cache | Core engine + LLM contract on one ticker |
| **v0.2** | Backtest harness for pivot + count layers; `--llm-mode disabled` baseline mandatory; report on curated 50-ticker universe over 5y; promotion gate for right-edge logistic model | Honest accuracy claim |
| **v0.3** | Watchlist module (manual + CSV/TXT import; TradingView TXT export accepted); scan dashboard; nightly batch scan with cache invalidation on new bar close | Headline scanner UX |
| **v0.4** | Local trade-outcome capture (manual + broker CSV import in wash-alpha pattern); personal stats panel; manual count override; personalization layer kicks in at ≥30 outcomes | Continuous improvement loop on-device |
| **v0.5** | Backtest trade-sim layer; ablation grid sweep; sensitivity table published in docs | Trade-derived metrics; defaults validation |
| **v1.0** | Polish, README, screenshots, `pipx install wave-alpha` (BYO key); docs site; migration story documented | Public OSS launch |
| **v1.1** | Bidirectional alerts + email/local-notify on scan completion; option-aware Wave-5-top short via puts (link to `wash-alpha`-compatible CSV format) | Live actionable workflow |
| **v1.2** | Pooling endpoint (Cloudflare Workers + R2); shared model trained when N≥1000 outcomes from ≥100 distinct installs; data-contract preview command | Community model |
| **v1.3+** | Model registry UI; user-selectable model versions; automatic promotion via beat-rate gate; rollback workflow | Versioned model lifecycle |

**Out-of-scope through v1.3:** true intraday (sub-1h), options pricing/Greeks, broker integration beyond CSV, mobile app, multi-tenant SaaS, paid intraday data integration (left as `OHLCVProvider` ABC for users to plug in).

## 13. Privacy contract

`wave-alpha` follows `wash-alpha`'s data privacy DNA: local-first, no telemetry, explicit opt-in for any external data flow.

**Always local:**
- All OHLCV bars (cached in SQLite)
- All counts, trade signals, P&L, user notes
- All subjective feedback (thumbs, manual count overrides)
- Account labels, broker info, position sizes (when imported)

**Sent to Anthropic API (always, when LLM enabled):**
- Ticker symbols
- Pivot timestamps and prices (relative or absolute)
- Candidate count structures (pattern, wave labels, scores)
- Multi-TF coherence metrics

**Sent to Yahoo Finance (always, for OHLCV fetch):**
- Ticker symbols only

**Sent to community pool (opt-in, default off, v1.2+):**
- See §10 contract table above; PII-free, prices as ATR ratios, anonymous rotatable hash

**Disclaimer policy:** every page and CLI output ends with:

> ⚠ Informational only. Confirm thesis. Not investment advice.

Same flavor as `wash-alpha`'s tax disclaimer. Not skippable.

## 14. Open questions and deferred decisions

Decisions intentionally deferred — to be resolved when the relevant version is in scope, not before:

| # | Question | Defer to | Reason |
|---|---|---|---|
| O1 | Watchlist source UX (manual / CSV / TV-TXT import) | v0.3 | Not a v0.1 dependency |
| O2 | Confirmation-entry vs immediate-entry as default | v0.5 ablation | Wait for empirical evidence |
| O3 | Per-degree vs shared right-edge confirmation model | v0.2 backtest | Wait for evidence of systematic differences |
| O4 | Initial LLM blend coefficient `α` | v0.2 backtest | Tune from data, not guess |
| O5 | Coherence multiplier shape (linear vs sigmoid) | v0.2 backtest | Validate on held-out window |
| O6 | Pooling endpoint hosting (CF Workers vs Fly.io vs self-host) | v1.2 | Not needed until pooling is real |
| O7 | Intraday data provider plug-in (Polygon / Tiingo / IBKR) | post-v1.0 | yfinance is enough for v1; users can plug in later |
| O8 | Pattern coverage expansion (running flats, X-waves, double zigzags) | v0.2+ | Add as backtest reveals coverage gaps |
| O9 | Time-symmetry / Fibonacci time projections as additional features | post-v1.0 | Lower-leverage accuracy improvement |
| O10 | Channeling lines (1-3 / 2 parallel) as a soft constraint | post-v1.0 | Same |

---

**Status: brainstorm complete; design approved. Next step: implementation plan via `superpowers:writing-plans`.**
