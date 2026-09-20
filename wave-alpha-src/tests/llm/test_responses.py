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
