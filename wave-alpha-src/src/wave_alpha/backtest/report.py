from __future__ import annotations

from datetime import UTC, datetime

from wave_alpha.backtest.count_layer import CountLayerResult
from wave_alpha.backtest.pivot_layer import PivotLayerResult
from wave_alpha.backtest.runner import BacktestConfig
from wave_alpha.backtest.trade_layer import TradeLayerResult
from wave_alpha.trades.models import TradeRules


def render_count_markdown(result: CountLayerResult) -> str:
    lines: list[str] = []
    lines.append("# Count Layer Backtest Report")
    lines.append("")
    lines.append(f"_generated {datetime.now(UTC).isoformat(timespec='seconds')}_")
    lines.append("")
    lines.append(f"**Sample size:** n={result.sample_size}")
    lines.append(f"**Forward window:** {result.n_bars_forward} bars")
    lines.append("")
    lines.append("## Headline rates")
    lines.append("")
    lines.append(f"- top-1 hit rate: **{result.top1_hit_rate:.2f}** (n={result.sample_size})")
    lines.append(f"- top-3 hit rate: **{result.top3_hit_rate:.2f}** (n={result.sample_size})")
    lines.append("")
    lines.append("## by coherence bucket")
    lines.append("")
    lines.append("| bucket | n | top-1 | top-3 |")
    lines.append("|---|---:|---:|---:|")
    for name, row in result.by_coherence_bucket().items():
        n = int(row["sample_size"])
        lines.append(f"| {name} | {n} | {row['top1_hit_rate']:.2f} | {row['top3_hit_rate']:.2f} |")
    lines.append("")
    lines.append("## by year")
    lines.append("")
    lines.append("| year | n | top-1 | top-3 |")
    lines.append("|---|---:|---:|---:|")
    for yr, row in result.by_year().items():
        n = int(row["sample_size"])
        lines.append(f"| {yr} | {n} | {row['top1_hit_rate']:.2f} | {row['top3_hit_rate']:.2f} |")
    lines.append("")
    return "\n".join(lines)


def render_pivot_markdown(result: PivotLayerResult) -> str:
    lines: list[str] = []
    lines.append("# Pivot Layer Backtest Report")
    lines.append("")
    lines.append(f"_generated {datetime.now(UTC).isoformat(timespec='seconds')}_")
    lines.append("")
    lines.append(f"**Sample size:** n={result.sample_size}")
    lines.append("")
    lines.append("## per-degree confirmation rate")
    lines.append("")
    lines.append("| degree | rate |")
    lines.append("|---:|---:|")
    for d, rate in result.confirmation_rate_per_degree().items():
        lines.append(f"| d={d} | {rate:.2f} |")
    lines.append("")
    lines.append("## right-edge model calibration")
    lines.append("")
    lines.append(f"- Brier score: **{result.brier_score():.3f}**")
    lines.append(f"- 10-bin reliability calibration error: **{result.calibration_error():.3f}**")
    lines.append("")
    lines.append(
        "> _Caveat: v0.4 right-edge features are placeholder constants; the_"
        " _Brier and calibration numbers reflect a near-uniform prior, not_"
        " _a learned model. The v0.4.x logistic model trained on these labels_"
        " _replaces the heuristic and will produce a meaningful calibration._"
    )
    lines.append("")
    return "\n".join(lines)


def render_trade_markdown(
    result: TradeLayerResult, *, cfg: BacktestConfig, rules: TradeRules
) -> str:
    lines: list[str] = []
    lines.append("# Trade Layer Backtest Report")
    lines.append("")
    lines.append(f"_generated {datetime.now(UTC).isoformat(timespec='seconds')}_")
    lines.append("")
    lines.append(f"**Universe:** {len(cfg.universe)} tickers")
    lines.append(f"**Window:** {cfg.start} .. {cfg.end}")
    lines.append(f"**Step:** {cfg.step}")
    rules_dict = rules.model_dump(mode="python")
    rules_summary = ", ".join(
        f"{k}={v}"
        for k, v in rules_dict.items()
        if k
        in {
            "entry_trigger",
            "stop_source",
            "target_source",
            "target_R",
            "min_confidence",
            "time_stop_mode",
        }
    )
    lines.append(f"**Rules:** {rules_summary}")
    lines.append("")
    lines.append(f"**Closed trades:** {result.closed_trades}")
    lines.append(f"**Open at end:** {result.open_at_end}")
    if result.closed_trades > 0:
        cov_pct = round(result.coverage_4h * 100)
        n_3tf = sum(1 for t in result.trades if t.exit_reason != "open_at_end" and t.tf_count == 3)
        lines.append(
            f"**4h coverage:** {cov_pct}% ({n_3tf} / {result.closed_trades} "
            f"closed trades had 4h coherence)"
        )
    else:
        lines.append("**4h coverage:** n/a")
    if result.n_atr_skipped > 0:
        lines.append(
            f"**atr_multiple skips:** {result.n_atr_skipped} candidate(s) dropped "
            f"due to insufficient bars for ATR(14)"
        )
    lines.append("")

    lines.append("## Headline")
    lines.append("")
    lines.append(f"- hit rate:        **{result.hit_rate:.2f}** (n={result.closed_trades})")
    lines.append(f"- avg R:           **{result.avg_R:+.2f}**")
    lines.append(f"- expectancy:      **{result.expectancy:+.2f} R**")
    pf = result.profit_factor
    pf_str = "∞" if pf == float("inf") else f"{pf:.2f}"
    lines.append(f"- profit factor:   **{pf_str}**")
    lines.append(f"- max drawdown:    **{-result.max_drawdown_R:+.2f} R**")
    lines.append("")

    def _table(title: str, rows: dict) -> None:
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| key | n | hit | avg R | expectancy | PF |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for key, m in rows.items():
            n = int(m["sample_size"])
            pf_v = m["profit_factor"]
            pf_s = "∞" if pf_v == float("inf") else f"{pf_v:.2f}"
            lines.append(
                f"| {key} | {n} | {m['hit_rate']:.2f} | {m['avg_R']:+.2f} | "
                f"{m['expectancy']:+.2f} | {pf_s} |"
            )
        lines.append("")

    _table("by direction", result.by_direction())
    _table("by pattern", result.by_pattern())
    _table("by coherence bucket", result.by_coherence_bucket())
    _table("by year", {str(yr): m for yr, m in result.by_year().items()})

    return "\n".join(lines)
