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

## Commit messages — Conventional Commits

Releases are automated. The version bump and PyPI publish are driven by
[Conventional Commits](https://www.conventionalcommits.org/) on `main`:

- `feat: …` → minor bump (new functionality)
- `fix: …` → patch bump (bug fix)
- `feat!: …` or a `BREAKING CHANGE:` footer → major bump
- `docs: …`, `chore: …`, `refactor: …`, `test: …`, `style: …`, `ci: …` → no release

A scope is optional but encouraged: `feat(scan): add 4h-only mode`. See `RELEASING.md`
for the full pipeline.

## Pull request checklist

- [ ] Tests pass (`uv run pytest`).
- [ ] Lint clean (`uv run ruff check .`).
- [ ] Formatted (`uv run ruff format .`).
- [ ] Commit messages follow Conventional Commits (otherwise no release will be cut).
- [ ] CHANGELOG.md updated under `## [Unreleased]` (optional — auto-generated entries
      will be inserted on release, but a hand-written summary is welcome).
- [ ] If adding a wave template: dropped a YAML in `src/wave_alpha/elliott/templates_data/`,
      no code change needed (validator picks it up).
- [ ] If touching the LLM payload: privacy contract test in
      `tests/llm/test_privacy_contract.py` still passes.

## Conventions

- All prices use `decimal.Decimal`; never `float`.
- Engine output is the source of truth. The LLM is a reranker, never a counter.
- New persisted state must use the `_config_path()` / `_watchlist_path()` / `_scan_dir()`
  indirections in `cli/app.py` so tests can monkeypatch locations.
