from __future__ import annotations

import json
import os
import socket
import webbrowser
from dataclasses import asdict, is_dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import typer
from loguru import logger

from wave_alpha import __version__
from wave_alpha.data.cached import CachedProvider
from wave_alpha.data.fallback import FallbackProvider
from wave_alpha.data.provider import OHLCVProvider
from wave_alpha.data.stooq import StooqProvider
from wave_alpha.data.yahoo import YahooProvider
from wave_alpha.llm.cache import LLMResponseCache
from wave_alpha.llm.client import AnthropicClient, CacheOnlyClient, LLMClient
from wave_alpha.llm.modes import LLMMode
from wave_alpha.llm.runner import LLMRunner
from wave_alpha.pipeline import AnalyzeRequest, run_analysis
from wave_alpha.prefs.config import load_config, resolve_api_key, resolve_stooq_api_key
from wave_alpha.scan.archive import DEFAULT_SCAN_DIR, save_report
from wave_alpha.scan.orchestrator import run_scan as _run_scan
from wave_alpha.watchlist.store import (
    DEFAULT_WATCHLIST_PATH,
)
from wave_alpha.watchlist.store import (
    add_entry as _wl_add,
)
from wave_alpha.watchlist.store import (
    load_watchlist as _wl_load,
)
from wave_alpha.watchlist.store import (
    remove_entry as _wl_remove,
)

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Elliott Wave analysis for swing traders.",
)

config_app = typer.Typer(no_args_is_help=True, help="Manage wave-alpha config.")
app.add_typer(config_app, name="config")

backtest_app = typer.Typer(
    no_args_is_help=True,
    help="Walk-forward backtest harness (pivot + count + trade layers).",
)
app.add_typer(backtest_app, name="backtest")

watchlist_app = typer.Typer(no_args_is_help=True, help="Manage the watchlist.")
app.add_typer(watchlist_app, name="watchlist")

train_app = typer.Typer(no_args_is_help=True, help="Train and gate models.")
app.add_typer(train_app, name="train")

from wave_alpha.cli.history_app import history_app  # noqa: E402

app.add_typer(history_app, name="history")

_DEFAULT_DATA_DIR = Path.home() / ".wave_alpha"

_DISCLAIMER_BANNER = """\
=============================== wave-alpha disclaimer ===============================
This tool is for educational and research use only. It is NOT financial advice.
Past performance does not predict future results. You are solely responsible
for any trading decisions you make.
Full text: see DISCLAIMER.md   |   silence: WAVE_ALPHA_NO_DISCLAIMER=1
=====================================================================================
"""


def _disclaimer_sentinel_path() -> Path:
    return _DEFAULT_DATA_DIR / ".disclaimer_ack"


def _maybe_show_disclaimer() -> None:
    # Any non-empty value suppresses the banner; convention is "1".
    if os.environ.get("WAVE_ALPHA_NO_DISCLAIMER"):
        return
    sentinel = _disclaimer_sentinel_path()
    if sentinel.exists():
        return
    typer.echo(_DISCLAIMER_BANNER, err=True)
    try:
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("acknowledged\n")
    except OSError:
        # Sentinel persistence is best-effort; without it the banner
        # will show again next run, which is acceptable degradation.
        pass


@app.callback()
def _root(ctx: typer.Context) -> None:
    """wave-alpha — local-first Elliott Wave analysis."""
    _maybe_show_disclaimer()


_YF_4H_HISTORY_DAYS = 730


def _warn_if_4h_window_partial(start: date) -> None:
    """Emit a stderr warning when *start* predates yfinance's 4h-history limit."""
    cutoff = date.today() - timedelta(days=_YF_4H_HISTORY_DAYS)
    if start < cutoff:
        typer.echo(
            f"[warn] 4h timeframe data is unavailable from yfinance before "
            f"~{cutoff.isoformat()}. Coherence will degrade to 2-timeframe "
            f"(weekly + daily) for as_of dates before that cutoff.",
            err=True,
        )


def _config_path() -> Path:
    """Indirection so tests can monkeypatch the location."""
    from wave_alpha.prefs.config import DEFAULT_CONFIG_PATH

    return DEFAULT_CONFIG_PATH


def _watchlist_path() -> Path:
    """Indirection so tests can monkeypatch the location."""
    return DEFAULT_WATCHLIST_PATH


def _scan_dir() -> Path:
    """Indirection so tests can monkeypatch the location."""
    return DEFAULT_SCAN_DIR


@watchlist_app.command("add")
def watchlist_add(symbol: str = typer.Argument(...)) -> None:
    """Add a symbol to the watchlist (idempotent)."""
    e = _wl_add(symbol, path=_watchlist_path())
    typer.echo(f"{e.symbol}  added_on={e.added_on.isoformat()}")


@watchlist_app.command("rm")
def watchlist_rm(symbol: str = typer.Argument(...)) -> None:
    """Remove a symbol from the watchlist."""
    if _wl_remove(symbol, path=_watchlist_path()):
        typer.echo(f"removed {symbol.upper()}")
    else:
        typer.echo(f"{symbol.upper()} not in watchlist", err=True)
        raise typer.Exit(code=1)


@watchlist_app.command("ls")
def watchlist_ls() -> None:
    """List watchlist entries."""
    entries = _wl_load(_watchlist_path())
    if not entries:
        typer.echo("(empty — use `wave-alpha watchlist add SYMBOL`)")
        return
    for e in entries:
        notes = f"  # {e.notes}" if e.notes else ""
        typer.echo(f"{e.symbol:<8}  {e.added_on.isoformat()}{notes}")


@watchlist_app.command("import")
def watchlist_import(
    path: Path = typer.Argument(..., exists=True, readable=True),  # noqa: B008
    fmt: str = typer.Option(
        "csv", "--format", help="Source format: csv or tv-txt", case_sensitive=False
    ),
) -> None:
    """Import symbols from a CSV or TradingView TXT export."""
    from wave_alpha.watchlist.parsers import parse_csv_symbols, parse_tv_txt_symbols

    parser = {"csv": parse_csv_symbols, "tv-txt": parse_tv_txt_symbols}.get(fmt.lower())
    if parser is None:
        raise typer.BadParameter(f"unknown format: {fmt}")
    symbols = parser(path)
    initial_set = {e.symbol for e in _wl_load(_watchlist_path())}
    symbols_added: list[str] = []
    for s in symbols:
        _wl_add(s, path=_watchlist_path())
        if s not in initial_set:
            symbols_added.append(s)
    added = len(symbols_added)
    typer.echo(f"imported {added} new symbol(s) from {path.name}")


@backtest_app.command("count")
def backtest_count(
    universe: Path = typer.Option(..., "--universe", help="Path to ticker universe file."),  # noqa: B008
    start: str = typer.Option(..., "--start", help="Backtest start date YYYY-MM-DD."),
    end: str = typer.Option(..., "--end", help="Backtest end date YYYY-MM-DD."),
    step: int = typer.Option(5, "--step", help="Subsample every Nth business day."),
    n_bars_forward: int = typer.Option(
        20, "--n-bars-forward", help="Forward bars for direction labeling."
    ),
    out: Path = typer.Option(..., "--out", help="Output markdown report path."),  # noqa: B008
) -> None:
    """Run the count-layer backtest and write a markdown report."""
    from wave_alpha.backtest.report import render_count_markdown
    from wave_alpha.backtest.runner import BacktestConfig, run_count_layer
    from wave_alpha.backtest.universe import load_universe

    cfg = BacktestConfig(
        universe=load_universe(universe),
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        step=step,
        n_bars_forward=n_bars_forward,
    )
    _warn_if_4h_window_partial(cfg.start)
    typer.echo(f"running count-layer backtest: {len(cfg.universe)} tickers, step={step}")
    result = run_count_layer(cfg, provider=_default_provider())
    out.write_text(render_count_markdown(result))
    typer.echo(f"wrote {out} (n={result.sample_size})")


@backtest_app.command("pivot")
def backtest_pivot(
    universe: Path = typer.Option(  # noqa: B008
        ..., "--universe", help="Path to ticker universe file."
    ),
    start: str = typer.Option(..., "--start", help="Initial snapshot date YYYY-MM-DD."),
    end: str = typer.Option(..., "--end", help="Final snapshot date YYYY-MM-DD."),
    out: Path = typer.Option(  # noqa: B008
        ..., "--out", help="Output markdown report path."
    ),
) -> None:
    """Run the pivot-layer backtest and write a markdown report."""
    from wave_alpha.backtest.report import render_pivot_markdown
    from wave_alpha.backtest.runner import BacktestConfig, run_pivot_layer
    from wave_alpha.backtest.universe import load_universe

    cfg = BacktestConfig(
        universe=load_universe(universe),
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
    )
    _warn_if_4h_window_partial(cfg.start)
    typer.echo(f"running pivot-layer backtest: {len(cfg.universe)} tickers")
    result = run_pivot_layer(cfg, provider=_default_provider())
    out.write_text(render_pivot_markdown(result))
    typer.echo(f"wrote {out} (n={result.sample_size})")


@backtest_app.command("trade")
def backtest_trade(
    universe: Path = typer.Option(  # noqa: B008
        ..., "--universe", help="Path to universe txt file."
    ),
    start: str = typer.Option(..., "--start", help="ISO start date."),
    end: str = typer.Option(..., "--end", help="ISO end date."),
    step: int = typer.Option(1, "--step", help="Signal-generation cadence (business days)."),
    rules: Path | None = typer.Option(  # noqa: B008
        None, "--rules", help="Optional YAML rules file."
    ),
    out: Path = typer.Option(..., "--out", help="Markdown report output path."),  # noqa: B008
    json_out: Path | None = typer.Option(  # noqa: B008
        None, "--json", help="Optional JSON sidecar."
    ),
    trades_csv: Path | None = typer.Option(  # noqa: B008
        None, "--trades-csv", help="Optional per-trade CSV."
    ),
) -> None:
    """Run the trade-layer backtest under one TradeRules config."""
    import csv
    import json as _json
    from dataclasses import asdict

    import yaml

    from wave_alpha.backtest.report import render_trade_markdown
    from wave_alpha.backtest.runner import BacktestConfig, run_trade_layer
    from wave_alpha.backtest.universe import load_universe
    from wave_alpha.trades.models import TradeRules

    cfg = BacktestConfig(
        universe=load_universe(universe),
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        step=step,
    )
    _warn_if_4h_window_partial(cfg.start)
    if rules is not None:
        rules_dict = yaml.safe_load(rules.read_text()) or {}
        rules_obj = TradeRules.model_validate(rules_dict)
    else:
        rules_obj = TradeRules()

    typer.echo(
        f"running trade-layer backtest: {len(cfg.universe)} tickers, "
        f"step={step}, entry={rules_obj.entry_trigger}"
    )
    provider = _default_provider()
    result = run_trade_layer(cfg, provider=provider, rules=rules_obj)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_trade_markdown(result, cfg=cfg, rules=rules_obj))
    typer.echo(f"wrote {out}")

    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "config": {
                "universe": cfg.universe,
                "start": cfg.start.isoformat(),
                "end": cfg.end.isoformat(),
                "step": cfg.step,
            },
            "rules": rules_obj.model_dump(mode="json"),
            "headline": {
                "closed_trades": result.closed_trades,
                "open_at_end": result.open_at_end,
                "hit_rate": result.hit_rate,
                "avg_R": result.avg_R,
                "expectancy": result.expectancy,
                "profit_factor": (
                    None if result.profit_factor == float("inf") else result.profit_factor
                ),
                "max_drawdown_R": result.max_drawdown_R,
                "coverage_4h": result.coverage_4h,
                "n_atr_skipped": result.n_atr_skipped,
            },
            "by_direction": result.by_direction(),
            "by_pattern": result.by_pattern(),
            "by_coherence_bucket": result.by_coherence_bucket(),
            "by_tf_count": {str(k): v for k, v in result.by_tf_count().items()},
            "by_year": {str(k): v for k, v in result.by_year().items()},
        }
        json_out.write_text(_json.dumps(payload, indent=2, default=_json_default))
        typer.echo(f"wrote {json_out}")

    if trades_csv is not None:
        trades_csv.parent.mkdir(parents=True, exist_ok=True)
        with trades_csv.open("w", newline="") as fh:
            if not result.trades:
                fh.write("")  # empty file is acceptable
            else:
                fields = list(asdict(result.trades[0]).keys())
                w = csv.DictWriter(fh, fieldnames=fields)
                w.writeheader()
                for t in result.trades:
                    row = asdict(t)
                    # Decimals → strings for fidelity
                    for k, v in row.items():
                        if isinstance(v, Decimal):
                            row[k] = str(v)
                    w.writerow(row)
        typer.echo(f"wrote {trades_csv}")


@backtest_app.command("grid")
def backtest_grid(
    universe: Path = typer.Option(  # noqa: B008
        ..., "--universe", help="Path to universe txt file."
    ),
    start: str = typer.Option(..., "--start", help="ISO start date."),
    end: str = typer.Option(..., "--end", help="ISO end date."),
    step: int = typer.Option(1, "--step", help="Signal-generation cadence (business days)."),
    grid: Path = typer.Option(..., "--grid", help="YAML grid config path."),  # noqa: B008
    out: Path = typer.Option(..., "--out", help="Output directory."),  # noqa: B008
    phase_name: str = typer.Option(
        "Phase", "--phase-name", help='Label for the report header (e.g. "Phase 1").'
    ),
) -> None:
    """Run an ablation grid sweep over (universe x dates x cells)."""
    import csv
    import json as _json
    from dataclasses import asdict

    import yaml

    from wave_alpha.backtest.grid import GridAxis, GridConfig, run_grid
    from wave_alpha.backtest.grid_report import render_grid_markdown
    from wave_alpha.backtest.report import render_trade_markdown
    from wave_alpha.backtest.runner import BacktestConfig
    from wave_alpha.backtest.universe import load_universe
    from wave_alpha.trades.models import TradeRules

    raw = yaml.safe_load(grid.read_text()) or {}
    axes = [GridAxis(**a) for a in raw.get("axes", [])]
    fixed = raw.get("fixed", {}) or {}
    grid_config = GridConfig(axes=axes, fixed=fixed)

    base_cfg = BacktestConfig(
        universe=load_universe(universe),
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        step=step,
    )
    _warn_if_4h_window_partial(base_cfg.start)
    typer.echo(
        f"running grid sweep: {sum(1 for _ in grid_config.cells())} cells, "
        f"{len(base_cfg.universe)} tickers, step={step}"
    )

    provider = _default_provider()
    result = run_grid(grid_config, base_cfg=base_cfg, provider=provider)

    out.mkdir(parents=True, exist_ok=True)
    cells_dir = out / "cells"
    cells_dir.mkdir(exist_ok=True)

    # Top-level grid markdown
    (out / "grid.md").write_text(
        render_grid_markdown(result, config=grid_config, phase_name=phase_name)
    )

    # Cross-cell summary CSV
    summary_path = out / "summary.csv"
    with summary_path.open("w", newline="") as fh:
        if not result.rows:
            fh.write("")
        else:
            axis_names = list(result.rows[0].cell.axis_values.keys())
            fields = [
                "cell_id",
                *axis_names,
                "closed_trades",
                "open_at_end",
                "hit_rate",
                "avg_R",
                "expectancy",
                "profit_factor",
                "max_drawdown_R",
                "coverage_4h",
                "n_atr_skipped",
            ]
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            for row in result.rows:
                pf = row.result.profit_factor
                w.writerow(
                    {
                        "cell_id": row.cell.cell_id,
                        **{n: str(row.cell.axis_values[n]) for n in axis_names},
                        "closed_trades": row.result.closed_trades,
                        "open_at_end": row.result.open_at_end,
                        "hit_rate": f"{row.result.hit_rate:.4f}",
                        "avg_R": f"{row.result.avg_R:.4f}",
                        "expectancy": f"{row.result.expectancy:.4f}",
                        "profit_factor": ("" if pf == float("inf") else f"{pf:.4f}"),
                        "max_drawdown_R": f"{row.result.max_drawdown_R:.4f}",
                        "coverage_4h": f"{row.result.coverage_4h:.4f}",
                        "n_atr_skipped": row.result.n_atr_skipped,
                    }
                )

    # Per-cell artifacts
    for row in result.rows:
        cell_dir = cells_dir / row.cell.cell_id
        cell_dir.mkdir(exist_ok=True)
        rules = TradeRules.model_validate(row.cell.trade_rules_kwargs())
        (cell_dir / "report.md").write_text(
            render_trade_markdown(row.result, cfg=base_cfg, rules=rules)
        )
        # JSON sidecar mirrors the trade-layer CLI shape
        payload = {
            "cell_id": row.cell.cell_id,
            "axis_values": row.cell.axis_values,
            "fixed": row.cell.fixed,
            "rules": rules.model_dump(mode="json"),
            "headline": {
                "closed_trades": row.result.closed_trades,
                "open_at_end": row.result.open_at_end,
                "hit_rate": row.result.hit_rate,
                "avg_R": row.result.avg_R,
                "expectancy": row.result.expectancy,
                "profit_factor": (
                    None if row.result.profit_factor == float("inf") else row.result.profit_factor
                ),
                "max_drawdown_R": row.result.max_drawdown_R,
                "coverage_4h": row.result.coverage_4h,
                "n_atr_skipped": row.result.n_atr_skipped,
            },
            "by_direction": row.result.by_direction(),
            "by_pattern": row.result.by_pattern(),
            "by_coherence_bucket": row.result.by_coherence_bucket(),
            "by_tf_count": {str(k): v for k, v in row.result.by_tf_count().items()},
            "by_year": {str(k): v for k, v in row.result.by_year().items()},
        }
        (cell_dir / "result.json").write_text(_json.dumps(payload, indent=2, default=_json_default))
        # Per-cell trades CSV
        with (cell_dir / "trades.csv").open("w", newline="") as fh:
            if not row.result.trades:
                fh.write("")
                continue
            fields = list(asdict(row.result.trades[0]).keys())
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            for t in row.result.trades:
                trow = asdict(t)
                for k, v in trow.items():
                    if isinstance(v, Decimal):
                        trow[k] = str(v)
                w.writerow(trow)

    typer.echo(f"wrote {out / 'grid.md'}")
    typer.echo(f"wrote {summary_path}")
    typer.echo(f"wrote {len(result.rows)} cells under {cells_dir}")


@config_app.command("show")
def config_show() -> None:
    """Print current config as YAML."""
    import yaml

    from wave_alpha.prefs.config import load_config

    cfg = load_config(_config_path())
    typer.echo(yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False))


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Config key: api_key, scan_model, deep_model, alpha"),
    value: str = typer.Argument(...),
) -> None:
    """Set a config value and persist to ~/.wave_alpha/config.yaml."""
    from wave_alpha.prefs.config import load_config, save_config
    from wave_alpha.prefs.models import AppConfig

    cfg = load_config(_config_path())
    if key in {"api_key", "scan_model", "deep_model"}:
        new_llm = cfg.llm.model_copy(update={key: value})
    elif key == "alpha":
        try:
            alpha = float(value)
        except ValueError as exc:
            raise typer.BadParameter("alpha must be a float in [0, 1]") from exc
        if not 0.0 <= alpha <= 1.0:
            raise typer.BadParameter("alpha must be in [0, 1]")
        new_llm = cfg.llm.model_copy(update={"alpha": alpha})
    else:
        raise typer.BadParameter(f"unknown key: {key}")
    save_config(AppConfig(llm=new_llm), _config_path())
    typer.echo(f"updated {key}")


@app.command()
def version() -> None:
    """Print the wave-alpha version."""
    typer.echo(__version__)


@app.command()
def analyze(
    ticker: str = typer.Argument(..., help="Ticker symbol, e.g. AAPL"),
    as_of: str | None = typer.Option(
        None,
        "--as-of",
        help="Analysis as-of date (YYYY-MM-DD). Defaults to today at invocation.",
    ),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSON to stdout instead of human-readable output."
    ),
    llm_mode: LLMMode = typer.Option(  # noqa: B008
        LLMMode.DISABLED,
        "--llm-mode",
        help="LLM execution mode: live, cached, or disabled.",
    ),
    alpha: float | None = typer.Option(
        None, "--alpha", help="Override LLMConfig.alpha (0..1). Defaults to config."
    ),
) -> None:
    """Run Elliott Wave analysis on TICKER and print counts, coherence, and signals."""
    resolved_as_of = date.fromisoformat(as_of) if as_of else date.today()
    cfg = load_config()
    effective_alpha = alpha if alpha is not None else cfg.llm.alpha
    provider = _default_provider()
    runner = _build_runner(cfg, mode=llm_mode) if llm_mode is not LLMMode.DISABLED else None
    req = AnalyzeRequest(ticker=ticker.upper(), as_of=resolved_as_of)
    logger.debug("analyze request {} mode={} alpha={}", req, llm_mode.value, effective_alpha)
    result = run_analysis(
        req,
        provider=provider,
        llm_mode=llm_mode,
        llm_runner=runner,
        alpha=effective_alpha,
        enabled_opt_in_templates=cfg.templates.enabled_opt_in_names(),
    )
    # Best-effort history snapshot (never raises)
    from wave_alpha.history import service as history_service

    history_service.record_snapshot(
        result, ticker.upper(), resolved_as_of, db_path=_DEFAULT_DATA_DIR / "history.sqlite"
    )
    if json_out:
        typer.echo(json.dumps(_serialize(result), default=_json_default))
    else:
        _print_human(result)


@app.command()
def resolve(
    ticker: str | None = typer.Option(None, "--ticker", "-t", help="Ticker symbol to resolve."),
    all_tickers: bool = typer.Option(
        False, "--all", help="Resolve all tickers with unresolved history rows."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON summary to stdout."),
) -> None:
    """Resolve any unresolved or pending history snapshots for one ticker
    or all tickers. Walks daily bars from the provider and updates outcome
    status in ~/.wave_alpha/history.sqlite."""
    if not ticker and not all_tickers:
        typer.echo("Specify --ticker T or --all", err=True)
        raise typer.Exit(code=1)

    # Lazy imports — consistent with CLI style
    import json as _json
    from datetime import UTC
    from datetime import date as _date
    from datetime import datetime as _datetime

    from wave_alpha.domain import Interval
    from wave_alpha.history import resolver as history_resolver
    from wave_alpha.history import store as history_store

    db_path = _DEFAULT_DATA_DIR / "history.sqlite"
    provider = _default_provider()
    today = _date.today()

    if all_tickers:
        all_rows = history_store.recent_across_all(db_path=db_path, limit=10_000)
        tickers = sorted({r.ticker for r in all_rows})
    else:
        tickers = [ticker.upper()]

    summaries = []
    for t in tickers:
        rows = history_store.unresolved_for(t, db_path=db_path)
        if not rows:
            summaries.append(
                {
                    "ticker": t,
                    "total_unresolved": 0,
                    "rows_resolved": 0,
                    "rows_still_pending": 0,
                    "errors": [],
                }
            )
            continue
        earliest = min(r.as_of for r in rows)
        try:
            bars = provider.fetch(t, Interval.DAILY, end=today)
            bars_in_range = [b for b in bars if b.timestamp >= earliest]
            updated = history_resolver.resolve_pending_for(
                t, bars_in_range, db_path=db_path, today=today
            )
            still_pending = sum(1 for _ in history_store.unresolved_for(t, db_path=db_path))
            summaries.append(
                {
                    "ticker": t,
                    "total_unresolved": len(rows),
                    "rows_resolved": updated,
                    "rows_still_pending": still_pending,
                    "errors": [],
                }
            )
        except Exception as exc:
            summaries.append(
                {
                    "ticker": t,
                    "total_unresolved": len(rows),
                    "rows_resolved": 0,
                    "rows_still_pending": len(rows),
                    "errors": [str(exc)],
                }
            )

    if json_output:
        output = {
            "resolved_at": _datetime.now(tz=UTC).isoformat(),
            "tickers": summaries,
        }
        typer.echo(_json.dumps(output, default=str))
    else:
        for s in summaries:
            typer.echo(
                f"{s['ticker']}: resolved {s['rows_resolved']}/{s['total_unresolved']}, "
                f"still pending {s['rows_still_pending']}"
            )
            if s["errors"]:
                for e in s["errors"]:
                    typer.echo(f"  error: {e}", err=True)


def _build_runner(cfg, *, mode: LLMMode) -> LLMRunner:
    """Build a runner for LIVE or CACHED mode.

    LIVE  → real AnthropicClient; raises BadParameter if no API key.
    CACHED → real client if a key is available (so a cache miss after live use
             still benefits from the API), else CacheOnlyClient with the
             configured model id (so reproducible runs work without a key).
    """
    api_key = resolve_api_key(cfg)
    if api_key:
        client: LLMClient = AnthropicClient(api_key=api_key, model=cfg.llm.scan_model)
    elif mode is LLMMode.LIVE:
        raise typer.BadParameter(
            "no Anthropic API key configured; set ANTHROPIC_API_KEY or run "
            "'wave-alpha config set api_key <key>'"
        )
    else:
        client = CacheOnlyClient(model=cfg.llm.scan_model)
    cache = LLMResponseCache(db_path=_DEFAULT_DATA_DIR / "llm_cache.sqlite")
    _DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return LLMRunner(client=client, cache=cache, mode=mode)


def _default_provider() -> OHLCVProvider:
    """Production provider stack: Yahoo (primary) + Stooq (fallback) wrapped
    in a SQLite-backed cache.

    The cache lives at `~/.wave_alpha/cache.sqlite` and persists OHLCV bars
    across CLI invocations so a `wave-alpha analyze AAPL` run only hits an
    upstream when the cache is cold for that `(ticker, interval)` pair.

    Stooq covers daily/weekly only; 4h fetches stay Yahoo-only. Stooq's
    free CSV endpoint requires a captcha-issued API key; without one the
    StooqProvider short-circuits to [] and FallbackProvider stays on Yahoo.
    """
    _DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    stooq_key = resolve_stooq_api_key(cfg)
    return CachedProvider(
        upstream=FallbackProvider([YahooProvider(), StooqProvider(api_key=stooq_key)]),
        db_path=_DEFAULT_DATA_DIR / "cache.sqlite",
    )


def _serialize(result: Any) -> Any:
    """JSON-serialize a mixed graph of dataclasses, Pydantic models, lists, and dicts.

    Pydantic models are converted via `model_dump(mode="json")` (handles Decimal/
    date/Enum cleanly). Dataclasses are walked recursively. Anything else falls
    through to `_json_default` at encoding time.
    """
    if is_dataclass(result) and not isinstance(result, type):
        return {k: _serialize(v) for k, v in asdict(result).items()}
    if isinstance(result, list):
        return [_serialize(v) for v in result]
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if isinstance(result, dict):
        return {k: _serialize(v) for k, v in result.items()}
    return result


def _json_default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, date):
        return o.isoformat()
    raise TypeError(f"unserializable: {type(o).__name__}")


def _print_human(result: Any) -> None:
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
    typer.echo(f"signals: {len(result.signals)}")
    for sig in result.signals:
        typer.echo(
            f"  {sig.direction.upper()}  entry={sig.entry_price}  "
            f"stop={sig.stop_price}  target={sig.target_price}  rr={sig.rr:.2f}"
        )
    typer.echo("\n⚠ Informational only. Confirm thesis. Not investment advice.")


def _pick_port(*, start: int = 8765, end: int = 8775) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"no free port in {start}-{end}")


@app.command()
def ui(
    port: int | None = typer.Option(
        None, "--port", help="TCP port to bind. Default: first free port in 8765-8775."
    ),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Don't open the browser automatically."
    ),
    reload: bool = typer.Option(False, "--reload", help="uvicorn auto-reload (dev only)."),
) -> None:
    """Launch the local web UI in a browser."""
    import uvicorn  # local import — uvicorn is in [ui] extra

    chosen_port = port or _pick_port()
    url = f"http://127.0.0.1:{chosen_port}"
    typer.echo(f"wave-alpha UI on {url}  (Ctrl-C to quit)")
    if not no_browser:
        webbrowser.open(url)
    uvicorn.run(
        "wave_alpha.web.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=chosen_port,
        reload=reload,
        log_level="info",
    )


@app.command()
def scan(
    symbols: str | None = typer.Option(
        None, "--symbols", help="Comma-separated symbols. Overrides --watchlist."
    ),
    watchlist: bool = typer.Option(
        True, "--watchlist/--no-watchlist", help="Use watchlist when --symbols not given."
    ),
    as_of: str | None = typer.Option(None, "--as-of", help="Scan as-of YYYY-MM-DD."),
    llm_mode: LLMMode = typer.Option(  # noqa: B008
        LLMMode.DISABLED, "--llm-mode", help="LLM mode (default: disabled = $0)."
    ),
) -> None:
    """Run a scan over multiple tickers and persist a report."""
    resolved_as_of = date.fromisoformat(as_of) if as_of else date.today()
    if symbols:
        sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    elif watchlist:
        sym_list = [e.symbol for e in _wl_load(_watchlist_path())]
    else:
        sym_list = []
    if not sym_list:
        typer.echo("no symbols to scan: pass --symbols or populate the watchlist")
        raise typer.Exit(code=1)

    cfg = load_config()
    provider = _default_provider()
    runner_obj = _build_runner(cfg, mode=llm_mode) if llm_mode is not LLMMode.DISABLED else None
    from wave_alpha.right_edge.loader import load_right_edge_model

    right_edge_model = load_right_edge_model(cfg.right_edge)
    typer.echo(f"scanning {len(sym_list)} symbol(s) as_of={resolved_as_of}…")
    report = _run_scan(
        symbols=sym_list,
        as_of=resolved_as_of,
        provider=provider,
        llm_mode=llm_mode,
        llm_runner=runner_obj,
        alpha=cfg.llm.alpha,
        right_edge_model=right_edge_model,
        enabled_opt_in_templates=cfg.templates.enabled_opt_in_names(),
    )
    out = save_report(report, dir=_scan_dir())
    typer.echo(
        f"wrote {out}  (signals={report.n_with_signal}/{report.n_total}, errors={report.n_errored})"
    )
    for r in report.rows:
        if r.has_error:
            typer.echo(f"  ✗ {r.ticker}  {r.error}")
        elif r.has_signal:
            typer.echo(
                f"  ✓ {r.ticker}  {r.top_pattern}  rr={r.signal_rr:.2f}  "
                f"entry={r.signal_entry}  stop={r.signal_stop}  target={r.signal_target}"
            )
        else:
            score = f"{r.top_score:.2f}" if r.top_score is not None else "n/a"
            typer.echo(f"  • {r.ticker}  {r.top_pattern or '—'}  score={score}  (no signal)")


def _evaluate_promotion(**kwargs):
    """Thin wrapper around evaluate_promotion — exists at module level so tests
    can monkeypatch it without patching inside the lazy import block."""
    from wave_alpha.right_edge.promotion import evaluate_promotion

    return evaluate_promotion(**kwargs)


def _load_rows_by_quarter(
    *,
    universe_file: Path,
    start_date: str,
    end_date: str,
    provider: object,
) -> tuple[date, date, list, dict[date, list]]:
    """Shared setup for both train commands: load universe, compute as-of pairs,
    assemble training rows, and bucket by quarter.

    Returns:
        (start, end, folds, rows_by_quarter)
    """
    import itertools

    from wave_alpha.right_edge.training import (
        _next_quarter_start,
        assemble_training_rows,
        quarter_folds,
        quarter_start,
    )

    universe = [
        s.strip().upper()
        for s in universe_file.read_text().splitlines()
        if s.strip() and not s.strip().startswith("#")
    ]

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)

    folds = quarter_folds(start, end)
    if not folds:
        typer.echo(f"No folds between {start} and {end}; need ≥5 quarters (4 warmup + 1 test).")
        raise typer.Exit(1)

    # Walk ALL quarter boundaries so warmup quarters produce training rows.
    _boundaries = [start]
    _cur = start
    while _cur < end:
        _cur = _next_quarter_start(_cur)
        _boundaries.append(min(_cur, end))
    as_of_pairs = list(itertools.pairwise(_boundaries))

    typer.echo(
        f"Assembling training rows for {len(universe)} ticker(s) x {len(as_of_pairs)} as-of pair(s)..."
    )
    rows = assemble_training_rows(
        provider=provider,
        universe=universe,
        as_of_pairs=as_of_pairs,
    )
    typer.echo(f"  → {len(rows)} training rows assembled")

    if not rows:
        typer.echo("No training rows produced — check universe file and date range.")
        raise typer.Exit(1)

    rows_by_quarter: dict[date, list] = {}
    for r in rows:
        rows_by_quarter.setdefault(quarter_start(r.as_of), []).append(r)

    return start, end, folds, rows_by_quarter


@train_app.command("right-edge")
def train_right_edge(
    universe_file: Path = typer.Option(  # noqa: B008
        Path("data/universe_curated.txt"),
        "--universe",
        help="Path to ticker universe file (one symbol per line; # = comment).",
    ),
    start_date: str = typer.Option("2021-01-01", "--start", help="Training start date YYYY-MM-DD."),
    end_date: str = typer.Option(..., "--end", help="Training end date YYYY-MM-DD (required)."),
    write: bool = typer.Option(False, "--write", help="Write artifact to --output path."),
    force: bool = typer.Option(False, "--force", help="Write even if verdict is HOLD/REJECT."),
    seed: int = typer.Option(42, "--seed", help="RNG seed for logistic fit and bootstrap CI."),
    with_fib_features: bool = typer.Option(
        False,
        "--with-fib-features",
        help="Train with the 14 fib structural features appended to feature_order.",
    ),
    output_path: Path = typer.Option(  # noqa: B008
        Path("src/wave_alpha/right_edge/models/right_edge_logistic_v1.json"),
        "--output",
        help="Path to write the JSON artifact.",
    ),
    use_outcomes: bool = typer.Option(
        False,
        "--use-outcomes",
        help="Blend outcome-labeled rows from history.sqlite into the final refit.",
    ),
    outcome_weight_target: int | None = typer.Option(
        None,
        "--outcome-weight-target",
        help="Per-degree outcome row count at which outcomes match synthetic mass. "
        "Default reads from right_edge.outcome_training.n_target_per_degree in config.yaml.",
    ),
    outcome_limit: int | None = typer.Option(
        None,
        "--outcome-limit",
        help="Cap on outcome rows processed (debug/fast iteration).",
    ),
    drop_time_stops: bool = typer.Option(
        False,
        "--drop-time-stops",
        help="Exclude time_stop snapshots from the outcome dataset.",
    ),
) -> None:
    """Run walk-forward training, gate the result, optionally write the artifact."""
    from wave_alpha.right_edge.training import (
        serialize_artifact,
        walk_forward,
        write_artifact,
    )

    provider = _default_provider()
    _start, _end, folds, rows_by_quarter = _load_rows_by_quarter(
        universe_file=universe_file,
        start_date=start_date,
        end_date=end_date,
        provider=provider,
    )

    typer.echo("Running walk-forward...")
    wf = walk_forward(rows_by_quarter, folds=folds, seed=seed, with_fib=with_fib_features)
    typer.echo(f"  → {wf.n_train_rows} train rows, {wf.n_oos_rows} OOS predictions")

    outcome_metadata: dict | None = None
    if use_outcomes:
        from collections import Counter

        from wave_alpha.right_edge.outcome_dataset import (
            WeightSchedule,
            build_outcome_dataset,
        )
        from wave_alpha.right_edge.training import (
            WalkForwardResult,
            train_blended_final,
        )

        # Load config defaults; CLI flags override.
        cfg_outcome = load_config().right_edge.outcome_training
        schedule = WeightSchedule(
            n_target_per_degree=outcome_weight_target
            if outcome_weight_target is not None
            else cfg_outcome.n_target_per_degree,
            include_time_stops=cfg_outcome.include_time_stops and not drop_time_stops,
            time_stop_label=cfg_outcome.time_stop_label,
        )
        synth_rows_all = [r for rs in rows_by_quarter.values() for r in rs]
        n_synth_per_degree = dict(Counter(r.degree for r in synth_rows_all))
        db_path = _DEFAULT_DATA_DIR / "history.sqlite"

        typer.echo("Building outcome dataset (recompute from cached bars)...")
        ds = build_outcome_dataset(
            db_path=db_path,
            bar_provider=provider,
            degree=2,  # primary degree; multi-degree extension is a follow-up
            schedule=schedule,
            n_synth_per_degree=n_synth_per_degree,
            limit=outcome_limit,
        )
        typer.echo(
            f"  -> outcome rows: {len(ds.rows)} | "
            f"skipped_drift: {ds.skipped_drift} | "
            f"skipped_no_bars: {ds.skipped_no_bars} | "
            f"skipped_wrong_degree: {ds.skipped_wrong_degree}"
        )

        logistic_blended, platt_blended = train_blended_final(
            synth_rows_all,
            ds,
            seed=seed,
            with_fib=with_fib_features,
        )
        # Splice blended results back into the WalkForwardResult for serialization.
        wf = WalkForwardResult(
            folds=wf.folds,
            final_logistic=logistic_blended,
            platt_per_degree=platt_blended,
            oos_predictions=wf.oos_predictions,
            n_train_rows=wf.n_train_rows + len(ds.rows),
            n_oos_rows=wf.n_oos_rows,
        )
        outcome_metadata = {
            "n_outcome_rows": len(ds.rows),
            "skipped_drift": ds.skipped_drift,
            "skipped_no_bars": ds.skipped_no_bars,
            "skipped_wrong_degree": ds.skipped_wrong_degree,
            "n_target_per_degree": schedule.n_target_per_degree,
            "include_time_stops": schedule.include_time_stops,
            "time_stop_label": schedule.time_stop_label,
        }

    # Heuristic baseline on the same OOS prediction set.
    from wave_alpha.right_edge.loader import HeuristicAdapter

    heuristic = HeuristicAdapter()
    log_preds: list[tuple[float, int, int]] = [
        (cp.calibrated_proba, cp.outcome, cp.degree) for cp in wf.oos_predictions
    ]
    heu_preds: list[tuple[float, int, int]] = [
        (heuristic.predict_proba(cp.features, degree=cp.degree), cp.outcome, cp.degree)
        for cp in wf.oos_predictions
    ]

    gate = _evaluate_promotion(logistic_preds=log_preds, heuristic_preds=heu_preds, seed=seed)

    # --- Output verdict + CI summary ---
    typer.echo("")
    typer.echo(f"Verdict: {gate.verdict}")
    lo, mid, hi = gate.logistic_brier_ci
    typer.echo(f"  Logistic  Brier 95% CI: [{lo:.4f}, {mid:.4f}, {hi:.4f}]")
    lo, mid, hi = gate.heuristic_brier_ci
    typer.echo(f"  Heuristic Brier 95% CI: [{lo:.4f}, {mid:.4f}, {hi:.4f}]")

    if gate.per_degree_calibration:
        typer.echo("  Per-degree calibration error (ECE):")
        for d in sorted(gate.per_degree_calibration):
            m = gate.per_degree_calibration[d]
            typer.echo(
                f"    degree={d}  logistic={m['logistic']:.4f}  heuristic={m['heuristic']:.4f}"
            )
    else:
        typer.echo("  Per-degree calibration: (no OOS predictions by degree)")

    # --- Optional artifact write ---
    if write:
        if gate.verdict not in {"PROMOTE", "PROMOTE_CALIBRATION"} and not force:
            typer.echo(f"\nNot writing — verdict is {gate.verdict}. Use --force to write anyway.")
            raise typer.Exit(1)
        if gate.verdict not in {"PROMOTE", "PROMOTE_CALIBRATION"} and force:
            typer.echo(f"\nWARNING: Writing artifact despite {gate.verdict} verdict (--force).")
        artifact = serialize_artifact(
            wf=wf, gate=gate, seed=seed, outcome_training=outcome_metadata
        )
        write_artifact(artifact, output_path)
        typer.echo(f"\nWrote artifact to {output_path}")


@train_app.command("outcome-eval")
def train_outcome_eval(
    universe_file: Path = typer.Option(  # noqa: B008
        Path("data/universe_curated.txt"),
        "--universe",
        help="Path to ticker universe file (one symbol per line; # = comment).",
    ),
    start_date: str = typer.Option("2021-01-01", "--start", help="Training start date YYYY-MM-DD."),
    end_date: str = typer.Option(..., "--end", help="Training end date YYYY-MM-DD (required)."),
    quarters: int | None = typer.Option(
        None, "--quarters", help="Override quarters; default derived from start/end."
    ),
    fold_count: int | None = typer.Option(None, "--folds", help="Override fold count; default 4."),
    holdout_fraction: float = typer.Option(
        0.20, "--holdout-fraction", help="Fraction of resolved outcomes held out by exit_date."
    ),
    seed: int = typer.Option(42, "--seed", help="RNG seed (logistic fit, bootstrap)."),
    with_fib_features: bool = typer.Option(False, "--with-fib-features"),
    outcome_weight_target: int | None = typer.Option(
        None,
        "--outcome-weight-target",
        help="Override n_target_per_degree (default reads from config.yaml).",
    ),
    include_time_stops: bool = typer.Option(
        True,
        "--include-time-stops/--no-time-stops",
        help="Whether time_stop outcomes contribute (default reads from config.yaml).",
    ),
    out_path: Path | None = typer.Option(  # noqa: B008
        None, "--out", help="Markdown report destination. Default: reports/outcome_eval_<end>.md."
    ),
) -> None:
    """Evaluate whether outcome-driven training improves right-edge model quality.

    Read-only: trains two arms (synthetic-only vs synthetic+outcomes), scores
    both on a temporally held-out slice of realized outcomes, writes a
    markdown report with per-degree Brier/ECE and a paired bootstrap Δ Brier CI.

    Does NOT write model artifacts.
    """
    from wave_alpha.right_edge.outcome_dataset import WeightSchedule
    from wave_alpha.right_edge.outcome_eval import (
        OutcomeEvalConfig,
        format_markdown_report,
        run_outcome_eval,
    )

    provider = _default_provider()
    _start, _end, folds, rows_by_quarter = _load_rows_by_quarter(
        universe_file=universe_file,
        start_date=start_date,
        end_date=end_date,
        provider=provider,
    )

    # Schedule: config defaults, CLI overrides win.
    cfg_outcome = load_config().right_edge.outcome_training
    schedule = WeightSchedule(
        n_target_per_degree=outcome_weight_target
        if outcome_weight_target is not None
        else cfg_outcome.n_target_per_degree,
        include_time_stops=include_time_stops and cfg_outcome.include_time_stops,
        time_stop_label=cfg_outcome.time_stop_label,
    )

    universe = [
        line.strip()
        for line in universe_file.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    cfg = OutcomeEvalConfig(
        end=_end,
        universe=universe,
        quarters=quarters if quarters is not None else len(rows_by_quarter),
        fold_count=fold_count if fold_count is not None else len(folds),
        holdout_fraction=holdout_fraction,
        seed=seed,
        with_fib=with_fib_features,
        schedule=schedule,
    )

    typer.echo("Running outcome-training evaluation...")
    typer.echo(f"  synthetic rows by quarter: {sum(len(rs) for rs in rows_by_quarter.values())}")
    db_path = _DEFAULT_DATA_DIR / "history.sqlite"
    result = run_outcome_eval(
        cfg,
        rows_by_quarter=rows_by_quarter,
        folds=folds,
        bar_provider=provider,
        history_db_path=db_path,
    )

    typer.echo(f"\nVerdict: {result.verdict}")
    lo, mid, hi = result.brier_delta_ci
    typer.echo(f"  Δ Brier 95% CI (B - A): [{lo:.4f}, {mid:.4f}, {hi:.4f}]")
    typer.echo(f"  Arm A overall Brier: {result.arm_a.brier_overall:.4f}")
    typer.echo(f"  Arm B overall Brier: {result.arm_b.brier_overall:.4f}")

    if out_path is None:
        out_path = Path("reports") / f"outcome_eval_{_end.isoformat()}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(format_markdown_report(result))
    typer.echo(f"\nReport written to {out_path}")


@train_app.command("calibrated-heuristic")
def train_calibrated_heuristic(
    universe_file: Path = typer.Option(  # noqa: B008
        Path("data/universe_curated.txt"),
        "--universe",
        help="Path to ticker universe file (one symbol per line; # = comment).",
    ),
    start_date: str = typer.Option("2021-01-01", "--start", help="Training start date YYYY-MM-DD."),
    end_date: str = typer.Option(..., "--end", help="Training end date YYYY-MM-DD (required)."),
    write: bool = typer.Option(False, "--write", help="Write artifact to --output path."),
    force: bool = typer.Option(False, "--force", help="Write even if verdict is HOLD/REJECT."),
    seed: int = typer.Option(42, "--seed", help="RNG seed for bootstrap CI."),
    output_path: Path = typer.Option(  # noqa: B008
        Path("src/wave_alpha/right_edge/models/right_edge_calibrated_heuristic_v1.json"),
        "--output",
        help="Path to write the JSON artifact.",
    ),
) -> None:
    """Fit per-degree Platt scalers on top of the heuristic.

    Same walk-forward + paired-bootstrap gate as ``train right-edge``; the model
    being evaluated is "heuristic + Platt" vs raw heuristic.  Produces a smaller
    artifact than the logistic — just the Platt (a, b) pairs, no logistic weights.
    """
    from wave_alpha.right_edge.training import (
        fit_calibrated_heuristic,
        serialize_calibrated_heuristic_artifact,
        write_artifact,
    )

    provider = _default_provider()
    _start, _end, folds, rows_by_quarter = _load_rows_by_quarter(
        universe_file=universe_file,
        start_date=start_date,
        end_date=end_date,
        provider=provider,
    )

    typer.echo("Fitting calibrated heuristic (walk-forward Platt on top of heuristic)...")
    final_platt, oos = fit_calibrated_heuristic(rows_by_quarter, folds=folds)
    typer.echo(f"  → {len(oos)} OOS predictions")

    if not oos:
        typer.echo("No OOS predictions produced — check universe file and date range.")
        raise typer.Exit(1)

    # Gate: calibrated heuristic vs raw heuristic on the same OOS set.
    from wave_alpha.right_edge.loader import HeuristicAdapter

    raw_heuristic = HeuristicAdapter()
    cal_preds: list[tuple[float, int, int]] = [
        (cp.calibrated_proba, cp.outcome, cp.degree) for cp in oos
    ]
    raw_preds: list[tuple[float, int, int]] = [
        (raw_heuristic.predict_proba(cp.features, degree=cp.degree), cp.outcome, cp.degree)
        for cp in oos
    ]

    gate = _evaluate_promotion(logistic_preds=cal_preds, heuristic_preds=raw_preds, seed=seed)

    # --- Output verdict + CI summary ---
    typer.echo("")
    typer.echo(f"Verdict: {gate.verdict}")
    lo, mid, hi = gate.logistic_brier_ci
    typer.echo(f"  Calibrated-heuristic Brier 95% CI: [{lo:.4f}, {mid:.4f}, {hi:.4f}]")
    lo, mid, hi = gate.heuristic_brier_ci
    typer.echo(f"  Raw heuristic        Brier 95% CI: [{lo:.4f}, {mid:.4f}, {hi:.4f}]")

    if gate.per_degree_calibration:
        typer.echo("  Per-degree calibration error (ECE):")
        for d in sorted(gate.per_degree_calibration):
            m = gate.per_degree_calibration[d]
            typer.echo(f"    degree={d}  calibrated={m['logistic']:.4f}  raw={m['heuristic']:.4f}")
    else:
        typer.echo("  Per-degree calibration: (no OOS predictions by degree)")

    # --- Optional artifact write ---
    if write:
        if gate.verdict not in {"PROMOTE", "PROMOTE_CALIBRATION"} and not force:
            typer.echo(f"\nNot writing — verdict is {gate.verdict}. Use --force to write anyway.")
            raise typer.Exit(1)
        if gate.verdict not in {"PROMOTE", "PROMOTE_CALIBRATION"} and force:
            typer.echo(f"\nWARNING: Writing artifact despite {gate.verdict} verdict (--force).")
        artifact = serialize_calibrated_heuristic_artifact(platt=final_platt, gate=gate)
        write_artifact(artifact, output_path)
        typer.echo(f"\nWrote artifact to {output_path}")


def _engine_ranker_default_artifact_path() -> Path:
    """Default on-disk location for the trained logistic artifact.

    Mirrors `right_edge` precedent: written into the in-package models
    directory so `auto` mode loads it. Tests monkeypatch this.
    """
    return Path("src/wave_alpha/engine_ranker/models/engine_ranker_logistic_v1.json")


def _engine_ranker_provider():
    """Indirected for tests: production uses _default_provider()."""
    return _default_provider()


def _engine_ranker_history_db() -> Path:
    """Default history DB path. Indirected for tests."""
    from wave_alpha.cli.history_app import _history_db_path

    return _history_db_path()


@train_app.command("engine-ranker")
def train_engine_ranker(
    use_outcomes: bool = typer.Option(
        True,
        "--use-outcomes/--no-outcomes",
        help="Read resolved snapshots from history.sqlite (only mode supported today).",
    ),
    include_time_stops: bool = typer.Option(True, "--include-time-stops/--drop-time-stops"),
    split: float = typer.Option(0.7, "--split", help="Walk-forward train/holdout split fraction."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan; do not train or write."),
    write: bool = typer.Option(False, "--write", help="Write artifact if verdict=PROMOTE."),
    force: bool = typer.Option(False, "--force", help="Write regardless of verdict."),
    output: Path = typer.Option(  # noqa: B008
        None,
        "--output",
        "-o",
        help="Artifact output path. Defaults to the in-package models directory.",
    ),
) -> None:
    """Fit an outcome-trained engine-ranker logistic from history.sqlite."""
    import json as _json_mod

    from wave_alpha.engine_ranker import heuristic as heuristic_mod
    from wave_alpha.engine_ranker import promotion as promo_mod
    from wave_alpha.engine_ranker import training as training_mod

    if not use_outcomes:
        typer.echo(
            "--no-outcomes mode is not supported; engine-ranker requires resolved rows.",
            err=True,
        )
        raise typer.Exit(code=2)

    db_path = _engine_ranker_history_db()
    provider = _engine_ranker_provider()
    artifact_path = output if output is not None else _engine_ranker_default_artifact_path()

    dataset = training_mod.build_outcome_dataset(
        db_path=db_path,
        provider=provider,
        include_time_stops=include_time_stops,
    )
    typer.echo(
        f"dataset: {len(dataset.rows)} rows "
        f"(target={dataset.n_target}, non-target={dataset.n_non_target}, "
        f"skipped_no_features={dataset.skipped_no_features}, "
        f"skipped_unresolved={dataset.skipped_unresolved})"
    )
    if dry_run:
        typer.echo("--dry-run: not training, not writing.")
        return

    if not dataset.rows:
        typer.echo("no rows to train on; exiting.", err=True)
        raise typer.Exit(code=2)

    candidate = training_mod.train_logistic(dataset, walk_forward_split_fraction=split)
    candidate_model = training_mod.candidate_to_loadable(candidate)
    baseline = heuristic_mod.HeuristicEngineRanker()
    _train_rows, holdout = training_mod.walk_forward_split(dataset.rows, split=split)
    report = promo_mod.evaluate_promotion(
        candidate=candidate_model,
        baseline=baseline,
        holdout_rows=holdout,
        min_resolved_per_degree=30,
        n_bootstrap=10_000,
    )

    typer.echo(
        f"promotion: verdict={report.verdict.value} "
        f"brier_lift_mean={report.brier_lift_mean:+.4f} "
        f"brier_ci=({report.brier_lift_ci[0]:+.4f}, {report.brier_lift_ci[1]:+.4f}) "
        f"r_lift_mean={report.r_lift_mean:+.4f} "
        f"r_ci=({report.r_lift_ci[0]:+.4f}, {report.r_lift_ci[1]:+.4f})"
    )

    if not write:
        return
    if report.verdict.value != "PROMOTE" and not force:
        typer.echo(
            f"refusing to write artifact with verdict={report.verdict.value} "
            f"(use --force to override).",
            err=True,
        )
        raise typer.Exit(code=1)

    # With --force, the user's verdict goes into the gate — the loader's
    # PROMOTE-only check will still refuse to auto-load a HOLD/INSUFFICIENT_DATA
    # artifact, which is the right behavior. --force is for offline inspection.
    payload = training_mod.serialize_artifact(candidate, gate_verdict=report.verdict.value)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(_json_mod.dumps(payload, indent=2))
    typer.echo(f"wrote artifact to {artifact_path}")


if __name__ == "__main__":
    app()
