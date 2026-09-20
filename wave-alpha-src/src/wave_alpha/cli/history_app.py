"""`wave-alpha history` sub-app: stats and prune commands."""

from __future__ import annotations

import json as _json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import typer

history_app = typer.Typer(no_args_is_help=True, help="Inspect and prune history.sqlite.")


_DEFAULT_DATA_DIR = Path.home() / ".wave_alpha"


def _history_db_path() -> Path:
    """Resolve the on-disk path of history.sqlite. Indirected for tests."""
    return _DEFAULT_DATA_DIR / "history.sqlite"


def _distinct_tickers(db_path: Path) -> list[str]:
    """Return all ticker symbols present in snapshots, ordered ascending."""
    conn = sqlite3.connect(db_path)
    try:
        return [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT ticker FROM snapshots ORDER BY ticker"
            ).fetchall()
        ]
    finally:
        conn.close()


def _parse_older_than(value: str) -> int:
    """Parse `730d` or `730` → 730 days. Rejects non-day units explicitly."""
    s = value.strip().lower()
    if s.endswith("d"):
        s = s[:-1]
    try:
        days = int(s)
    except ValueError as exc:
        raise typer.BadParameter(f"--older-than: expected `<N>d` or `<N>`, got {value!r}") from exc
    if days < 0:
        raise typer.BadParameter("--older-than must be non-negative")
    return days


@history_app.command("stats")
def stats(json: bool = typer.Option(False, "--json", help="Emit JSON output.")) -> None:
    """Print per-ticker row counts, outcome-status counts, oldest/newest as_of."""
    db_path = _history_db_path()
    if not db_path.exists():
        if json:
            typer.echo(_json.dumps({"rows": 0, "tickers": []}))
        else:
            typer.echo("history.sqlite does not exist yet.")
        return

    conn = sqlite3.connect(db_path)
    try:
        per_ticker = conn.execute(
            """
            SELECT ticker, COUNT(*), MIN(as_of), MAX(as_of)
            FROM snapshots
            GROUP BY ticker
            ORDER BY ticker
            """
        ).fetchall()
        outcome_counts = dict(
            conn.execute(
                """
                SELECT COALESCE(outcome_status, 'unresolved'), COUNT(*)
                FROM snapshots
                GROUP BY outcome_status
                """
            ).fetchall()
        )
    finally:
        conn.close()

    total = sum(r[1] for r in per_ticker)
    size_bytes = db_path.stat().st_size

    if json:
        typer.echo(
            _json.dumps(
                {
                    "rows": total,
                    "size_bytes": size_bytes,
                    "outcome_counts": outcome_counts,
                    "tickers": [
                        {"ticker": t, "rows": n, "oldest": o, "newest": x}
                        for (t, n, o, x) in per_ticker
                    ],
                }
            )
        )
        return

    typer.echo(f"history.sqlite at {db_path}")
    typer.echo(f"  total rows: {total}")
    typer.echo(f"  on-disk size: {size_bytes / 1024:.1f} KiB")
    typer.echo(f"  by outcome_status: {outcome_counts}")
    typer.echo("  per ticker (rows / oldest / newest):")
    for ticker, n, oldest, newest in per_ticker:
        typer.echo(f"    {ticker:>6}  {n:>5}  {oldest}  →  {newest}")


@history_app.command("prune")
def prune(
    older_than: str = typer.Option(..., "--older-than", help="e.g. `730d` or `730`."),
    ticker: str | None = typer.Option(None, "--ticker", help="Limit to one ticker."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be deleted."),
    json: bool = typer.Option(False, "--json", help="Emit JSON output."),
) -> None:
    """Manually prune snapshots older than --older-than days."""
    from wave_alpha.history import store as _store

    db_path = _history_db_path()
    if not db_path.exists():
        typer.echo("history.sqlite does not exist yet; nothing to prune.")
        return

    days = _parse_older_than(older_than)
    cutoff = date.today() - timedelta(days=days)

    tickers = [ticker] if ticker else _distinct_tickers(db_path)

    if dry_run:
        results: dict[str, int] = {}
        conn = sqlite3.connect(db_path)
        try:
            for t in tickers:
                n = conn.execute(
                    "SELECT COUNT(*) FROM snapshots WHERE ticker = ? AND as_of < ?",
                    (t, cutoff.isoformat()),
                ).fetchone()[0]
                if n:
                    results[t] = n
        finally:
            conn.close()
        if json:
            typer.echo(
                _json.dumps(
                    {"dry_run": True, "cutoff": cutoff.isoformat(), "would_delete": results}
                )
            )
        else:
            total = sum(results.values())
            typer.echo(f"DRY-RUN: would delete {total} rows older than {cutoff.isoformat()}")
            for t, n in results.items():
                typer.echo(f"  {t}: {n}")
        return

    deleted_by_ticker: dict[str, int] = {}
    for t in tickers:
        deleted_by_ticker[t] = _store.prune_older_than(db_path=db_path, ticker=t, cutoff=cutoff)

    total = sum(deleted_by_ticker.values())
    if json:
        typer.echo(_json.dumps({"cutoff": cutoff.isoformat(), "deleted": deleted_by_ticker}))
    else:
        typer.echo(f"Pruned {total} rows older than {cutoff.isoformat()}")
        for t, n in deleted_by_ticker.items():
            if n:
                typer.echo(f"  {t}: deleted {n}")


def _default_provider_for_backfill():
    """Indirected for tests. In production: CachedProvider over Yahoo, no
    network at the daily-resolution end since we only re-read cached bars
    for as-of dates already in cache.sqlite."""
    from wave_alpha.cli.app import _default_provider

    return _default_provider()


@history_app.command("backfill-signals")
def backfill_signals(
    ticker: str | None = typer.Option(None, "--ticker", "-t", help="One ticker to backfill."),
    all_tickers: bool = typer.Option(
        False, "--all", help="Backfill every ticker present in history.sqlite."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan; do not write."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON summary."),
) -> None:
    """Replay run_analysis at every existing (ticker, as_of) and upsert the
    snapshot under the current schema. The historical use case is upgrading
    pre-`max_holding_bars` rows so the outcome resolver can act on them."""
    if not ticker and not all_tickers:
        typer.echo("Specify --ticker T or --all", err=True)
        raise typer.Exit(code=1)

    from wave_alpha.history import service as history_service
    from wave_alpha.history import store as history_store

    db_path = _history_db_path()
    if not db_path.exists():
        typer.echo("history.sqlite does not exist yet; nothing to backfill.")
        raise typer.Exit(code=0)

    tickers = _distinct_tickers(db_path) if all_tickers else [ticker.upper()]

    provider = _default_provider_for_backfill()
    summaries = []
    for t in tickers:
        rows = history_store.recent(t, n=10_000, db_path=db_path)
        as_ofs = sorted({r.as_of for r in rows})
        replayed = 0
        skipped = 0
        if not dry_run:
            for as_of in as_ofs:
                ok = history_service.replay_snapshot(
                    ticker=t, as_of=as_of, provider=provider, db_path=db_path
                )
                if ok:
                    replayed += 1
                else:
                    skipped += 1
        summaries.append(
            {
                "ticker": t,
                "candidates": len(as_ofs),
                "replayed": replayed,
                "skipped": skipped,
            }
        )

    if json_output:
        typer.echo(_json.dumps({"dry_run": dry_run, "tickers": summaries}))
    else:
        for s in summaries:
            typer.echo(
                f"{s['ticker']}: {s['replayed']}/{s['candidates']} replayed"
                f" ({s['skipped']} skipped){' [dry-run]' if dry_run else ''}"
            )


def _quarterly_dates(start: date, end: date) -> list[date]:
    """Return quarter-end dates (Mar-31, Jun-30, Sep-30, Dec-31) within [start, end]."""
    quarter_ends_per_year = [(3, 31), (6, 30), (9, 30), (12, 31)]
    out: list[date] = []
    for year in range(start.year, end.year + 1):
        for month, day in quarter_ends_per_year:
            d = date(year, month, day)
            if start <= d <= end:
                out.append(d)
    return out


def _read_universe(path: Path) -> list[str]:
    """Read newline-delimited tickers; skip blanks and `#` comments. Uppercase."""
    tickers: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tickers.append(line.upper())
    return tickers


@history_app.command("densify")
def densify(
    universe: Path = typer.Option(  # noqa: B008
        ..., "--universe", help="Newline-delimited ticker file."
    ),
    start: str = typer.Option(..., "--start", help="ISO start date inclusive."),
    end: str = typer.Option(..., "--end", help="ISO end date inclusive."),
    cadence: str = typer.Option(
        "quarterly", "--cadence", help="Currently supports `quarterly` only."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan; do not write."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON summary."),
) -> None:
    """Generate snapshots over (universe x cadence) using cached bars. LLM is
    forced disabled. Tickers missing bars at the requested as_of are silently
    skipped (universe drift is normal — e.g. COIN didn't exist in 2018)."""
    if cadence != "quarterly":
        typer.echo(f"Only --cadence quarterly is supported (got {cadence!r}).", err=True)
        raise typer.Exit(code=2)

    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    if end_d < start_d:
        typer.echo("--end must be >= --start", err=True)
        raise typer.Exit(code=2)

    tickers = _read_universe(universe)
    as_ofs = _quarterly_dates(start_d, end_d)
    total_attempts = len(tickers) * len(as_ofs)

    if dry_run:
        typer.echo(
            f"Plan: {len(tickers)} tickers x {len(as_ofs)} dates = {total_attempts} attempts"
        )
        typer.echo(
            f"  date range: {as_ofs[0] if as_ofs else '-'} -> {as_ofs[-1] if as_ofs else '-'}"
        )
        return

    from wave_alpha.history import service as history_service

    db_path = _history_db_path()
    provider = _default_provider_for_backfill()
    summaries = []
    for t in tickers:
        written = 0
        skipped = 0
        for as_of in as_ofs:
            ok = history_service.replay_snapshot(
                ticker=t, as_of=as_of, provider=provider, db_path=db_path
            )
            if ok:
                written += 1
            else:
                skipped += 1
        summaries.append({"ticker": t, "written": written, "skipped": skipped})

    if json_output:
        typer.echo(
            _json.dumps(
                {
                    "tickers": summaries,
                    "total_attempts": total_attempts,
                    "total_written": sum(s["written"] for s in summaries),
                }
            )
        )
    else:
        total_written = sum(s["written"] for s in summaries)
        typer.echo(f"densify: {total_written}/{total_attempts} snapshots written")
        for s in summaries:
            typer.echo(f"  {s['ticker']:>6}  written={s['written']}  skipped={s['skipped']}")
