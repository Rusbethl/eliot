from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from wave_alpha.engine_ranker.loader import EngineRankerModel

from wave_alpha.coherence.score import (
    CoherenceInputs,
    CoherenceResult,
    three_way,
)
from wave_alpha.data.point_in_time import PointInTimeView
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval
from wave_alpha.elliott.enumerator import enumerate_counts
from wave_alpha.elliott.models import WaveCount
from wave_alpha.llm.modes import LLMMode
from wave_alpha.llm.ranking import blend_scores
from wave_alpha.llm.responses import ScanResponse
from wave_alpha.pivots.multi_degree import detect_multi_degree
from wave_alpha.trades.derive import derive_signal
from wave_alpha.trades.models import TradeRules, TradeSignal

_DEFAULT_ATR_MULTIPLES: list[Decimal] = [
    Decimal("0.5"),
    Decimal("1"),
    Decimal("2"),
    Decimal("4"),
    Decimal("8"),
]
# The "calibrated baseline" degree — where empirical EBT lives (degree-2
# was empirically fit in the 2026-05-07 EBT spec).  Single-degree consumers
# (right_edge assessment, count-layer backtest, web deepdive default) key
# off this.  The analyze pipeline itself enumerates across `_ENUMERATED_DEGREES`
# below.
_DEFAULT_DEGREE_FOR_COUNT = 2

# Degrees enumerated by `_enumerate_for` for the analyze pipeline.  Per-degree
# top-K cap from `_TOP_K_PER_DEGREE` keeps the candidate fan-out bounded.
# See spec 2026-05-08-multi-degree-enumeration-design.md §3.1.
_ENUMERATED_DEGREES: tuple[int, ...] = (1, 2, 3)
_TOP_K_PER_DEGREE: int = 2


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    ticker: str
    as_of: date
    rules: TradeRules = TradeRules()


@dataclass(frozen=True)
class RankedCount:
    candidate_id: str
    count: WaveCount
    engine_score: float
    llm_prob: float | None
    final_score: float
    engine_prob: float | None = None


@dataclass
class AnalyzeResult:
    ticker: str
    as_of: date
    counts_weekly: list[WaveCount] = field(default_factory=list)
    counts_daily: list[WaveCount] = field(default_factory=list)
    counts_4h: list[WaveCount] = field(default_factory=list)
    coherence: CoherenceResult | None = None
    ranked_daily: list[RankedCount] = field(default_factory=list)
    llm_scan: ScanResponse | None = None
    signals: list[TradeSignal] = field(default_factory=list)
    # Number of ranked candidates that derive_signal raised ValueError on
    # (typically atr_multiple stop_source with insufficient bars).  These
    # are silently skipped to preserve sibling candidates; the counter
    # surfaces the skip rate for diagnosis.
    n_atr_skipped: int = 0
    daily_bars: list[Bar] = field(default_factory=list)


_DEFAULT_TIMEFRAMES: tuple[Interval, Interval, Interval] = (
    Interval.WEEKLY,
    Interval.DAILY,
    Interval.HOURLY_4,
)


def run_analysis(
    req: AnalyzeRequest,
    *,
    provider: OHLCVProvider,
    llm_mode: LLMMode = LLMMode.DISABLED,
    llm_runner=None,
    alpha: float = 0.6,
    enabled_opt_in_templates: frozenset[str] = frozenset(),
    engine_ranker: EngineRankerModel | None = None,
    external_context: str | None = None,
    timeframes: tuple[Interval, Interval, Interval] = _DEFAULT_TIMEFRAMES,
) -> AnalyzeResult:
    """Run the three-tier (high/mid/low) analysis.

    `timeframes` is (high, mid, low) — default (WEEKLY, DAILY, HOURLY_4),
    the "Swing" preset. A caller can pass a different triple (e.g.
    dashboard.py's "Scalping" preset, (HOURLY_4, HOURLY_1, MINUTE_30)) to
    run the exact same coherence/ranking/signal-derivation logic one or
    two tiers lower. The `AnalyzeResult` fields keep their historical
    names (counts_weekly/counts_daily/counts_4h, daily_bars, ranked_daily)
    for backward compatibility with every other caller (CLI, backtest,
    history, web) that assumes the Swing default — under a non-default
    `timeframes`, those names hold the high/mid/low tier's data instead of
    literal weekly/daily/4h bars.
    """
    tf_high, tf_mid, tf_low = timeframes
    weekly = _bars(provider, req.ticker, tf_high, req.as_of)
    daily = _bars(provider, req.ticker, tf_mid, req.as_of)
    hourly4 = _bars(provider, req.ticker, tf_low, req.as_of)

    counts_weekly = _enumerate_for(weekly, tf_high, enabled_opt_in_templates)
    counts_daily = _enumerate_for(daily, tf_mid, enabled_opt_in_templates)
    counts_4h = _enumerate_for(hourly4, tf_low, enabled_opt_in_templates)

    coh = three_way(
        CoherenceInputs(
            weekly=counts_weekly[0] if counts_weekly else None,
            daily=counts_daily[0] if counts_daily else None,
            hourly4=counts_4h[0] if counts_4h else None,
        )
    )

    llm_scan = _maybe_run_scan(
        ticker=req.ticker,
        as_of=req.as_of,
        coh=coh,
        candidates=counts_daily,
        runner=llm_runner,
        mode=llm_mode,
        external_context=external_context,
    )
    if engine_ranker is None:
        from wave_alpha.engine_ranker.loader import load_engine_ranker
        from wave_alpha.prefs.config import load_config

        engine_ranker = load_engine_ranker(load_config().engine_ranker)

    ranked = _rank(
        candidates=counts_daily,
        coherence=coh,
        scan=llm_scan,
        alpha=alpha,
        llm_mode=llm_mode,
        engine_ranker=engine_ranker,
    )

    signals: list[TradeSignal] = []
    n_atr_skipped = 0
    if counts_daily and daily:
        latest_close = PointInTimeView(daily, req.as_of).latest().close
        for rc in ranked[:3]:
            adjusted = rc.count.model_copy(update={"score": rc.final_score})
            try:
                sig = derive_signal(
                    count=adjusted,
                    ticker=req.ticker,
                    as_of=req.as_of,
                    current_price=latest_close,
                    rules=req.rules,
                    bars=daily,
                )
            except ValueError:
                # atr_multiple stop_source needed bars but they're
                # missing or insufficient for ATR(14). Skip this
                # candidate without dropping the others in ranked[:3].
                n_atr_skipped += 1
                continue
            if sig is not None:
                signals.append(sig)

    return AnalyzeResult(
        ticker=req.ticker,
        as_of=req.as_of,
        counts_weekly=counts_weekly,
        counts_daily=counts_daily,
        counts_4h=counts_4h,
        coherence=coh,
        ranked_daily=ranked,
        llm_scan=llm_scan,
        signals=signals,
        n_atr_skipped=n_atr_skipped,
        daily_bars=daily,
    )


def _maybe_run_scan(
    *,
    ticker: str,
    as_of: date,
    coh: CoherenceResult,
    candidates: list[WaveCount],
    runner,
    mode: LLMMode,
    external_context: str | None = None,
) -> ScanResponse | None:
    if mode is LLMMode.DISABLED or runner is None or not candidates:
        return None
    from wave_alpha.llm.candidates import serialize_candidate_set
    from wave_alpha.llm.client import LLMRequest
    from wave_alpha.llm.prompts import SCAN_SYSTEM_PROMPT, build_scan_user_prompt
    from wave_alpha.llm.responses import parse_scan_response

    block = serialize_candidate_set(candidates)
    user = build_scan_user_prompt(
        ticker=ticker,
        as_of=as_of,
        candidates_block=block,
        coherence_score=coh.score,
        external_context=external_context,
    )
    raw = runner.run(LLMRequest(model=runner.client.model, system=SCAN_SYSTEM_PROMPT, user=user))
    if raw is None:
        return None
    allowed = {f"C{i + 1}" for i in range(len(candidates))}
    return parse_scan_response(raw, allowed_candidate_ids=allowed)


def _rank(
    *,
    candidates: list[WaveCount],
    coherence: CoherenceResult,
    scan: ScanResponse | None,
    alpha: float,
    llm_mode: LLMMode,
    engine_ranker: EngineRankerModel,
) -> list[RankedCount]:
    from wave_alpha.engine_ranker.features import EngineRankerFeatures
    from wave_alpha.engine_ranker.logistic import LogisticEngineRanker

    use_logistic = isinstance(engine_ranker, LogisticEngineRanker)
    probs: dict[str, float] = {}
    if scan is not None:
        probs = {c.id: c.probability for c in scan.candidates}
    out: list[RankedCount] = []
    for i, c in enumerate(candidates):
        cid = f"C{i + 1}"
        llm_prob = probs.get(cid) if scan is not None else None
        engine_prob: float | None = None
        if use_logistic:
            try:
                feats = EngineRankerFeatures.from_count(c, coherence)
                engine_prob = engine_ranker.predict_proba(feats)
            except ValueError:
                # Components missing or feature/weight mismatch. Set engine_prob=None
                # so this row falls back to the heuristic formula via the
                # `use_logistic_path=False` branch below. Note: in a logistic-mode
                # run with one fallback row, that row's final_score is on the legacy
                # scale (coh_multiplier * engine_score) while sibling rows are
                # engine_prob in [0,1] — they'll sort together but on different
                # scales. The trigger (None components on a count from
                # enumerate_counts) is unreachable in production; this branch
                # exists for defensive symmetry with EngineRankerFeatures.from_count.
                engine_prob = None
        final = blend_scores(
            coh_multiplier=coherence.multiplier,
            engine_score=c.score,
            llm_prob=llm_prob if llm_mode is not LLMMode.DISABLED else None,
            alpha=alpha,
            engine_prob=engine_prob,
            use_logistic_path=use_logistic and engine_prob is not None,
        )
        out.append(
            RankedCount(
                candidate_id=cid,
                count=c,
                engine_score=c.score,
                llm_prob=llm_prob,
                final_score=final,
                engine_prob=engine_prob,
            )
        )
    out.sort(key=lambda rc: rc.final_score, reverse=True)
    return out


def _bars(provider: OHLCVProvider, ticker: str, interval: Interval, as_of: date) -> list[Bar]:
    raw = provider.fetch(ticker, interval, end=as_of)
    if not raw:
        return []
    return PointInTimeView(raw, as_of).visible()


def _enumerate_for(
    bars: list[Bar],
    interval: Interval,
    enabled_opt_in_templates: frozenset[str] = frozenset(),
) -> list[WaveCount]:
    """Enumerate candidates across all configured degrees and pool the
    results.  Each degree contributes up to `_TOP_K_PER_DEGREE` candidates;
    degrees with no pivots contribute nothing.

    Per-degree top-K is preferred over a global top-K so that one degree's
    score distribution cannot crowd out the others (degree-2 has a
    calibrated score baseline from the 2026-05-07 EBT fit; degrees 1 and 3
    do not).  See spec 2026-05-08-multi-degree-enumeration-design.md §3.1.

    For composite templates (wxy) the lower-degree pivot list at degree-1
    is passed via ``sub_pivots`` so the enumerator can fit primitive
    corrective sub-counts.  See spec 2026-05-10-complex-corrections-design.md §3.6.

    ``enabled_opt_in_templates`` is forwarded to the enumerator so the
    caller's ``AppConfig.templates.enabled_opt_in_names()`` choice
    propagates to default-template filtering. See
    docs/superpowers/specs/2026-05-10-leading-diagonal-design.md §10.
    """
    if not bars:
        return []
    by_degree = detect_multi_degree(bars, interval=interval, atr_multiples=_DEFAULT_ATR_MULTIPLES)
    candidates: list[WaveCount] = []
    for degree in _ENUMERATED_DEGREES:
        pivots = by_degree.get(degree, [])
        sub_pivots = by_degree.get(degree - 1) if degree > 0 else None
        candidates.extend(
            enumerate_counts(
                pivots,
                degree=degree,
                top_k=_TOP_K_PER_DEGREE,
                sub_pivots=sub_pivots,
                enabled_opt_in_templates=enabled_opt_in_templates,
            )
        )
    return candidates
