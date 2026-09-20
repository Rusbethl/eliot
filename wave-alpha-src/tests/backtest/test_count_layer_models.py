from datetime import date

from wave_alpha.backtest.count_layer import CountEvaluation, CountLayerResult


def _ev(
    tk: str,
    pred: str,
    actual: str,
    coh: float,
    *,
    top1_hit: bool,
    as_of: date = date(2024, 1, 5),
) -> CountEvaluation:
    return CountEvaluation(
        ticker=tk,
        as_of=as_of,
        predicted_direction=pred,  # type: ignore[arg-type]
        actual_direction=actual,  # type: ignore[arg-type]
        coherence_score=coh,
        top1_hit=top1_hit,
        top3_directions=(pred,),  # type: ignore[arg-type]
        top3_hit=top1_hit,
    )


def test_count_evaluation_construction() -> None:
    ev = CountEvaluation(
        ticker="AAPL",
        as_of=date(2024, 1, 5),
        predicted_direction="up",
        actual_direction="up",
        coherence_score=0.8,
        top1_hit=True,
        top3_directions=("up", "down", "up"),
        top3_hit=True,
    )
    assert ev.top1_hit is True
    assert ev.top3_hit is True


def test_count_layer_result_aggregates() -> None:
    evs = [
        CountEvaluation(
            ticker="AAPL",
            as_of=date(2024, 1, 5),
            predicted_direction="up",
            actual_direction="up",
            coherence_score=0.8,
            top1_hit=True,
            top3_directions=("up",),
            top3_hit=True,
        ),
        CountEvaluation(
            ticker="MSFT",
            as_of=date(2024, 1, 5),
            predicted_direction="down",
            actual_direction="up",
            coherence_score=0.4,
            top1_hit=False,
            top3_directions=("down", "up"),
            top3_hit=True,
        ),
    ]
    result = CountLayerResult(evaluations=evs, n_bars_forward=20)
    assert result.top1_hit_rate == 0.5
    assert result.top3_hit_rate == 1.0
    assert result.sample_size == 2


def test_count_layer_result_buckets_by_coherence() -> None:
    evs = [
        # Three full-agreement samples, 2 hit
        _ev("A", "up", "up", 0.8, top1_hit=True),
        _ev("B", "up", "up", 0.85, top1_hit=True),
        _ev("C", "up", "down", 0.75, top1_hit=False),
        # Two partial samples, 1 hit
        _ev("D", "up", "up", 0.5, top1_hit=True),
        _ev("E", "down", "up", 0.6, top1_hit=False),
        # One disagreement sample, 0 hits
        _ev("F", "up", "down", 0.2, top1_hit=False),
    ]
    result = CountLayerResult(evaluations=evs, n_bars_forward=20)
    buckets = result.by_coherence_bucket()
    assert buckets["full"]["sample_size"] == 3
    assert buckets["full"]["top1_hit_rate"] == 2 / 3
    assert buckets["partial"]["sample_size"] == 2
    assert buckets["partial"]["top1_hit_rate"] == 0.5
    assert buckets["disagreement"]["sample_size"] == 1
    assert buckets["disagreement"]["top1_hit_rate"] == 0.0


def test_count_layer_result_buckets_by_year() -> None:
    evs = [
        _ev("A", "up", "up", 0.5, top1_hit=True, as_of=date(2023, 1, 1)),
        _ev("B", "up", "down", 0.5, top1_hit=False, as_of=date(2023, 6, 1)),
        _ev("C", "up", "up", 0.5, top1_hit=True, as_of=date(2024, 1, 1)),
    ]
    result = CountLayerResult(evaluations=evs)
    by_year = result.by_year()
    assert by_year[2023]["sample_size"] == 2
    assert by_year[2023]["top1_hit_rate"] == 0.5
    assert by_year[2024]["sample_size"] == 1
    assert by_year[2024]["top1_hit_rate"] == 1.0
