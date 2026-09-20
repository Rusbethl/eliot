# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Tooling

- Python 3.11+, managed with `uv`. Always invoke commands via `uv run …` — do not activate the venv manually.
- Build backend: `hatchling`. Linter/formatter: `ruff` (line-length 100, rules `E F I N UP B SIM RUF`, `E501` ignored).
- The web UI dependencies (`fastapi`, `uvicorn`, `jinja2`) live in the `[ui]` extra. `uv sync --extra dev` for engine work, `uv sync --extra ui` (or both) for UI work.

## Common commands

```bash
uv sync --extra dev                              # install dev deps
uv run pytest                                    # full test suite
uv run pytest tests/elliott -k impulse           # one folder + name filter
uv run pytest tests/test_pipeline.py::test_x -x  # single test, fail fast
uv run ruff check .                              # lint
uv run ruff format .                             # format

uv run wave-alpha analyze AAPL --as-of 2024-12-31 --json
uv run wave-alpha scan --symbols AAPL,MSFT --as-of 2026-05-01
uv run wave-alpha ui                             # http://127.0.0.1:8765
uv run wave-alpha backtest count --universe data/universe_curated.txt \
    --start 2020-01-01 --end 2024-12-31 --out reports/count.md
uv run wave-alpha train right-edge --end 2024-12-31 [--with-fib-features] [--write]
```

`pytest.ini_options` sets `--strict-markers`. If you add a custom marker, register it in `pyproject.toml`.

## Architecture

Single Python package `wave_alpha` under `src/`. The core is a deterministic Elliott Wave engine; the LLM is a strictly optional reranker, not a counter. Read `docs/superpowers/specs/2026-05-03-wave-alpha-design.md` for the full design; spec sections (`§5`, `§6`, …) are referenced throughout the code.

### Analysis pipeline (`pipeline.py:run_analysis`)

For each request, fetch bars at three intervals (weekly / daily / 4h) and run the same enumeration on each:

```
OHLCV bars
  → PointInTimeView(bars, as_of)              # lookahead-safety boundary
  → detect_multi_degree (ATR-scaled zigzag)   # pivots at degrees 0..N
  → enumerate_counts(pivots, degree=2)         # template fit + fib + recency → top-K WaveCount
  → three_way coherence(W, D, 4h)              # multiplier on engine_score
  → [optional] LLM scan rerank                 # blended via blend_scores(alpha, …)
  → derive_signal(top counts)                  # entry / stop / target / RR
```

`pipeline.run_analysis` always produces an `AnalyzeResult` even with empty bar series — every consumer (CLI, web, scan) tolerates partial results.

### Lookahead is enforced, not advisory

`data/point_in_time.py:PointInTimeView` is the **single source of truth** for as-of slicing. It hard-asserts ascending bar order and refuses to return any bar with `timestamp > as_of` via index access or `latest()`. The backtest harness, the live pipeline, and the right-edge feature extractor all flow through it. There is a synthetic-leak unit test (`tests/backtest/test_lookahead_guard.py`) that plants a violation to verify the assertion fires — when adding new fetch sites or backtest paths, route them through `PointInTimeView` and keep that test green.

### Wave templates are YAML, not code

Pattern definitions live in `src/wave_alpha/elliott/templates_data/*.yaml` (`impulse`, `zigzag`, `flat`, `expanded_flat`, `contracting_triangle`, `ending_diagonal`). Each template lists `waves` and `constraints` with `severity: hard | soft`. The `validator` rejects counts with hard violations; soft violations subtract from `engine_score`. To add a pattern: drop a YAML file in `templates_data/` (it ships in the wheel automatically via `pyproject.toml`'s `[tool.hatch.build.targets.wheel] packages = ["src/wave_alpha"]`, which includes every file under that tree) — no code change needed unless new constraint operators are introduced.

`enumerator.py` deduplicates multiple windows of the same template down to the highest-scoring instance before the global top-K cut, so one template family cannot crowd out alternates.

### Right-edge confirmation models (`right_edge/`)

Three implementations of the `ConfirmationModel` Protocol satisfy `predict_proba(features, *, degree)`:

- `heuristic` — hand-coded weighted sum, no artifact.
- `calibrated_heuristic` — heuristic + per-degree Platt scalers.
- `logistic` — full logistic regression with feature-order-stable JSON artifact.

`right_edge/loader.py:load_right_edge_model` selects via `RightEdgeConfig.kind`; `"auto"` prefers `logistic > calibrated_heuristic > heuristic` and silently falls back when an artifact is missing or malformed. **Train artifacts via `wave-alpha train …`; do not edit JSON artifacts by hand.** Promotion gating (paired bootstrap on Brier, per-degree ECE) lives in `right_edge/promotion.py:evaluate_promotion`; only `PROMOTE` / `PROMOTE_CALIBRATION` verdicts write without `--force`.

The fib structural features are gated by `--with-fib-features` at training time and the corresponding flag in the artifact / config — keep `feature_order` in the artifact in lockstep with `RightEdgeFeatures`.

**Outcome-driven retraining.** `wave-alpha train right-edge --use-outcomes` blends realized-outcome rows from `history.sqlite` into the final logistic + Platt refit. Features are **recomputed at training time** from `cache.sqlite` bars — no per-row storage is added to `history.sqlite`. Walk-forward stays synthetic-only as an OOS estimator; outcomes enter only at the final refit. The per-degree sample-weight ramp (default `n_target_per_degree=100`, configurable via `--outcome-weight-target` or `right_edge.outcome_training` in `config.yaml`) scales outcome influence from ~0 to synthetic-parity smoothly as the labeled dataset grows.

**`history.sqlite` retention.** Auto-prune runs inside `record_snapshot` after the resolver pass, deleting rows for the current ticker older than `history.retention_days` (default 730, configurable; 0 disables). Manual companions: `wave-alpha history stats` for inspection and `wave-alpha history prune --older-than 365d [--ticker T] [--dry-run]` for ad-hoc cleanup.

### LLM contract (`llm/`)

Three modes via `LLMMode` enum:
- `disabled` (default, mandatory for backtest comparisons) — no API key needed, deterministic.
- `live` — real `AnthropicClient`, writes to `~/.wave_alpha/llm_cache.sqlite`.
- `cached` — uses `LLMResponseCache`; if no API key, falls back to `CacheOnlyClient` so reproducible runs work keyless. Raises on cache miss.

Final ranking: `final = coh_multiplier × (alpha × engine_score + (1 - alpha) × llm_prob)`. **Privacy contract:** the only payload sent upstream is ticker symbol, pivot timestamps/prices, candidate count structures, and coherence metrics — no bars, no API key in payload. Preserve this when modifying `llm/prompts.py` or `llm/candidates.py:serialize_candidate_set`.

### Provider stack

`CachedProvider(upstream=YahooProvider(), db_path=~/.wave_alpha/cache.sqlite)` is the production provider. The cache auto-refetches when its tail is more than one calendar day older than the requested `end` date — daily cron runs against today's date will pull fresh bars. In tests, substitute a fake `OHLCVProvider` directly; do not hit yfinance.

### Persistence locations

All user-facing state lives under `~/.wave_alpha/`: `config.yaml`, `cache.sqlite` (OHLCV), `llm_cache.sqlite` (LLM responses), `watchlist.json`, `scans/<scan_id>.json`. Tests use the `_config_path()` / `_watchlist_path()` / `_scan_dir()` indirections in `cli/app.py` to monkeypatch these locations — keep that pattern when adding new persisted state.

### CLI structure (`cli/app.py`)

Single `typer.Typer` app with sub-apps: `config`, `backtest` (count, pivot), `watchlist` (add/rm/ls/import), `train` (right-edge, calibrated-heuristic). Top-level commands: `analyze`, `scan`, `ui`, `version`. Sub-app modules import lazily inside command bodies to keep `--help` fast and avoid loading uvicorn/numpy on unrelated invocations — preserve this when adding new commands.

### Web UI (`web/`)

FastAPI app constructed by `web/app.py:create_app` (factory used by `uv run wave-alpha ui`). Static assets are vendored under `web/static/` (TradingView Lightweight Charts is MIT, bundled — **do not introduce CDN runtime dependencies**). Templates and static assets ship in the wheel automatically because `pyproject.toml`'s `[tool.hatch.build.targets.wheel] packages = ["src/wave_alpha"]` includes every file under that tree (a previous `force-include` block was dropped in `c8f27d6` to avoid PyPI's "duplicate filename" rejection).

## Domain conventions

- All prices use `decimal.Decimal`; never `float`. `Bar`, `PivotEvent`, `WaveCount`, `TradeSignal` are frozen Pydantic v2 models.
- Bar `timestamp` is a `datetime.date` (no intraday timestamps even for 4h — the design treats 4h as a daily-aggregated supplementary timeframe).
- `Interval` enum string values (`"1d"`, `"1wk"`, `"4h"`) double as cache keys and yfinance interval strings — changing them invalidates the cache.
- `TradeRules.target_source="fixed_R"` (with `target_R`) is implemented and unit-tested but underperforms `fib_extension` on the curated 50-ticker universe (Δ = −0.013R, MARGINAL — see `docs/superpowers/specs/2026-05-08-target-r-sweep-design.md` §6). Keep the `fib_extension` default; treat `fixed_R` as a documented opt-in for users who prefer fixed-R risk management semantics.

## Plans and specs

Implementation plans (`docs/superpowers/plans/`) and design specs (`docs/superpowers/specs/`) are dated and tied to git commits via the `superpowers:writing-plans` / `superpowers:executing-plans` workflow. When making non-trivial changes to engine internals, the LLM contract, the backtest harness, or right-edge models, write/update the corresponding plan first rather than improvising.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **wave-alpha** (5992 symbols, 8169 relationships, 53 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/wave-alpha/context` | Codebase overview, check index freshness |
| `gitnexus://repo/wave-alpha/clusters` | All functional areas |
| `gitnexus://repo/wave-alpha/processes` | All execution flows |
| `gitnexus://repo/wave-alpha/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
