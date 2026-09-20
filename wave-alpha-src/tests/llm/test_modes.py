from wave_alpha.llm.modes import LLMMode


def test_llm_mode_string_values() -> None:
    assert LLMMode.LIVE.value == "live"
    assert LLMMode.CACHED.value == "cached"
    assert LLMMode.DISABLED.value == "disabled"


def test_llm_mode_lookup() -> None:
    assert LLMMode("live") is LLMMode.LIVE
    assert LLMMode("cached") is LLMMode.CACHED
    assert LLMMode("disabled") is LLMMode.DISABLED
