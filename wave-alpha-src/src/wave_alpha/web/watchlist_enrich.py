"""Join watchlist entries against a scan report so the watchlist page
can show last-known signal/bias/coherence/RR per symbol without doing
the join in a template."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from wave_alpha.scan.models import ScanReport, ScanRow
from wave_alpha.watchlist.models import WatchlistEntry

Bias = Literal["long", "short"]


@dataclass(frozen=True)
class EnrichedRow:
    """One watchlist entry plus its matched ScanRow (or None if not in scan)."""

    entry: WatchlistEntry
    row: ScanRow | None

    @property
    def has_signal(self) -> bool:
        return self.row is not None and self.row.has_signal

    @property
    def bias(self) -> Bias | None:
        if self.row is None or self.row.direction is None:
            return None
        return "long" if self.row.direction == "up" else "short"


def enrich(entries: list[WatchlistEntry], report: ScanReport | None) -> list[EnrichedRow]:
    """Pair each entry with its row from the scan report (case-insensitive
    symbol match) or with None if the symbol is not in the report. If
    `report` is None, every row gets None — page still renders."""
    by_ticker: dict[str, ScanRow] = {}
    if report is not None:
        for row in report.rows:
            by_ticker[row.ticker.upper()] = row
    return [EnrichedRow(entry=e, row=by_ticker.get(e.symbol.upper())) for e in entries]
