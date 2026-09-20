# PyPI Public Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `wave-alpha` publishable on PyPI as a polished public 0.6.0 release: legal/packaging blockers fixed, dependency surface honest, CI/CD wired for trusted publishing, and the "local-first / no-telemetry / optional-LLM" claims in the README backed by tests and config.

**Architecture:** All changes are additive or config-only. No engine code, no template changes, no LLM contract changes. Three buckets:
1. **Packaging hygiene** — LICENSE + pyproject metadata + extras restructure so `pip install wave-alpha` is lean.
2. **Release docs + machinery** — CHANGELOG, SECURITY, CONTRIBUTING, DISCLAIMER, RELEASING, GitHub Actions for test + Trusted-Publisher upload.
3. **Verification tests** — privacy contract for LLM payload, lean-install smoke test that confirms the engine works without `anthropic`.

**Tech Stack:** Existing — `hatchling`, `uv`, `pytest`, `ruff`. New — GitHub Actions, PyPI Trusted Publisher (OIDC, no API tokens).

---

## Out of scope (explicit deferrals)

These were considered and pushed to follow-up plans to keep this one shippable in a day:

- **Stooq / second OHLCV provider.** Real engineering work — provider abstraction, integration with `CachedProvider`, fixtures, decision on rate-limit fallback policy. Needs its own spec. Yahoo's rate limits will not actually break a 0.6.0 launch — most users analyze a watchlist, not the full S&P.
- **Examples notebook / `examples/quickstart.py`.** Worth doing but small enough to ship in 0.6.1 once we see what new users actually trip over. Would need brainstorming on what flow to showcase.
- **First-run analytics opt-out prompt.** We have *no* telemetry; the disclaimer is sufficient. Adding a prompt that says "we collect nothing" is anti-pattern.
- **README marketing polish for launch (HN/Reddit copy, screenshots-with-callouts, demo GIF).** Independent track — a marketing pass, not a release-readiness task.

---

## File structure

```
LICENSE                                  # CREATE — MIT, Copyright Alex Chen 2026
CHANGELOG.md                             # CREATE — Keep-a-Changelog format, seeded from git log
SECURITY.md                              # CREATE — vuln-reporting policy
CONTRIBUTING.md                          # CREATE — dev loop (uv + pytest + ruff)
DISCLAIMER.md                            # CREATE — "not financial advice" + risk warning
RELEASING.md                             # CREATE — version bump → tag → CI publishes

pyproject.toml                           # MODIFY — license SPDX, classifiers, urls, keywords;
                                         #          move anthropic to [llm] extra; bump 0.5.11 → 0.6.0

src/wave_alpha/__init__.py               # MODIFY — bump to 0.6.0
src/wave_alpha/py.typed                  # CREATE — empty marker file (PEP 561)
src/wave_alpha/llm/client.py             # MODIFY — friendly ImportError when [llm] extra missing
src/wave_alpha/cli/app.py                # MODIFY — first-run disclaimer banner

tests/test_packaging.py                  # CREATE — wheel includes LICENSE, py.typed, templates
tests/test_lean_install.py               # CREATE — engine import + analyze runs without anthropic
tests/llm/test_privacy_contract.py       # CREATE — payload contains no OHLCV, no api_key
tests/test_disclaimer_banner.py          # CREATE — banner shown once, not on subsequent runs

.github/workflows/test.yml               # CREATE — ruff + pytest matrix on push/PR
.github/workflows/publish.yml            # CREATE — tag → build → PyPI via OIDC

README.md                                # MODIFY — add Disclaimer link, [llm] install hint, License section
```

---

## Phase A — Legal & packaging blockers

These three tasks are the actual gate to publishing. After Phase A you *could* run `uv build && uv publish` and have a legitimate (if undecorated) PyPI page.

### Task 1: Add MIT LICENSE file

**Background.** The repo has no LICENSE. Without one, the code is "all rights reserved" by default — hostile for a public release. MIT is permissive, well-understood, and compatible with the bundled MIT-licensed TradingView Lightweight Charts in `web/static/`.

**Files:**
- Create: `LICENSE`

- [ ] **Step 1: Write LICENSE**

```
MIT License

Copyright (c) 2026 Alex Chen

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 2: Verify the file is at repo root**

Run: `ls -la LICENSE && head -1 LICENSE`
Expected: file exists, first line `MIT License`.

- [ ] **Step 3: Commit**

```bash
git add LICENSE
git commit -m "chore: add MIT LICENSE"
```

---

### Task 2: Fill in pyproject metadata for PyPI

**Background.** The current `pyproject.toml` has the bare minimum (name, version, deps). PyPI's project page would render with no Homepage, no Source link, no classifiers, no keywords — visually a flag for "abandonware." Use PEP 639 SPDX form for the license (modern standard; do *not* also use the `License :: OSI Approved :: MIT` classifier — it is deprecated under PEP 639 and newer hatchling will reject the combination).

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add license, keywords, urls, classifiers**

Edit the `[project]` table to insert these fields *after* `authors` and *before* `dependencies`:

```toml
license = "MIT"
license-files = ["LICENSE"]
keywords = [
  "elliott-wave",
  "trading",
  "swing-trading",
  "technical-analysis",
  "backtest",
  "stocks",
  "finance",
]
classifiers = [
  "Development Status :: 4 - Beta",
  "Intended Audience :: Financial and Insurance Industry",
  "Intended Audience :: Developers",
  "Operating System :: OS Independent",
  "Programming Language :: Python :: 3",
  "Programming Language :: Python :: 3.11",
  "Programming Language :: Python :: 3.12",
  "Programming Language :: Python :: 3.13",
  "Topic :: Office/Business :: Financial :: Investment",
  "Topic :: Scientific/Engineering :: Information Analysis",
  "Typing :: Typed",
]

[project.urls]
Homepage = "https://github.com/chen-star/wave-alpha"
Repository = "https://github.com/chen-star/wave-alpha"
Issues = "https://github.com/chen-star/wave-alpha/issues"
Changelog = "https://github.com/chen-star/wave-alpha/blob/main/CHANGELOG.md"
```

- [ ] **Step 2: Verify pyproject parses and metadata is exposed**

Run: `uv build --wheel 2>&1 | tail -3 && uv run python -c "import importlib.metadata as m; md = m.metadata('wave-alpha'); print(md['License-Expression'], '|', md.get_all('Classifier')[:3], '|', md.get_all('Project-URL')[:2])"`
Expected: build succeeds; output shows `MIT | [...]  | [...]`.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore(packaging): add license, classifiers, keywords, project URLs"
```

---

### Task 3: Move `anthropic` into `[llm]` optional extra

**Background.** `LLMMode.disabled` is the documented default and the only allowed mode for backtests, but `anthropic` is currently a hard runtime dep. Every install pulls ~6MB of LLM SDK + httpx upgrades that 90% of users do not need. The lone import in `src/wave_alpha/llm/client.py:74` is already lazy (inside `_client()`), so the code change is small. The `cached` mode falls back to `CacheOnlyClient` per `CLAUDE.md` and never imports `anthropic`, so it remains usable on a lean install.

**Files:**
- Modify: `pyproject.toml` (extras table)
- Modify: `src/wave_alpha/llm/client.py:71-76`
- Modify: `README.md` (Installation section)

- [ ] **Step 1: Move dependency**

In `pyproject.toml`:
1. Remove `"anthropic>=0.39",` from the top-level `dependencies` array.
2. Add a new entry under `[project.optional-dependencies]`:

```toml
llm = [
  "anthropic>=0.39",
]
```

- [ ] **Step 2: Friendly ImportError when extra missing**

Replace lines 71-76 of `src/wave_alpha/llm/client.py` (the `_client` body) with:

```python
    def _client(self) -> Any:
        if self._sdk_factory is not None:
            return self._sdk_factory(api_key=self.api_key)
        try:
            import anthropic
        except ImportError as e:
            raise ImportError(
                "live LLM mode requires the 'llm' extra; install with "
                "`pip install wave-alpha[llm]` (or `uv pip install wave-alpha[llm]`)"
            ) from e
        return anthropic.Anthropic(api_key=self.api_key)
```

- [ ] **Step 3: Update README install commands**

In `README.md`, find the Installation section and replace any `pip install wave-alpha` line with two variants:

```markdown
# Lean install (deterministic engine + CLI + web UI):
uv pip install wave-alpha[ui]

# With optional LLM reranker:
uv pip install wave-alpha[ui,llm]
```

If the README installs only show `wave-alpha[ui]` without an LLM mention, add a short note: "The LLM reranker is opt-in — install the `llm` extra to enable `--llm-mode live` or `cached`."

- [ ] **Step 4: Verify with the full extras still installed**

Run: `uv sync --extra dev --extra ui --extra llm && uv run pytest -x -q`
Expected: all tests still pass (this is the regression check before we test the lean install in Task 4).

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/wave_alpha/llm/client.py README.md
git commit -m "feat(packaging): move anthropic into [llm] optional extra"
```

---

### Task 4: Lean-install smoke test

**Background.** Task 3 split the dep, but we need to *prove* `import wave_alpha` and a real `analyze` call work without `anthropic` installed — otherwise a future stray top-level import slips through. This test runs in a temp venv so it does not pollute the dev environment.

**Files:**
- Create: `tests/test_lean_install.py`

- [ ] **Step 1: Write the test**

```python
"""Verify wave_alpha works without the [llm] extra installed.

Builds a wheel from the working tree, installs it into an isolated venv with
*only* the [ui] extra (no anthropic), and runs a non-LLM analysis. This
guards the contract that the deterministic engine never depends on the LLM.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.slow
def test_engine_runs_without_anthropic(tmp_path: Path) -> None:
    if shutil.which("uv") is None:
        pytest.skip("uv not available")

    # Build wheel into tmp_path
    dist = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist)],
        cwd=REPO_ROOT,
        check=True,
    )
    wheels = list(dist.glob("wave_alpha-*.whl"))
    assert len(wheels) == 1, f"expected one wheel, got {wheels}"

    # Fresh venv with ONLY base deps (no [llm] extra)
    venv = tmp_path / "venv"
    subprocess.run(["uv", "venv", str(venv)], check=True)
    py = venv / "bin" / "python"
    subprocess.run(
        ["uv", "pip", "install", "--python", str(py), str(wheels[0])],
        check=True,
    )

    # anthropic must NOT be importable
    proc = subprocess.run(
        [str(py), "-c", "import anthropic"],
        capture_output=True,
    )
    assert proc.returncode != 0, "anthropic should not be installed in lean venv"

    # wave_alpha must be importable and engine must work
    proc = subprocess.run(
        [
            str(py),
            "-c",
            "import wave_alpha; "
            "from wave_alpha.elliott.templates import load_templates; "
            "ts = load_templates(); "
            "assert len(ts) >= 6, f'expected >=6 templates, got {len(ts)}'; "
            "print('OK', len(ts))",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"engine import failed: {proc.stderr}"
    assert proc.stdout.startswith("OK"), proc.stdout
```

- [ ] **Step 2: Register the `slow` marker**

In `pyproject.toml` under `[tool.pytest.ini_options]`, change the `addopts` line and add markers:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"
markers = [
  "slow: tests that build wheels or hit subprocess (deselect with '-m \"not slow\"')",
]
```

- [ ] **Step 3: Run the test, watch it pass**

Run: `uv run pytest tests/test_lean_install.py -v`
Expected: PASS in ~10-30s (wheel build is the slow part).

- [ ] **Step 4: Confirm `not slow` deselects it**

Run: `uv run pytest -m "not slow" tests/test_lean_install.py -v`
Expected: 1 deselected.

- [ ] **Step 5: Commit**

```bash
git add tests/test_lean_install.py pyproject.toml
git commit -m "test(packaging): assert engine works without [llm] extra"
```

---

## Phase B — Release hygiene docs & tests

### Task 5: CHANGELOG.md seeded from git history

**Background.** Going from no changelog to a thorough one is impractical. Seed with a top "Unreleased" section, summarize the path 0.5.x → 0.6.0 from the merge commits, and adopt Keep-a-Changelog going forward.

**Files:**
- Create: `CHANGELOG.md`

- [ ] **Step 1: Gather recent commit summary**

Run: `git log --oneline --no-merges v0.5.0..HEAD 2>/dev/null || git log --oneline --no-merges -20`

Read the output and group by type (feat / fix / docs / refactor / chore). Use this to populate the 0.6.0 entry below.

- [ ] **Step 2: Write CHANGELOG.md**

```markdown
# Changelog

All notable changes to **wave-alpha** are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] — 2026-05-09 — Public release

### Added
- **MIT license** and full PyPI metadata (classifiers, project URLs, keywords).
- `[llm]` optional extra for the Anthropic SDK; default install no longer pulls it.
- `py.typed` marker so downstream type-checkers see the public API.
- First-run CLI disclaimer banner (shown once, dismissible by env var).
- Privacy-contract test asserting LLM payloads contain no OHLCV bars and no API key.
- GitHub Actions: `test.yml` (ruff + pytest matrix), `publish.yml` (PyPI Trusted Publisher / OIDC).
- `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`, `DISCLAIMER.md`, `RELEASING.md`.

### Changed
- `pip install wave-alpha` is now lean — install `[ui]` for the web app and `[llm]` for the LLM reranker.
- `LiveLLMClient` raises a friendly `ImportError` when the `[llm]` extra is missing.

### Notes
- No engine, template, or LLM-contract behavior changes in 0.6.0. This is a packaging and release-readiness release.

## [0.5.x] — Pre-public

The 0.5 series was developed privately. Highlights from the cumulative path to 0.6.0:

- **Engine & data:** multi-degree ATR-scaled pivot detection, six YAML wave templates with hard/soft constraints, three-way (W/D/4h) coherence multiplier, point-in-time view enforcing lookahead safety, cached OHLCV provider with staleness invalidation.
- **Right-edge models:** `heuristic`, `calibrated_heuristic`, and `logistic` confirmation models with paired-bootstrap promotion gating and per-degree ECE checks.
- **LLM contract:** `disabled` / `live` / `cached` modes; cache-only fallback for keyless reproducible runs; `final = coh × (α · engine + (1−α) · llm_prob)` ranking blend.
- **Backtest harness:** count-level and pivot-level harnesses with hard-enforced lookahead via `PointInTimeView`.
- **CLI & UI:** `analyze`, `scan`, `watchlist`, `train`, `backtest`, `ui` subcommands; FastAPI web UI with vendored TradingView Lightweight Charts (no CDN runtime).
- **History:** per-ticker snapshot store with stability badge.

[Unreleased]: https://github.com/chen-star/wave-alpha/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/chen-star/wave-alpha/releases/tag/v0.6.0
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: add CHANGELOG.md seeded for 0.6.0 public release"
```

---

### Task 6: SECURITY.md

**Background.** Public Python packages get random vulnerability reports filed as public issues. SECURITY.md sets a private channel and signals you take security seriously.

**Files:**
- Create: `SECURITY.md`

- [ ] **Step 1: Write SECURITY.md**

```markdown
# Security policy

## Supported versions

`wave-alpha` follows semantic versioning. Only the latest minor release receives security fixes.

| Version | Supported          |
| ------- | ------------------ |
| 0.6.x   | :white_check_mark: |
| < 0.6   | :x:                |

## Reporting a vulnerability

**Please do not file public GitHub issues for security vulnerabilities.**

Email **jxchen31@gmail.com** with:

- A description of the vulnerability and its impact.
- Steps to reproduce, ideally with a minimal proof-of-concept.
- Any suggested mitigation.

You should receive a response within 7 days. If the report is accepted, we will:

1. Acknowledge receipt and assign a tracking ID.
2. Develop and test a fix in a private branch.
3. Coordinate a release and public disclosure with the reporter.

## Scope

In-scope:

- The `wave_alpha` Python package and its CLI.
- The local FastAPI web UI shipped under `wave_alpha.web`.
- The `~/.wave_alpha/` SQLite caches and config files (write-path issues, injection, etc.).

Out-of-scope:

- Third-party dependencies (report upstream).
- Yahoo Finance / external data correctness.
- Trading losses or strategy performance — see [`DISCLAIMER.md`](DISCLAIMER.md).
```

- [ ] **Step 2: Commit**

```bash
git add SECURITY.md
git commit -m "docs: add SECURITY.md with private vulnerability reporting channel"
```

---

### Task 7: CONTRIBUTING.md

**Background.** Reduces the support burden of "how do I run tests" issues and signals the project accepts contributions.

**Files:**
- Create: `CONTRIBUTING.md`

- [ ] **Step 1: Write CONTRIBUTING.md**

```markdown
# Contributing to wave-alpha

Thanks for your interest! This is a small project — please open an issue to discuss
larger changes before sending a PR so we can avoid wasted work.

## Dev environment

`wave-alpha` uses [`uv`](https://docs.astral.sh/uv/) for dependency management. Install it
first, then:

```bash
git clone https://github.com/chen-star/wave-alpha.git
cd wave-alpha
uv sync --extra dev --extra ui --extra llm   # full toolchain
```

Always invoke commands via `uv run …`; do not activate the venv manually.

## Tests, lint, format

```bash
uv run pytest                  # full suite (skip slow tests with: -m "not slow")
uv run ruff check .            # lint
uv run ruff format .           # auto-format
```

CI runs `ruff check`, `ruff format --check`, and `pytest` on Python 3.11/3.12/3.13
across macOS and Linux. PRs must be green.

## Project layout

- `src/wave_alpha/elliott/` — pivot detection, templates, validator, enumerator.
- `src/wave_alpha/right_edge/` — confirmation models (heuristic, calibrated, logistic).
- `src/wave_alpha/llm/` — optional LLM reranker (gated behind the `[llm]` extra).
- `src/wave_alpha/data/` — OHLCV providers, cache, point-in-time view.
- `src/wave_alpha/backtest/` — count-level and pivot-level harnesses.
- `src/wave_alpha/cli/` — Typer app.
- `src/wave_alpha/web/` — FastAPI app + Jinja templates + vendored static assets.
- `docs/superpowers/specs/` — design specs.
- `docs/superpowers/plans/` — dated implementation plans.

See `CLAUDE.md` for deeper architectural conventions.

## Pull request checklist

- [ ] Tests pass (`uv run pytest`).
- [ ] Lint clean (`uv run ruff check .`).
- [ ] Formatted (`uv run ruff format .`).
- [ ] CHANGELOG.md updated under `## [Unreleased]`.
- [ ] If adding a wave template: dropped a YAML in `src/wave_alpha/elliott/templates_data/`,
      no code change needed (validator picks it up).
- [ ] If touching the LLM payload: privacy contract test in
      `tests/llm/test_privacy_contract.py` still passes.

## Conventions

- All prices use `decimal.Decimal`; never `float`.
- Engine output is the source of truth. The LLM is a reranker, never a counter.
- New persisted state must use the `_config_path()` / `_watchlist_path()` / `_scan_dir()`
  indirections in `cli/app.py` so tests can monkeypatch locations.
```

- [ ] **Step 2: Commit**

```bash
git add CONTRIBUTING.md
git commit -m "docs: add CONTRIBUTING.md with dev loop and PR checklist"
```

---

### Task 8: DISCLAIMER.md + first-run CLI banner + README link

**Background.** This is a trading tool published to the public. A loud, unambiguous disclaimer protects the author and sets expectations. The CLI banner shows once on first run (tracked by a sentinel file in `~/.wave_alpha/`), is suppressible via `WAVE_ALPHA_NO_DISCLAIMER=1` for CI, and never shows in `--json` output paths.

**Files:**
- Create: `DISCLAIMER.md`
- Modify: `src/wave_alpha/cli/app.py` (add banner)
- Create: `tests/test_disclaimer_banner.py`
- Modify: `README.md` (add Disclaimer section linking to DISCLAIMER.md)

- [ ] **Step 1: Write DISCLAIMER.md**

```markdown
# Disclaimer

**`wave-alpha` is not financial advice.**

This software is provided for **educational and research purposes only**. It is a
deterministic Elliott Wave analysis tool that produces calibrated probabilistic counts
and trade-shape signals — it does **not** provide investment advice, trade
recommendations, or solicitation to buy or sell any security.

## What this means in practice

- **Past patterns are not guarantees of future results.** Backtest equity curves
  reflect a specific universe, time window, and parameter choice. Out-of-sample
  performance is generally worse, often much worse.
- **The engine is not infallible.** Wave counts are inherently subjective; even
  rule-validated counts can fail when market regime changes.
- **The LLM reranker can hallucinate.** When enabled, the LLM influences ranking
  but does not validate counts. Treat narration as commentary, not analysis.
- **Data may be wrong, stale, or missing.** `wave-alpha` uses Yahoo Finance via
  `yfinance`. Splits, dividends, halts, and corporate actions can produce artifacts.
- **No warranty.** See `LICENSE`. The software is provided "as is."

## Your responsibility

- You are solely responsible for any trading decisions you make.
- Do your own research. Validate counts manually before trading.
- Never trade money you cannot afford to lose.
- Consult a licensed financial advisor for personalized advice.

## Author

`wave-alpha` is maintained by Alex Chen. The author is not a registered investment
advisor and has no fiduciary duty to users of this software.
```

- [ ] **Step 2: Locate the CLI app entrypoint**

Run: `grep -n "^app = typer" /Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/cli/app.py`

Note the line number where `app` is defined and where the first command is registered. The banner needs to fire from a Typer `callback` (top-level invocation hook) so it appears once before any command executes.

- [ ] **Step 3: Add the banner code**

Add this helper near the top of `src/wave_alpha/cli/app.py` (after imports, before `app = typer.Typer(...)`):

```python
import os
from pathlib import Path

_DISCLAIMER_BANNER = """\
┌─ wave-alpha — disclaimer ──────────────────────────────────────────────┐
│ This tool is for educational and research use only. It is NOT          │
│ financial advice. Past performance does not predict future results.    │
│ You are solely responsible for any trading decisions.                  │
│ Full text: https://github.com/chen-star/wave-alpha/blob/main/DISCLAIMER.md │
│ (this notice shows once; suppress with WAVE_ALPHA_NO_DISCLAIMER=1)     │
└────────────────────────────────────────────────────────────────────────┘
"""


def _disclaimer_sentinel_path() -> Path:
    return Path.home() / ".wave_alpha" / ".disclaimer_ack"


def _maybe_show_disclaimer() -> None:
    if os.environ.get("WAVE_ALPHA_NO_DISCLAIMER"):
        return
    sentinel = _disclaimer_sentinel_path()
    if sentinel.exists():
        return
    import sys

    print(_DISCLAIMER_BANNER, file=sys.stderr)
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("acknowledged\n")
```

Then register a Typer callback. Find the line `app = typer.Typer(...)` and *immediately after it* add:

```python
@app.callback()
def _root(ctx: typer.Context) -> None:
    """wave-alpha — local-first Elliott Wave analysis."""
    _maybe_show_disclaimer()
```

If a `@app.callback()` already exists, instead add `_maybe_show_disclaimer()` as the first line of its body.

- [ ] **Step 4: Write the banner test**

Create `tests/test_disclaimer_banner.py`:

```python
"""Banner shown once on first run, never again, suppressible by env var."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from wave_alpha.cli import app as cli_app

runner = CliRunner(mix_stderr=False)


def _patch_sentinel(monkeypatch, tmp_path: Path) -> Path:
    sentinel = tmp_path / ".disclaimer_ack"
    monkeypatch.setattr(cli_app, "_disclaimer_sentinel_path", lambda: sentinel)
    monkeypatch.delenv("WAVE_ALPHA_NO_DISCLAIMER", raising=False)
    return sentinel


def test_banner_shown_on_first_run(monkeypatch, tmp_path):
    sentinel = _patch_sentinel(monkeypatch, tmp_path)
    assert not sentinel.exists()

    result = runner.invoke(cli_app.app, ["version"])
    assert result.exit_code == 0
    assert "wave-alpha — disclaimer" in result.stderr
    assert sentinel.exists()


def test_banner_suppressed_after_first_run(monkeypatch, tmp_path):
    sentinel = _patch_sentinel(monkeypatch, tmp_path)
    sentinel.write_text("acknowledged\n")

    result = runner.invoke(cli_app.app, ["version"])
    assert result.exit_code == 0
    assert "disclaimer" not in result.stderr.lower()


def test_banner_suppressed_by_env_var(monkeypatch, tmp_path):
    _patch_sentinel(monkeypatch, tmp_path)
    monkeypatch.setenv("WAVE_ALPHA_NO_DISCLAIMER", "1")

    result = runner.invoke(cli_app.app, ["version"])
    assert result.exit_code == 0
    assert "disclaimer" not in result.stderr.lower()
```

- [ ] **Step 5: Run banner tests**

Run: `uv run pytest tests/test_disclaimer_banner.py -v`
Expected: 3 passed.

- [ ] **Step 6: Add Disclaimer section to README**

In `README.md`, find the existing "Disclaimer" anchor in the table of contents (line ~51) and ensure it points at a real section near the bottom. Replace or add:

```markdown
## Disclaimer

**This software is for educational and research use only. It is not financial advice.**
See [`DISCLAIMER.md`](DISCLAIMER.md) for the full text. By using `wave-alpha` you accept that
trading decisions are solely your responsibility.

## License

Released under the [MIT License](LICENSE).
```

- [ ] **Step 7: Commit**

```bash
git add DISCLAIMER.md src/wave_alpha/cli/app.py tests/test_disclaimer_banner.py README.md
git commit -m "feat(cli): add DISCLAIMER and first-run CLI banner"
```

---

### Task 9: Privacy contract test

**Background.** `CLAUDE.md` documents the LLM payload contract: ticker, pivot timestamps/prices, candidate counts, coherence — *no* bars, *no* API key. This test pins that contract so a future refactor cannot quietly start sending OHLCV bars upstream.

**Files:**
- Create: `tests/llm/test_privacy_contract.py`

- [ ] **Step 1: Write the test**

```python
"""Pins the privacy contract documented in CLAUDE.md and the README:

The LLM payload may contain ticker symbol, pivot timestamps/prices, candidate
count structures, and coherence metrics. It MUST NOT contain raw OHLCV bars
or any form of the API key.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from wave_alpha.elliott.models import (
    PivotEvent,
    PivotState,
    PivotType,
    WaveCount,
)
from wave_alpha.llm.candidates import serialize_candidate_set


def _pivot(d: str, price: str, kind: PivotType) -> PivotEvent:
    return PivotEvent(
        timestamp=date.fromisoformat(d),
        price=Decimal(price),
        type=kind,
        state=PivotState.CONFIRMED,
        degree=2,
    )


def _wave_count() -> WaveCount:
    return WaveCount(
        pattern="impulse",
        degree=2,
        score=0.82,
        engine_score=0.82,
        pivots=[
            _pivot("2026-01-02", "100.00", PivotType.LOW),
            _pivot("2026-01-15", "115.00", PivotType.HIGH),
            _pivot("2026-02-01", "108.00", PivotType.LOW),
            _pivot("2026-02-20", "130.00", PivotType.HIGH),
            _pivot("2026-03-01", "122.00", PivotType.LOW),
            _pivot("2026-03-20", "150.00", PivotType.HIGH),
        ],
        soft_violations=[],
    )


_FORBIDDEN_TOKENS = (
    # OHLCV column names — any leak of bar data
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adj_close",
    "adjclose",
    # Credential tokens
    "api_key",
    "apikey",
    "anthropic",
    "sk-ant",
    "bearer",
    "authorization",
)


def test_serialize_candidate_set_has_no_forbidden_tokens():
    payload = serialize_candidate_set([_wave_count()])
    lower = payload.lower()
    for token in _FORBIDDEN_TOKENS:
        assert token not in lower, (
            f"forbidden token {token!r} found in LLM candidate payload:\n{payload}"
        )


def test_serialize_candidate_set_contains_only_count_structure():
    payload = serialize_candidate_set([_wave_count()])
    # Sanity check positive content (so a future bug that empties the payload
    # doesn't pass the negative test trivially)
    assert "pattern=impulse" in payload
    assert "degree=2" in payload
    assert "100.00" in payload  # pivot price


@pytest.mark.parametrize("attr", ["api_key", "ANTHROPIC_API_KEY"])
def test_secrets_never_appear(monkeypatch, attr):
    monkeypatch.setenv(attr, "sk-ant-leak-canary-12345")
    payload = serialize_candidate_set([_wave_count()])
    assert "leak-canary" not in payload
    assert "sk-ant" not in payload.lower()
```

- [ ] **Step 2: Run, watch it pass**

Run: `uv run pytest tests/llm/test_privacy_contract.py -v`
Expected: 4 passed.

- [ ] **Step 3: Manually break the contract to verify the test catches it (then revert)**

Temporarily edit `src/wave_alpha/llm/candidates.py:14` to insert `f"  open=100 high=110 close=105"` into one of the pivot lines. Re-run the test — it should fail on `forbidden token 'open' found`. Revert the edit.

- [ ] **Step 4: Commit**

```bash
git add tests/llm/test_privacy_contract.py
git commit -m "test(llm): pin privacy contract — payload contains no OHLCV or credentials"
```

---

### Task 10: `py.typed` marker

**Background.** PEP 561. Without this file, downstream type-checkers (`mypy`, `pyright`) treat `wave_alpha` as untyped even though our public API is fully annotated.

**Files:**
- Create: `src/wave_alpha/py.typed` (empty file)
- Modify: `pyproject.toml` (force-include)

- [ ] **Step 1: Create the empty marker**

Run: `touch /Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/py.typed`

- [ ] **Step 2: Force-include in wheel**

In `pyproject.toml`, add a line under the existing `[tool.hatch.build.targets.wheel.force-include]`:

```toml
"src/wave_alpha/py.typed" = "wave_alpha/py.typed"
```

- [ ] **Step 3: Verify py.typed lands in the wheel**

Run: `uv build --wheel 2>&1 | tail -1 && uv run python -c "import zipfile, glob; w = sorted(glob.glob('dist/wave_alpha-*.whl'))[-1]; names = zipfile.ZipFile(w).namelist(); assert 'wave_alpha/py.typed' in names, names; print('OK')"`
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add src/wave_alpha/py.typed pyproject.toml
git commit -m "chore(typing): add py.typed marker (PEP 561)"
```

---

## Phase C — CI/CD

### Task 11: GitHub Actions test workflow

**Background.** Catches regressions on every PR. Runs ruff + pytest across a Python and OS matrix. Skips the `slow` lean-install test on PRs (it builds a wheel and is duplicated by the publish workflow's build step) but runs it on `main` pushes.

**Files:**
- Create: `.github/workflows/test.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: test

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

concurrency:
  group: test-${{ github.ref }}
  cancel-in-progress: true

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true
      - run: uv sync --extra dev --extra ui --extra llm
      - run: uv run ruff check .
      - run: uv run ruff format --check .

  test:
    needs: lint
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest]
        python: ["3.11", "3.12", "3.13"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          enable-cache: true
      - run: uv python install ${{ matrix.python }}
      - run: uv sync --extra dev --extra ui --extra llm --python ${{ matrix.python }}
      - name: pytest (fast)
        if: github.event_name == 'pull_request'
        run: uv run pytest -m "not slow" -q
      - name: pytest (full)
        if: github.event_name == 'push'
        run: uv run pytest -q
```

- [ ] **Step 2: Verify YAML syntax**

Run: `uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml'))" && echo OK`
Expected: `OK`.

- [ ] **Step 3: Commit and push to test the workflow**

```bash
git add .github/workflows/test.yml
git commit -m "ci: add lint + pytest matrix workflow"
git push origin main
```

Then watch: `gh run list --workflow=test.yml --limit=1` — wait for it to complete, then `gh run view <id> --log-failed` if it fails. Iterate until green before moving to Task 12.

---

### Task 12: GitHub Actions publish workflow (Trusted Publisher / OIDC)

**Background.** PyPI Trusted Publishers eliminate long-lived API tokens — the workflow authenticates via GitHub's OIDC token. This requires a one-time manual setup on PyPI before the workflow can succeed.

**Manual prerequisite (do this BEFORE the first tag):**

1. Create a PyPI account if needed and reserve the `wave-alpha` project name by uploading a manual 0.6.0rc1 wheel, OR use https://pypi.org/manage/account/publishing/ "Add a pending publisher" form.
2. Register a Trusted Publisher with these values:
   - PyPI Project Name: `wave-alpha`
   - Owner: `chen-star`
   - Repository name: `wave-alpha`
   - Workflow name: `publish.yml`
   - Environment name: `pypi`
3. In the GitHub repo, create an environment named `pypi` (Settings → Environments → New) — optionally add a required reviewer for extra safety.

**Files:**
- Create: `.github/workflows/publish.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: publish

on:
  push:
    tags: ["v*.*.*"]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - name: Verify tag matches package version
        run: |
          TAG="${GITHUB_REF#refs/tags/v}"
          PKG=$(uv run python -c "import wave_alpha; print(wave_alpha.__version__)")
          test "$TAG" = "$PKG" || { echo "tag $TAG != package $PKG"; exit 1; }
      - run: uv build
      - uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/

  publish:
    needs: build
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/
      - uses: pypa/gh-action-pypi-publish@release/v1

  github-release:
    needs: publish
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/
      - name: Create GitHub Release
        run: |
          TAG="${GITHUB_REF#refs/tags/}"
          gh release create "$TAG" dist/* \
            --title "$TAG" \
            --notes "See [CHANGELOG.md](https://github.com/chen-star/wave-alpha/blob/main/CHANGELOG.md) for release notes."
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

- [ ] **Step 2: Verify YAML syntax**

Run: `uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/publish.yml'))" && echo OK`
Expected: `OK`.

- [ ] **Step 3: Commit (do NOT tag yet — that triggers a publish)**

```bash
git add .github/workflows/publish.yml
git commit -m "ci: add Trusted Publisher workflow for PyPI releases"
git push origin main
```

The first real tag push is part of Task 14.

---

### Task 13: RELEASING.md

**Background.** Captures the release ritual so it is not lost. Aimed at the maintainer, not at users.

**Files:**
- Create: `RELEASING.md`

- [ ] **Step 1: Write RELEASING.md**

```markdown
# Releasing wave-alpha

A release is gated by a git tag matching `v*.*.*` on `main`. CI builds and publishes; do not run `uv publish` from your laptop.

## Prerequisites (one-time)

- PyPI Trusted Publisher registered (see `.github/workflows/publish.yml` for the values).
- GitHub repo Environment named `pypi` exists.

## Steps

1. **Confirm `main` is green.** `gh run list --workflow=test.yml --branch=main --limit=1` — must be `success`.

2. **Bump version in two places** (must match):
   - `pyproject.toml` → `version = "X.Y.Z"`
   - `src/wave_alpha/__init__.py` → `__version__ = "X.Y.Z"`

3. **Update `CHANGELOG.md`:** move items from `## [Unreleased]` into a new `## [X.Y.Z] — YYYY-MM-DD` section. Add a new empty `## [Unreleased]` on top.

4. **Commit:**
   ```bash
   git add pyproject.toml src/wave_alpha/__init__.py CHANGELOG.md
   git commit -m "chore(release): X.Y.Z"
   git push origin main
   ```

5. **Wait for the test workflow on the bump commit to pass.**

6. **Tag and push:**
   ```bash
   git tag -a vX.Y.Z -m "Release X.Y.Z"
   git push origin vX.Y.Z
   ```

7. **Watch publish:** `gh run watch --workflow=publish.yml`. The workflow will:
   - Verify the tag matches `__version__`.
   - Build the wheel + sdist.
   - Publish to PyPI via OIDC.
   - Create a GitHub Release with the artifacts attached.

8. **Smoke test the published package:**
   ```bash
   uvx --refresh wave-alpha==X.Y.Z version
   ```

## If something goes wrong

- **Tag pushed but build failed:** delete the tag (`git tag -d vX.Y.Z && git push origin :refs/tags/vX.Y.Z`), fix, re-tag.
- **Wheel uploaded to PyPI but is broken:** PyPI does not allow re-uploading the same version. Bump to `X.Y.(Z+1)`, never reuse a version number.
- **OIDC publish fails:** check the Trusted Publisher config matches `chen-star/wave-alpha` + `publish.yml` + `pypi` environment exactly.

## Pre-release versions

For betas/release-candidates, tag as `vX.Y.Z-rc1` etc. The publish workflow accepts any `v*.*.*` pattern; PyPI will list them as pre-releases.
```

- [ ] **Step 2: Commit**

```bash
git add RELEASING.md
git commit -m "docs: add RELEASING.md describing tag-driven publish workflow"
```

---

## Phase D — Cut the release

### Task 14: Bump to 0.6.0 and tag

**Background.** Final task. Everything above must be merged to `main` and CI must be green before tagging.

**Files:**
- Modify: `pyproject.toml` (version)
- Modify: `src/wave_alpha/__init__.py` (__version__)
- Modify: `CHANGELOG.md` (move Unreleased → 0.6.0 if Task 5 was kept Unreleased)

- [ ] **Step 1: Verify CI green**

Run: `gh run list --workflow=test.yml --branch=main --limit=1`
Expected: `success` for the latest commit on `main`.

- [ ] **Step 2: Bump version**

In `pyproject.toml`:
```toml
version = "0.6.0"
```

In `src/wave_alpha/__init__.py`:
```python
__version__ = "0.6.0"
```

If Task 5's CHANGELOG was kept in `## [Unreleased]`, move that block under `## [0.6.0] — 2026-05-09 — Public release` and re-add an empty `## [Unreleased]` on top.

- [ ] **Step 3: Smoke test the build locally**

Run: `rm -rf dist && uv build && uv run python -c "import zipfile, glob; w = sorted(glob.glob('dist/wave_alpha-0.6.0-*.whl'))[-1]; z = zipfile.ZipFile(w); names = z.namelist(); assert 'wave_alpha/py.typed' in names; assert any(n.endswith('templates_data/impulse.yaml') for n in names); assert any(n.startswith('wave_alpha-0.6.0.dist-info/licenses/LICENSE') or n.startswith('wave_alpha-0.6.0.dist-info/LICENSE') for n in names); print('OK', w)"`
Expected: `OK dist/wave_alpha-0.6.0-...whl`.

- [ ] **Step 4: Commit, push, wait for CI**

```bash
git add pyproject.toml src/wave_alpha/__init__.py CHANGELOG.md
git commit -m "chore(release): 0.6.0"
git push origin main
```

Wait: `gh run watch --workflow=test.yml` — must be green.

- [ ] **Step 5: Tag and push**

```bash
git tag -a v0.6.0 -m "Release 0.6.0 — first public release"
git push origin v0.6.0
```

- [ ] **Step 6: Watch the publish workflow**

Run: `gh run watch --workflow=publish.yml`
Expected: `success`. PyPI page should now exist at https://pypi.org/project/wave-alpha/.

- [ ] **Step 7: Smoke test the published package**

Run: `uvx --refresh wave-alpha==0.6.0 version`
Expected: prints `0.6.0`.

- [ ] **Step 8: Announce (optional, not part of the technical work)**

GitHub Release auto-created by Task 12's workflow. From here, marketing/launch is a separate track.

---

## Verification matrix — what each task proves

| Concern | Where it is enforced |
| --- | --- |
| Project has a license | LICENSE file (Task 1); `license = "MIT"` in pyproject (Task 2); `licenses/LICENSE` in wheel (Task 14 §3) |
| Lean install does not pull anthropic | `tests/test_lean_install.py` (Task 4); CI matrix runs it on `main` (Task 11) |
| LLM payload contains no OHLCV / API key | `tests/llm/test_privacy_contract.py` (Task 9) |
| Public API is typed for downstream | `py.typed` in wheel (Task 10 §3) |
| Disclaimer is unmissable | first-run banner test (Task 8 §4); README link (Task 8 §6); DISCLAIMER.md (Task 8 §1) |
| Releases are reproducible & untokened | Trusted Publisher OIDC workflow (Task 12); RELEASING.md (Task 13) |
| Tag never drifts from `__version__` | tag-vs-version check in `publish.yml` (Task 12 §1) |
| PyPI page is decorated | classifiers, urls, keywords (Task 2) |

---

## Self-review notes

- **Spec coverage:** All 13 items from the original audit conversation are mapped to a task or explicitly deferred in "Out of scope."
- **Order dependencies:** Task 1 → 2 (pyproject references LICENSE). Task 3 → 4 (lean test asserts the dep split). Task 11 → 12 (publish reuses build patterns established in test). Tasks 5–10 are independent and can be parallelized in subagent execution.
- **TDD applied where it matters:** privacy contract (9), banner behavior (8), lean install (4), wheel contents (10, 14). Pure-config tasks use a verification command in lieu of a test.
- **No "later" placeholders:** every code block is the final content the engineer writes.
