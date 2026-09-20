from __future__ import annotations

from typing import Literal

CoherenceBucket = Literal["full", "partial", "disagreement"]

# (name, lo_inclusive, hi_exclusive). hi=1.01 to include 1.0 in "full".
COHERENCE_BUCKETS: tuple[tuple[CoherenceBucket, float, float], ...] = (
    ("full", 0.7, 1.01),
    ("partial", 0.4, 0.7),
    ("disagreement", 0.0, 0.4),
)


def bucket_for(coh: float) -> CoherenceBucket:
    for name, lo, hi in COHERENCE_BUCKETS:
        if lo <= coh < hi:
            return name
    return "disagreement"  # safety fallback for negative scores
