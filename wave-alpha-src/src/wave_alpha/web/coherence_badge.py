from __future__ import annotations

from typing import Literal

from wave_alpha.coherence.score import CoherenceResult

Status = Literal["ok", "warn", "missing"]
_AGREEMENT_THRESHOLD = 0.6


def build_tri_badge(coh: CoherenceResult) -> list[tuple[str, Status]]:
    """Per-timeframe status for the tri-badge.

    W: weekly_daily >= threshold -> ok; missing -> missing; otherwise -> warn.
    D: present whenever weekly_daily key exists (D is the trunk) -> ok; else missing.
    4h: daily_4h >= threshold -> ok; key missing -> missing; otherwise -> warn.

    Spec §11 #3.
    """
    if coh.fallback_used == "insufficient_history":
        return [("W", "missing"), ("D", "missing"), ("4h", "missing")]

    pw = coh.pairwise
    weekly_daily = pw.get("weekly_daily")
    daily_4h = pw.get("daily_4h")

    w_status: Status
    if weekly_daily is None:
        w_status = "missing"
    elif weekly_daily >= _AGREEMENT_THRESHOLD:
        w_status = "ok"
    else:
        w_status = "warn"

    d_status: Status = "ok" if weekly_daily is not None else "missing"

    h4_status: Status
    if coh.fallback_used == "no_4h_history" or daily_4h is None:
        h4_status = "missing"
    elif daily_4h >= _AGREEMENT_THRESHOLD:
        h4_status = "ok"
    else:
        h4_status = "warn"

    return [("W", w_status), ("D", d_status), ("4h", h4_status)]
