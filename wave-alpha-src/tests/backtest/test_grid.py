from __future__ import annotations

import pytest

from wave_alpha.backtest.grid import GridAxis, GridConfig, GridResult, GridResultRow, run_grid


def test_grid_config_cartesian_product_shape() -> None:
    """GridConfig.cells() yields the cartesian product of axis levels.
    A 4x2x2 axes config produces 16 cells."""
    config = GridConfig(
        axes=[
            GridAxis(name="min_confidence", levels=[0.35, 0.45, 0.55, 0.65]),
            GridAxis(name="direction_modes", levels=[["long"], ["long", "short"]]),
            GridAxis(name="entry_trigger", levels=["immediate", "confirmation"]),
        ],
        fixed={"stop_source": "count_invalidation", "target_source": "fib_extension"},
    )
    cells = list(config.cells())
    assert len(cells) == 16


def test_grid_config_2x2x2x4_phase2_shape() -> None:
    """Phase 2 grid: 2x2x2x4 = 32 cells."""
    config = GridConfig(
        axes=[
            GridAxis(name="entry_trigger", levels=["immediate", "confirmation"]),
            GridAxis(name="stop_source", levels=["count_invalidation", "atr_multiple"]),
            GridAxis(name="target_source", levels=["fib_extension", "fixed_R"]),
            GridAxis(name="min_confidence", levels=[0.35, 0.45, 0.55, 0.65]),
        ],
        fixed={"direction_modes": ["long"], "target_R": 2.0},
    )
    cells = list(config.cells())
    assert len(cells) == 32


def test_grid_cell_ids_are_deterministic() -> None:
    """Two GridConfig instances with identical axes produce identical cells."""
    cfg1 = GridConfig(
        axes=[
            GridAxis(name="entry_trigger", levels=["immediate", "confirmation"]),
            GridAxis(name="min_confidence", levels=[0.45, 0.55]),
        ],
        fixed={},
    )
    cfg2 = GridConfig(
        axes=[
            GridAxis(name="entry_trigger", levels=["immediate", "confirmation"]),
            GridAxis(name="min_confidence", levels=[0.45, 0.55]),
        ],
        fixed={},
    )
    ids1 = [c.cell_id for c in cfg1.cells()]
    ids2 = [c.cell_id for c in cfg2.cells()]
    assert ids1 == ids2
    # Specific format check
    assert ids1 == [
        "entry-immediate_minconf-0.45",
        "entry-immediate_minconf-0.55",
        "entry-confirmation_minconf-0.45",
        "entry-confirmation_minconf-0.55",
    ]


def test_grid_cell_id_handles_direction_modes() -> None:
    """direction_modes=['long'] → 'long', ['long','short'] → 'both'."""
    cfg = GridConfig(
        axes=[
            GridAxis(
                name="direction_modes",
                levels=[["long"], ["long", "short"]],
            ),
        ],
        fixed={},
    )
    ids = [c.cell_id for c in cfg.cells()]
    assert ids == ["dir-long", "dir-both"]


def test_grid_cell_trade_rules_kwargs_merges_axis_and_fixed() -> None:
    """trade_rules_kwargs() returns axis_values overlaid on fixed values."""
    cfg = GridConfig(
        axes=[GridAxis(name="min_confidence", levels=[0.45])],
        fixed={"stop_source": "count_invalidation", "slippage_bps": 5},
    )
    cell = next(cfg.cells())
    assert cell.trade_rules_kwargs() == {
        "stop_source": "count_invalidation",
        "slippage_bps": 5,
        "min_confidence": 0.45,
    }


# ---------------------------------------------------------------------------
# T2 tests — GridResult aggregation
# ---------------------------------------------------------------------------

from decimal import Decimal  # noqa: E402

from wave_alpha.backtest.trade_layer import TradeLayerResult  # noqa: E402


def _result_with(closed: int, expectancy: float, pf: float = 1.0) -> TradeLayerResult:
    """Synthetic TradeLayerResult with controllable headline metrics.
    realized_R per closed trade = expectancy (so avg_R = expectancy)."""
    from datetime import date as _date

    from wave_alpha.backtest.trade_layer import TradeRecord

    trades = [
        TradeRecord(
            ticker="TEST",
            signal_as_of=_date(2024, 1, 1),
            fill_date=_date(2024, 1, 2),
            fill_price=Decimal("100"),
            exit_date=_date(2024, 1, 10),
            exit_price=Decimal("110"),
            exit_reason="target" if expectancy >= 0 else "stop",
            direction="long",
            pattern="impulse",
            wave_label="5",
            coherence_score=0.8,
            coherence_bucket="full",
            bars_held=8,
            risk_per_share=Decimal("1"),
            realized_pnl_per_share=Decimal(str(expectancy)),
            realized_R=expectancy,
            confidence=0.7,
            tf_count=2,
            degree=2,
            expected_bars_to_target=25,
        )
        for _ in range(closed)
    ]
    return TradeLayerResult(trades=trades)


def test_grid_result_ranked_by_expectancy_descending() -> None:
    cfg = GridConfig(
        axes=[GridAxis(name="min_confidence", levels=[0.35, 0.45, 0.55])],
        fixed={},
    )
    cells = list(cfg.cells())
    result = GridResult(
        rows=[
            GridResultRow(cell=cells[0], result=_result_with(closed=300, expectancy=-0.10)),
            GridResultRow(cell=cells[1], result=_result_with(closed=300, expectancy=+0.20)),
            GridResultRow(cell=cells[2], result=_result_with(closed=300, expectancy=+0.05)),
        ]
    )
    ranked = result.ranked_by_expectancy()
    assert [r.result.expectancy for r in ranked] == pytest.approx([0.20, 0.05, -0.10])


def test_grid_result_winner_respects_sample_size_floor() -> None:
    """Highest-expectancy cell must have closed_trades >= 200 to win."""
    cfg = GridConfig(
        axes=[GridAxis(name="min_confidence", levels=[0.35, 0.45])],
        fixed={},
    )
    cells = list(cfg.cells())
    result = GridResult(
        rows=[
            # Highest expectancy but tiny sample → ineligible
            GridResultRow(cell=cells[0], result=_result_with(closed=50, expectancy=+1.0)),
            # Lower expectancy but enough trades → wins
            GridResultRow(cell=cells[1], result=_result_with(closed=1000, expectancy=+0.10)),
        ]
    )
    winner = result.winner()
    assert winner is not None
    assert winner.cell.axis_values["min_confidence"] == 0.45
    assert winner.result.closed_trades == 1000


def test_grid_result_winner_returns_none_when_all_below_floor() -> None:
    cfg = GridConfig(
        axes=[GridAxis(name="min_confidence", levels=[0.35, 0.45])],
        fixed={},
    )
    cells = list(cfg.cells())
    result = GridResult(
        rows=[
            GridResultRow(cell=cells[0], result=_result_with(closed=10, expectancy=+0.5)),
            GridResultRow(cell=cells[1], result=_result_with(closed=20, expectancy=+0.3)),
        ]
    )
    assert result.winner() is None


def test_grid_result_axis_sensitivity_marginal_means() -> None:
    """For a 2x2 grid, each axis level has 2 cells; the marginal mean
    is the average expectancy of those 2 cells."""
    cfg = GridConfig(
        axes=[
            GridAxis(name="min_confidence", levels=[0.35, 0.55]),
            GridAxis(name="entry_trigger", levels=["immediate", "confirmation"]),
        ],
        fixed={},
    )
    cells = list(cfg.cells())
    # 4 cells; pick expectancies: (0.35, immediate)=+0.20, (0.35, confirmation)=-0.10,
    # (0.55, immediate)=+0.40, (0.55, confirmation)=+0.00
    result = GridResult(
        rows=[
            GridResultRow(cell=cells[0], result=_result_with(closed=300, expectancy=+0.20)),
            GridResultRow(cell=cells[1], result=_result_with(closed=300, expectancy=-0.10)),
            GridResultRow(cell=cells[2], result=_result_with(closed=300, expectancy=+0.40)),
            GridResultRow(cell=cells[3], result=_result_with(closed=300, expectancy=+0.00)),
        ]
    )
    sens = result.axis_sensitivity()
    # min_confidence=0.35 averages 0.20 + -0.10 = 0.05
    assert sens["min_confidence"]["0.35"]["expectancy"] == pytest.approx(0.05)
    # min_confidence=0.55 averages 0.40 + 0.00 = 0.20
    assert sens["min_confidence"]["0.55"]["expectancy"] == pytest.approx(0.20)
    # entry_trigger=immediate averages 0.20 + 0.40 = 0.30
    assert sens["entry_trigger"]["immediate"]["expectancy"] == pytest.approx(0.30)
    # entry_trigger=confirmation averages -0.10 + 0.00 = -0.05
    assert sens["entry_trigger"]["confirmation"]["expectancy"] == pytest.approx(-0.05)


def _result_with_pf(*, n_wins: int, n_losses: int, win_r: float, loss_r: float) -> TradeLayerResult:
    """Build a TradeLayerResult with controllable wins/losses → controllable PF."""
    from datetime import date as _date

    from wave_alpha.backtest.trade_layer import TradeRecord

    trades = []
    for realized_r in [win_r] * n_wins + [loss_r] * n_losses:
        trades.append(
            TradeRecord(
                ticker="TEST",
                signal_as_of=_date(2024, 1, 1),
                fill_date=_date(2024, 1, 2),
                fill_price=Decimal("100"),
                exit_date=_date(2024, 1, 10),
                exit_price=Decimal("110"),
                exit_reason="target" if realized_r > 0 else "stop",
                direction="long",
                pattern="impulse",
                wave_label="5",
                coherence_score=0.8,
                coherence_bucket="full",
                bars_held=8,
                risk_per_share=Decimal("1"),
                realized_pnl_per_share=Decimal(str(realized_r)),
                realized_R=realized_r,
                confidence=0.7,
                tf_count=2,
                degree=2,
                expected_bars_to_target=25,
            )
        )
    return TradeLayerResult(trades=trades)


def test_grid_axis_sensitivity_excludes_degenerate_pf_cells() -> None:
    """Mixed finite + inf cells: the finite cell's level shows its own PF
    (not dragged down by averaging in the inf), and the all-inf level
    surfaces as inf (uniformly degenerate-positive — opposite of "0.00")."""
    cfg = GridConfig(axes=[GridAxis(name="min_confidence", levels=[0.35, 0.45])], fixed={})
    cells = list(cfg.cells())

    # PF = total_wins / total_losses = (2 * 1.5) / (2 * 1.0) = 1.5
    finite_pf = _result_with_pf(n_wins=2, n_losses=2, win_r=1.5, loss_r=-1.0)
    assert finite_pf.profit_factor == pytest.approx(1.5)

    # No losses → PF is inf
    inf_pf = _result_with_pf(n_wins=10, n_losses=0, win_r=1.0, loss_r=-1.0)
    assert inf_pf.profit_factor == float("inf")

    result = GridResult(
        rows=[
            GridResultRow(cell=cells[0], result=finite_pf),
            GridResultRow(cell=cells[1], result=inf_pf),
        ]
    )
    sens = result.axis_sensitivity()
    pf_means = [m["profit_factor"] for m in sens["min_confidence"].values()]

    # The finite cell's level shows its own PF (1.5).
    assert any(abs(v - 1.5) < 1e-9 for v in pf_means), f"expected 1.5 in {pf_means}"
    # The all-inf level surfaces as inf — NOT 0.0 (which would falsely
    # imply "catastrophic losses" when the truth is "no losses at all").
    assert pf_means.count(float("inf")) == 1, f"expected one inf entry in {pf_means}"


def test_grid_axis_sensitivity_finite_only_level_averages_normally() -> None:
    """Two cells at the same level, both with finite PF, average normally."""
    cfg = GridConfig(axes=[GridAxis(name="min_confidence", levels=[0.35])], fixed={})
    cell = next(cfg.cells())
    pf_a = _result_with_pf(n_wins=2, n_losses=2, win_r=1.5, loss_r=-1.0)
    pf_b = _result_with_pf(n_wins=2, n_losses=2, win_r=2.0, loss_r=-1.0)
    assert pf_a.profit_factor == pytest.approx(1.5)
    assert pf_b.profit_factor == pytest.approx(2.0)
    result = GridResult(
        rows=[
            GridResultRow(cell=cell, result=pf_a),
            GridResultRow(cell=cell, result=pf_b),
        ]
    )
    sens = result.axis_sensitivity()
    # Both cells share the same level (only one level here), so the mean
    # is averaged across both: (1.5 + 2.0) / 2 = 1.75.
    assert sens["min_confidence"]["0.35"]["profit_factor"] == pytest.approx(1.75)


def test_grid_config_rejects_duplicate_axis_names() -> None:
    """GridConfig with duplicate axis names should raise ValueError."""
    import pytest

    with pytest.raises(ValueError, match="duplicate axis name"):
        GridConfig(
            axes=[
                GridAxis(name="min_confidence", levels=[0.35, 0.45]),
                GridAxis(name="min_confidence", levels=[0.55, 0.65]),
            ],
            fixed={},
        )


def test_grid_config_rejects_axis_name_in_fixed() -> None:
    """GridConfig with an axis name that also appears in fixed should raise ValueError."""
    import pytest

    with pytest.raises(ValueError, match=r"axis name.*also in fixed|fixed.*axis name"):
        GridConfig(
            axes=[GridAxis(name="min_confidence", levels=[0.35, 0.45])],
            fixed={"min_confidence": 0.50},
        )


# ---------------------------------------------------------------------------
# T3 tests — phase1_trigger GO/NO-GO
# ---------------------------------------------------------------------------

from wave_alpha.backtest.grid import phase1_trigger  # noqa: E402


def test_phase1_trigger_go_when_expectancy_threshold_met() -> None:
    """One cell with expectancy >= +0.05R AND closed_trades >= 200 → GO."""
    cfg = GridConfig(
        axes=[GridAxis(name="min_confidence", levels=[0.35, 0.45])],
        fixed={},
    )
    cells = list(cfg.cells())
    result = GridResult(
        rows=[
            GridResultRow(cell=cells[0], result=_result_with(closed=300, expectancy=-0.10)),
            GridResultRow(cell=cells[1], result=_result_with(closed=500, expectancy=+0.07)),
        ]
    )
    verdict, reason = phase1_trigger(result)
    assert verdict == "GO"
    assert "+0.07" in reason or "0.07" in reason


def test_phase1_trigger_no_go_when_all_negative() -> None:
    """All cells negative expectancy and PF < 1.05 → NO-GO."""
    cfg = GridConfig(
        axes=[GridAxis(name="min_confidence", levels=[0.35, 0.45])],
        fixed={},
    )
    cells = list(cfg.cells())
    # _result_with positive expectancy gives inf PF (all wins). To get a
    # finite PF below 1.05 with negative expectancy, use _result_with_pf.
    losing_cell = _result_with_pf(n_wins=10, n_losses=20, win_r=1.0, loss_r=-1.0)
    # PF = 10 / 20 = 0.5, expectancy = (10 - 20) / 30 = -0.333
    assert losing_cell.profit_factor == 0.5
    assert losing_cell.expectancy == pytest.approx(-1 / 3)
    barely_losing = _result_with_pf(n_wins=15, n_losses=15, win_r=1.0, loss_r=-1.0)
    # PF = 1.0, expectancy = 0.0 — fails both threshold and (1.0 < 1.05)
    assert barely_losing.profit_factor == 1.0
    assert barely_losing.expectancy == 0.0
    result = GridResult(
        rows=[
            GridResultRow(cell=cells[0], result=losing_cell),
            GridResultRow(cell=cells[1], result=barely_losing),
        ]
    )
    verdict, _ = phase1_trigger(result)
    assert verdict == "NO-GO"


def test_phase1_trigger_no_go_when_positive_but_undersample() -> None:
    """Cell has expectancy=+0.10 but only 100 closed_trades → NO-GO."""
    cfg = GridConfig(
        axes=[GridAxis(name="min_confidence", levels=[0.35])],
        fixed={},
    )
    cell = next(cfg.cells())
    result = GridResult(
        rows=[GridResultRow(cell=cell, result=_result_with(closed=100, expectancy=+0.10))]
    )
    verdict, _ = phase1_trigger(result)
    assert verdict == "NO-GO"


def test_phase1_trigger_go_via_pf_threshold() -> None:
    """Cell has expectancy below 0.05 threshold but PF >= 1.05 → GO."""
    cfg = GridConfig(
        axes=[GridAxis(name="min_confidence", levels=[0.35])],
        fixed={},
    )
    cell = next(cfg.cells())
    # Construct a result where avg_R < 0.05 but PF >= 1.05.
    # 200 wins of +1.0, 100 losses of -1.9: gain=200, loss=190, PF=1.0526,
    # avg_R = (200-190)/300 = 0.0333 (below 0.05 expectancy threshold).
    tlr = _result_with_pf(n_wins=200, n_losses=100, win_r=1.0, loss_r=-1.9)
    assert tlr.profit_factor >= 1.05
    assert tlr.expectancy < 0.05
    result = GridResult(rows=[GridResultRow(cell=cell, result=tlr)])
    verdict, _ = phase1_trigger(result)
    assert verdict == "GO"


# ---------------------------------------------------------------------------
# T4 tests — run_grid orchestration
# ---------------------------------------------------------------------------


def test_run_grid_calls_run_trade_layer_per_cell(monkeypatch) -> None:
    """run_grid invokes run_trade_layer once per cell with the cell's TradeRules."""
    from datetime import date as _date

    from wave_alpha.backtest import grid as grid_mod
    from wave_alpha.backtest.runner import BacktestConfig

    cfg = GridConfig(
        axes=[GridAxis(name="min_confidence", levels=[0.45, 0.55])],
        fixed={"slippage_bps": 5},
    )
    base_cfg = BacktestConfig(
        universe=["AAPL"],
        start=_date(2024, 1, 1),
        end=_date(2024, 6, 30),
        step=5,
    )

    captured_rules: list = []

    def fake_run_trade_layer(cfg, *, provider, rules):
        captured_rules.append(rules)
        return _result_with(closed=300, expectancy=+0.10)

    monkeypatch.setattr(grid_mod, "run_trade_layer", fake_run_trade_layer)

    class _NullProvider:
        def fetch(self, ticker, interval, start=None, end=None):
            return []

    result = run_grid(cfg, base_cfg=base_cfg, provider=_NullProvider())

    assert len(result.rows) == 2
    assert len(captured_rules) == 2
    assert captured_rules[0].min_confidence == 0.45
    assert captured_rules[0].slippage_bps == 5
    assert captured_rules[1].min_confidence == 0.55


# ---------------------------------------------------------------------------
# T5 tests — render_grid_markdown
# ---------------------------------------------------------------------------


def test_render_grid_markdown_contains_required_sections() -> None:
    """Renderer produces sections: ranked cells, axis sensitivity,
    winner/verdict block, header with cell count + axes."""
    from wave_alpha.backtest.grid_report import render_grid_markdown

    cfg = GridConfig(
        axes=[
            GridAxis(name="min_confidence", levels=[0.35, 0.45]),
            GridAxis(name="entry_trigger", levels=["immediate", "confirmation"]),
        ],
        fixed={"stop_source": "count_invalidation"},
    )
    cells = list(cfg.cells())
    result = GridResult(
        rows=[
            GridResultRow(cell=cells[0], result=_result_with(closed=300, expectancy=-0.10)),
            GridResultRow(cell=cells[1], result=_result_with(closed=300, expectancy=+0.10)),
            GridResultRow(cell=cells[2], result=_result_with(closed=300, expectancy=-0.05)),
            GridResultRow(cell=cells[3], result=_result_with(closed=300, expectancy=+0.20)),
        ]
    )
    md = render_grid_markdown(result, config=cfg, phase_name="Phase 1")
    assert "# Grid Sweep Report" in md
    assert "Phase 1" in md
    assert "## Per-cell summary" in md
    assert "## Axis sensitivity" in md
    assert "## Verdict" in md
    # Cell IDs appear in summary
    assert cells[3].cell_id in md
    # Axis names appear in sensitivity
    assert "min_confidence" in md
    assert "entry_trigger" in md
    # Phase 1 trigger block appears (gated on phase_name prefix)
    assert "**Phase 1 trigger:**" in md
    # Winner expectancy renders with explicit sign — guards against
    # silent regressions to plain {:.3f} that drop the leading + on
    # positive numbers.
    assert "+0.200" in md  # cells[3] expectancy = +0.20, formatted as +.3f


def test_render_grid_markdown_phase2_omits_trigger_line() -> None:
    """Phase 2 reports get the same Verdict section but no Phase 1 trigger
    line — gated on phase_name.lower().startswith('phase 1')."""
    from wave_alpha.backtest.grid_report import render_grid_markdown

    cfg = GridConfig(
        axes=[GridAxis(name="min_confidence", levels=[0.35, 0.45])],
        fixed={},
    )
    cells = list(cfg.cells())
    result = GridResult(
        rows=[
            GridResultRow(cell=cells[0], result=_result_with(closed=300, expectancy=+0.10)),
            GridResultRow(cell=cells[1], result=_result_with(closed=300, expectancy=+0.20)),
        ]
    )
    md = render_grid_markdown(result, config=cfg, phase_name="Phase 2")
    assert "## Verdict" in md
    assert "Phase 1 trigger" not in md  # phase 2 must NOT emit the GO/NO-GO line
