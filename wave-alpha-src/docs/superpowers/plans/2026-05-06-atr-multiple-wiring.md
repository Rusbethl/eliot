# `atr_multiple` Stop Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the existing `_stop_price_atr_multiple` helper into `derive_signal` so that `TradeRules.stop_source = "atr_multiple"` produces an ATR-based stop instead of silently falling through to `last_pivot`.

**Architecture:** Add `bars: list[Bar] | None = None` kwarg to `derive_signal`, dispatch on `rules.stop_source` inside `_stop_price` to call `_stop_price_atr_multiple` when `atr_multiple` is requested. Raise `ValueError` if bars are missing or insufficient — `pipeline.run_analysis` catches at the per-candidate boundary so one bad candidate does not lose its ranked siblings. Multiple = 2 and period = 14 are hard-coded; configurable knobs are deferred to the ablation grid spec.

**Tech Stack:** Python 3.11, `pydantic` v2 (`TradeRules` schema unchanged), `pytest`, `Decimal` throughout. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-06-atr-multiple-wiring-design.md`

**Branch:** `feat/atr-multiple-wiring` (already created at the spec commit `83166a8`).

---

## File structure

**Modified:**
- `src/wave_alpha/trades/derive.py` — extend `_stop_price` signature with `bars`/`anchor` kwargs and dispatch the `atr_multiple` branch; extend `derive_signal` signature with `bars` kwarg and pass it through.
- `src/wave_alpha/pipeline.py` — pass `bars=daily` to `derive_signal`; wrap the call in `try/except ValueError` at the per-candidate granularity.
- `tests/trades/test_derive.py` — append five tests pinning the wiring invariants (long stop, short stop, raise-no-bars, raise-insufficient-bars, count_invalidation back-compat).
- `tests/test_pipeline.py` — append one integration test pinning the per-candidate skip boundary.

**Not modified:**
- `src/wave_alpha/trades/models.py` — no `TradeRules` schema change (deferred to grid spec, per spec §8 W1).
- `src/wave_alpha/trades/derive.py:_stop_price_atr_multiple` — already direction-correct and unit-tested in `tests/trades/test_derive_atr_stop.py`.
- Any test that calls `derive_signal` with `stop_source` other than `atr_multiple` — `bars=None` default keeps them green.

---

## Task 1: Wire `atr_multiple` dispatch into `derive_signal`

Single TDD-flavored task that lands the four `derive_signal`-level invariants from spec §4.1–§4.5 in one commit. The dispatch logic in `_stop_price` is the smallest piece of code that satisfies all four; once written, the additional tests pin it.

**Files:**
- Modify: `src/wave_alpha/trades/derive.py`
- Modify: `tests/trades/test_derive.py`

- [ ] **Step 1: Add a synthetic-bars helper to `tests/trades/test_derive.py`**

`tests/trades/test_derive_atr_stop.py` already defines `_series(prices: list[float])`, but it lives in a different test file. Don't import across test files — add a small inline helper at the top of the new test block.

Append to `tests/trades/test_derive.py` (after the existing top-level imports and helpers, before the new tests in Steps 2–6):

```python
def _atr_bars(price: float = 100.0, n: int = 30) -> list[Bar]:
    """Synthetic bar series with high-low=2 → ATR ≈ 2 after the 14-bar warmup.

    Mirrors the construction in tests/trades/test_derive_atr_stop.py:_series.
    Inline rather than imported so the two test files stay decoupled.
    """
    out: list[Bar] = []
    for i in range(n):
        d = date(2024, 1, 1) + timedelta(days=i)
        out.append(
            Bar(
                timestamp=d,
                open=Decimal(str(price)),
                high=Decimal(str(price + 1)),
                low=Decimal(str(price - 1)),
                close=Decimal(str(price)),
                volume=1000,
            )
        )
    return out
```

You will also need imports for `timedelta` and `Bar`. Inspect the existing `tests/trades/test_derive.py` imports: if `from datetime import date, timedelta` is not already there, add `timedelta` to that import line. If `from wave_alpha.domain import Bar` is missing, add it next to the existing `wave_alpha` imports.

- [ ] **Step 2: Write the failing test for the long path (spec §4.1)**

Append to `tests/trades/test_derive.py`:

```python
def test_atr_multiple_long_stop_below_entry() -> None:
    """For a 4-seg W4-complete (long) impulse, atr_multiple stop must equal
    entry - 2*ATR — i.e. on the loss side, with the ATR magnitude.
    Synthetic bars give ATR=2 → stop = 120 - 2*2 = 116."""
    count = _bullish_w5_setup()  # 4-segment, implied direction = long
    bars = _atr_bars(price=120.0)
    sig = derive_signal(
        count=count,
        ticker="TEST",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(stop_source="atr_multiple"),
        bars=bars,
    )
    assert sig is not None
    assert sig.direction == "long"
    assert sig.stop_price == Decimal("116")
    assert sig.stop_price < sig.entry_price
```

- [ ] **Step 3: Run to confirm failure**

```bash
uv run pytest tests/trades/test_derive.py::test_atr_multiple_long_stop_below_entry -xvs
```

Expected: `TypeError: derive_signal() got an unexpected keyword argument 'bars'`.

- [ ] **Step 4: Extend `_stop_price` signature and dispatch**

Edit `src/wave_alpha/trades/derive.py`. Replace the entire current `_stop_price` function (now at the location matching `def _stop_price(count: WaveCount, direction: str, rules: TradeRules) -> Decimal:`) with:

```python
def _stop_price(
    count: WaveCount,
    direction: str,
    rules: TradeRules,
    *,
    bars: list[Bar] | None = None,
    anchor: Decimal | None = None,
) -> Decimal:
    """Count-invalidation stop. The invalidation level is the price extremum
    that, if breached, falsifies the count's premise.

    For 5-segment impulses (W5 complete, expecting reversal), the invalidation
    is the W5 extreme — i.e. the last pivot. The earlier implementation
    returned `segs[3].end.price` for any 4-or-more segment impulse, which
    placed a W5-complete short's stop at the W4 end (on the trade-direction
    side, below entry) — the wrong side, leading to immediate stop-outs.

    For 4-segment impulses (W4 complete, expecting W5 continuation), the
    invalidation is the W4 end — already correct for either implied direction.

    For `atr_multiple`, dispatches to `_stop_price_atr_multiple` (multiple=2,
    period=14 hard-coded — see spec 2026-05-06-atr-multiple-wiring-design.md
    §8 W1). Raises ValueError if bars or anchor are missing.
    """
    if rules.stop_source == "atr_multiple":
        if bars is None:
            raise ValueError("atr_multiple stop_source requires bars")
        if anchor is None:
            raise ValueError("atr_multiple stop_source requires anchor")
        return _stop_price_atr_multiple(
            bars=bars,
            anchor=anchor,
            direction=direction,  # type: ignore[arg-type]
            multiple=Decimal("2"),
        )
    segs = count.segments()
    last_pivot = count.pivots[-1].price
    if rules.stop_source == "count_invalidation":
        if count.pattern in {"impulse", "leading_diagonal", "ending_diagonal"}:
            if len(segs) == 4:
                return segs[3].end.price
            if len(segs) >= 5:
                return last_pivot
        return last_pivot
    return last_pivot
```

The body keeps the existing `count_invalidation` logic verbatim. Only the signature and the new `atr_multiple` block are new.

- [ ] **Step 5: Extend `derive_signal` signature and pass `bars`/`anchor` through**

In `src/wave_alpha/trades/derive.py`, find `def derive_signal(...)` and:

1. Add `bars: list[Bar] | None = None,` as the last kwarg (after `rules: TradeRules,`).
2. Update the `_stop_price(count, direction, rules)` call to `_stop_price(count, direction, rules, bars=bars, anchor=current_price)`.

The full updated `derive_signal` becomes:

```python
def derive_signal(
    *,
    count: WaveCount,
    ticker: str,
    as_of: date,
    current_price: Decimal,
    rules: TradeRules,
    bars: list[Bar] | None = None,
) -> TradeSignal | None:
    """Convert a WaveCount into a TradeSignal per the rules. Returns None if
    confidence is below the minimum or the implied direction is excluded.

    `bars` is required only when `rules.stop_source == "atr_multiple"`;
    other stop sources use the count's pivots and ignore `bars`.
    """
    if count.score < rules.min_confidence:
        return None

    direction = _implied_direction(count)
    if direction not in rules.direction_modes:
        return None

    stop = _stop_price(count, direction, rules, bars=bars, anchor=current_price)
    if stop == current_price:
        return None  # zero-risk degenerate

    target = _target_price(count, direction, rules, anchor=current_price)
    expected = _DEFAULT_BARS_PER_DEGREE.get(count.degree, 25)
    max_hold = int(expected * 1.5)

    risk = abs(current_price - stop)
    breakeven = current_price + (risk if direction == "long" else -risk)

    return TradeSignal(
        ticker=ticker,
        as_of=as_of,
        direction=direction,
        entry_price=current_price,
        stop_price=stop,
        target_price=target,
        count_id=_count_id(count),
        count_pattern=count.pattern,
        wave_label=_current_wave_label(count),
        degree=count.degree,
        confidence=count.score,
        expected_bars_to_target=expected,
        max_holding_bars=max_hold,
        breakeven_trigger=breakeven,
    )
```

The two changes are: the new `bars: list[Bar] | None = None,` line, and the `bars=bars, anchor=current_price` kwargs on the `_stop_price(...)` call. Every other line is verbatim from the existing function — preserved here for clarity since the engineer may not have the surrounding context.

- [ ] **Step 6: Run the failing test to verify it passes**

```bash
uv run pytest tests/trades/test_derive.py::test_atr_multiple_long_stop_below_entry -xvs
```

Expected: PASS.

- [ ] **Step 7: Add the remaining four invariant tests (spec §4.2–§4.5)**

Append to `tests/trades/test_derive.py`:

```python
def test_atr_multiple_short_stop_above_entry() -> None:
    """For a 5-seg W5-complete (short) impulse, atr_multiple stop must equal
    entry + 2*ATR — i.e. on the adverse side, with the ATR magnitude.
    Synthetic bars give ATR=2 → stop = 139 + 2*2 = 143."""
    count = WaveCount(
        pattern="impulse",
        degree=2,
        score=0.7,
        pivots=[
            _piv(date(2024, 1, 1), "100", PivotType.LOW),
            _piv(date(2024, 1, 5), "110", PivotType.HIGH),
            _piv(date(2024, 1, 8), "104", PivotType.LOW),
            _piv(date(2024, 1, 15), "130", PivotType.HIGH),
            _piv(date(2024, 1, 18), "118", PivotType.LOW),
            _piv(date(2024, 1, 25), "140", PivotType.HIGH),
        ],
    )
    bars = _atr_bars(price=139.0)
    sig = derive_signal(
        count=count,
        ticker="TEST",
        as_of=date(2024, 1, 26),
        current_price=Decimal("139"),
        rules=TradeRules(stop_source="atr_multiple"),
        bars=bars,
    )
    assert sig is not None
    assert sig.direction == "short"
    assert sig.stop_price == Decimal("143")
    assert sig.stop_price > sig.entry_price


def test_atr_multiple_raises_without_bars() -> None:
    """atr_multiple needs bars; absence is a hard error, not a silent fallback."""
    count = _bullish_w5_setup()
    with pytest.raises(ValueError, match="atr_multiple"):
        derive_signal(
            count=count,
            ticker="TEST",
            as_of=date(2024, 1, 19),
            current_price=Decimal("120"),
            rules=TradeRules(stop_source="atr_multiple"),
            # bars omitted — defaults to None
        )


def test_atr_multiple_raises_with_insufficient_bars() -> None:
    """atr_multiple needs >= period bars (default 14); fewer is a hard error.
    The error originates in _stop_price_atr_multiple and propagates."""
    count = _bullish_w5_setup()
    with pytest.raises(ValueError, match="ATR unavailable"):
        derive_signal(
            count=count,
            ticker="TEST",
            as_of=date(2024, 1, 19),
            current_price=Decimal("120"),
            rules=TradeRules(stop_source="atr_multiple"),
            bars=_atr_bars(price=120.0, n=10),  # < 14
        )


def test_count_invalidation_works_without_bars() -> None:
    """Back-compat: default stop_source must keep working when bars are not
    provided. Existing callers (test_derive_long_signal_from_w5_setup, etc.)
    rely on the bars=None default."""
    count = _bullish_w5_setup()
    sig = derive_signal(
        count=count,
        ticker="TEST",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(),  # default stop_source="count_invalidation"
        # bars omitted
    )
    assert sig is not None
```

`pytest` must be imported at the top of the file. Inspect the imports — if `import pytest` is not present, add it.

- [ ] **Step 8: Run all five wiring tests**

```bash
uv run pytest tests/trades/test_derive.py -k "atr_multiple or count_invalidation_works_without_bars" -v
```

Expected: 5 passed.

- [ ] **Step 9: Run the full suite to catch regressions**

```bash
uv run pytest -x
```

Expected: all green. Existing tests are unaffected because `bars=None` is the default.

- [ ] **Step 10: Lint**

```bash
uv run ruff check src/wave_alpha/trades/derive.py tests/trades/test_derive.py
```

Expected: `All checks passed!`

- [ ] **Step 11: Commit**

```bash
git add src/wave_alpha/trades/derive.py tests/trades/test_derive.py
git commit -m "$(cat <<'EOF'
feat(derive): wire atr_multiple stop_source into derive_signal

Previously TradeRules.stop_source="atr_multiple" silently fell through
to last_pivot — the schema accepted it but _stop_price_atr_multiple was
never called. The 2026-05-05 sanity run discovered this as S5.

Wire the dispatch in _stop_price (multiple=2, period=14 hard-coded;
configurable knobs deferred to the grid spec per design §8 W1).
derive_signal accepts an opt-in bars kwarg; when stop_source is
atr_multiple, missing or insufficient bars raise ValueError. Other
stop_source values ignore bars (back-compat).

Five tests pin the invariants: long+short stops on correct sides,
raise-without-bars, raise-with-insufficient-bars, count_invalidation
back-compat with bars=None. Spec
docs/superpowers/specs/2026-05-06-atr-multiple-wiring-design.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Pipeline integration (per-candidate ValueError handling)

Wire `bars=daily` into the `pipeline.run_analysis` call to `derive_signal`, and wrap that call in a narrow `try/except ValueError` so one bad candidate does not lose its siblings in `ranked[:3]`.

**Files:**
- Modify: `src/wave_alpha/pipeline.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing pipeline integration test (spec §4.6)**

Append to `tests/test_pipeline.py`:

```python
def test_pipeline_skips_bad_atr_candidate_keeps_others(monkeypatch) -> None:
    """One ranked candidate raising ValueError from derive_signal must not
    drop the others in `ranked[:3]`. Pins the per-candidate try/except
    boundary in pipeline.run_analysis."""
    from decimal import Decimal as _D

    from wave_alpha import pipeline as pipeline_mod
    from wave_alpha.trades.models import TradeRules, TradeSignal

    provider = _SyntheticImpulseProvider()

    call_count = {"n": 0}
    real_derive = pipeline_mod.derive_signal

    def flaky_derive(**kwargs):
        call_count["n"] += 1
        # First candidate raises; remaining candidates use the real path.
        if call_count["n"] == 1:
            raise ValueError("ATR unavailable: synthetic test failure")
        return real_derive(**kwargs)

    monkeypatch.setattr(pipeline_mod, "derive_signal", flaky_derive)

    result = run_analysis(
        AnalyzeRequest(
            ticker="AAPL",
            as_of=date(2024, 12, 31),
            rules=TradeRules(stop_source="atr_multiple"),
        ),
        provider=provider,
    )

    # The first candidate's ValueError was swallowed; subsequent candidates
    # ran. We don't assert an exact signal count (depends on count
    # acceptance), only that we did NOT crash and call_count > 1
    # (i.e. iteration continued past the raising candidate).
    assert call_count["n"] >= 2
    # All produced signals (if any) must be valid TradeSignal instances.
    assert all(isinstance(s, TradeSignal) for s in result.signals)
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_pipeline.py::test_pipeline_skips_bad_atr_candidate_keeps_others -xvs
```

Expected: the test raises `ValueError("ATR unavailable: synthetic test failure")` because `pipeline.run_analysis` does not catch it today. `call_count["n"]` will be 1 at the point of failure.

- [ ] **Step 3: Update `pipeline.run_analysis` to pass `bars` and catch per-candidate**

Edit `src/wave_alpha/pipeline.py`. Find the existing signal-generation block (it currently looks like):

```python
    signals: list[TradeSignal] = []
    if counts_daily and daily:
        latest_close = PointInTimeView(daily, req.as_of).latest().close
        for rc in ranked[:3]:
            adjusted = rc.count.model_copy(update={"score": rc.final_score})
            sig = derive_signal(
                count=adjusted,
                ticker=req.ticker,
                as_of=req.as_of,
                current_price=latest_close,
                rules=req.rules,
            )
            if sig is not None:
                signals.append(sig)
```

Replace with:

```python
    signals: list[TradeSignal] = []
    if counts_daily and daily:
        latest_close = PointInTimeView(daily, req.as_of).latest().close
        for rc in ranked[:3]:
            adjusted = rc.count.model_copy(update={"score": rc.final_score})
            try:
                sig = derive_signal(
                    count=adjusted,
                    ticker=req.ticker,
                    as_of=req.as_of,
                    current_price=latest_close,
                    rules=req.rules,
                    bars=daily,
                )
            except ValueError:
                # atr_multiple stop_source needed bars but they're
                # missing or insufficient for ATR(14). Skip this
                # candidate without dropping the others in ranked[:3].
                continue
            if sig is not None:
                signals.append(sig)
```

The two changes: `bars=daily,` kwarg added to the `derive_signal(...)` call, and the call wrapped in `try/except ValueError` with a `continue`. Note that `daily` is already post-`PointInTimeView` (see `_bars` at lines 195–199 of the same file) — no second slicing required.

- [ ] **Step 4: Run the failing test to verify it passes**

```bash
uv run pytest tests/test_pipeline.py::test_pipeline_skips_bad_atr_candidate_keeps_others -xvs
```

Expected: PASS, with `call_count["n"] >= 2` (the first call raised, subsequent calls happened).

- [ ] **Step 5: Run the full pipeline suite**

```bash
uv run pytest tests/test_pipeline.py -v
```

Expected: all green. The new `bars=daily` kwarg is harmless for non-`atr_multiple` calls (it's ignored downstream).

- [ ] **Step 6: Run the full suite**

```bash
uv run pytest -x
```

Expected: all green.

- [ ] **Step 7: Lint**

```bash
uv run ruff check src/wave_alpha/pipeline.py tests/test_pipeline.py
```

Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add src/wave_alpha/pipeline.py tests/test_pipeline.py
git commit -m "$(cat <<'EOF'
feat(pipeline): pass bars to derive_signal, catch per-candidate ValueError

Wires the atr_multiple stop_source through pipeline.run_analysis: pass
the already-point-in-time-sliced `daily` bars to derive_signal, and
wrap the per-candidate call in try/except ValueError so one
ATR-unavailable candidate does not drop the others in ranked[:3].

The catch is narrowly scoped: derive_signal returns None for non-fatal
cases (zero-risk, direction excluded) and raises only for the
atr_multiple-bars contract. So the exception type is unambiguous.

Test test_pipeline_skips_bad_atr_candidate_keeps_others pins the
boundary via a monkeypatched flaky derive_signal that raises on the
first call.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Final review + version bump

The wiring changes the observable behavior of `stop_source="atr_multiple"` for any prior user. Per spec §9, that warrants a minor version bump.

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/wave_alpha/__init__.py`
- Modify: `uv.lock` (auto-regenerated)

- [ ] **Step 1: Final test pass**

```bash
uv run pytest
```

Expected: all green (470+ tests, exact count depends on how many were added in Tasks 1–2).

- [ ] **Step 2: Lint**

```bash
uv run ruff check .
```

Expected: `All checks passed!`

- [ ] **Step 3: Bump version in `pyproject.toml`**

Edit `pyproject.toml`. Find the line `version = "0.5.4"` (set during the prior sanity-run merge) and change to:

```toml
version = "0.5.5"
```

- [ ] **Step 4: Bump runtime `__version__` in `src/wave_alpha/__init__.py`**

Edit `src/wave_alpha/__init__.py`. Change `__version__ = "0.5.4"` to `__version__ = "0.5.5"`.

The pyproject and runtime constants must move together — the CLI's `wave-alpha version` reads from `__init__.py`, the build reads from `pyproject.toml`.

- [ ] **Step 5: Refresh the lock**

```bash
uv lock
```

Expected: `Updated wave-alpha v0.5.4 -> v0.5.5`.

- [ ] **Step 6: Verify the bump**

```bash
uv run wave-alpha version
```

Expected: prints `0.5.5`.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/wave_alpha/__init__.py
git commit -m "$(cat <<'EOF'
chore(release): bump to v0.5.5

atr_multiple stop_source is now wired (was a silent fallback to
last_pivot before). Any prior user of stop_source="atr_multiple"
gets new behavior — this is the documented goal, not a regression.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 8: Print branch summary**

```bash
git log --oneline main..HEAD
```

Expected: 4 commits — spec, Task 1 (wire), Task 2 (pipeline), Task 3 (bump).

---

## Final Review Boundary

```bash
uv run pytest
uv run ruff check .
git log --oneline main..HEAD
```

Expected: full suite green, lint clean, ~4 commits in the new branch. The branch is ready to merge to `main` (merge-commit per project convention, e.g. `Merge feat/atr-multiple-wiring — atr_multiple stop wired + v0.5.5`). Once merged, the ablation grid spec is unblocked: it can sweep both `count_invalidation` and `atr_multiple` cleanly, with no plumbing concerns.

---

## Self-Review Checklist (controller)

- [ ] **Spec coverage:** every spec section maps to a task — §3.1 API changes → Task 1 Steps 4–5; §3.2 pipeline integration → Task 2 Steps 3–4; §3.3 lookahead safety → relies on existing `_bars` boundary, no new task; §4.1–§4.5 tests → Task 1 Steps 2,7; §4.6 pipeline test → Task 2 Step 1; §5 file structure → Task 1+2 file lists; §6 invariants → covered by tests; §7 risks → no implementation tasks (documented); §8 deferred → no implementation tasks; §9 roadmap → Task 3 (version bump).
- [ ] **Placeholder scan:** every step has actual code or commands. The `_atr_bars` helper is shown in full; the new tests are shown in full; the `_stop_price` and `derive_signal` implementations are shown in full. The pipeline `try/except` block is shown in full with the surrounding context. No `# similar to ...` shorthand — code is repeated even when verbose because the engineer may read tasks out of order.
- [ ] **Type consistency:** `bars: list[Bar] | None = None` matches everywhere (Task 1 Step 4 in `_stop_price`, Step 5 in `derive_signal`, Task 2 Step 3 call site). `anchor: Decimal | None = None` matches between `_stop_price` signature and `derive_signal` call. The synthetic-bars helper returns `list[Bar]` matching the signature.
- [ ] **No phantom symbols:** `_atr_bars` defined Task 1 Step 1, used Steps 2 and 7. `_bullish_w5_setup`, `_piv`, `PivotType`, `WaveCount`, `TradeRules`, `Decimal`, `date`, `pytest` are all existing imports in `tests/trades/test_derive.py`. `_SyntheticImpulseProvider` is defined in `tests/test_pipeline.py:54`.
- [ ] **TDD:** Task 1 and Task 2 are failing-test-first. Task 3 is operational (no test discipline because there is no behavior change — only metadata).
- [ ] **Frequent commits:** 3 commits, each self-contained and revertible.
