from datetime import date

from wave_alpha.llm.prompts import (
    DEEP_DIVE_SYSTEM_PROMPT,
    SCAN_SYSTEM_PROMPT,
    build_deep_dive_user_prompt,
    build_scan_user_prompt,
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


def test_build_scan_user_prompt_omits_context_section_when_not_given() -> None:
    """Backward compatibility: existing callers that never pass
    external_context must get byte-identical prompts to before this
    feature existed (no dangling 'External context' header)."""
    prompt = build_scan_user_prompt(
        ticker="AAPL",
        as_of=date(2024, 12, 31),
        candidates_block="[C1] pattern=impulse",
        coherence_score=0.82,
    )
    assert "External context" not in prompt


def test_build_scan_user_prompt_includes_external_context_when_given() -> None:
    prompt = build_scan_user_prompt(
        ticker="BTCUSDT",
        as_of=date(2024, 12, 31),
        candidates_block="[C1] pattern=impulse",
        coherence_score=0.48,
        external_context="Fed cut rates 50bps; large liquidity cluster at 58000 on Coinglass heatmap.",
    )
    assert "External context" in prompt
    assert "liquidity cluster at 58000" in prompt


def test_build_scan_user_prompt_ignores_blank_context() -> None:
    prompt = build_scan_user_prompt(
        ticker="BTCUSDT",
        as_of=date(2024, 12, 31),
        candidates_block="[C1] pattern=impulse",
        coherence_score=0.48,
        external_context="   ",
    )
    assert "External context" not in prompt


def test_build_deep_dive_user_prompt_includes_external_context_when_given() -> None:
    prompt = build_deep_dive_user_prompt(
        ticker="BTCUSDT",
        as_of=date(2024, 12, 31),
        candidates_block="[C1] pattern=impulse",
        scenarios_block="[S1] tentative_high",
        coherence_score=0.48,
        external_context="Funding rate deeply negative; short squeeze risk.",
    )
    assert "External context" in prompt
    assert "short squeeze risk" in prompt
