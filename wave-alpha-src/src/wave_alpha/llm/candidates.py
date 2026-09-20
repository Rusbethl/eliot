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
