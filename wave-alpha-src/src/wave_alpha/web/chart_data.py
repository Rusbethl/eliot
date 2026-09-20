from __future__ import annotations

from typing import Any

from wave_alpha.domain import Bar
from wave_alpha.elliott.models import WaveCount
from wave_alpha.pivots.models import PivotState, PivotType


def build_chart_payload(*, bars: list[Bar], top_count: WaveCount | None) -> dict[str, Any]:
    """Convert engine outputs into the JSON shape Lightweight Charts expects.

    bars     → list of {time, open, high, low, close} dicts (sorted)
    markers  → one per pivot in `top_count`, labeled P1..PN; confirmed pivots
               render as circles, tentative as squares
    """
    bar_payload = [
        {
            "time": b.timestamp.isoformat(),
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "close": float(b.close),
        }
        for b in bars
    ]
    markers = _markers(top_count) if top_count is not None else []
    return {"bars": bar_payload, "markers": markers}


def _markers(count: WaveCount) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, p in enumerate(count.pivots, start=1):
        out.append(
            {
                "time": p.timestamp.isoformat(),
                "price": float(p.price),
                "position": "belowBar" if p.type is PivotType.LOW else "aboveBar",
                "shape": "circle" if p.state is PivotState.CONFIRMED else "square",
                "color": "#3fb950" if p.type is PivotType.LOW else "#f85149",
                "text": f"P{i}",
            }
        )
    return out
