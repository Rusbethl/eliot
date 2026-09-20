from __future__ import annotations

import json
from pathlib import Path

from wave_alpha.scan.models import ScanReport

DEFAULT_SCAN_DIR = Path.home() / ".wave_alpha" / "scans"


def save_report(report: ScanReport, *, dir: Path = DEFAULT_SCAN_DIR) -> Path:
    dir.mkdir(parents=True, exist_ok=True)
    path = dir / f"{report.scan_id}.json"
    path.write_text(json.dumps(report.model_dump(mode="json")))
    return path


def load_report(scan_id: str, *, dir: Path = DEFAULT_SCAN_DIR) -> ScanReport | None:
    path = dir / f"{scan_id}.json"
    if not path.exists():
        return None
    return ScanReport.model_validate(json.loads(path.read_text()))


def list_reports(*, dir: Path = DEFAULT_SCAN_DIR) -> list[str]:
    """Return scan_ids newest-first (lex sort works because IDs are ISO timestamps)."""
    if not dir.exists():
        return []
    return sorted(
        (p.stem for p in dir.glob("*.json")),
        reverse=True,
    )
