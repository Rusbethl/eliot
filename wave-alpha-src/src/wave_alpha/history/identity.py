"""Stable identity hash for a WaveCount used by the history-stability badge.

The hash captures (pattern, degree, ordered pivot sequence). Any drift in
pivots — including a single bar shifting an extreme — yields a different
hash, so two snapshots with the same hash represent genuinely the same
count, not just the same template/label.
"""

from __future__ import annotations

import hashlib

from wave_alpha.elliott.models import WaveCount


def top_count_hash(c: WaveCount) -> str:
    key = (
        c.pattern,
        c.degree,
        tuple((p.timestamp.isoformat(), str(p.price)) for p in c.pivots),
    )
    # repr of (str, int, tuple[tuple[str, str]]) is stable across CPython versions —
    # all components are stdlib built-ins with spec-defined repr.
    return hashlib.sha256(repr(key).encode()).hexdigest()[:16]
