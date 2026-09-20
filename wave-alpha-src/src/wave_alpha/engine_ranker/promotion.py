"""Promotion gate for candidate LogisticEngineRanker artifacts.

PROMOTE requires:
  (a) lower 95% CI bound of mean(Brier_baseline - Brier_candidate) ≥ 0
  (b) lower 95% CI bound of mean(R_candidate - R_baseline) ≥ 0
  (c) every observed degree in holdout has ≥ min_resolved_per_degree rows

Otherwise HOLD (a/b failed) or INSUFFICIENT_DATA (c failed).

The R-expectancy gate uses a simplified proxy: each holdout row contributes
+1 R if its label is 1 and the model would have selected it (prob ≥ 0.5),
-1 R if label is 0 and prob ≥ 0.5, 0 R otherwise. This is intentionally
coarse — the real backtest harness runs separately; this gate just rejects
obvious regressions.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from wave_alpha.engine_ranker.training import OutcomeDatasetRow


class PromotionVerdict(StrEnum):
    PROMOTE = "PROMOTE"
    HOLD = "HOLD"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class PromotionReport:
    verdict: PromotionVerdict
    brier_lift_mean: float
    brier_lift_ci: tuple[float, float]
    r_lift_mean: float
    r_lift_ci: tuple[float, float]
    rows_per_degree: dict[int, int]


def evaluate_promotion(
    *,
    candidate,
    baseline,
    holdout_rows: list[OutcomeDatasetRow],
    min_resolved_per_degree: int = 30,
    n_bootstrap: int = 10_000,
    random_state: int = 42,
) -> PromotionReport:
    # TODO(post-v1): replace the coarse +1/-1/0 R proxy with
    # pipeline.derive_signal-based realized R per the spec §5.5.
    # The proxy passes a logistic that's just well-calibrated; the
    # derive_signal path would test trade economics directly.
    rows_per_degree = Counter(r.degree for r in holdout_rows)
    if not rows_per_degree or any(n < min_resolved_per_degree for n in rows_per_degree.values()):
        return PromotionReport(
            verdict=PromotionVerdict.INSUFFICIENT_DATA,
            brier_lift_mean=float("nan"),
            brier_lift_ci=(float("nan"), float("nan")),
            r_lift_mean=float("nan"),
            r_lift_ci=(float("nan"), float("nan")),
            rows_per_degree=dict(rows_per_degree),
        )

    n = len(holdout_rows)
    p_c = np.array([candidate.predict_proba(r.features) for r in holdout_rows])
    p_b = np.array([baseline.predict_proba(r.features) for r in holdout_rows])
    y = np.array([r.label for r in holdout_rows])

    brier_c = (p_c - y) ** 2
    brier_b = (p_b - y) ** 2
    brier_diff = brier_b - brier_c  # positive = candidate wins

    r_c = np.where(p_c >= 0.5, np.where(y == 1, 1.0, -1.0), 0.0)
    r_b = np.where(p_b >= 0.5, np.where(y == 1, 1.0, -1.0), 0.0)
    r_diff = r_c - r_b  # positive = candidate wins

    rng = np.random.default_rng(random_state)
    brier_resamples = np.empty(n_bootstrap)
    r_resamples = np.empty(n_bootstrap)
    for i in range(n_bootstrap):
        idx = rng.integers(0, n, size=n)
        brier_resamples[i] = brier_diff[idx].mean()
        r_resamples[i] = r_diff[idx].mean()

    brier_ci = (
        float(np.percentile(brier_resamples, 2.5)),
        float(np.percentile(brier_resamples, 97.5)),
    )
    r_ci = (float(np.percentile(r_resamples, 2.5)), float(np.percentile(r_resamples, 97.5)))

    if brier_ci[0] >= 0 and r_ci[0] >= 0:
        verdict = PromotionVerdict.PROMOTE
    else:
        verdict = PromotionVerdict.HOLD

    return PromotionReport(
        verdict=verdict,
        brier_lift_mean=float(brier_diff.mean()),
        brier_lift_ci=brier_ci,
        r_lift_mean=float(r_diff.mean()),
        r_lift_ci=r_ci,
        rows_per_degree=dict(rows_per_degree),
    )
