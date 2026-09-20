#!/usr/bin/env python3
"""Run wave-alpha's Elliott Wave analysis on a Binance symbol (e.g. BTCUSDT).

Mirrors `wave-alpha analyze <TICKER>` exactly (same output format, same
history-snapshot recording), but sources bars from Binance's public REST
API instead of Yahoo Finance/Stooq. See:
  - wave_alpha/data/binance.py        (the Binance OHLCVProvider)
  - wave_alpha/data/binance_stack.py  (why HOURLY_4 bypasses the disk cache)

Usage:
    python analyze_binance.py BTCUSDT
    python analyze_binance.py BTCUSDT --as-of 2026-09-18
    python analyze_binance.py BTCUSDT --json
    python analyze_binance.py BTCUSDT --llm-mode live   # needs ANTHROPIC_API_KEY
                                                          # or `wave-alpha config
                                                          # set api_key <key>`
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from loguru import logger

from wave_alpha.cli.app import _build_runner, _json_default, _serialize
from wave_alpha.data.binance_stack import binance_provider_stack
from wave_alpha.llm.modes import LLMMode
from wave_alpha.pipeline import AnalyzeRequest, run_analysis
from wave_alpha.prefs.config import load_config

_DEFAULT_DATA_DIR = Path.home() / ".wave_alpha"
_BINANCE_CACHE_PATH = _DEFAULT_DATA_DIR / "binance_cache.sqlite"


def _print_human(result) -> None:
    """Same layout as wave_alpha.cli.app._print_human, plus each ranked
    candidate's LLM narrative (upstream never prints it, so any reasoning
    about --context — news/heatmap/on-chain — would otherwise be silently
    generated and never shown)."""
    import typer

    narratives: dict[str, str] = {}
    if result.llm_scan is not None:
        narratives = {c.id: c.narrative for c in result.llm_scan.candidates}

    typer.echo(f"ticker:  {result.ticker}")
    typer.echo(f"as_of:   {result.as_of.isoformat()}")
    typer.echo(f"daily counts:  {len(result.counts_daily)} candidates")
    if result.coherence:
        typer.echo(f"coherence score:  {result.coherence.score:.2f}")
    typer.echo(f"ranked: {len(result.ranked_daily)}")
    for rc in result.ranked_daily[:5]:
        prob = "n/a" if rc.llm_prob is None else f"{rc.llm_prob:.2f}"
        typer.echo(
            f"  {rc.candidate_id} {rc.count.pattern}  engine={rc.engine_score:.2f}"
            f"  llm_prob={prob}  final={rc.final_score:.2f}"
        )
        note = narratives.get(rc.candidate_id)
        if note:
            typer.echo(f"      -> {note}")
    if result.llm_scan is not None and result.llm_scan.engine_missed:
        typer.echo(f"  [!] LLM flagged a possibly better count the engine missed:")
        typer.echo(f"      {result.llm_scan.engine_missed_reason}")
    typer.echo(f"signals: {len(result.signals)}")
    for sig in result.signals:
        typer.echo(
            f"  {sig.direction.upper()}  entry={sig.entry_price}  "
            f"stop={sig.stop_price}  target={sig.target_price}  rr={sig.rr:.2f}"
        )
    typer.echo("\n⚠ Informational only. Confirm thesis. Not investment advice.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticker", help='Binance symbol, e.g. "BTCUSDT" (not "BTC-USD").')
    parser.add_argument(
        "--as-of", default=None, help="Analysis as-of date (YYYY-MM-DD). Defaults to today."
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_out", help="Emit JSON instead of human output."
    )
    parser.add_argument(
        "--llm-mode",
        choices=[m.value for m in LLMMode],
        default=LLMMode.DISABLED.value,
        help="LLM execution mode: live, cached, or disabled (default).",
    )
    parser.add_argument(
        "--alpha", type=float, default=None, help="Override LLMConfig.alpha (0..1)."
    )
    parser.add_argument(
        "--context",
        default=None,
        help=(
            "Free-text external context to hand the LLM alongside the wave "
            'candidates: news, rumors, macro/DXY/oil view, on-chain data, '
            "funding rate/open interest, or what you see on the Coinglass "
            "liquidity heatmap. Only used when --llm-mode is not disabled. "
            'Example: --context "Fed held rates; large liquidity cluster '
            'around 58000 on the heatmap; funding rate slightly negative."'
        ),
    )
    parser.add_argument(
        "--context-file",
        default=None,
        help=(
            "Path to a text file whose contents are used as --context "
            "(handy for a longer news/heatmap writeup you keep in a file "
            "instead of typing on the command line). If both are given, "
            "--context-file wins."
        ),
    )
    args = parser.parse_args()

    external_context = args.context
    if args.context_file:
        external_context = Path(args.context_file).read_text(encoding="utf-8")

    resolved_as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    llm_mode = LLMMode(args.llm_mode)
    cfg = load_config()
    effective_alpha = args.alpha if args.alpha is not None else cfg.llm.alpha

    _DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    provider = binance_provider_stack(db_path=_BINANCE_CACHE_PATH)
    runner = _build_runner(cfg, mode=llm_mode) if llm_mode is not LLMMode.DISABLED else None

    ticker = args.ticker.upper()
    req = AnalyzeRequest(ticker=ticker, as_of=resolved_as_of)
    logger.debug("binance analyze request {} mode={} alpha={}", req, llm_mode.value, effective_alpha)

    result = run_analysis(
        req,
        provider=provider,
        llm_mode=llm_mode,
        llm_runner=runner,
        alpha=effective_alpha,
        enabled_opt_in_templates=cfg.templates.enabled_opt_in_names(),
        external_context=external_context,
    )

    # Best-effort history snapshot, same as the built-in `analyze` command —
    # keeps `wave-alpha resolve` / `wave-alpha history` working for Binance
    # tickers too, sharing the same history.sqlite.
    from wave_alpha.history import service as history_service

    history_service.record_snapshot(
        result, ticker, resolved_as_of, db_path=_DEFAULT_DATA_DIR / "history.sqlite"
    )

    if args.json_out:
        print(json.dumps(_serialize(result), default=_json_default))
    else:
        _print_human(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
