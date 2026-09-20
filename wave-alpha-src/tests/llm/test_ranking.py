import pytest

from wave_alpha.llm.ranking import blend_scores


def test_alpha_one_returns_engine_score_times_multiplier() -> None:
    assert blend_scores(
        coh_multiplier=1.0, engine_score=0.6, llm_prob=0.9, alpha=1.0
    ) == pytest.approx(0.6)


def test_alpha_zero_returns_llm_prob_times_multiplier() -> None:
    assert blend_scores(
        coh_multiplier=1.0, engine_score=0.6, llm_prob=0.9, alpha=0.0
    ) == pytest.approx(0.9)


def test_alpha_default_is_engine_dominant() -> None:
    blended = blend_scores(coh_multiplier=1.0, engine_score=0.6, llm_prob=0.9, alpha=0.6)
    assert blended == pytest.approx(0.6 * 0.6 + 0.4 * 0.9)


def test_coherence_multiplier_scales_result() -> None:
    base = blend_scores(coh_multiplier=1.0, engine_score=0.5, llm_prob=0.5, alpha=0.6)
    scaled = blend_scores(coh_multiplier=1.2, engine_score=0.5, llm_prob=0.5, alpha=0.6)
    assert scaled == pytest.approx(1.2 * base)


def test_llm_disabled_via_none_treats_alpha_as_one() -> None:
    """When LLM is disabled, llm_prob=None means the blend is engine-only."""
    assert blend_scores(
        coh_multiplier=1.0, engine_score=0.6, llm_prob=None, alpha=0.6
    ) == pytest.approx(0.6)


def test_alpha_out_of_range_raises() -> None:
    with pytest.raises(ValueError):
        blend_scores(coh_multiplier=1.0, engine_score=0.6, llm_prob=0.5, alpha=1.5)


# ---------------------------------------------------------------------------
# Logistic path (new in T5)
# ---------------------------------------------------------------------------


def test_logistic_path_no_llm_returns_engine_prob() -> None:
    """With use_logistic_path=True and llm_prob=None, the function must
    return engine_prob unchanged — no coh_multiplier discount, no alpha."""
    out = blend_scores(
        coh_multiplier=1.5,  # intentionally != 1 to prove it's ignored
        engine_score=0.0,  # ignored on the logistic path
        llm_prob=None,
        alpha=0.6,
        engine_prob=0.72,
        use_logistic_path=True,
    )
    assert out == pytest.approx(0.72)


def test_logistic_path_with_llm_blends_two_probabilities() -> None:
    out = blend_scores(
        coh_multiplier=1.5,
        engine_score=0.0,
        llm_prob=0.40,
        alpha=0.7,
        engine_prob=0.80,
        use_logistic_path=True,
    )
    # 0.7 * 0.80 + 0.3 * 0.40 = 0.56 + 0.12 = 0.68
    assert out == pytest.approx(0.68)


def test_legacy_path_unchanged_when_use_logistic_false() -> None:
    """The heuristic-path formula must be byte-identical to today's behavior."""
    out = blend_scores(
        coh_multiplier=1.2,
        engine_score=0.5,
        llm_prob=0.7,
        alpha=0.6,
    )
    # 1.2 * (0.6 * 0.5 + 0.4 * 0.7) = 1.2 * (0.30 + 0.28) = 1.2 * 0.58 = 0.696
    assert out == pytest.approx(0.696)


def test_logistic_path_requires_engine_prob() -> None:
    with pytest.raises(ValueError, match="engine_prob"):
        blend_scores(
            coh_multiplier=1.0,
            engine_score=0.5,
            llm_prob=0.5,
            alpha=0.5,
            use_logistic_path=True,
            engine_prob=None,
        )
