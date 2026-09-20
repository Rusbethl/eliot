from wave_alpha.coherence.score import CoherenceResult
from wave_alpha.web.coherence_badge import build_tri_badge


def _coh(pairwise: dict, fallback: str | None = None) -> CoherenceResult:
    return CoherenceResult(score=0.5, multiplier=1.0, pairwise=pairwise, fallback_used=fallback)


def test_all_three_agree_returns_three_checks() -> None:
    coh = _coh({"weekly_daily": 0.8, "daily_4h": 0.7, "weekly_4h": 0.7})
    badges = build_tri_badge(coh)
    assert badges == [("W", "ok"), ("D", "ok"), ("4h", "ok")]


def test_4h_disagreement_renders_warn() -> None:
    coh = _coh({"weekly_daily": 0.8, "daily_4h": 0.3, "weekly_4h": 0.4})
    badges = build_tri_badge(coh)
    assert badges == [("W", "ok"), ("D", "ok"), ("4h", "warn")]


def test_weekly_disagreement_renders_warn() -> None:
    coh = _coh({"weekly_daily": 0.4, "daily_4h": 0.7, "weekly_4h": 0.5})
    badges = build_tri_badge(coh)
    assert badges == [("W", "warn"), ("D", "ok"), ("4h", "ok")]


def test_no_4h_renders_missing() -> None:
    coh = _coh({"weekly_daily": 0.8}, fallback="no_4h_history")
    badges = build_tri_badge(coh)
    assert badges == [("W", "ok"), ("D", "ok"), ("4h", "missing")]


def test_insufficient_history_returns_only_daily() -> None:
    coh = _coh({}, fallback="insufficient_history")
    badges = build_tri_badge(coh)
    # Without weekly, can't say W is ok — render as missing
    assert badges == [("W", "missing"), ("D", "missing"), ("4h", "missing")]
