"""Markdown renderer for cross-cell GridResult summaries.

Spec: docs/superpowers/specs/2026-05-06-ablation-grid-design.md §3.8.
"""

from __future__ import annotations

from datetime import UTC, datetime

from wave_alpha.backtest.grid import (
    SAMPLE_SIZE_FLOOR,
    GridConfig,
    GridResult,
    phase1_trigger,
)


def render_grid_markdown(
    result: GridResult,
    *,
    config: GridConfig,
    phase_name: str,
) -> str:
    """Render the grid result as a markdown report with sections:
    header, per-cell summary, axis sensitivity, verdict.
    """
    lines: list[str] = []
    lines.append("# Grid Sweep Report")
    lines.append("")
    lines.append(f"_generated {datetime.now(UTC).isoformat(timespec='seconds')}_")
    lines.append("")
    lines.append(f"**{phase_name}** — {len(result.rows)} cells")
    axes_str = " x ".join(f"{a.name}({len(a.levels)})" for a in config.axes)
    lines.append(f"**Axes:** {axes_str}")
    if config.fixed:
        fixed_str = ", ".join(f"{k}={v}" for k, v in config.fixed.items())
        lines.append(f"**Held fixed:** {fixed_str}")
    lines.append("")

    # Per-cell summary
    lines.append("## Per-cell summary")
    lines.append("")
    lines.append("| cell_id | n | hit | avg R | expectancy | PF |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for row in result.rows:
        n = row.result.closed_trades
        hit = row.result.hit_rate
        avg_r = row.result.avg_R
        exp = row.result.expectancy
        pf = row.result.profit_factor
        pf_s = "∞" if pf == float("inf") else f"{pf:.2f}"
        lines.append(
            f"| {row.cell.cell_id} | {n} | {hit:.2f} | {avg_r:+.2f} | {exp:+.2f} | {pf_s} |"
        )
    lines.append("")

    # Axis sensitivity
    lines.append("## Axis sensitivity")
    lines.append("")
    lines.append("| axis | level | mean expectancy | mean PF | mean closed_trades |")
    lines.append("|---|---|---:|---:|---:|")
    sens = result.axis_sensitivity()
    for axis_name, levels in sens.items():
        for level_key, metrics in levels.items():
            pf_val = metrics["profit_factor"]
            pf_s = "∞" if pf_val == float("inf") else f"{pf_val:.2f}"
            lines.append(
                f"| {axis_name} | {level_key} | "
                f"{metrics['expectancy']:+.3f} | "
                f"{pf_s} | "
                f"{int(metrics['closed_trades'])} |"
            )
    lines.append("")

    # Verdict (only meaningful for Phase 1; harmless for Phase 2)
    lines.append("## Verdict")
    lines.append("")
    winner = result.winner()
    if winner is None:
        lines.append(
            f"**No winner.** No cell met the sample-size floor "
            f"(closed_trades >= {SAMPLE_SIZE_FLOOR})."
        )
    else:
        winner_pf = winner.result.profit_factor
        winner_pf_s = "∞" if winner_pf == float("inf") else f"{winner_pf:.2f}"
        lines.append(
            f"**Best cell:** `{winner.cell.cell_id}` — "
            f"expectancy={winner.result.expectancy:+.3f}R, "
            f"PF={winner_pf_s}, "
            f"n={winner.result.closed_trades}"
        )
        lines.append("")
        lines.append("**Axis values:**")
        for k, v in winner.cell.axis_values.items():
            lines.append(f"- `{k}` = `{v}`")
    lines.append("")

    if phase_name.lower().startswith("phase 1"):
        verdict, reason = phase1_trigger(result)
        lines.append(f"**Phase 1 trigger:** **{verdict}**")
        lines.append("")
        lines.append(f"_{reason}_")
        lines.append("")

    return "\n".join(lines)
