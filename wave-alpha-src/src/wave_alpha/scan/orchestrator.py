from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal, cast

from wave_alpha.coherence.score import predicted_direction
from wave_alpha.data.point_in_time import PointInTimeView
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Interval
from wave_alpha.llm.modes import LLMMode
from wave_alpha.llm.runner import LLMRunner
from wave_alpha.pipeline import AnalyzeRequest, run_analysis
from wave_alpha.right_edge.assessment import assess_from_bars
from wave_alpha.right_edge.loader import ConfirmationModel
from wave_alpha.scan.models import ScanReport, ScanRow


def run_scan(
    *,
    symbols: list[str],
    as_of: date,
    provider: OHLCVProvider,
    llm_mode: LLMMode = LLMMode.DISABLED,
    llm_runner: LLMRunner | None = None,
    alpha: float = 0.6,
    right_edge_model: ConfirmationModel | None = None,
    enabled_opt_in_templates: frozenset[str] = frozenset(),
) -> ScanReport:
    """Run analyze for each symbol; aggregate one ScanRow per ticker.

    Errors are captured per-ticker — one bad symbol does not abort the scan.
    LLM is disabled by default; pass llm_runner + LLMMode.LIVE to enable.
    Pass right_edge_model to populate ScanRow.right_edge_proba (the trained
    confirmation probability for the most recent tentative degree-2 pivot).
    """
    started = datetime.now(UTC)
    rows: list[ScanRow] = []
    for sym in symbols:
        rows.append(
            _scan_one(
                sym,
                as_of,
                provider,
                llm_mode,
                llm_runner,
                alpha,
                right_edge_model,
                enabled_opt_in_templates,
            )
        )
    completed = datetime.now(UTC)
    return ScanReport(
        scan_id=started.strftime("%Y-%m-%dT%H-%M-%S"),
        started_at=started,
        completed_at=completed,
        as_of=as_of,
        rows=rows,
    )


def _scan_one(
    symbol: str,
    as_of: date,
    provider: OHLCVProvider,
    llm_mode: LLMMode,
    llm_runner: LLMRunner | None,
    alpha: float,
    right_edge_model: ConfirmationModel | None,
    enabled_opt_in_templates: frozenset[str] = frozenset(),
) -> ScanRow:
    try:
        req = AnalyzeRequest(ticker=symbol.upper(), as_of=as_of)
        result = run_analysis(
            req,
            provider=provider,
            llm_mode=llm_mode,
            llm_runner=llm_runner,
            alpha=alpha,
            enabled_opt_in_templates=enabled_opt_in_templates,
        )
    except Exception as exc:  # capture per-ticker; one bad symbol must not abort the scan
        return ScanRow(
            ticker=symbol.upper(),
            as_of=as_of,
            error=f"{type(exc).__name__}: {exc}",
        )

    right_edge_proba = _right_edge_proba(symbol.upper(), as_of, provider, right_edge_model)

    if not result.counts_daily:
        return ScanRow(
            ticker=symbol.upper(),
            as_of=as_of,
            coherence=result.coherence.score if result.coherence else None,
            right_edge_proba=right_edge_proba,
            notes="no daily counts",
        )

    top_count = result.counts_daily[0]
    direction = predicted_direction(top_count)
    signal = result.signals[0] if result.signals else None
    return ScanRow(
        ticker=symbol.upper(),
        as_of=as_of,
        coherence=result.coherence.score if result.coherence else None,
        top_pattern=top_count.pattern,
        top_score=result.ranked_daily[0].final_score if result.ranked_daily else top_count.score,
        direction=cast("Literal['up', 'down'] | None", direction),
        signal_rr=Decimal(str(signal.rr)) if signal else None,
        signal_entry=signal.entry_price if signal else None,
        signal_stop=signal.stop_price if signal else None,
        signal_target=signal.target_price if signal else None,
        right_edge_proba=right_edge_proba,
    )


def _right_edge_proba(
    ticker: str,
    as_of: date,
    provider: OHLCVProvider,
    model: ConfirmationModel | None,
) -> float | None:
    """Best-effort right-edge confidence; failures are silent so a bad model
    artifact never aborts a scan row."""
    if model is None:
        return None
    try:
        raw = provider.fetch(ticker, Interval.DAILY, end=as_of)
        if not raw:
            return None
        bars = PointInTimeView(raw, as_of).visible()
        assessment = assess_from_bars(bars, model=model, interval=Interval.DAILY)
        return assessment.proba if assessment is not None else None
    except Exception:
        return None
