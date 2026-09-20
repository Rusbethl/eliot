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
    """Parse the model's JSON reply.

    Live models sometimes emit a valid JSON object followed by trailing
    prose (a closing remark, a second illustrative example, etc.) even
    when the system prompt says "respond with valid JSON" — this is
    common on some Claude versions/temperatures. `json.loads` rejects
    that as "Extra data", crashing an otherwise-usable response. We only
    need the *first* JSON object on the line, so decode just that and
    ignore anything after it, rather than requiring the whole string to
    be nothing but JSON.
    """
    cleaned = _strip_fences(raw)
    try:
        loaded, _end = json.JSONDecoder().raw_decode(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMParseError(f"invalid JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise LLMParseError("response root must be a JSON object")
    return loaded


def _validate_probabilities(probs: list[float], *, where: str) -> None:
    total = sum(probs)
    if not 0.99 <= total <= 1.01:
        raise LLMParseError(f"{where} probabilities must sum to ~1.0; got {total:.3f}")


def parse_scan_response(raw: str, *, allowed_candidate_ids: set[str] | None = None) -> ScanResponse:
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
