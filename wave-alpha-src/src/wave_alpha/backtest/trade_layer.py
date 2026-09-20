from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from wave_alpha.backtest.coherence_bucket import COHERENCE_BUCKETS, CoherenceBucket, bucket_for
from wave_alpha.backtest.walk_forward import iter_business_days
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.domain import Bar, Interval
from wave_alpha.llm.modes import LLMMode
from wave_alpha.pipeline import AnalyzeRequest, run_analysis
from wave_alpha.trades.models import TradeRules, TradeSignal
from wave_alpha.trades.outcome import resolve_outcome

ExitReason = Literal["target", "stop", "time_stop", "open_at_end"]
Direction = Literal["long", "short"]


def _metrics_for(records: list[TradeRecord]) -> dict[str, float]:
    closed = [r for r in records if r.exit_reason != "open_at_end"]
    if not closed:
        return {
            "sample_size": 0,
            "hit_rate": 0.0,
            "avg_R": 0.0,
            "expectancy": 0.0,
            "profit_factor": 0.0,
        }
    wins = sum(1 for r in closed if r.realized_R > 0)
    avg_r = sum(r.realized_R for r in closed) / len(closed)
    gains = sum(r.realized_R for r in closed if r.realized_R > 0)
    losses = abs(sum(r.realized_R for r in closed if r.realized_R < 0))
    pf = (float("inf") if gains > 0 else 0.0) if losses == 0 else gains / losses
    return {
        "sample_size": len(closed),
        "hit_rate": wins / len(closed),
        "avg_R": avg_r,
        "expectancy": avg_r,
        "profit_factor": pf,
    }


@dataclass(frozen=True)
class TradeRecord:
    """One simulated trade. `exit_reason='open_at_end'` ⇒ exit_date and
    exit_price are None and the record is excluded from headline metrics."""

    ticker: str
    signal_as_of: date
    fill_date: date
    fill_price: Decimal
    exit_date: date | None
    exit_price: Decimal | None
    exit_reason: ExitReason
    direction: Direction
    pattern: str
    wave_label: str
    coherence_score: float
    coherence_bucket: CoherenceBucket
    bars_held: int
    risk_per_share: Decimal
    realized_pnl_per_share: Decimal
    realized_R: float  # noqa: N815 — match domain naming (R is a unit)
    confidence: float
    tf_count: Literal[2, 3]
    degree: int
    expected_bars_to_target: int


@dataclass
class TradeLayerResult:
    trades: list[TradeRecord] = field(default_factory=list)
    # Number of ranked candidates dropped because derive_signal raised
    # ValueError (typically atr_multiple stop_source with insufficient
    # bars).  Surfaces the silent-skip rate for diagnosis; aggregated
    # across all tickers + as_ofs in run_trade_layer.
    n_atr_skipped: int = 0

    @property
    def closed_trades(self) -> int:
        return sum(1 for t in self.trades if t.exit_reason != "open_at_end")

    @property
    def open_at_end(self) -> int:
        return sum(1 for t in self.trades if t.exit_reason == "open_at_end")

    @property
    def hit_rate(self) -> float:
        closed = [t for t in self.trades if t.exit_reason != "open_at_end"]
        if not closed:
            return 0.0
        return sum(1 for t in closed if t.realized_R > 0) / len(closed)

    @property
    def avg_R(self) -> float:  # noqa: N802 — match domain naming
        closed = [t for t in self.trades if t.exit_reason != "open_at_end"]
        if not closed:
            return 0.0
        return sum(t.realized_R for t in closed) / len(closed)

    @property
    def expectancy(self) -> float:
        return self.avg_R

    @property
    def profit_factor(self) -> float:
        closed = [t for t in self.trades if t.exit_reason != "open_at_end"]
        if not closed:
            return 0.0
        gains = sum(t.realized_R for t in closed if t.realized_R > 0)
        losses = abs(sum(t.realized_R for t in closed if t.realized_R < 0))
        if losses == 0:
            return float("inf") if gains > 0 else 0.0
        return gains / losses

    @property
    def max_drawdown_R(self) -> float:  # noqa: N802
        closed = sorted(
            (t for t in self.trades if t.exit_reason != "open_at_end"),
            key=lambda t: (t.exit_date or date.min, t.ticker),
        )
        if not closed:
            return 0.0
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in closed:
            cumulative += t.realized_R
            peak = max(peak, cumulative)
            max_dd = max(max_dd, peak - cumulative)
        return max_dd

    def by_direction(self) -> dict[str, dict[str, float]]:
        groups: dict[str, list[TradeRecord]] = {}
        for t in self.trades:
            groups.setdefault(t.direction, []).append(t)
        return {k: _metrics_for(v) for k, v in groups.items()}

    def by_pattern(self) -> dict[str, dict[str, float]]:
        groups: dict[str, list[TradeRecord]] = {}
        for t in self.trades:
            groups.setdefault(t.pattern, []).append(t)
        return {k: _metrics_for(v) for k, v in groups.items()}

    def by_coherence_bucket(self) -> dict[str, dict[str, float]]:
        # static insertion order, matches CountLayerResult.by_coherence_bucket
        groups: dict[str, list[TradeRecord]] = {name: [] for (name, _, _) in COHERENCE_BUCKETS}
        for t in self.trades:
            groups[t.coherence_bucket].append(t)
        return {k: _metrics_for(v) for k, v in groups.items()}

    def by_year(self) -> dict[int, dict[str, float]]:
        groups: dict[int, list[TradeRecord]] = {}
        for t in self.trades:
            groups.setdefault(t.signal_as_of.year, []).append(t)
        return {yr: _metrics_for(v) for yr, v in sorted(groups.items())}

    @property
    def coverage_4h(self) -> float:
        """Fraction of closed trades whose signal had 4h coherence available."""
        closed = [t for t in self.trades if t.exit_reason != "open_at_end"]
        if not closed:
            return 0.0
        return sum(1 for t in closed if t.tf_count == 3) / len(closed)

    def by_tf_count(self) -> dict[int, dict[str, float]]:
        groups: dict[int, list[TradeRecord]] = {}
        for t in self.trades:
            groups.setdefault(t.tf_count, []).append(t)
        return {tf: _metrics_for(v) for tf, v in sorted(groups.items())}


@dataclass
class TradeLayerEvaluator:
    """Per-ticker walk: signal generation at --step + bar-by-bar exit."""

    provider: OHLCVProvider
    rules: TradeRules

    def evaluate(
        self,
        ticker: str,
        *,
        start: date,
        end: date,
        step: int,
    ) -> TradeLayerResult:
        result = TradeLayerResult()
        # Per-ticker accumulator for atr_multiple skip count; surfaced
        # on the returned TradeLayerResult and aggregated in
        # run_trade_layer across the universe.
        self._n_atr_skipped = 0

        # Fetch all daily bars once for the exit walk. Buffer past `end` is
        # not needed — we cap on `end`.
        all_bars = self.provider.fetch(ticker, Interval.DAILY, end=end)
        if not all_bars:
            return result
        bars_by_date = {b.timestamp: b for b in all_bars}
        bar_dates = [b.timestamp for b in all_bars]
        date_to_idx = {d: i for i, d in enumerate(bar_dates)}

        as_ofs = list(iter_business_days(start, end, step=step))
        next_eligible_date: date | None = None  # set after a position closes

        for t in as_ofs:
            if next_eligible_date is not None and t < next_eligible_date:
                continue
            signal = self._signal_at(ticker, t)
            if signal is None:
                continue
            fill = self._open_position(
                signal=signal,
                bar_dates=bar_dates,
                bars_by_date=bars_by_date,
                end=end,
            )
            if fill is None:
                continue  # confirmation failed or no fill bar in window
            record = self._walk_to_exit(
                signal=signal,
                fill_date=fill.timestamp,
                fill_price=self._slipped_entry(fill.open, signal.direction),
                bar_dates=bar_dates,
                bars_by_date=bars_by_date,
                date_to_idx=date_to_idx,
                end=end,
            )
            result.trades.append(record)
            if record.exit_reason != "open_at_end" and record.exit_date is not None:
                next_eligible_date = record.exit_date + timedelta(days=1)

        result.n_atr_skipped = self._n_atr_skipped
        return result

    def _signal_at(self, ticker: str, as_of: date) -> TradeSignal | None:
        analysis = run_analysis(
            AnalyzeRequest(ticker=ticker, as_of=as_of, rules=self.rules),
            provider=self.provider,
            llm_mode=LLMMode.DISABLED,
        )
        # Accumulate atr_multiple skip count even when no signal is emitted —
        # the skip rate matters for diagnosis whether or not a final signal
        # survived.
        self._n_atr_skipped += analysis.n_atr_skipped
        if not analysis.signals:
            return None
        coh_score = analysis.coherence.score if analysis.coherence else 0.0
        fallback = (
            analysis.coherence.fallback_used if analysis.coherence else "insufficient_history"
        )
        # Stash coherence on the signal via a side-channel for _walk_to_exit
        self._last_coherence_score = coh_score
        self._last_tf_count: Literal[2, 3] = 3 if fallback is None else 2
        return analysis.signals[0]

    def _open_position(
        self,
        *,
        signal: TradeSignal,
        bar_dates: list[date],
        bars_by_date: dict[date, Bar],
        end: date,
    ) -> Bar | None:
        next_idx = next((i for i, d in enumerate(bar_dates) if d > signal.as_of), None)
        if next_idx is None:
            return None

        if self.rules.entry_trigger == "immediate":
            fill_bar = bars_by_date[bar_dates[next_idx]]
            return fill_bar if fill_bar.timestamp <= end else None

        # confirmation: T+1 must close in signal direction; fill at T+2 open.
        confirm_idx = next_idx
        fill_idx = next_idx + 1
        if fill_idx >= len(bar_dates):
            return None
        confirm_bar = bars_by_date[bar_dates[confirm_idx]]
        if signal.direction == "long" and confirm_bar.close <= confirm_bar.open:
            return None
        if signal.direction == "short" and confirm_bar.close >= confirm_bar.open:
            return None
        fill_bar = bars_by_date[bar_dates[fill_idx]]
        return fill_bar if fill_bar.timestamp <= end else None

    def _slipped_entry(self, base: Decimal, direction: str) -> Decimal:
        bps = Decimal(self.rules.slippage_bps) / Decimal("10000")
        if direction == "long":
            return base * (Decimal("1") + bps)
        return base * (Decimal("1") - bps)

    def _slipped_exit(self, base: Decimal, direction: str) -> Decimal:
        bps = Decimal(self.rules.slippage_bps) / Decimal("10000")
        if direction == "long":
            return base * (Decimal("1") - bps)
        return base * (Decimal("1") + bps)

    def _walk_to_exit(
        self,
        *,
        signal: TradeSignal,
        fill_date: date,
        fill_price: Decimal,
        bar_dates: list[date],
        bars_by_date: dict[date, Bar],
        date_to_idx: dict[date, int],
        end: date,
    ) -> TradeRecord:
        # Adapter over the shared resolver. The resolver implements the
        # "raw engine accuracy" semantic (exit at published stop/target prices,
        # 1-indexed bars_held, time_stop fires at bars_held >= max_holding_bars).
        # The backtest models realistic fills on top: gap-aware adverse stops on
        # non-fill bars, and a +1 time-stop bar boundary (preserving the legacy
        # `if offset >= max_hold` 0-indexed semantic). Both are layered here
        # without touching the resolver.
        walked_bars = [bars_by_date[d] for d in bar_dates if fill_date <= d <= end]
        max_hold = (
            self.rules.time_stop_bars
            if self.rules.time_stop_mode == "fixed" and self.rules.time_stop_bars is not None
            else signal.max_holding_bars
        )
        synthetic_signal = signal.model_copy(
            update={
                "as_of": fill_date - timedelta(days=1),
                "entry_price": fill_price,
                # Backtest holds N+1 bars (fill bar + N post-fill bars) before
                # time-stop fires at bar.close. The resolver's 1-indexed
                # `i >= max_holding_bars` boundary needs +1 to match.
                "max_holding_bars": max_hold + 1,
            }
        )
        base = resolve_outcome(synthetic_signal, walked_bars, as_of_close=end)
        risk = abs(fill_price - signal.stop_price)

        if base is None or base.status == "pending":
            return self._make_open_at_end_record(
                signal=signal,
                fill_date=fill_date,
                fill_price=fill_price,
                risk_per_share=risk,
                bar_dates=bar_dates,
                end=end,
            )

        assert base.exit_price is not None
        assert base.exit_date is not None
        assert base.bars_held is not None

        # Gap-aware stop fill on non-fill bars: if a stop hits and the bar's
        # open gapped past the stop (adverse), fill at open, not at the
        # published stop price. Fill-bar stops use the published price
        # (entry was intra-bar, no gap concept yet).
        raw_exit_price = base.exit_price
        if base.status == "stop":
            exit_bar = bars_by_date[base.exit_date]
            is_fill_bar = base.exit_date == fill_date
            if not is_fill_bar:
                if signal.direction == "long":
                    raw_exit_price = min(exit_bar.open, signal.stop_price)
                else:
                    raw_exit_price = max(exit_bar.open, signal.stop_price)

        exit_price = self._slipped_exit(raw_exit_price, signal.direction)

        return self._make_record(
            signal=signal,
            fill_date=fill_date,
            fill_price=fill_price,
            exit_date=base.exit_date,
            exit_price=exit_price,
            exit_reason=base.status,  # type: ignore[arg-type]
            bars_held=base.bars_held - 1,  # adapter maps 1-indexed → backtest 0-indexed
            risk_per_share=risk,
        )

    def _make_record(
        self,
        *,
        signal: TradeSignal,
        fill_date: date,
        fill_price: Decimal,
        exit_date: date,
        exit_price: Decimal,
        exit_reason: ExitReason,
        bars_held: int,
        risk_per_share: Decimal,
    ) -> TradeRecord:
        commission = Decimal("2") * self.rules.commission_per_share
        borrow = Decimal("0")
        if signal.direction == "short" and bars_held > 0:
            borrow = (
                fill_price * Decimal(str(self.rules.short_borrow_annual_pct)) * Decimal(bars_held)
            ) / Decimal("252")
        dir_sign = Decimal("1") if signal.direction == "long" else Decimal("-1")
        gross = (exit_price - fill_price) * dir_sign
        realized_pnl = gross - commission - borrow
        realized_r = float(realized_pnl / risk_per_share) if risk_per_share else 0.0
        return TradeRecord(
            ticker=signal.ticker,
            signal_as_of=signal.as_of,
            fill_date=fill_date,
            fill_price=fill_price,
            exit_date=exit_date,
            exit_price=exit_price,
            exit_reason=exit_reason,
            direction=signal.direction,
            pattern=signal.count_pattern,
            wave_label=signal.wave_label,
            coherence_score=self._last_coherence_score,
            coherence_bucket=bucket_for(self._last_coherence_score),
            bars_held=bars_held,
            risk_per_share=risk_per_share,
            realized_pnl_per_share=realized_pnl,
            realized_R=realized_r,
            confidence=signal.confidence,
            tf_count=self._last_tf_count,
            degree=signal.degree,
            expected_bars_to_target=signal.expected_bars_to_target,
        )

    def _make_open_at_end_record(
        self,
        *,
        signal: TradeSignal,
        fill_date: date,
        fill_price: Decimal,
        risk_per_share: Decimal,
        bar_dates: list[date],
        end: date,
    ) -> TradeRecord:
        bars_in_window = sum(1 for d in bar_dates if fill_date <= d <= end)
        return TradeRecord(
            ticker=signal.ticker,
            signal_as_of=signal.as_of,
            fill_date=fill_date,
            fill_price=fill_price,
            exit_date=None,
            exit_price=None,
            exit_reason="open_at_end",
            direction=signal.direction,
            pattern=signal.count_pattern,
            wave_label=signal.wave_label,
            coherence_score=self._last_coherence_score,
            coherence_bucket=bucket_for(self._last_coherence_score),
            bars_held=max(bars_in_window - 1, 0),
            risk_per_share=risk_per_share,
            realized_pnl_per_share=Decimal("0"),
            realized_R=0.0,
            confidence=signal.confidence,
            tf_count=self._last_tf_count,
            degree=signal.degree,
            expected_bars_to_target=signal.expected_bars_to_target,
        )
