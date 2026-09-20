# Multi-degree Enumeration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-degree enumeration in `pipeline.py:_enumerate_for` with a 3-degree loop ([1, 2, 3]) so `analyze` surfaces patterns at three time horizons. Closes S6 from the 2026-05-05 sanity run.

**Architecture:** Single-point change at `pipeline.py:_enumerate_for` (lines 217-222). The pivot detector and `enumerate_counts` are already degree-parameterized — the change is at the assembly layer. Per-degree top-K = 2 → up to 6 candidates per analyze (was 5 single-degree). No schema changes.

**Tech Stack:** Python 3.11 / `uv`, `pytest` for tests, `ruff` for lint.

---

## File Structure

**Modified:**
- `src/wave_alpha/pipeline.py` — replace `_DEFAULT_DEGREE_FOR_COUNT = 2` constant with `_ENUMERATED_DEGREES = (1, 2, 3)` + `_TOP_K_PER_DEGREE = 2`; replace single-call body with multi-degree loop.
- `tests/test_pipeline.py` — 2 new tests pinning multi-degree emission and per-degree K=2 cap.
- `tests/trades/test_derive.py` — 1 new test pinning per-degree EBT (degrees 1, 2, 3).

**Conditionally modified (only if T5 audit finds breakage):**
- Existing tests in `tests/test_pipeline.py` that asserted on `len(result.counts_daily)` etc. with the old single-degree top-5 cap.

**Branch:** `feat/multi-degree-enumeration` already created from `main` HEAD `15a52f6`. Spec is committed at `088a91b` on this branch.

**Version target:** v0.5.10 (minor bump — visible behavior change in `analyze` output).

---

### Task 1: Write 3 failing tests

**Files:**
- Modify: `tests/test_pipeline.py` (append 2 tests using `monkeypatch` on `detect_multi_degree` for deterministic multi-degree pivot input)
- Modify: `tests/trades/test_derive.py` (append 1 test for per-degree EBT)

- [ ] **Step 1: Append the multi-degree pipeline tests to `tests/test_pipeline.py`**

The tests use `monkeypatch.setattr` on `wave_alpha.pipeline.detect_multi_degree` to inject a canned multi-degree pivot dict. This is deterministic and avoids relying on synthetic-bar pivot detection, which may not produce the exact degree distribution we need.

Append at the end of `tests/test_pipeline.py` (after the existing tests):

```python
from wave_alpha.elliott.models import WaveCount
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
from wave_alpha.elliott.enumerator import enumerate_counts as _real_enumerate_counts


def _piv(d: date, price: str, t: PivotType, *, degree: int) -> PivotEvent:
    return PivotEvent(
        interval=Interval.DAILY,
        degree=degree,
        timestamp=d,
        price=Decimal(price),
        type=t,
        state=PivotState.CONFIRMED,
        confirmed_at=d,
    )


def _multi_degree_pivot_dict(start: date) -> dict[int, list[PivotEvent]]:
    """Canned 4-segment impulse pivots replicated at degrees 1, 2, 3.

    Each degree gets the same shape (so enumerate_counts produces real
    candidates at each), with progressively larger price scales so the
    detector-style separation is preserved.
    """
    out: dict[int, list[PivotEvent]] = {}
    for d in (1, 2, 3):
        scale = Decimal(str(10 * d))  # 10, 20, 30 absolute price
        out[d] = [
            _piv(start + timedelta(days=0), str(Decimal("100") + scale * 0), PivotType.LOW, degree=d),
            _piv(start + timedelta(days=5), str(Decimal("100") + scale * 1), PivotType.HIGH, degree=d),
            _piv(start + timedelta(days=8), str(Decimal("100") + scale * Decimal("0.4")), PivotType.LOW, degree=d),
            _piv(start + timedelta(days=15), str(Decimal("100") + scale * 3), PivotType.HIGH, degree=d),
            _piv(start + timedelta(days=18), str(Decimal("100") + scale * Decimal("1.8")), PivotType.LOW, degree=d),
        ]
    return out


def test_run_analysis_emits_multi_degree_candidates(monkeypatch) -> None:
    """Pipeline must surface candidates across all three configured degrees
    (1, 2, 3) when input pivots support each.  Pins S6 multi-degree
    enumeration: prior to this, only degree-2 candidates were emitted
    regardless of pivot input."""
    canned = _multi_degree_pivot_dict(date(2024, 6, 1))

    def fake_detect(bars, *, interval, atr_multiples):
        return canned

    monkeypatch.setattr("wave_alpha.pipeline.detect_multi_degree", fake_detect)

    provider = _SyntheticImpulseProvider()
    result = run_analysis(
        AnalyzeRequest(ticker="TEST", as_of=date(2024, 12, 31)),
        provider=provider,
        llm_mode=LLMMode.DISABLED,
    )
    degrees = {c.degree for c in result.counts_daily}
    assert len(degrees) >= 2, f"expected ≥2 degrees, got {degrees}"
    assert degrees.issubset({1, 2, 3}), f"unexpected degrees: {degrees - {1, 2, 3}}"


def test_run_analysis_top_k_per_degree_cap(monkeypatch) -> None:
    """No more than 2 candidates per degree are emitted (the
    pipeline._TOP_K_PER_DEGREE cap).  Pins the cap so enumerator changes
    don't accidentally inflate the candidate fan-out."""
    canned = _multi_degree_pivot_dict(date(2024, 6, 1))

    def fake_detect(bars, *, interval, atr_multiples):
        return canned

    monkeypatch.setattr("wave_alpha.pipeline.detect_multi_degree", fake_detect)

    provider = _SyntheticImpulseProvider()
    result = run_analysis(
        AnalyzeRequest(ticker="TEST", as_of=date(2024, 12, 31)),
        provider=provider,
        llm_mode=LLMMode.DISABLED,
    )
    by_deg: dict[int, int] = {}
    for c in result.counts_daily:
        by_deg[c.degree] = by_deg.get(c.degree, 0) + 1
    for degree, count in by_deg.items():
        assert count <= 2, f"degree {degree} produced {count} candidates (>2)"
```

If the `from wave_alpha.elliott.models import WaveCount` import is unused (depends on whether the actual test bodies use it), it can be omitted — but the imports for `PivotEvent`, `PivotState`, `PivotType` are required for `_piv`. The `_real_enumerate_counts` import is unused in the tests above and should be omitted.

Final import block to add:
```python
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType
```

(The other imports — `date`, `timedelta`, `Decimal`, `Interval`, `AnalyzeRequest`, `run_analysis`, `LLMMode` — are already in the file.)

- [ ] **Step 2: Append the per-degree EBT test to `tests/trades/test_derive.py`**

This test pins the C5 invariant at the `derive_signal` unit level
(deterministic — the pipeline's score-driven `ranked[:3]` may not surface
all 3 degrees as signals).

Append at the end of `tests/trades/test_derive.py`:

```python
def test_derive_signal_per_degree_ebt() -> None:
    """The C5 invariant from the sanity run: per-degree EBT diversity
    is non-trivial.  Degree-2 signals use empirical EBT (17/29);
    degree-1 use heuristic 10/15; degree-3 use heuristic 60/90.  Pins
    the wiring through derive_signal at the unit level so multi-degree
    enumeration produces signals with degree-appropriate time-stops."""
    base = _bullish_w5_setup()  # degree=2 by default

    sig_d1 = derive_signal(
        count=base.model_copy(update={"degree": 1}),
        ticker="TEST",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(stop_source="count_invalidation"),
    )
    sig_d2 = derive_signal(
        count=base,
        ticker="TEST",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(stop_source="count_invalidation"),
    )
    sig_d3 = derive_signal(
        count=base.model_copy(update={"degree": 3}),
        ticker="TEST",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(stop_source="count_invalidation"),
    )

    assert sig_d1 is not None
    assert sig_d1.expected_bars_to_target == 10
    assert sig_d1.max_holding_bars == 15

    assert sig_d2 is not None
    assert sig_d2.expected_bars_to_target == 17
    assert sig_d2.max_holding_bars == 29

    assert sig_d3 is not None
    assert sig_d3.expected_bars_to_target == 60
    assert sig_d3.max_holding_bars == 90
```

This test partially overlaps with existing `test_derive_signal_uses_empirical_ebt_for_degree_2` and `test_derive_signal_falls_back_to_heuristic_for_unfit_degrees`. The new test is broader (covers all 3 degrees in one place) and serves as the C5-invariant pin.

---

### Task 2: Verify all 3 tests fail (RED)

**Files:** none (test execution only)

- [ ] **Step 1: Run the new tests**

```bash
uv run pytest tests/test_pipeline.py::test_run_analysis_emits_multi_degree_candidates tests/test_pipeline.py::test_run_analysis_top_k_per_degree_cap tests/trades/test_derive.py::test_derive_signal_per_degree_ebt -v
```

**Expected outcomes for RED:**

- `test_run_analysis_emits_multi_degree_candidates` — should FAIL because `_enumerate_for` returns only degree-2 candidates today (the canned `fake_detect` returns degrees 1, 2, 3 but the pipeline filters to degree-2). Assertion `len(degrees) >= 2` fails with `degrees == {2}`.
- `test_run_analysis_top_k_per_degree_cap` — may PASS by accident (one degree, ≤5 candidates each). This is a forward-compatibility pin; passing pre-implementation is OK because the cap holds trivially when only one degree exists.
- `test_derive_signal_per_degree_ebt` — already passes today (the EBT logic is degree-keyed and works for all 3 degrees right now). This is a forward-compatibility pin to lock the C5 invariant.

If `test_run_analysis_emits_multi_degree_candidates` passes pre-implementation, the multi-degree change is somehow already in place — investigate before proceeding.

---

### Task 3: Implement the multi-degree assembly

**Files:**
- Modify: `src/wave_alpha/pipeline.py:34` (replace `_DEFAULT_DEGREE_FOR_COUNT = 2`)
- Modify: `src/wave_alpha/pipeline.py:217-222` (replace `_enumerate_for` body)

- [ ] **Step 1: Locate the constant and function**

Verify the locations:

```bash
grep -n "_DEFAULT_DEGREE_FOR_COUNT\|def _enumerate_for" src/wave_alpha/pipeline.py
```

Expected output:
```
34:_DEFAULT_DEGREE_FOR_COUNT = 2
217:def _enumerate_for(bars: list[Bar], interval: Interval) -> list[WaveCount]:
221:    pivots: list[PivotEvent] = by_degree.get(_DEFAULT_DEGREE_FOR_COUNT, [])
222:    return enumerate_counts(pivots, degree=_DEFAULT_DEGREE_FOR_COUNT, top_k=5)
```

Line numbers may shift slightly between commits but the structure is stable.

- [ ] **Step 2: Replace the constant**

Edit `src/wave_alpha/pipeline.py` line 34. Replace:

```python
_DEFAULT_DEGREE_FOR_COUNT = 2
```

With:

```python
_ENUMERATED_DEGREES: tuple[int, ...] = (1, 2, 3)
_TOP_K_PER_DEGREE: int = 2
```

- [ ] **Step 3: Replace the `_enumerate_for` body**

Edit `src/wave_alpha/pipeline.py:217-222`. Replace:

```python
def _enumerate_for(bars: list[Bar], interval: Interval) -> list[WaveCount]:
    if not bars:
        return []
    by_degree = detect_multi_degree(bars, interval=interval, atr_multiples=_DEFAULT_ATR_MULTIPLES)
    pivots: list[PivotEvent] = by_degree.get(_DEFAULT_DEGREE_FOR_COUNT, [])
    return enumerate_counts(pivots, degree=_DEFAULT_DEGREE_FOR_COUNT, top_k=5)
```

With:

```python
def _enumerate_for(bars: list[Bar], interval: Interval) -> list[WaveCount]:
    """Enumerate candidates across all configured degrees and pool the
    results.  Each degree contributes up to `_TOP_K_PER_DEGREE` candidates;
    degrees with no pivots contribute nothing.

    Per-degree top-K is preferred over a global top-K so that one degree's
    score distribution cannot crowd out the others (degree-2 has a
    calibrated score baseline from the 2026-05-07 EBT fit; degrees 1 and 3
    do not).  See spec 2026-05-08-multi-degree-enumeration-design.md §3.1.
    """
    if not bars:
        return []
    by_degree = detect_multi_degree(bars, interval=interval, atr_multiples=_DEFAULT_ATR_MULTIPLES)
    candidates: list[WaveCount] = []
    for degree in _ENUMERATED_DEGREES:
        pivots = by_degree.get(degree, [])
        candidates.extend(
            enumerate_counts(pivots, degree=degree, top_k=_TOP_K_PER_DEGREE)
        )
    return candidates
```

The `PivotEvent` type annotation on the local `pivots` variable is dropped — it's no longer load-bearing inside the loop and the inferred type from `by_degree.get(degree, [])` is correct.

- [ ] **Step 4: Verify the import for PivotEvent is no longer load-bearing**

After the edit, `PivotEvent` may no longer be referenced in `pipeline.py`. Check:

```bash
grep -n "PivotEvent" src/wave_alpha/pipeline.py
```

If no matches remain, remove the unused import to keep ruff clean:

```python
# Remove if no longer used:
# from wave_alpha.pivots.models import PivotEvent
```

If `PivotEvent` is still used elsewhere in `pipeline.py` (e.g., as a type alias or in a different function), leave the import.

---

### Task 4: Verify all tests pass (GREEN) + full suite regression

**Files:** none (test execution only)

- [ ] **Step 1: Run the 3 new tests**

```bash
uv run pytest tests/test_pipeline.py::test_run_analysis_emits_multi_degree_candidates tests/test_pipeline.py::test_run_analysis_top_k_per_degree_cap tests/trades/test_derive.py::test_derive_signal_per_degree_ebt -v
```

Expected: all 3 PASS.

- [ ] **Step 2: Run the full pipeline test file**

```bash
uv run pytest tests/test_pipeline.py -v
```

Expected: all tests pass. If any pre-existing test fails because it relied on `len(result.counts_daily) <= 5` with the old top-5 single-degree cap, see T5.

- [ ] **Step 3: Run the full suite**

```bash
uv run pytest 2>&1 | tail -10
```

Expected: 506 + 3 = 509 passed, no failures. If failures, proceed to T5 audit.

---

### Task 5: Audit & fix any test breakage from candidate fan-out

Skip this task if T4 step 3 returned green.

**Files:** any test file with failures from T4.

- [ ] **Step 1: List failing tests**

```bash
uv run pytest 2>&1 | grep -E "FAILED|ERROR" | head -20
```

- [ ] **Step 2: Per failure, decide policy**

For each failure, classify into one of:

A. **Test assumed single-degree output** (e.g., `assert all(c.degree == 2 for c in result.counts_daily)`):
   - If the test's intent was to pin degree-2 behavior specifically, change the assertion to filter: `degree_2_counts = [c for c in result.counts_daily if c.degree == 2]` and assert on those.
   - If the test's intent was generic, relax the assertion to allow {1, 2, 3}.

B. **Test assumed top-5 cap** (e.g., `assert len(result.counts_daily) <= 5`):
   - Bump the cap to 6 (or remove the cap if irrelevant to the test's intent).

C. **Test depended on a specific candidate count** (e.g., `assert len(result.counts_daily) == 1`):
   - Investigate whether the original number was a deliberate fixture choice or coincidental. If deliberate, construct a single-degree input fixture (e.g., a synthetic bar series that only produces degree-2 pivots) to preserve the original intent. If coincidental, update to the new expected count.

- [ ] **Step 3: Re-run full suite after fixes**

```bash
uv run pytest 2>&1 | tail -5
```

Expected: 509 passed, no failures.

---

### Task 6: Lint + format

**Files:** any modified files.

- [ ] **Step 1: Run ruff check on modified files**

```bash
uv run ruff check src/wave_alpha/pipeline.py tests/test_pipeline.py tests/trades/test_derive.py
```

Expected: `All checks passed!`. If failures, fix and re-run.

- [ ] **Step 2: Run ruff format on modified files**

```bash
uv run ruff format --check src/wave_alpha/pipeline.py tests/test_pipeline.py tests/trades/test_derive.py
```

Expected: `N files already formatted`. If reformatting needed, run `uv run ruff format <file>` and re-stage.

- [ ] **Step 3: Don't run ruff format on the rest of the repo**

The pre-existing project state has 14 files that ruff considers unformatted (carried from prior sessions). Don't sweep them up in this commit — out of scope for S6.

---

### Task 7: Bump version to 0.5.10 and commit

**Files:**
- Modify: `pyproject.toml:7`
- Modify: `src/wave_alpha/__init__.py:1`

- [ ] **Step 1: Bump pyproject.toml**

Edit `pyproject.toml` line 7. Replace:

```toml
version = "0.5.9"
```

With:

```toml
version = "0.5.10"
```

- [ ] **Step 2: Bump `__init__.py`**

Edit `src/wave_alpha/__init__.py` line 1. Replace:

```python
__version__ = "0.5.9"
```

With:

```python
__version__ = "0.5.10"
```

- [ ] **Step 3: Sync uv.lock**

```bash
uv sync --extra dev
```

Expected: `uv.lock` updates the wave-alpha entry to 0.5.10.

- [ ] **Step 4: Final test pass**

```bash
uv run pytest 2>&1 | tail -3
```

Expected: 509 passed (or +N if T5 added tests).

- [ ] **Step 5: Stage and commit**

Stage only the project files (not AGENTS.md / CLAUDE.md gitnexus drift):

```bash
git add src/wave_alpha/pipeline.py \
    tests/test_pipeline.py \
    tests/trades/test_derive.py \
    pyproject.toml \
    src/wave_alpha/__init__.py \
    uv.lock
```

If T5 modified any test files beyond those listed, add them too. Verify staging:

```bash
git status --short
```

Files in `M `/`A ` (staged) should be only the ones above. Files in ` M` (unstaged) should be the gitnexus drift on AGENTS.md / CLAUDE.md — leave those alone.

- [ ] **Step 6: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(engine): multi-degree enumeration — closes S6

pipeline._enumerate_for now emits candidates across degrees [1, 2, 3]
instead of only degree 2.  Pivot detector and elliott.enumerator are
already degree-parameterized; the change is at the assembly layer.

Per-degree top-K = 2 (6 candidates max per analyze, was 5 single-
degree).  Each candidate carries count.degree; downstream derive_signal
keys on it for the empirical degree-2 EBT (17/29) and the heuristic
EBTs for degrees 1 (10/15) and 3 (60/90).

Empirical EBT refit for degrees 1 and 3 is a deferred follow-up
spec — needs multi-degree trade data, which requires this ship first.

Closes S6 from 2026-05-05 sanity run §9.  v0.5.9 → v0.5.10.

See: docs/superpowers/specs/2026-05-08-multi-degree-enumeration-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Merge to main + push

**Files:** none (git-only)

- [ ] **Step 1: Discard any gitnexus drift on AGENTS.md / CLAUDE.md**

`git checkout -- AGENTS.md CLAUDE.md` to clear the auto-generated drift before checkout. The drift will regenerate after the merge.

- [ ] **Step 2: Switch to main and merge with --no-ff**

```bash
git checkout main
git merge --no-ff feat/multi-degree-enumeration -m "Merge feat/multi-degree-enumeration — multi-degree enumeration + v0.5.10"
```

- [ ] **Step 3: Verify tests on merged main**

```bash
uv run pytest 2>&1 | tail -3
```

Expected: 509+ passed.

- [ ] **Step 4: Push to origin**

```bash
git push origin main
```

- [ ] **Step 5: Delete the feature branch**

```bash
git branch -d feat/multi-degree-enumeration
```

- [ ] **Step 6: Confirm origin is in sync**

```bash
git log --oneline -5
git status
```

Expected: HEAD shows the merge commit + spec commit + feature commit; working tree clean (apart from the inevitable gitnexus drift on AGENTS.md / CLAUDE.md).

---

## Self-review checklist (run before declaring plan ready)

- [x] Spec coverage: every §3 component (pipeline assembly change) → T3
- [x] Spec §4 tests (3 of them) → T1 (each test in its own step), T2 RED, T4 GREEN
- [x] Spec §5 file structure → T3 (modified files), T1 (test files)
- [x] Spec §6 lookahead invariants → no new code paths; existing guard tests stay green per T4
- [x] Spec §9 W1-W5 deferred decisions → none touched in this plan (correctly out-of-scope)
- [x] Spec §10 v0.5.10 bump → T7
- [x] No "TBD" / "TODO" placeholders in steps
- [x] Every step that runs a command shows the exact command + expected result
- [x] Every step that writes code shows the actual code

## Notes for the executor

- T1 step 1's `_multi_degree_pivot_dict` fixture is the only non-trivial test infrastructure. It produces canned pivots at degrees 1, 2, 3 with the same 4-segment-impulse shape so each degree's enumerator call can produce real candidates (the validator + score path is exercised at each degree).
- T5 is conditional. The expected baseline is 506 + 3 = 509 passing. If pre-existing tests break, T5 fixes them; otherwise skip directly to T6.
- The gitnexus auto-rewrite of AGENTS.md / CLAUDE.md happens after every commit. T7 step 5 stages only project files; T8 step 1 discards the drift before checkout. Don't try to commit the drift.
- The next-next phase after S6 ships is the empirical EBT refit for degrees 1 and 3 (W1 from the spec). Don't conflate it with this plan.
