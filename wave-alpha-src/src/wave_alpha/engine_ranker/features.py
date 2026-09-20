"""Engine-ranker feature vector and extraction helper."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from wave_alpha.coherence.score import CoherenceResult
from wave_alpha.elliott.models import WaveCount


class EngineRankerFeatures(BaseModel):
    """The four inputs to the logistic re-ranker, plus the degree used for
    per-degree intercept lookup. Bounded ranges:

    - template_fit ∈ [0, 1] (soft-violation penalty from enumerator)
    - fib ∈ [0, 1] (confluence_score output)
    - recency ∈ [0.2, 1.0] (linear decay over pivot gap)
    - coherence ∈ [0, 1] (three_way coherence score; NOT the multiplier)
    """

    model_config = ConfigDict(frozen=True)

    template_fit: float
    fib: float
    recency: float
    coherence: float
    degree: int

    @classmethod
    def from_count(
        cls, count: WaveCount, coherence: CoherenceResult | None
    ) -> EngineRankerFeatures:
        """Build features from a scored WaveCount and the snapshot's coherence
        result.

        Raises:
            ValueError: if any of the component fields on `count` is None
                (i.e. the count was never run through the enumerator's
                scorer). Refusing here prevents phantom rows in training.
        """
        if (
            count.template_fit_component is None
            or count.fib_component is None
            or count.recency_component is None
        ):
            raise ValueError(
                "WaveCount has None components; cannot build EngineRankerFeatures. "
                "Was the count built by hand instead of via enumerate_counts()?"
            )
        coh_score = 0.5 if coherence is None else float(coherence.score)
        return cls(
            template_fit=float(count.template_fit_component),
            fib=float(count.fib_component),
            recency=float(count.recency_component),
            coherence=coh_score,
            degree=int(count.degree),
        )
