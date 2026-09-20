from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wave_alpha.cli import app as cli_app
from wave_alpha.coherence.score import CoherenceResult
from wave_alpha.domain import Interval
from wave_alpha.elliott.models import WaveCount
from wave_alpha.history import store
from wave_alpha.pipeline import AnalyzeResult, RankedCount
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
from wave_alpha.trades.models import TradeSignal


def _fake_result() -> AnalyzeResult:
    pivots = [
        PivotEvent(
            degree=1,
            timestamp=date(2024, 1, 1),
            price=Decimal("100"),
            interval=Interval.DAILY,
            type=PivotType.LOW,
            state=PivotState.CONFIRMED,
        ),
        PivotEvent(
            degree=1,
            timestamp=date(2024, 6, 1),
            price=Decimal("130"),
            interval=Interval.DAILY,
            type=PivotType.HIGH,
            state=PivotState.CONFIRMED,
        ),
    ]
    return AnalyzeResult(
        ticker="AAPL",
        as_of=date(2024, 6, 1),
        ranked_daily=[
            RankedCount(
                candidate_id="C1",
                count=WaveCount(pattern="impulse", degree=1, pivots=pivots, score=0.7),
                engine_score=0.7,
                llm_prob=None,
                final_score=0.7,
            )
        ],
        signals=[
            TradeSignal(
                ticker="AAPL",
                as_of=date(2024, 6, 1),
                direction="long",
                entry_price=Decimal("130"),
                stop_price=Decimal("125"),
                target_price=Decimal("145"),
                count_id="C1",
                count_pattern="impulse",
                wave_label="3",
                degree=1,
                confidence=0.7,
                expected_bars_to_target=20,
                max_holding_bars=60,
            )
        ],
        coherence=CoherenceResult(score=0.8, multiplier=1.05, pairwise={}, fallback_used=None),
    )


def test_cli_analyze_writes_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_app, "_DEFAULT_DATA_DIR", tmp_path)
    monkeypatch.setattr(cli_app, "run_analysis", lambda *a, **k: _fake_result())
    monkeypatch.setattr(cli_app, "_default_provider", lambda: object())

    runner = CliRunner()
    result = runner.invoke(
        cli_app.app,
        ["analyze", "AAPL", "--as-of", "2024-06-01", "--json"],
    )
    assert result.exit_code == 0, result.output

    db = tmp_path / "history.sqlite"
    assert db.exists()
    rows = store.recent("AAPL", 10, db_path=db)
    assert len(rows) == 1
    assert rows[0].as_of == date(2024, 6, 1)
