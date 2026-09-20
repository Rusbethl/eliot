from datetime import date, datetime

from wave_alpha.history.service import _compute_badge
from wave_alpha.history.store import StoredRow


def _row(as_of_iso: str, top_hash: str) -> StoredRow:
    return StoredRow(
        ticker="AAPL",
        as_of=date.fromisoformat(as_of_iso),
        engine_version="0.5.11",
        top_count_hash=top_hash,
        payload_json="{}",
        created_at=datetime.combine(date.fromisoformat(as_of_iso), datetime.min.time()),
    )


def test_single_row_returns_none() -> None:
    rows = [_row("2024-12-31", "h-A")]
    assert _compute_badge(rows) is None


def test_empty_rows_returns_none() -> None:
    assert _compute_badge([]) is None


def test_all_same_hash() -> None:
    rows = [_row(f"2024-12-{i:02d}", "h-A") for i in range(5, 0, -1)]
    badge = _compute_badge(rows)
    assert badge is not None
    assert badge.streak == 5
    assert badge.matches_in_window == 5
    assert badge.window == 5
    assert badge.last_changed_on is None


def test_streak_breaks_at_first_difference() -> None:
    rows = [
        _row("2024-12-05", "h-A"),
        _row("2024-12-04", "h-A"),
        _row("2024-12-03", "h-A"),
        _row("2024-12-02", "h-B"),  # break
        _row("2024-12-01", "h-A"),
    ]
    badge = _compute_badge(rows)
    assert badge is not None
    assert badge.streak == 3
    assert badge.matches_in_window == 4  # h-A appears 4 times overall
    assert badge.last_changed_on == date(2024, 12, 2)


def test_flip_flop_sequence() -> None:
    rows = [
        _row("2024-12-05", "h-A"),
        _row("2024-12-04", "h-B"),
        _row("2024-12-03", "h-A"),
        _row("2024-12-02", "h-B"),
        _row("2024-12-01", "h-A"),
    ]
    badge = _compute_badge(rows)
    assert badge is not None
    assert badge.streak == 1
    assert badge.matches_in_window == 3
    assert badge.last_changed_on == date(2024, 12, 4)


def test_window_reflects_actual_rows_not_requested() -> None:
    rows = [_row("2024-12-05", "h-A"), _row("2024-12-04", "h-A")]
    badge = _compute_badge(rows)
    assert badge is not None
    assert badge.window == 2  # only 2 rows actually present
