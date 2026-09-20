from __future__ import annotations


def blend_scores(
    *,
    coh_multiplier: float,
    engine_score: float,
    llm_prob: float | None,
    alpha: float,
    engine_prob: float | None = None,
    use_logistic_path: bool = False,
) -> float:
    """Final ranking. Two paths:

    1. Logistic path (use_logistic_path=True): engine_prob is a calibrated
       probability that already includes coherence as a feature.

           final = alpha * engine_prob + (1 - alpha) * llm_prob

       coh_multiplier and engine_score are ignored. llm_prob=None collapses
       to engine_prob.

    2. Legacy heuristic path (default): preserves the v0 formula bit-for-bit:

           final = coh_multiplier * (alpha * engine_score + (1 - alpha) * llm_prob)

       llm_prob=None collapses to coh_multiplier * engine_score.
    """
    if not 0.0 <= alpha <= 1.0:
        raise ValueError(f"alpha must be in [0, 1]; got {alpha}")
    if use_logistic_path:
        if engine_prob is None:
            raise ValueError("use_logistic_path=True requires engine_prob")
        if llm_prob is None:
            return engine_prob
        return alpha * engine_prob + (1.0 - alpha) * llm_prob
    if llm_prob is None:
        return coh_multiplier * engine_score
    return coh_multiplier * (alpha * engine_score + (1.0 - alpha) * llm_prob)
