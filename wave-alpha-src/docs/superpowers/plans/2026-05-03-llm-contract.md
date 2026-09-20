# wave-alpha v0.2 — LLM Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic Anthropic-API-backed reranking-and-narrating layer on top of the v0.1 engine — scan path (Haiku, 1-line per count) and deep-dive path (Sonnet, multi-paragraph), both temperature=0 with response caching keyed by `(model_id, prompt_hash)`, plus an `α`-blend final ranking and an `--llm-mode {live, cached, disabled}` switch.

**Architecture:** Pure-Python `llm/` package wrapping the `anthropic` SDK. The LLM is a judge inside the engine's already-enumerated candidate set — it never invents counts or pivots. A `LLMRunner` orchestrates cache lookup → API call → cache write. Prompts use a static system prompt (with prompt-cache breakpoint) + a per-call user prompt serializing the engine's top-K WaveCounts as compact text. Responses are JSON, validated against Pydantic schemas, with one repair-retry on parse miss. API key and model selection live in `~/.wave_alpha/config.yaml` via a new `prefs/` module.

**Tech Stack:** `anthropic>=0.39` (BYO key), Pydantic v2, SQLModel/SQLite for cache, existing engine-core types (WaveCount, AnalyzeResult, CoherenceResult).

**Reference spec:** `docs/superpowers/specs/2026-05-03-wave-alpha-design.md` §3 (tech stack), §6 (LLM contract), §13 (privacy).

**Depends on:** v0.1 engine core (merged to `main`).

---

## File structure

```
src/wave_alpha/
├── prefs/
│   ├── __init__.py
│   ├── models.py         # AppConfig, LLMConfig (Pydantic)
│   └── config.py         # load/save ~/.wave_alpha/config.yaml
├── llm/
│   ├── __init__.py
│   ├── modes.py          # LLMMode enum
│   ├── candidates.py     # WaveCount → compact text for prompts
│   ├── prompts.py        # system + user prompt builders
│   ├── responses.py      # ScanResponse, DeepDiveResponse, parse_response
│   ├── client.py         # AnthropicClient (auth, model pin, temperature=0)
│   ├── cache.py          # SQLite LLMResponseCache
│   ├── runner.py         # LLMRunner — cache + client orchestrator
│   └── ranking.py        # blend_scores
└── pipeline.py           # extend AnalyzeResult with llm + ranked output

tests/
├── prefs/test_config.py
└── llm/
    ├── test_modes.py
    ├── test_candidates.py
    ├── test_prompts.py
    ├── test_responses.py
    ├── test_cache.py
    ├── test_runner.py
    └── test_ranking.py
```

---

## Phase A — Prefs and config

### Task 1: AppConfig + LLMConfig models

**Files:**
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/prefs/__init__.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/prefs/models.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/tests/prefs/__init__.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/tests/prefs/test_models.py`

- [ ] **Step 1: Write failing test**

`tests/prefs/test_models.py`:

```python
from wave_alpha.prefs.models import AppConfig, LLMConfig


def test_llm_config_defaults() -> None:
    cfg = LLMConfig()
    assert cfg.scan_model == "claude-haiku-4-5-20251001"
    assert cfg.deep_model == "claude-sonnet-4-6"
    assert cfg.alpha == 0.6
    assert cfg.api_key is None


def test_app_config_defaults() -> None:
    cfg = AppConfig()
    assert isinstance(cfg.llm, LLMConfig)
    assert cfg.default_period_years == 2
    assert cfg.default_degree == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/prefs/test_models.py -v`
Expected: FAIL with "No module named 'wave_alpha.prefs'".

- [ ] **Step 3: Implement models**

Create `src/wave_alpha/prefs/__init__.py`:

```python
from wave_alpha.prefs.models import AppConfig, LLMConfig

__all__ = ["AppConfig", "LLMConfig"]
```

Create `src/wave_alpha/prefs/models.py`:

```python
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LLMConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    api_key: str | None = None
    scan_model: str = "claude-haiku-4-5-20251001"
    deep_model: str = "claude-sonnet-4-6"
    alpha: float = Field(default=0.6, ge=0.0, le=1.0)


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    llm: LLMConfig = Field(default_factory=LLMConfig)
    default_period_years: int = 2
    default_degree: int = 2
```

Create empty `tests/prefs/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/prefs/test_models.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/prefs/__init__.py src/wave_alpha/prefs/models.py tests/prefs/__init__.py tests/prefs/test_models.py
git commit -m "$(cat <<'EOF'
feat(prefs): AppConfig and LLMConfig pydantic models

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: YAML round-trip for AppConfig

**Files:**
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/prefs/config.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/tests/prefs/test_config.py`

- [ ] **Step 1: Write failing tests**

`tests/prefs/test_config.py`:

```python
from pathlib import Path

from wave_alpha.prefs.config import (
    DEFAULT_CONFIG_PATH,
    load_config,
    resolve_api_key,
    save_config,
)
from wave_alpha.prefs.models import AppConfig, LLMConfig


def test_load_config_returns_defaults_when_file_missing(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "missing.yaml")
    assert cfg == AppConfig()


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    cfg = AppConfig(llm=LLMConfig(api_key="sk-test", alpha=0.7))
    save_config(cfg, target)
    loaded = load_config(target)
    assert loaded.llm.api_key == "sk-test"
    assert loaded.llm.alpha == 0.7


def test_resolve_api_key_prefers_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    cfg = AppConfig(llm=LLMConfig(api_key="sk-file"))
    assert resolve_api_key(cfg) == "sk-env"


def test_resolve_api_key_falls_back_to_config(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = AppConfig(llm=LLMConfig(api_key="sk-file"))
    assert resolve_api_key(cfg) == "sk-file"


def test_default_config_path_under_dot_wave_alpha() -> None:
    assert DEFAULT_CONFIG_PATH.name == "config.yaml"
    assert DEFAULT_CONFIG_PATH.parent.name == ".wave_alpha"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/prefs/test_config.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement config loader**

Create `src/wave_alpha/prefs/config.py`:

```python
from __future__ import annotations

import os
from pathlib import Path

import yaml

from wave_alpha.prefs.models import AppConfig

DEFAULT_CONFIG_PATH: Path = Path.home() / ".wave_alpha" / "config.yaml"


def load_config(path: Path | None = None) -> AppConfig:
    """Load config from YAML; return defaults if the file does not exist."""
    target = path or DEFAULT_CONFIG_PATH
    if not target.exists():
        return AppConfig()
    raw = yaml.safe_load(target.read_text()) or {}
    return AppConfig.model_validate(raw)


def save_config(cfg: AppConfig, path: Path | None = None) -> None:
    target = path or DEFAULT_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False))


def resolve_api_key(cfg: AppConfig) -> str | None:
    """Return the effective API key. Env var takes precedence over config file."""
    env = os.environ.get("ANTHROPIC_API_KEY")
    if env:
        return env
    return cfg.llm.api_key
```

Update `src/wave_alpha/prefs/__init__.py`:

```python
from wave_alpha.prefs.config import (
    DEFAULT_CONFIG_PATH,
    load_config,
    resolve_api_key,
    save_config,
)
from wave_alpha.prefs.models import AppConfig, LLMConfig

__all__ = [
    "AppConfig",
    "LLMConfig",
    "DEFAULT_CONFIG_PATH",
    "load_config",
    "save_config",
    "resolve_api_key",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/prefs/test_config.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/prefs/config.py src/wave_alpha/prefs/__init__.py tests/prefs/test_config.py
git commit -m "$(cat <<'EOF'
feat(prefs): YAML round-trip and API key resolver

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase B — LLM modes and candidate serialization

### Task 3: LLMMode enum

**Files:**
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/llm/__init__.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/llm/modes.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/tests/llm/__init__.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/tests/llm/test_modes.py`

- [ ] **Step 1: Write failing test**

`tests/llm/test_modes.py`:

```python
from wave_alpha.llm.modes import LLMMode


def test_llm_mode_string_values() -> None:
    assert LLMMode.LIVE.value == "live"
    assert LLMMode.CACHED.value == "cached"
    assert LLMMode.DISABLED.value == "disabled"


def test_llm_mode_lookup() -> None:
    assert LLMMode("live") is LLMMode.LIVE
    assert LLMMode("cached") is LLMMode.CACHED
    assert LLMMode("disabled") is LLMMode.DISABLED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/llm/test_modes.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement enum**

Create `src/wave_alpha/llm/__init__.py` (empty for now).

Create `src/wave_alpha/llm/modes.py`:

```python
from __future__ import annotations

from enum import StrEnum


class LLMMode(StrEnum):
    """Run mode for the LLM stage.

    LIVE     — call the Anthropic API and write responses to the cache.
    CACHED   — cache-only; raise on miss. Used for paper-reproducible runs.
    DISABLED — skip the LLM stage entirely; engine score is the final score
               (alpha treated as 1.0). The mandatory backtest baseline.
    """

    LIVE = "live"
    CACHED = "cached"
    DISABLED = "disabled"
```

Create empty `tests/llm/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/llm/test_modes.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/llm/__init__.py src/wave_alpha/llm/modes.py tests/llm/__init__.py tests/llm/test_modes.py
git commit -m "$(cat <<'EOF'
feat(llm): LLMMode enum (live/cached/disabled)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Serialize WaveCount candidates as compact text

**Files:**
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/llm/candidates.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/tests/llm/test_candidates.py`

- [ ] **Step 1: Write failing test**

`tests/llm/test_candidates.py`:

```python
from datetime import date
from decimal import Decimal

from wave_alpha.domain import Interval
from wave_alpha.elliott.models import WaveCount
from wave_alpha.llm.candidates import serialize_candidate, serialize_candidate_set
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


def _impulse() -> WaveCount:
    return WaveCount(
        pattern="impulse",
        degree=2,
        score=0.72,
        pivots=[
            _piv(date(2024, 1, 1), "100", PivotType.LOW),
            _piv(date(2024, 1, 5), "110", PivotType.HIGH),
            _piv(date(2024, 1, 8), "104", PivotType.LOW),
            _piv(date(2024, 1, 15), "130", PivotType.HIGH),
            _piv(date(2024, 1, 18), "118", PivotType.LOW),
        ],
    )


def test_serialize_candidate_includes_pattern_score_and_pivots() -> None:
    c = _impulse()
    text = serialize_candidate(c, candidate_id="C1")
    assert "C1" in text
    assert "impulse" in text
    assert "0.72" in text
    # All five pivots referenced (compact: P1..P5)
    for ts in ("2024-01-01", "2024-01-15"):
        assert ts in text
    assert "100" in text and "130" in text


def test_serialize_candidate_set_uses_stable_ids() -> None:
    a = _impulse()
    b = _impulse().model_copy(update={"score": 0.65})
    block = serialize_candidate_set([a, b])
    assert "C1" in block and "C2" in block
    # Highest score first preserved (caller's order)
    first = block.index("C1")
    second = block.index("C2")
    assert first < second


def test_empty_candidate_set_yields_empty_block() -> None:
    assert serialize_candidate_set([]) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/llm/test_candidates.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement serializer**

Create `src/wave_alpha/llm/candidates.py`:

```python
from __future__ import annotations

from wave_alpha.elliott.models import WaveCount


def serialize_candidate(count: WaveCount, *, candidate_id: str) -> str:
    """Compact text serialization of a WaveCount, suitable for LLM prompts."""
    lines: list[str] = [
        f"[{candidate_id}] pattern={count.pattern} degree={count.degree} score={count.score:.2f}",
    ]
    for i, p in enumerate(count.pivots, start=1):
        lines.append(
            f"  P{i} ts={p.timestamp.isoformat()} price={p.price} type={p.type.value} state={p.state.value}"
        )
    if count.soft_violations:
        lines.append(f"  soft_violations={','.join(count.soft_violations)}")
    return "\n".join(lines)


def serialize_candidate_set(counts: list[WaveCount]) -> str:
    """Serialize a list of candidates with stable C1, C2, ... identifiers."""
    if not counts:
        return ""
    blocks = [serialize_candidate(c, candidate_id=f"C{i + 1}") for i, c in enumerate(counts)]
    return "\n".join(blocks)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/llm/test_candidates.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/llm/candidates.py tests/llm/test_candidates.py
git commit -m "$(cat <<'EOF'
feat(llm): compact text serialization of WaveCount candidates

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase C — Prompts

### Task 5: System and user prompts

**Files:**
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/llm/prompts.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/tests/llm/test_prompts.py`

The system prompt is static across calls — declare it as a module-level string so a prompt-cache breakpoint can be inserted between system and user messages by the client wrapper (Task 7).

- [ ] **Step 1: Write failing test**

`tests/llm/test_prompts.py`:

```python
from datetime import date

from wave_alpha.llm.prompts import (
    SCAN_SYSTEM_PROMPT,
    DEEP_DIVE_SYSTEM_PROMPT,
    build_scan_user_prompt,
    build_deep_dive_user_prompt,
)


def test_scan_system_prompt_mentions_schema_and_constraints() -> None:
    assert "JSON" in SCAN_SYSTEM_PROMPT
    assert "engine_missed" in SCAN_SYSTEM_PROMPT
    assert "candidate" in SCAN_SYSTEM_PROMPT.lower()


def test_deep_dive_system_prompt_distinct_from_scan() -> None:
    assert SCAN_SYSTEM_PROMPT != DEEP_DIVE_SYSTEM_PROMPT
    assert "narrative" in DEEP_DIVE_SYSTEM_PROMPT.lower()


def test_build_scan_user_prompt_includes_ticker_and_candidates() -> None:
    prompt = build_scan_user_prompt(
        ticker="AAPL",
        as_of=date(2024, 12, 31),
        candidates_block="[C1] pattern=impulse degree=2 score=0.70",
        coherence_score=0.82,
    )
    assert "AAPL" in prompt
    assert "2024-12-31" in prompt
    assert "0.82" in prompt
    assert "C1" in prompt


def test_build_deep_dive_user_prompt_supports_scenarios() -> None:
    prompt = build_deep_dive_user_prompt(
        ticker="AAPL",
        as_of=date(2024, 12, 31),
        candidates_block="[C1] pattern=impulse",
        scenarios_block="[S1] tentative_high",
        coherence_score=0.55,
    )
    assert "AAPL" in prompt
    assert "S1" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/llm/test_prompts.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement prompts**

Create `src/wave_alpha/llm/prompts.py`:

```python
from __future__ import annotations

from datetime import date

SCAN_SYSTEM_PROMPT: str = """\
You are an Elliott Wave assistant inside a deterministic engine. The engine has already enumerated rule-compliant candidate counts. Your job is to rerank them and write a one-line narrative for each.

You MUST respond with valid JSON matching this schema:
{
  "candidates": [
    {"id": "C1", "probability": 0.42, "narrative": "one short sentence"}
  ],
  "engine_missed": false,
  "engine_missed_reason": null
}

Rules:
- Probabilities sum to 1.0 across all candidates listed in the input.
- Use only candidate IDs present in the input. Do not invent counts.
- Narratives are at most 140 characters, plain text, no markdown.
- Set engine_missed=true only if a clearly better count exists that the engine did not surface; set engine_missed_reason to a short rationale.
"""

DEEP_DIVE_SYSTEM_PROMPT: str = """\
You are an Elliott Wave assistant inside a deterministic engine. The engine has enumerated rule-compliant candidate counts and right-edge scenarios. Your job is to rerank them and write narratives.

You MUST respond with valid JSON matching this schema:
{
  "top_two": [
    {
      "id": "C1",
      "probability": 0.55,
      "narrative_paragraphs": ["...", "...", "..."],
      "invalidation": "price level or condition",
      "fib_targets": ["1.618 ext at ...", "..."],
      "multi_tf_commentary": "one or two sentences",
      "what_would_change_my_mind": "one or two sentences"
    }
  ],
  "alternates": [
    {"id": "C3", "probability": 0.10, "narrative": "one paragraph"}
  ],
  "scenarios": [
    {"id": "S1", "probability": 0.40, "narrative": "one sentence", "invalidation": "..."}
  ],
  "engine_missed": false,
  "engine_missed_reason": null
}

Rules:
- Use only candidate IDs and scenario IDs present in the input. Do not invent counts or scenarios.
- top_two contains exactly 0, 1, or 2 entries (depending on candidate count).
- Probabilities across top_two + alternates sum to 1.0.
- narrative_paragraphs has exactly three short paragraphs (~3-4 sentences each).
- Plain text, no markdown.
"""


def build_scan_user_prompt(
    *,
    ticker: str,
    as_of: date,
    candidates_block: str,
    coherence_score: float,
) -> str:
    return (
        f"Ticker: {ticker}\n"
        f"As-of: {as_of.isoformat()}\n"
        f"Multi-TF coherence: {coherence_score:.2f}\n"
        f"\nCandidates:\n{candidates_block or '(none)'}\n"
    )


def build_deep_dive_user_prompt(
    *,
    ticker: str,
    as_of: date,
    candidates_block: str,
    scenarios_block: str,
    coherence_score: float,
) -> str:
    return (
        f"Ticker: {ticker}\n"
        f"As-of: {as_of.isoformat()}\n"
        f"Multi-TF coherence: {coherence_score:.2f}\n"
        f"\nCandidates:\n{candidates_block or '(none)'}\n"
        f"\nRight-edge scenarios:\n{scenarios_block or '(none)'}\n"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/llm/test_prompts.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/llm/prompts.py tests/llm/test_prompts.py
git commit -m "$(cat <<'EOF'
feat(llm): scan and deep-dive system prompts plus user-prompt builders

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase D — Response parsing

### Task 6: Response models and parser

**Files:**
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/llm/responses.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/tests/llm/test_responses.py`

- [ ] **Step 1: Write failing tests**

`tests/llm/test_responses.py`:

```python
import pytest

from wave_alpha.llm.responses import (
    DeepDiveResponse,
    LLMParseError,
    ScanResponse,
    parse_deep_dive_response,
    parse_scan_response,
)


def test_parse_scan_response_happy_path() -> None:
    raw = (
        '{"candidates": [{"id": "C1", "probability": 0.7, "narrative": "ok"},'
        '{"id": "C2", "probability": 0.3, "narrative": "alt"}],'
        '"engine_missed": false, "engine_missed_reason": null}'
    )
    parsed = parse_scan_response(raw)
    assert isinstance(parsed, ScanResponse)
    assert len(parsed.candidates) == 2
    assert parsed.candidates[0].id == "C1"
    assert parsed.engine_missed is False


def test_parse_scan_response_rejects_unknown_candidate_ids() -> None:
    raw = (
        '{"candidates": [{"id": "X99", "probability": 1.0, "narrative": "n/a"}],'
        '"engine_missed": false, "engine_missed_reason": null}'
    )
    with pytest.raises(LLMParseError):
        parse_scan_response(raw, allowed_candidate_ids={"C1", "C2"})


def test_parse_scan_response_rejects_probabilities_not_summing_to_one() -> None:
    raw = (
        '{"candidates": [{"id": "C1", "probability": 0.4, "narrative": "ok"}],'
        '"engine_missed": false, "engine_missed_reason": null}'
    )
    with pytest.raises(LLMParseError):
        parse_scan_response(raw, allowed_candidate_ids={"C1"})


def test_parse_scan_response_strips_markdown_fences() -> None:
    raw = (
        "```json\n"
        '{"candidates": [{"id": "C1", "probability": 1.0, "narrative": "n"}],'
        '"engine_missed": false, "engine_missed_reason": null}\n'
        "```"
    )
    parsed = parse_scan_response(raw)
    assert parsed.candidates[0].probability == 1.0


def test_parse_deep_dive_response_happy_path() -> None:
    raw = (
        '{"top_two": [{"id": "C1", "probability": 0.6,'
        '"narrative_paragraphs": ["a","b","c"],'
        '"invalidation": "below 100", "fib_targets": ["1.618 = 150"],'
        '"multi_tf_commentary": "ok", "what_would_change_my_mind": "drop"}],'
        '"alternates": [{"id": "C2", "probability": 0.4, "narrative": "alt"}],'
        '"scenarios": [{"id": "S1", "probability": 0.3, "narrative": "n", "invalidation": "i"}],'
        '"engine_missed": false, "engine_missed_reason": null}'
    )
    parsed = parse_deep_dive_response(raw)
    assert isinstance(parsed, DeepDiveResponse)
    assert parsed.top_two[0].id == "C1"
    assert len(parsed.top_two[0].narrative_paragraphs) == 3
    assert parsed.scenarios[0].id == "S1"


def test_parse_invalid_json_raises_llm_parse_error() -> None:
    with pytest.raises(LLMParseError):
        parse_scan_response("not json at all")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/llm/test_responses.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement parser**

Create `src/wave_alpha/llm/responses.py`:

```python
from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict, ValidationError

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LLMParseError(ValueError):
    """Raised when an LLM response cannot be parsed against the expected schema."""


class ScanCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    probability: float
    narrative: str


class ScanResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    candidates: list[ScanCandidate]
    engine_missed: bool
    engine_missed_reason: str | None = None


class DeepDiveTop(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    probability: float
    narrative_paragraphs: list[str]
    invalidation: str
    fib_targets: list[str]
    multi_tf_commentary: str
    what_would_change_my_mind: str


class DeepDiveAlternate(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    probability: float
    narrative: str


class DeepDiveScenario(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    probability: float
    narrative: str
    invalidation: str


class DeepDiveResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    top_two: list[DeepDiveTop]
    alternates: list[DeepDiveAlternate]
    scenarios: list[DeepDiveScenario]
    engine_missed: bool
    engine_missed_reason: str | None = None


def _strip_fences(raw: str) -> str:
    return _FENCE_RE.sub("", raw).strip()


def _load_json(raw: str) -> dict:
    cleaned = _strip_fences(raw)
    try:
        loaded = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMParseError(f"invalid JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise LLMParseError("response root must be a JSON object")
    return loaded


def _validate_probabilities(probs: list[float], *, where: str) -> None:
    total = sum(probs)
    if not 0.99 <= total <= 1.01:
        raise LLMParseError(f"{where} probabilities must sum to ~1.0; got {total:.3f}")


def parse_scan_response(
    raw: str, *, allowed_candidate_ids: set[str] | None = None
) -> ScanResponse:
    try:
        parsed = ScanResponse.model_validate(_load_json(raw))
    except ValidationError as exc:
        raise LLMParseError(f"schema mismatch: {exc}") from exc
    ids = {c.id for c in parsed.candidates}
    if allowed_candidate_ids is not None and not ids.issubset(allowed_candidate_ids):
        unknown = ids - allowed_candidate_ids
        raise LLMParseError(f"unknown candidate ids: {sorted(unknown)}")
    _validate_probabilities([c.probability for c in parsed.candidates], where="candidates")
    return parsed


def parse_deep_dive_response(
    raw: str,
    *,
    allowed_candidate_ids: set[str] | None = None,
    allowed_scenario_ids: set[str] | None = None,
) -> DeepDiveResponse:
    try:
        parsed = DeepDiveResponse.model_validate(_load_json(raw))
    except ValidationError as exc:
        raise LLMParseError(f"schema mismatch: {exc}") from exc
    cand_ids = {t.id for t in parsed.top_two} | {a.id for a in parsed.alternates}
    if allowed_candidate_ids is not None and not cand_ids.issubset(allowed_candidate_ids):
        unknown = cand_ids - allowed_candidate_ids
        raise LLMParseError(f"unknown candidate ids: {sorted(unknown)}")
    sc_ids = {s.id for s in parsed.scenarios}
    if allowed_scenario_ids is not None and not sc_ids.issubset(allowed_scenario_ids):
        unknown = sc_ids - allowed_scenario_ids
        raise LLMParseError(f"unknown scenario ids: {sorted(unknown)}")
    _validate_probabilities(
        [t.probability for t in parsed.top_two] + [a.probability for a in parsed.alternates],
        where="top_two+alternates",
    )
    for t in parsed.top_two:
        if len(t.narrative_paragraphs) != 3:
            raise LLMParseError(
                f"top_two entry {t.id} must have exactly 3 narrative_paragraphs; got "
                f"{len(t.narrative_paragraphs)}"
            )
    return parsed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/llm/test_responses.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/llm/responses.py tests/llm/test_responses.py
git commit -m "$(cat <<'EOF'
feat(llm): pydantic response models and strict parsers

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase E — Anthropic client wrapper

### Task 7: AnthropicClient with prompt-cache breakpoint

**Files:**
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/llm/client.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/tests/llm/test_client.py`
- Modify: `/Users/alexchen/Documents/project/wave_alpha/pyproject.toml` (add `anthropic` dep)

The Anthropic SDK supports prompt caching via `cache_control: {"type": "ephemeral"}` on a system content block. We always add the breakpoint after the static system prompt so subsequent calls hit the cache for the static portion.

- [ ] **Step 1: Add dependency**

Modify `pyproject.toml` `[project] dependencies` — append:

```toml
  "anthropic>=0.39",
```

Run: `uv sync --extra dev`

- [ ] **Step 2: Write failing test**

`tests/llm/test_client.py`:

```python
from unittest.mock import MagicMock

import pytest

from wave_alpha.llm.client import AnthropicClient, LLMRequest, MissingAPIKeyError


class _StubMessages:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.last_kwargs: dict | None = None

    def create(self, **kwargs):  # noqa: ANN003 — mimic SDK signature
        self.last_kwargs = kwargs
        block = MagicMock()
        block.text = self.response_text
        msg = MagicMock()
        msg.content = [block]
        return msg


class _StubAnthropic:
    def __init__(self, response_text: str) -> None:
        self.messages = _StubMessages(response_text)


def test_client_missing_api_key_raises() -> None:
    with pytest.raises(MissingAPIKeyError):
        AnthropicClient(api_key=None)


def test_client_sends_system_block_with_cache_control() -> None:
    stub = _StubAnthropic('{"ok": true}')
    client = AnthropicClient(api_key="sk-test", _sdk_factory=lambda **_: stub)
    req = LLMRequest(model="claude-haiku-4-5-20251001", system="SYS", user="USR")
    text = client.run(req)
    assert text == '{"ok": true}'
    sent = stub.messages.last_kwargs
    assert sent["model"] == "claude-haiku-4-5-20251001"
    assert sent["temperature"] == 0
    sys_block = sent["system"]
    assert isinstance(sys_block, list) and len(sys_block) == 1
    assert sys_block[0]["text"] == "SYS"
    assert sys_block[0]["cache_control"] == {"type": "ephemeral"}
    assert sent["messages"][0] == {"role": "user", "content": "USR"}


def test_client_uses_max_tokens_default() -> None:
    stub = _StubAnthropic('{"ok": true}')
    client = AnthropicClient(api_key="sk-test", _sdk_factory=lambda **_: stub)
    client.run(LLMRequest(model="claude-haiku-4-5-20251001", system="S", user="U"))
    assert stub.messages.last_kwargs["max_tokens"] == 2048
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/llm/test_client.py -v`
Expected: FAIL with import error.

- [ ] **Step 4: Implement client**

Create `src/wave_alpha/llm/client.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict


class MissingAPIKeyError(RuntimeError):
    """Raised when an LLM call is attempted without a configured API key."""


class LLMRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    system: str
    user: str
    max_tokens: int = 2048


@dataclass
class AnthropicClient:
    """Thin wrapper over the official Anthropic SDK.

    All calls run with temperature=0 and a prompt-cache breakpoint after the
    static system block (per spec §6 — "Prompt caching"). Pass _sdk_factory to
    inject a stub in tests; default lazily imports the real SDK.
    """

    api_key: str | None
    _sdk_factory: Callable[..., Any] | None = None

    def __post_init__(self) -> None:
        if not self.api_key:
            raise MissingAPIKeyError(
                "no Anthropic API key configured; set ANTHROPIC_API_KEY or run 'wave-alpha config set api_key'"
            )

    def _client(self) -> Any:
        if self._sdk_factory is not None:
            return self._sdk_factory(api_key=self.api_key)
        import anthropic  # local import keeps anthropic optional at import time

        return anthropic.Anthropic(api_key=self.api_key)

    def run(self, req: LLMRequest) -> str:
        sdk = self._client()
        message = sdk.messages.create(
            model=req.model,
            max_tokens=req.max_tokens,
            temperature=0,
            system=[{"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": req.user}],
        )
        # Extract first text block
        for block in message.content:
            text = getattr(block, "text", None)
            if text:
                return text
        raise RuntimeError("Anthropic response contained no text block")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/llm/test_client.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/wave_alpha/llm/client.py tests/llm/test_client.py
git commit -m "$(cat <<'EOF'
feat(llm): AnthropicClient wrapper with prompt-cache breakpoint

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase F — Response cache

### Task 8: SQLite-backed LLM response cache

**Files:**
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/llm/cache.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/tests/llm/test_cache.py`

- [ ] **Step 1: Write failing test**

`tests/llm/test_cache.py`:

```python
from datetime import datetime
from pathlib import Path

from wave_alpha.llm.cache import LLMResponseCache, prompt_hash


def test_prompt_hash_deterministic() -> None:
    h1 = prompt_hash("system", "user")
    h2 = prompt_hash("system", "user")
    assert h1 == h2
    assert h1 != prompt_hash("system", "user2")


def test_cache_roundtrip(tmp_path: Path) -> None:
    cache = LLMResponseCache(db_path=tmp_path / "llm.sqlite")
    key = ("claude-haiku-4-5-20251001", prompt_hash("S", "U"))
    assert cache.get(*key) is None
    cache.put(*key, response='{"ok": true}', created_at=datetime(2024, 1, 1))
    assert cache.get(*key) == '{"ok": true}'


def test_cache_distinguishes_models(tmp_path: Path) -> None:
    cache = LLMResponseCache(db_path=tmp_path / "llm.sqlite")
    h = prompt_hash("S", "U")
    cache.put("haiku", h, response="A", created_at=datetime(2024, 1, 1))
    cache.put("sonnet", h, response="B", created_at=datetime(2024, 1, 1))
    assert cache.get("haiku", h) == "A"
    assert cache.get("sonnet", h) == "B"


def test_cache_overwrite_replaces_existing(tmp_path: Path) -> None:
    cache = LLMResponseCache(db_path=tmp_path / "llm.sqlite")
    key = ("haiku", prompt_hash("S", "U"))
    cache.put(*key, response="A", created_at=datetime(2024, 1, 1))
    cache.put(*key, response="B", created_at=datetime(2024, 1, 2))
    assert cache.get(*key) == "B"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/llm/test_cache.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement cache**

Create `src/wave_alpha/llm/cache.py`:

```python
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_response (
    model_id    TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    response    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (model_id, prompt_hash)
);
"""


def prompt_hash(system: str, user: str) -> str:
    """Deterministic hash of (system, user) prompt pair."""
    h = hashlib.sha256()
    h.update(system.encode("utf-8"))
    h.update(b"\x00")
    h.update(user.encode("utf-8"))
    return h.hexdigest()


class LLMResponseCache:
    """SQLite cache keyed by (model_id, prompt_hash)."""

    def __init__(self, db_path: Path | str) -> None:
        self._path = str(db_path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._path) as conn:
            conn.executescript(_SCHEMA)

    def get(self, model_id: str, prompt_hash_: str) -> str | None:
        with sqlite3.connect(self._path) as conn:
            row = conn.execute(
                "SELECT response FROM llm_response WHERE model_id=? AND prompt_hash=?",
                (model_id, prompt_hash_),
            ).fetchone()
        return row[0] if row else None

    def put(
        self, model_id: str, prompt_hash_: str, *, response: str, created_at: datetime
    ) -> None:
        with sqlite3.connect(self._path) as conn:
            conn.execute(
                "INSERT INTO llm_response(model_id, prompt_hash, response, created_at) "
                "VALUES (?,?,?,?) "
                "ON CONFLICT(model_id, prompt_hash) DO UPDATE SET "
                "response=excluded.response, created_at=excluded.created_at",
                (model_id, prompt_hash_, response, created_at.isoformat()),
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/llm/test_cache.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/llm/cache.py tests/llm/test_cache.py
git commit -m "$(cat <<'EOF'
feat(llm): SQLite response cache keyed by (model_id, prompt_hash)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase G — Runner orchestration

### Task 9: LLMRunner glues client + cache + modes

**Files:**
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/llm/runner.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/tests/llm/test_runner.py`

- [ ] **Step 1: Write failing test**

`tests/llm/test_runner.py`:

```python
from datetime import datetime
from pathlib import Path

import pytest

from wave_alpha.llm.cache import LLMResponseCache
from wave_alpha.llm.client import LLMRequest
from wave_alpha.llm.modes import LLMMode
from wave_alpha.llm.runner import CachedResponseMissError, LLMRunner


class _StubClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def run(self, req: LLMRequest) -> str:
        self.calls += 1
        return self.response


def _runner(tmp_path: Path, *, mode: LLMMode, response: str = '{"ok": true}') -> tuple[LLMRunner, _StubClient]:
    cache = LLMResponseCache(db_path=tmp_path / "llm.sqlite")
    client = _StubClient(response)
    now = lambda: datetime(2024, 1, 1)  # noqa: E731
    runner = LLMRunner(client=client, cache=cache, mode=mode, now=now)
    return runner, client


def test_disabled_mode_returns_none_without_calling(tmp_path: Path) -> None:
    runner, client = _runner(tmp_path, mode=LLMMode.DISABLED)
    req = LLMRequest(model="haiku", system="S", user="U")
    assert runner.run(req) is None
    assert client.calls == 0


def test_live_mode_calls_client_and_writes_cache(tmp_path: Path) -> None:
    runner, client = _runner(tmp_path, mode=LLMMode.LIVE, response="R1")
    req = LLMRequest(model="haiku", system="S", user="U")
    assert runner.run(req) == "R1"
    assert client.calls == 1
    # second call hits cache
    assert runner.run(req) == "R1"
    assert client.calls == 1


def test_cached_mode_returns_cached_response(tmp_path: Path) -> None:
    runner_live, _ = _runner(tmp_path, mode=LLMMode.LIVE, response="R2")
    req = LLMRequest(model="haiku", system="S", user="U")
    runner_live.run(req)
    runner_cached, client = _runner(tmp_path, mode=LLMMode.CACHED)
    assert runner_cached.run(req) == "R2"
    assert client.calls == 0


def test_cached_mode_misses_raise(tmp_path: Path) -> None:
    runner, _ = _runner(tmp_path, mode=LLMMode.CACHED)
    req = LLMRequest(model="haiku", system="S", user="U")
    with pytest.raises(CachedResponseMissError):
        runner.run(req)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/llm/test_runner.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement runner**

Create `src/wave_alpha/llm/runner.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from wave_alpha.llm.cache import LLMResponseCache, prompt_hash
from wave_alpha.llm.client import LLMRequest
from wave_alpha.llm.modes import LLMMode


class CachedResponseMissError(RuntimeError):
    """Raised by LLMMode.CACHED when no cached response exists."""


@dataclass
class LLMRunner:
    """Orchestrates cache-lookup → API call → cache-write per the configured mode.

    DISABLED → return None (caller skips LLM blending).
    CACHED   → cache-only; raise CachedResponseMissError on miss.
    LIVE     → cache-first, fall through to client, write back.
    """

    client: object  # AnthropicClient or stub with .run(LLMRequest) -> str
    cache: LLMResponseCache
    mode: LLMMode
    now: Callable[[], datetime] = datetime.utcnow

    def run(self, req: LLMRequest) -> str | None:
        if self.mode is LLMMode.DISABLED:
            return None
        h = prompt_hash(req.system, req.user)
        cached = self.cache.get(req.model, h)
        if cached is not None:
            return cached
        if self.mode is LLMMode.CACHED:
            raise CachedResponseMissError(
                f"no cached response for model={req.model} hash={h[:12]}"
            )
        response = self.client.run(req)  # type: ignore[attr-defined]
        self.cache.put(req.model, h, response=response, created_at=self.now())
        return response
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/llm/test_runner.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/llm/runner.py tests/llm/test_runner.py
git commit -m "$(cat <<'EOF'
feat(llm): LLMRunner orchestrates cache + client per mode

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase H — Final ranking blend

### Task 10: blend_scores pure function

**Files:**
- Create: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/llm/ranking.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/tests/llm/test_ranking.py`

- [ ] **Step 1: Write failing test**

`tests/llm/test_ranking.py`:

```python
import pytest

from wave_alpha.llm.ranking import blend_scores


def test_alpha_one_returns_engine_score_times_multiplier() -> None:
    assert blend_scores(coh_multiplier=1.0, engine_score=0.6, llm_prob=0.9, alpha=1.0) == pytest.approx(0.6)


def test_alpha_zero_returns_llm_prob_times_multiplier() -> None:
    assert blend_scores(coh_multiplier=1.0, engine_score=0.6, llm_prob=0.9, alpha=0.0) == pytest.approx(0.9)


def test_alpha_default_is_engine_dominant() -> None:
    blended = blend_scores(coh_multiplier=1.0, engine_score=0.6, llm_prob=0.9, alpha=0.6)
    assert blended == pytest.approx(0.6 * 0.6 + 0.4 * 0.9)


def test_coherence_multiplier_scales_result() -> None:
    base = blend_scores(coh_multiplier=1.0, engine_score=0.5, llm_prob=0.5, alpha=0.6)
    scaled = blend_scores(coh_multiplier=1.2, engine_score=0.5, llm_prob=0.5, alpha=0.6)
    assert scaled == pytest.approx(1.2 * base)


def test_llm_disabled_via_none_treats_alpha_as_one() -> None:
    """When LLM is disabled, llm_prob=None means the blend is engine-only."""
    assert blend_scores(coh_multiplier=1.0, engine_score=0.6, llm_prob=None, alpha=0.6) == pytest.approx(0.6)


def test_alpha_out_of_range_raises() -> None:
    with pytest.raises(ValueError):
        blend_scores(coh_multiplier=1.0, engine_score=0.6, llm_prob=0.5, alpha=1.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/llm/test_ranking.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement ranking**

Create `src/wave_alpha/llm/ranking.py`:

```python
from __future__ import annotations


def blend_scores(
    *,
    coh_multiplier: float,
    engine_score: float,
    llm_prob: float | None,
    alpha: float,
) -> float:
    """Final ranking per spec §6:

        final = coh_multiplier * (alpha * engine + (1 - alpha) * llm_prob)

    When ``llm_prob`` is None (mode=DISABLED or call skipped), the blend
    collapses to engine-only (alpha treated as 1.0).
    """
    if not 0.0 <= alpha <= 1.0:
        raise ValueError(f"alpha must be in [0, 1]; got {alpha}")
    if llm_prob is None:
        return coh_multiplier * engine_score
    return coh_multiplier * (alpha * engine_score + (1.0 - alpha) * llm_prob)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/llm/test_ranking.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/llm/ranking.py tests/llm/test_ranking.py
git commit -m "$(cat <<'EOF'
feat(llm): blend_scores per spec §6 final ranking formula

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase I — Pipeline integration

### Task 11: Extend AnalyzeResult with LLM fields and ranked counts

**Files:**
- Modify: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/pipeline.py`
- Modify: `/Users/alexchen/Documents/project/wave_alpha/tests/test_pipeline.py`

The existing `AnalyzeResult` carries engine output. Add an optional `llm_scan: ScanResponse | None` (or `llm_deep: DeepDiveResponse | None`) and a `ranked_daily: list[RankedCount]` field where each `RankedCount` pairs the original `WaveCount`, the candidate id, the engine score, the LLM probability (None if disabled), and the final blended score. Keep a single `analyze` entry point; choose the path via `LLMRequest`/mode arguments.

- [ ] **Step 1: Write failing test (mode=DISABLED keeps engine-only ranking)**

Append to `tests/test_pipeline.py`:

```python
from wave_alpha.llm.modes import LLMMode
from wave_alpha.pipeline import RankedCount


def test_run_analysis_disabled_mode_produces_engine_only_ranked_counts() -> None:
    provider = _SyntheticImpulseProvider()
    req = AnalyzeRequest(ticker="AAPL", as_of=date(2024, 12, 31))
    result = run_analysis(req, provider=provider, llm_mode=LLMMode.DISABLED)
    assert all(isinstance(rc, RankedCount) for rc in result.ranked_daily)
    if result.ranked_daily:
        rc = result.ranked_daily[0]
        assert rc.llm_prob is None
        # final = coh_multiplier * engine_score when llm_prob is None
        expected = result.coherence.multiplier * rc.engine_score
        assert abs(rc.final_score - expected) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pipeline.py::test_run_analysis_disabled_mode_produces_engine_only_ranked_counts -v`
Expected: FAIL — `RankedCount`/`llm_mode` undefined.

- [ ] **Step 3: Modify pipeline.py**

Replace the body of `run_analysis` and surrounding model definitions:

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
from wave_alpha.llm.modes import LLMMode
from wave_alpha.llm.ranking import blend_scores
from wave_alpha.llm.responses import ScanResponse
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
_DEFAULT_DEGREE_FOR_COUNT = 2


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    ticker: str
    as_of: date
    rules: TradeRules = TradeRules()


@dataclass(frozen=True)
class RankedCount:
    candidate_id: str
    count: WaveCount
    engine_score: float
    llm_prob: float | None
    final_score: float


@dataclass
class AnalyzeResult:
    ticker: str
    as_of: date
    counts_weekly: list[WaveCount] = field(default_factory=list)
    counts_daily: list[WaveCount] = field(default_factory=list)
    counts_4h: list[WaveCount] = field(default_factory=list)
    coherence: CoherenceResult | None = None
    ranked_daily: list[RankedCount] = field(default_factory=list)
    llm_scan: ScanResponse | None = None
    signals: list[TradeSignal] = field(default_factory=list)


def run_analysis(
    req: AnalyzeRequest,
    *,
    provider: OHLCVProvider,
    llm_mode: LLMMode = LLMMode.DISABLED,
    llm_runner=None,  # LLMRunner | None
    alpha: float = 0.6,
) -> AnalyzeResult:
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

    llm_scan = _maybe_run_scan(
        ticker=req.ticker,
        as_of=req.as_of,
        coh=coh,
        candidates=counts_daily,
        runner=llm_runner,
        mode=llm_mode,
    )
    ranked = _rank(
        candidates=counts_daily,
        coh_multiplier=coh.multiplier,
        scan=llm_scan,
        alpha=alpha,
        llm_mode=llm_mode,
    )

    signals: list[TradeSignal] = []
    if counts_daily and daily:
        latest_close = PointInTimeView(daily, req.as_of).latest().close
        for rc in ranked[:3]:
            adjusted = rc.count.model_copy(update={"score": rc.final_score})
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
        ranked_daily=ranked,
        llm_scan=llm_scan,
        signals=signals,
    )


def _maybe_run_scan(
    *,
    ticker: str,
    as_of: date,
    coh: CoherenceResult,
    candidates: list[WaveCount],
    runner,
    mode: LLMMode,
) -> ScanResponse | None:
    if mode is LLMMode.DISABLED or runner is None or not candidates:
        return None
    from wave_alpha.llm.candidates import serialize_candidate_set
    from wave_alpha.llm.client import LLMRequest
    from wave_alpha.llm.prompts import SCAN_SYSTEM_PROMPT, build_scan_user_prompt
    from wave_alpha.llm.responses import parse_scan_response

    block = serialize_candidate_set(candidates)
    user = build_scan_user_prompt(
        ticker=ticker, as_of=as_of, candidates_block=block, coherence_score=coh.score
    )
    raw = runner.run(LLMRequest(model=runner.client.model, system=SCAN_SYSTEM_PROMPT, user=user))  # type: ignore[attr-defined]
    if raw is None:
        return None
    allowed = {f"C{i + 1}" for i in range(len(candidates))}
    return parse_scan_response(raw, allowed_candidate_ids=allowed)


def _rank(
    *,
    candidates: list[WaveCount],
    coh_multiplier: float,
    scan: ScanResponse | None,
    alpha: float,
    llm_mode: LLMMode,
) -> list[RankedCount]:
    probs: dict[str, float] = {}
    if scan is not None:
        probs = {c.id: c.probability for c in scan.candidates}
    out: list[RankedCount] = []
    for i, c in enumerate(candidates):
        cid = f"C{i + 1}"
        llm_prob = probs.get(cid) if scan is not None else None
        final = blend_scores(
            coh_multiplier=coh_multiplier,
            engine_score=c.score,
            llm_prob=llm_prob if llm_mode is not LLMMode.DISABLED else None,
            alpha=alpha,
        )
        out.append(RankedCount(candidate_id=cid, count=c, engine_score=c.score, llm_prob=llm_prob, final_score=final))
    out.sort(key=lambda rc: rc.final_score, reverse=True)
    return out


def _bars(provider: OHLCVProvider, ticker: str, interval: Interval, as_of: date) -> list[Bar]:
    raw = provider.fetch(ticker, interval, end=as_of)
    if not raw:
        return []
    return PointInTimeView(raw, as_of).visible()


def _enumerate_for(bars: list[Bar], interval: Interval) -> list[WaveCount]:
    if not bars:
        return []
    by_degree = detect_multi_degree(bars, interval=interval, atr_multiples=_DEFAULT_ATR_MULTIPLES)
    pivots: list[PivotEvent] = by_degree.get(_DEFAULT_DEGREE_FOR_COUNT, [])
    return enumerate_counts(pivots, degree=_DEFAULT_DEGREE_FOR_COUNT, top_k=5)
```

The `runner.client.model` attribute is referenced by `_maybe_run_scan`. Add a `model: str` field to `AnthropicClient` so the runner can convey the active model id. Update `src/wave_alpha/llm/client.py` `AnthropicClient`:

```python
@dataclass
class AnthropicClient:
    api_key: str | None
    model: str = "claude-haiku-4-5-20251001"
    _sdk_factory: Callable[..., Any] | None = None
    ...
```

Update tests in `tests/llm/test_client.py` accordingly — `AnthropicClient(api_key="sk-test", model="claude-haiku-4-5-20251001", _sdk_factory=...)`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_pipeline.py tests/llm/test_client.py -v`
Expected: all PASS.

Run: `uv run pytest`
Expected: full suite passes.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/pipeline.py src/wave_alpha/llm/client.py tests/test_pipeline.py tests/llm/test_client.py
git commit -m "$(cat <<'EOF'
feat(pipeline): wire LLM scan, ranking, and blended final score

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Live-mode end-to-end test with stub client

**Files:**
- Modify: `/Users/alexchen/Documents/project/wave_alpha/tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline.py`:

```python
from datetime import datetime as _datetime

from wave_alpha.llm.cache import LLMResponseCache
from wave_alpha.llm.client import LLMRequest as _LLMRequest
from wave_alpha.llm.runner import LLMRunner


class _StubLLMClient:
    model = "claude-haiku-4-5-20251001"

    def __init__(self, response: str) -> None:
        self.response = response

    def run(self, req: _LLMRequest) -> str:  # noqa: ARG002
        return self.response


def test_run_analysis_live_mode_uses_llm_probabilities(tmp_path) -> None:
    # Stub returns a probability vector that boosts C2 over C1
    raw = (
        '{"candidates": ['
        '{"id": "C1", "probability": 0.2, "narrative": "low"},'
        '{"id": "C2", "probability": 0.5, "narrative": "boosted"},'
        '{"id": "C3", "probability": 0.3, "narrative": "mid"}],'
        '"engine_missed": false, "engine_missed_reason": null}'
    )
    client = _StubLLMClient(response=raw)
    cache = LLMResponseCache(db_path=tmp_path / "llm.sqlite")
    runner = LLMRunner(client=client, cache=cache, mode=LLMMode.LIVE, now=lambda: _datetime(2024, 1, 1))

    provider = _SyntheticImpulseProvider()
    req = AnalyzeRequest(ticker="AAPL", as_of=date(2024, 12, 31))
    result = run_analysis(req, provider=provider, llm_mode=LLMMode.LIVE, llm_runner=runner, alpha=0.0)
    # alpha=0 → final_score driven entirely by llm_prob (× coh)
    if len(result.ranked_daily) >= 2:
        top = result.ranked_daily[0]
        assert top.candidate_id == "C2"
        assert top.llm_prob == 0.5
```

- [ ] **Step 2: Run test, verify it fails (until enumerator returns ≥2 candidates)**

Run: `uv run pytest tests/test_pipeline.py::test_run_analysis_live_mode_uses_llm_probabilities -v`
Expected: PASS or `if` block early-exits gracefully — depending on synthetic provider output. The test asserts only when ≥2 candidates exist; the stubbed JSON allows up to 3 IDs but `parse_scan_response` will raise `LLMParseError` if `allowed_candidate_ids` < {C1, C2, C3}.

If the synthetic provider yields fewer than 3 candidates and the test fails on parse error, broaden the synthetic provider in this test to a 6-pivot impulse that consistently yields 3+ candidates. Keep this as the only step-3 fix; do not over-engineer.

- [ ] **Step 3: Commit**

```bash
git add tests/test_pipeline.py
git commit -m "$(cat <<'EOF'
test(pipeline): live LLM-mode end-to-end with stub client

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase J — CLI integration

### Task 13: analyze command accepts --llm-mode and --alpha

**Files:**
- Modify: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/cli/app.py`
- Modify: `/Users/alexchen/Documents/project/wave_alpha/tests/test_cli.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_cli.py`:

```python
def test_analyze_help_documents_llm_mode_flag() -> None:
    res = runner.invoke(app, ["analyze", "--help"])
    assert res.exit_code == 0
    assert "--llm-mode" in res.stdout
    assert "--alpha" in res.stdout


def test_analyze_disabled_mode_smoke(monkeypatch) -> None:
    from wave_alpha.cli import app as app_module

    monkeypatch.setattr(app_module, "_default_provider", lambda: _SyntheticImpulseProvider())
    res = runner.invoke(
        app, ["analyze", "AAPL", "--as-of", "2024-12-31", "--llm-mode", "disabled", "--json"]
    )
    assert res.exit_code == 0, res.stdout
    payload = json.loads(res.stdout)
    assert "ranked_daily" in payload
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — flags not present.

- [ ] **Step 3: Implement in CLI**

Modify `src/wave_alpha/cli/app.py` `analyze` signature and body:

```python
from wave_alpha.llm.cache import LLMResponseCache
from wave_alpha.llm.client import AnthropicClient, MissingAPIKeyError
from wave_alpha.llm.modes import LLMMode
from wave_alpha.llm.runner import LLMRunner
from wave_alpha.prefs.config import load_config, resolve_api_key

# ... existing imports ...

@app.command()
def analyze(
    ticker: str = typer.Argument(..., help="Ticker symbol, e.g. AAPL"),
    as_of: str | None = typer.Option(None, "--as-of"),
    json_out: bool = typer.Option(False, "--json"),
    llm_mode: LLMMode = typer.Option(
        LLMMode.DISABLED,
        "--llm-mode",
        help="LLM execution mode: live, cached, or disabled.",
    ),
    alpha: float | None = typer.Option(
        None, "--alpha", help="Override LLMConfig.alpha (0..1). Defaults to config."
    ),
) -> None:
    resolved_as_of = date.fromisoformat(as_of) if as_of else date.today()
    cfg = load_config()
    effective_alpha = alpha if alpha is not None else cfg.llm.alpha
    provider = _default_provider()
    runner = _build_runner(cfg, mode=llm_mode) if llm_mode is not LLMMode.DISABLED else None
    req = AnalyzeRequest(ticker=ticker.upper(), as_of=resolved_as_of)
    logger.debug("analyze request {} mode={} alpha={}", req, llm_mode.value, effective_alpha)
    result = run_analysis(
        req,
        provider=provider,
        llm_mode=llm_mode,
        llm_runner=runner,
        alpha=effective_alpha,
    )
    if json_out:
        typer.echo(json.dumps(_serialize(result), default=_json_default))
    else:
        _print_human(result)


def _build_runner(cfg, *, mode: LLMMode) -> LLMRunner | None:
    api_key = resolve_api_key(cfg)
    try:
        client = AnthropicClient(api_key=api_key, model=cfg.llm.scan_model)
    except MissingAPIKeyError as exc:
        if mode is LLMMode.LIVE:
            raise typer.BadParameter(str(exc)) from exc
        return None
    cache = LLMResponseCache(db_path=_DEFAULT_DATA_DIR / "llm_cache.sqlite")
    _DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return LLMRunner(client=client, cache=cache, mode=mode)
```

Update `_print_human` to surface ranked output:

```python
def _print_human(result: Any) -> None:
    typer.echo(f"ticker:  {result.ticker}")
    typer.echo(f"as_of:   {result.as_of.isoformat()}")
    typer.echo(f"daily counts:  {len(result.counts_daily)} candidates")
    if result.coherence:
        typer.echo(f"coherence score:  {result.coherence.score:.2f}")
    typer.echo(f"ranked: {len(result.ranked_daily)}")
    for rc in result.ranked_daily[:5]:
        prob = "n/a" if rc.llm_prob is None else f"{rc.llm_prob:.2f}"
        typer.echo(
            f"  {rc.candidate_id} {rc.count.pattern}  engine={rc.engine_score:.2f}"
            f"  llm_prob={prob}  final={rc.final_score:.2f}"
        )
    typer.echo(f"signals: {len(result.signals)}")
    for sig in result.signals:
        typer.echo(
            f"  {sig.direction.upper()}  entry={sig.entry_price}  "
            f"stop={sig.stop_price}  target={sig.target_price}  rr={sig.rr:.2f}"
        )
    typer.echo("\n⚠ Informational only. Confirm thesis. Not investment advice.")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS.

Run: `uv run pytest`
Expected: full suite passes.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/cli/app.py tests/test_cli.py
git commit -m "$(cat <<'EOF'
feat(cli): analyze --llm-mode and --alpha; print ranked_daily

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: config command (init, show, set)

**Files:**
- Modify: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/cli/app.py`
- Create: `/Users/alexchen/Documents/project/wave_alpha/tests/test_cli_config.py`

- [ ] **Step 1: Write failing test**

`tests/test_cli_config.py`:

```python
from pathlib import Path

from typer.testing import CliRunner

from wave_alpha.cli.app import app

runner = CliRunner()


def test_config_show_returns_yaml(tmp_path: Path, monkeypatch) -> None:
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr("wave_alpha.cli.app._config_path", lambda: cfg_path)
    res = runner.invoke(app, ["config", "show"])
    assert res.exit_code == 0
    assert "llm:" in res.stdout


def test_config_set_writes_file(tmp_path: Path, monkeypatch) -> None:
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr("wave_alpha.cli.app._config_path", lambda: cfg_path)
    res = runner.invoke(app, ["config", "set", "api_key", "sk-test"])
    assert res.exit_code == 0
    assert "sk-test" in cfg_path.read_text()


def test_config_set_alpha_validates_range(tmp_path: Path, monkeypatch) -> None:
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr("wave_alpha.cli.app._config_path", lambda: cfg_path)
    res = runner.invoke(app, ["config", "set", "alpha", "1.5"])
    assert res.exit_code != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_config.py -v`
Expected: FAIL — `config` command undefined.

- [ ] **Step 3: Add config Typer subcommand**

Modify `src/wave_alpha/cli/app.py`. Add after existing imports/`app` declaration:

```python
config_app = typer.Typer(no_args_is_help=True, help="Manage wave-alpha config.")
app.add_typer(config_app, name="config")


def _config_path() -> Path:
    """Indirection so tests can monkeypatch the location."""
    from wave_alpha.prefs.config import DEFAULT_CONFIG_PATH

    return DEFAULT_CONFIG_PATH


@config_app.command("show")
def config_show() -> None:
    """Print current config as YAML."""
    import yaml
    from wave_alpha.prefs.config import load_config

    cfg = load_config(_config_path())
    typer.echo(yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False))


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Config key: api_key, scan_model, deep_model, alpha"),
    value: str = typer.Argument(...),
) -> None:
    """Set a config value and persist to ~/.wave_alpha/config.yaml."""
    from wave_alpha.prefs.config import load_config, save_config
    from wave_alpha.prefs.models import AppConfig, LLMConfig

    cfg = load_config(_config_path())
    if key in {"api_key", "scan_model", "deep_model"}:
        new_llm = cfg.llm.model_copy(update={key: value})
    elif key == "alpha":
        try:
            alpha = float(value)
        except ValueError as exc:
            raise typer.BadParameter("alpha must be a float in [0, 1]") from exc
        if not 0.0 <= alpha <= 1.0:
            raise typer.BadParameter("alpha must be in [0, 1]")
        new_llm = cfg.llm.model_copy(update={"alpha": alpha})
    else:
        raise typer.BadParameter(f"unknown key: {key}")
    save_config(AppConfig(llm=new_llm), _config_path())
    typer.echo(f"updated {key}")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_cli_config.py -v`
Expected: PASS.

Run: `uv run pytest`
Expected: full suite passes.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/cli/app.py tests/test_cli_config.py
git commit -m "$(cat <<'EOF'
feat(cli): config show and config set commands

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase K — Wrap-up

### Task 15: Bump version and document the new flags in README

**Files:**
- Modify: `/Users/alexchen/Documents/project/wave_alpha/src/wave_alpha/__init__.py`
- Modify: `/Users/alexchen/Documents/project/wave_alpha/pyproject.toml`
- Modify: `/Users/alexchen/Documents/project/wave_alpha/README.md`

- [ ] **Step 1: Bump version**

Modify `src/wave_alpha/__init__.py`:

```python
__version__ = "0.2.0"
```

Modify `pyproject.toml` `[project]` `version`:

```toml
version = "0.2.0"
```

- [ ] **Step 2: Document LLM flags in README**

Append to `README.md` a section titled `## LLM (v0.2)` describing:
- `wave-alpha config set api_key sk-...`
- `wave-alpha analyze AAPL --llm-mode {live,cached,disabled}`
- `--alpha 0.0..1.0` blend coefficient
- The `LLMMode.DISABLED` baseline is the engine-only ranking; `LLMMode.LIVE` calls Anthropic and writes to `~/.wave_alpha/llm_cache.sqlite`; `LLMMode.CACHED` reads cache only and raises on miss.
- Privacy: ticker symbols, pivot timestamps/prices, candidate count structures, and coherence metrics are sent to Anthropic. No P&L, no portfolio, no user notes.

Concrete content (paste verbatim):

```markdown
## LLM (v0.2)

wave-alpha can rerank and narrate the engine's candidate counts using an Anthropic API key (BYO).

```bash
# One-time setup
wave-alpha config set api_key sk-...

# Run with LLM (live API, writes to ~/.wave_alpha/llm_cache.sqlite)
wave-alpha analyze AAPL --llm-mode live

# Reproducible run from cached responses (raises on miss)
wave-alpha analyze AAPL --llm-mode cached

# Engine-only baseline (default; mandatory for backtest comparisons)
wave-alpha analyze AAPL --llm-mode disabled

# Tune the blend
wave-alpha analyze AAPL --llm-mode live --alpha 0.6
```

Final ranking: `final = coh_multiplier × (alpha × engine_score + (1 - alpha) × llm_prob)`.

What's sent to Anthropic when LLM is enabled: ticker symbols, pivot timestamps and prices, candidate count structures, and multi-TF coherence metrics. Nothing else.
```

- [ ] **Step 3: Verify**

Run: `uv run wave-alpha version`
Expected: `0.2.0`.

Run: `uv run pytest`
Expected: full suite passes.

- [ ] **Step 4: Commit**

```bash
git add src/wave_alpha/__init__.py pyproject.toml uv.lock README.md
git commit -m "$(cat <<'EOF'
chore(release): bump to v0.2.0; document LLM flags

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final review

Run the full suite and ruff:

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

All three must pass. Then merge `feat/v0.2-llm-contract` into `main` via `--no-ff` merge commit, mirroring the v0.1 cadence.

---

## Self-review notes (writer)

- Spec §6 coverage: scan-path (Tasks 5–9, 11), deep-dive prompts (Task 5), determinism via `temperature=0` and pinned model IDs (Task 7), cache (Task 8), `--llm-mode {live,cached,disabled}` (Task 13), prompt-cache breakpoint (Task 7), α-blend (Task 10).
- Spec §13 privacy: explicitly documented in Task 15 README — only ticker/pivot/count/coherence sent.
- Deferred (per spec roadmap): vision verify (needs chart PNG → Plan 3 deep-dive page); deep-dive prompt path is wired in `prompts.py` but the deep-dive runner branch lives in Plan 3's `/deepdive/{ticker}/counts` route. Plan 2 ships scan path end-to-end; deep-dive is plumbing-only here.
- Type consistency: `LLMRunner.run(LLMRequest) -> str | None`, `_StubLLMClient.model` matches `AnthropicClient.model`; `RankedCount.candidate_id` matches `ScanCandidate.id` via the `f"C{i+1}"` convention.
- Testing: each unit has its own test file; one end-to-end pipeline test for live mode with stub client. No tests call the real Anthropic API.
