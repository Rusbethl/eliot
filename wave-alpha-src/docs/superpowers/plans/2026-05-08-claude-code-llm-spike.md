# Claude Code Max LLM Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a `ClaudeCodeClient` that shells out to `claude -p` and a throwaway side-by-side comparison script, so a single `analyze` invocation can be run through both `AnthropicClient` and `ClaudeCodeClient` to settle the spec's three success criteria.

**Architecture:** Two new files. `src/wave_alpha/llm/claude_code_client.py` is a `LLMClient` Protocol implementation that calls `subprocess.run(["claude", "-p", user, "--system-prompt", system, "--model", model, "--output-format", "text"])` and returns stdout. `scripts/spike_claude_code_compare.py` runs `pipeline.run_analysis` once with `LLMMode.DISABLED` to obtain real candidates, builds the same `LLMRequest` that `_maybe_run_scan` would build (`SCAN_SYSTEM_PROMPT` + `build_scan_user_prompt`), calls each client directly bypassing `LLMRunner`/cache, then dumps `reports/claude_code_spike.json`.

**Tech Stack:** Python 3.11+, `uv`, `subprocess` (stdlib), `pytest` for the unit test, the existing `wave_alpha.llm.*` modules. The `claude` CLI must be installed and the user logged into a Max plan; `ANTHROPIC_API_KEY` must also be set so the baseline `AnthropicClient` run works.

**Spec:** `docs/superpowers/specs/2026-05-08-claude-code-llm-spike-design.md`

---

## File Structure

| File | Status | Responsibility |
|------|--------|----------------|
| `src/wave_alpha/llm/claude_code_client.py` | NEW | `ClaudeCodeClient` dataclass implementing `LLMClient` Protocol via `subprocess.run` to `claude -p`. |
| `tests/llm/test_claude_code_client.py` | NEW | Unit test with monkeypatched `subprocess.run`; verifies argv composition and stdout passthrough. |
| `scripts/spike_claude_code_compare.py` | NEW | Throwaway orchestration script: builds one `LLMRequest`, runs both clients, dumps comparison JSON. Not registered in CLI. |
| `reports/claude_code_spike.json` | RUNTIME | Output of the spike run. `reports/` is gitignored — artifact is local-only, used to decide go/no-go. |

`cli/app.py`, `LLMMode`, `RuntimeConfig`, `pyproject.toml`, and any user-facing CLI flag are **not** modified.

---

## Task 1: Add `ClaudeCodeClient` (TDD)

**Files:**
- Create: `src/wave_alpha/llm/claude_code_client.py`
- Test: `tests/llm/test_claude_code_client.py`

The class is a small dataclass that mirrors `CacheOnlyClient`'s shape (no inheritance, satisfies the `LLMClient` Protocol structurally via a `model: str` attribute and a `run(LLMRequest) -> str` method). Calls the CLI as a subprocess with `check=True` and a 60s timeout — any non-zero exit, timeout, or missing binary surfaces as an exception per the spec's "fail loud" stance (§6).

- [ ] **Step 1: Write the failing test**

Create `tests/llm/test_claude_code_client.py` with:

```python
import subprocess
from unittest.mock import MagicMock

import pytest

from wave_alpha.llm.claude_code_client import ClaudeCodeClient
from wave_alpha.llm.client import LLMRequest


def test_claude_code_client_returns_stdout(monkeypatch) -> None:
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        result = MagicMock()
        result.stdout = "canned response"
        result.returncode = 0
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)

    client = ClaudeCodeClient(model="claude-haiku-4-5-20251001")
    req = LLMRequest(model="claude-haiku-4-5-20251001", system="SYS", user="USR")
    text = client.run(req)

    assert text == "canned response"
    argv = captured["argv"]
    assert argv[0] == "claude"
    assert "-p" in argv
    assert "USR" in argv
    assert "--system-prompt" in argv
    assert "SYS" in argv
    assert "--model" in argv
    assert "claude-haiku-4-5-20251001" in argv
    assert captured["kwargs"]["check"] is True
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is True
    assert captured["kwargs"]["timeout"] == 60


def test_claude_code_client_propagates_subprocess_error(monkeypatch) -> None:
    def fake_run(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv, stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = ClaudeCodeClient(model="claude-haiku-4-5-20251001")
    req = LLMRequest(model="claude-haiku-4-5-20251001", system="s", user="u")
    with pytest.raises(subprocess.CalledProcessError):
        client.run(req)
```

- [ ] **Step 2: Run the test and verify it fails**

```bash
uv run pytest tests/llm/test_claude_code_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'wave_alpha.llm.claude_code_client'` (collection error; both tests fail to load).

- [ ] **Step 3: Implement minimal `ClaudeCodeClient`**

Create `src/wave_alpha/llm/claude_code_client.py`:

```python
from __future__ import annotations

import subprocess
from dataclasses import dataclass

from wave_alpha.llm.client import LLMRequest


@dataclass(frozen=True)
class ClaudeCodeClient:
    """LLMClient implementation that shells out to the `claude` CLI.

    Spike-only — uses the user's Claude Code Max-plan auth on disk
    (no API key argument). Bypasses prompt caching breakpoints and
    SDK-level features; transport is the simplest thing that works.
    See docs/superpowers/specs/2026-05-08-claude-code-llm-spike-design.md.
    """

    model: str

    def run(self, req: LLMRequest) -> str:
        result = subprocess.run(
            [
                "claude",
                "-p",
                req.user,
                "--system-prompt",
                req.system,
                "--model",
                self.model,
                "--output-format",
                "text",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        return result.stdout
```

- [ ] **Step 4: Run the test and verify it passes**

```bash
uv run pytest tests/llm/test_claude_code_client.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Lint check**

```bash
uv run ruff check src/wave_alpha/llm/claude_code_client.py tests/llm/test_claude_code_client.py
```

Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/llm/claude_code_client.py tests/llm/test_claude_code_client.py
git commit -m "feat(llm): add ClaudeCodeClient (spike) — subprocess transport for Max plan"
```

---

## Task 2: Spike orchestration script

**Files:**
- Create: `scripts/spike_claude_code_compare.py`
- Runtime output: `reports/claude_code_spike.json` (gitignored)

This is a one-shot script. It runs the engine once to obtain real candidates, builds the `LLMRequest` exactly the way `_maybe_run_scan` does (`SCAN_SYSTEM_PROMPT` + `build_scan_user_prompt(ticker, as_of, candidates_block, coherence_score)`), invokes each client directly bypassing `LLMRunner`/cache, and dumps a comparison JSON. The script's success criterion is producing the artifact; its real evaluation happens by reading the artifact afterward (per spec §3 and §9).

There is no automated test — this is a throwaway harness whose "test" is the run itself. TDD does not apply.

- [ ] **Step 1: Write the spike script**

Create `scripts/spike_claude_code_compare.py`:

```python
"""Spike: side-by-side compare AnthropicClient vs ClaudeCodeClient.

One-shot throwaway. Reads ANTHROPIC_API_KEY from env (required for the
baseline). Assumes the user is logged into the `claude` CLI for the
Claude Code path. Writes reports/claude_code_spike.json.

See docs/superpowers/specs/2026-05-08-claude-code-llm-spike-design.md.
Run: `uv run python scripts/spike_claude_code_compare.py`
"""

from __future__ import annotations

import json
import os
import time
import traceback
from datetime import date
from pathlib import Path

from wave_alpha.data.cached import CachedProvider
from wave_alpha.data.yahoo import YahooProvider
from wave_alpha.llm.candidates import serialize_candidate_set
from wave_alpha.llm.claude_code_client import ClaudeCodeClient
from wave_alpha.llm.client import AnthropicClient, LLMRequest
from wave_alpha.llm.modes import LLMMode
from wave_alpha.llm.prompts import SCAN_SYSTEM_PROMPT, build_scan_user_prompt
from wave_alpha.llm.responses import parse_scan_response
from wave_alpha.pipeline import AnalyzeRequest, run_analysis

TICKER = "AAPL"
AS_OF = date(2024, 12, 31)
MODEL = "claude-haiku-4-5-20251001"
OUT_PATH = Path("reports/claude_code_spike.json")


def _build_request() -> tuple[LLMRequest, set[str]]:
    """Run the engine pipeline once (LLM disabled) to get real candidates,
    then construct the same LLMRequest that `_maybe_run_scan` would build."""
    provider = CachedProvider(
        upstream=YahooProvider(),
        db_path=Path.home() / ".wave_alpha" / "cache.sqlite",
    )
    result = run_analysis(
        AnalyzeRequest(ticker=TICKER, as_of=AS_OF),
        provider=provider,
        llm_mode=LLMMode.DISABLED,
    )
    candidates = result.counts_daily
    if not candidates:
        raise RuntimeError(
            f"engine produced zero daily candidates for {TICKER} as-of {AS_OF}; "
            "pick a different ticker/date for the spike."
        )
    coh_score = result.coherence.score if result.coherence else 0.0
    block = serialize_candidate_set(candidates)
    user = build_scan_user_prompt(
        ticker=TICKER, as_of=AS_OF, candidates_block=block, coherence_score=coh_score
    )
    req = LLMRequest(model=MODEL, system=SCAN_SYSTEM_PROMPT, user=user)
    allowed = {f"C{i + 1}" for i in range(len(candidates))}
    return req, allowed


def _run_one(name: str, client, req: LLMRequest, allowed: set[str]) -> dict:
    record: dict = {"client": name}
    t0 = time.perf_counter()
    try:
        raw = client.run(req)
        record["raw"] = raw
        record["latency_s"] = time.perf_counter() - t0
        try:
            parsed = parse_scan_response(raw, allowed_candidate_ids=allowed)
            record["parsed"] = parsed.model_dump(mode="json")
            record["parse_error"] = None
        except Exception as e:
            record["parsed"] = None
            record["parse_error"] = f"{type(e).__name__}: {e}"
    except Exception as e:
        record["raw"] = None
        record["latency_s"] = time.perf_counter() - t0
        record["parsed"] = None
        record["parse_error"] = None
        record["client_error"] = (
            f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        )
    return record


def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit(
            "ANTHROPIC_API_KEY not set — the spike needs it for the baseline run. "
            "Set the env var and retry."
        )

    print(f"Building LLMRequest for {TICKER} as-of {AS_OF}...")
    req, allowed = _build_request()
    print(
        f"  prompt: system={len(req.system)} chars, user={len(req.user)} chars, "
        f"candidates={len(allowed)}"
    )

    print("Running AnthropicClient...")
    anthropic_record = _run_one(
        "anthropic", AnthropicClient(api_key=api_key, model=MODEL), req, allowed
    )

    print("Running ClaudeCodeClient...")
    claude_code_record = _run_one(
        "claude_code", ClaudeCodeClient(model=MODEL), req, allowed
    )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "ticker": TICKER,
        "as_of": AS_OF.isoformat(),
        "model": MODEL,
        "request": {
            "system_chars": len(req.system),
            "user_chars": len(req.user),
            "allowed_candidate_ids": sorted(allowed),
        },
        "anthropic": anthropic_record,
        "claude_code": claude_code_record,
    }
    OUT_PATH.write_text(json.dumps(artifact, indent=2, default=str))
    print(f"\nWrote {OUT_PATH}")
    print(
        f"  anthropic:   {anthropic_record['latency_s']:.2f}s, "
        f"parse_error={anthropic_record.get('parse_error')!r}, "
        f"client_error={'client_error' in anthropic_record}"
    )
    print(
        f"  claude_code: {claude_code_record['latency_s']:.2f}s, "
        f"parse_error={claude_code_record.get('parse_error')!r}, "
        f"client_error={'client_error' in claude_code_record}"
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the script imports cleanly (no run yet)**

```bash
uv run python -c "import scripts.spike_claude_code_compare as m; print('imports OK')"
```

Expected: `imports OK`. Import paths verified against `src/wave_alpha/data/` (`cached.CachedProvider`, `yahoo.YahooProvider`) at plan-write time; if anything has shifted, fix the import to match the current module layout before continuing.

- [ ] **Step 3: Lint check**

```bash
uv run ruff check scripts/spike_claude_code_compare.py
```

Expected: `All checks passed!`

- [ ] **Step 4: Commit the script (before running it)**

Commit the harness independently of the run, so the run output and any subsequent fixups are separable.

```bash
git add scripts/spike_claude_code_compare.py
git commit -m "feat(scripts): add Claude Code vs API side-by-side spike script"
```

- [ ] **Step 5: Run the spike**

Both credentials must be present. Verify:

```bash
test -n "$ANTHROPIC_API_KEY" && echo "API key set" || echo "API key MISSING"
which claude && echo "claude CLI present" || echo "claude CLI MISSING"
```

Both must show "set" / "present" before continuing. Then:

```bash
uv run python scripts/spike_claude_code_compare.py
```

Expected: console output ends with two summary lines showing wall-clock latency and parse status for each client. `reports/claude_code_spike.json` exists.

If the `claude` CLI flag surface differs from what `ClaudeCodeClient` assumes (`-p`, `--system-prompt`, `--model`, `--output-format text`), the run will fail with a `CalledProcessError` and the stderr will be in the exception message. Fix the argv in `src/wave_alpha/llm/claude_code_client.py` to match the real flags (e.g., flag rename, system prompt passed via stdin, etc.), update the test in `tests/llm/test_claude_code_client.py` to match, re-run both, and amend the existing commit from Task 1 with the corrected flags. Then re-run this step.

- [ ] **Step 6: Inspect the artifact against success criteria**

Read `reports/claude_code_spike.json` and check the three success criteria from spec §3:

1. **Functional equivalence** — `claude_code.parse_error` is `null` AND `claude_code.parsed.candidates[i].probability` values are within ~0.2 abs of the corresponding `anthropic.parsed.candidates[i].probability`.
2. **Latency tolerable** — `claude_code.latency_s` < 30, ideally < 10.
3. **Auth UX zero-friction** — the run completed without `ClaudeCodeClient` reading any API key (it doesn't, by construction — but confirm there were no auth-related stderr messages buried in `claude_code.client_error` if any).

Map the result to spec §9: pass / soft fail / hard fail.

- [ ] **Step 7: Record the outcome (no commit needed for the artifact)**

`reports/` is gitignored, so the artifact stays local. Print the verdict and the path; the user reads the file directly.

```bash
echo "Spike complete — review reports/claude_code_spike.json against spec §3 / §9."
```

---

## Out of Scope (per spec §8)

- New `LLMProvider` enum / config key.
- CLI flag (`--llm-provider claude-code`) and `wave-alpha config` UX.
- Fallback chain semantics (Claude Code → API → cache → disabled).
- Cache key handling when model id differs between transports.
- Scan-mode behavior (multi-ticker latency).
- Documentation in `README.md` / `CLAUDE.md`.
- Argv-exposure / privacy review of subprocess transport.

These are deferred to the post-spike full feature design, conditional on a spike pass per spec §9.
