# Leading Diagonal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `leading_diagonal` as a peer primitive motive template to the Elliott engine, differentiated from `ending_diagonal` by soft-constraint scoring (W2 retrace depth, W3 extension cap).

**Architecture:** YAML-only template addition. No enumerator, validator, DSL, or pipeline code changes — the existing primitive (non-composite) path in `enumerate_counts` already handles 5-wave motive templates. Differentiation from ED is purely through soft-constraint scoring nudging the `_score` formula. Acceptance gated by unit tests + golden fixtures + count-layer backtest neutrality on the curated 50-ticker universe.

**Tech Stack:** Python 3.11+, `uv`, Pydantic v2, `decimal.Decimal` for prices, `pytest`, `ruff`, YAML templates loaded by `PatternTemplate.from_yaml_file`, no new dependencies.

**Spec:** [`docs/superpowers/specs/2026-05-10-leading-diagonal-design.md`](../specs/2026-05-10-leading-diagonal-design.md)

---

## File map

**Created:**
- `src/wave_alpha/elliott/templates_data/leading_diagonal.yaml` — the new template
- `tests/elliott/test_leading_diagonal.py` — Layer 1 unit tests (template load + hard/soft constraint behavior)
- `tests/elliott/fixtures/leading_diagonal_golden/` — Layer 2 fixtures directory
- `tests/elliott/fixtures/leading_diagonal_golden/README.md` — fixture format docs
- `tests/elliott/fixtures/leading_diagonal_golden/synthetic_ld_positive.json` — synthetic positive seed
- `tests/elliott/fixtures/leading_diagonal_golden/synthetic_ed_negative.json` — synthetic negative seed
- `tests/elliott/test_leading_diagonal_golden.py` — Layer 2 golden loader
- `reports/leading_diagonal_count_before.md` — count-layer backtest output on `main` baseline
- `reports/leading_diagonal_count_after.md` — count-layer backtest output on the LD branch

**Modified:**
- `tests/test_lean_install.py` — bump `>=6` template-count assertion to `>=7`
- `tests/test_pipeline.py` — append Layer 4 integration test

**Not modified (intentionally):**
- `src/wave_alpha/elliott/enumerator.py` — primitive flat path already covers LD
- `src/wave_alpha/elliott/validator.py` — existing operators (`length`, `retrace`, `overlaps`, `min`) cover all LD constraints
- `src/wave_alpha/elliott/dsl.py` — no new operators
- `src/wave_alpha/elliott/templates.py` — no schema change
- `src/wave_alpha/elliott/fib_features.py` — LD falls through to legacy single-ratio confluence path (same as ED)
- `src/wave_alpha/pipeline.py` — no wiring changes
- `tests/elliott/test_templates.py` — uses `<=` subset assertion, unaffected by adding a new name

---

### Task 1: Ship the `leading_diagonal.yaml` template + load smoke test

**Files:**
- Create: `src/wave_alpha/elliott/templates_data/leading_diagonal.yaml`
- Create: `tests/elliott/test_leading_diagonal.py`

- [ ] **Step 1: Write the failing test**

`tests/elliott/test_leading_diagonal.py`:

```python
from wave_alpha.elliott.templates import PatternTemplate, load_bundled_templates


def test_leading_diagonal_template_loads_from_bundled_dir():
    tpl = PatternTemplate.load_bundled("leading_diagonal")
    assert tpl.name == "leading_diagonal"
    assert tpl.waves == ["1", "2", "3", "4", "5"]
    assert tpl.direction_alternates is True


def test_leading_diagonal_appears_in_load_bundled_templates():
    names = {t.name for t in load_bundled_templates()}
    assert "leading_diagonal" in names


def test_leading_diagonal_constraint_set():
    tpl = PatternTemplate.load_bundled("leading_diagonal")
    names = {c.name for c in tpl.constraints}
    assert names == {
        "wave_3_not_shortest",
        "wave_2_max_retrace",
        "wave_4_overlaps_wave_1",
        "wave_2_deep_retrace",
        "wave_2_not_excessive",
        "wave_3_not_extended",
    }


def test_leading_diagonal_severity_split():
    tpl = PatternTemplate.load_bundled("leading_diagonal")
    sev = {c.name: c.severity for c in tpl.constraints}
    assert sev["wave_3_not_shortest"] == "hard"
    assert sev["wave_2_max_retrace"] == "hard"
    assert sev["wave_4_overlaps_wave_1"] == "soft"
    assert sev["wave_2_deep_retrace"] == "soft"
    assert sev["wave_2_not_excessive"] == "soft"
    assert sev["wave_3_not_extended"] == "soft"


def test_leading_diagonal_sub_pattern_allowlist():
    tpl = PatternTemplate.load_bundled("leading_diagonal")
    odd = ["zigzag", "impulse"]
    even = ["zigzag", "flat"]
    assert tpl.allowed_sub_patterns["1"] == odd
    assert tpl.allowed_sub_patterns["2"] == even
    assert tpl.allowed_sub_patterns["3"] == odd
    assert tpl.allowed_sub_patterns["4"] == even
    assert tpl.allowed_sub_patterns["5"] == odd
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/elliott/test_leading_diagonal.py -v
```

Expected: FAIL — `leading_diagonal` YAML not found.

- [ ] **Step 3: Create the YAML template**

`src/wave_alpha/elliott/templates_data/leading_diagonal.yaml`:

```yaml
name: leading_diagonal
direction_alternates: true
waves: ["1", "2", "3", "4", "5"]
constraints:
  - name: wave_3_not_shortest
    severity: hard
    expr: "length(W3) >= min(length(W1), length(W5))"
    rationale: "Motive rule - W3 cannot be the shortest of 1, 3, 5."

  - name: wave_2_max_retrace
    severity: hard
    expr: "retrace(W2, W1) <= Decimal('0.99')"
    rationale: "W2 cannot fully retrace W1; full retrace means there is no W1."

  - name: wave_4_overlaps_wave_1
    severity: soft
    expr: "overlaps(W4, W1)"
    rationale: "Diagonals SHOULD show W4 overlapping W1; distinguishes diagonal from impulse."

  - name: wave_2_deep_retrace
    severity: soft
    expr: "retrace(W2, W1) >= Decimal('0.5')"
    rationale: "LD W2 typically retraces 50%+ of W1 (ED tends shallow)."

  - name: wave_2_not_excessive
    severity: soft
    expr: "retrace(W2, W1) <= Decimal('0.9')"
    rationale: "LD W2 rarely exceeds 90% of W1; deeper is usually a corrective misclassification."

  - name: wave_3_not_extended
    severity: soft
    expr: "length(W3) <= Decimal('1.618') * length(W1)"
    rationale: "LD W3 is compressed vs. impulse W3; heavy extension suggests impulse."

allowed_sub_patterns:
  "1": ["zigzag", "impulse"]
  "2": ["zigzag", "flat"]
  "3": ["zigzag", "impulse"]
  "4": ["zigzag", "flat"]
  "5": ["zigzag", "impulse"]
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/elliott/test_leading_diagonal.py -v
```

Expected: 5 PASS.

- [ ] **Step 5: Run full pytest to catch unexpected regressions**

```bash
uv run pytest -x
```

Expected: one failure in `tests/test_lean_install.py` — assertion `>=6` against actual 8 still passes (subset). Other tests should be green. If anything else fails because it asserts an exact template name set, fix it inline by extending the expected set.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/elliott/templates_data/leading_diagonal.yaml tests/elliott/test_leading_diagonal.py
git commit -m "$(cat <<'EOF'
feat(elliott): add leading_diagonal template (motive primitive)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Hard-constraint rejection tests (engine never emits invalid LDs)

**Files:**
- Modify: `tests/elliott/test_leading_diagonal.py`

This task adds focused unit tests that wire pivot fixtures through `enumerate_counts` with a restricted template list (LD-only) and assert the engine rejects pivot configurations that violate LD's hard constraints.

- [ ] **Step 1: Append the failing tests**

Append to `tests/elliott/test_leading_diagonal.py`:

```python
from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.domain import Interval
from wave_alpha.elliott.enumerator import enumerate_counts
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def _pivot(day: int, price: str, kind: PivotType, *, degree: int = 2) -> PivotEvent:
    """Build a degree-N PivotEvent at 2026-01-01 + ``day`` with the given price."""
    return PivotEvent(
        interval=Interval.DAILY,
        degree=degree,
        timestamp=date(2026, 1, 1) + timedelta(days=day),
        price=Decimal(price),
        type=kind,
        state=PivotState.CONFIRMED,
        confirmed_at=None,
    )


def _ld_only_templates():
    """Return a [leading_diagonal] template list for restricted enumeration."""
    return [PatternTemplate.load_bundled("leading_diagonal")]


def test_ld_hard_rejects_w3_shortest():
    """W3 strictly shorter than min(W1, W5) - the hard wave_3_not_shortest
    constraint must fire and the engine must not emit an LD candidate."""
    # Up-direction 5-wave with W3 deliberately tiny.
    #   W1: 100(L) -> 120(H)  length=20
    #   W2: 120(H) -> 110(L)  retrace=0.5
    #   W3: 110(L) -> 115(H)  length=5  (SHORTER than W1=20 and W5=25 -> hard fail)
    #   W4: 115(H) -> 108(L)  overlap with W1 OK for LD
    #   W5: 108(L) -> 133(H)  length=25
    pivots = [
        _pivot(0, "100", PivotType.LOW),
        _pivot(1, "120", PivotType.HIGH),
        _pivot(2, "110", PivotType.LOW),
        _pivot(3, "115", PivotType.HIGH),
        _pivot(4, "108", PivotType.LOW),
        _pivot(5, "133", PivotType.HIGH),
    ]
    counts = enumerate_counts(pivots, degree=2, templates=_ld_only_templates(), top_k=5)
    assert all(c.pattern != "leading_diagonal" for c in counts), (
        f"LD should be rejected on W3<min(W1,W5); got: {[c.pattern for c in counts]}"
    )


def test_ld_hard_rejects_w2_full_retrace():
    """W2 retrace >= 1.0 - the hard wave_2_max_retrace must fire."""
    #   W1: 100(L) -> 120(H)  length=20
    #   W2: 120(H) -> 100(L)  retrace=1.0  -> HARD FAIL (must be < 0.99)
    # Pivots after W2 are valid-shaped LD continuation for completeness.
    #   W3: 100(L) -> 130(H)  length=30
    #   W4: 130(H) -> 115(L)  overlap with W1
    #   W5: 115(L) -> 140(H)  length=25
    pivots = [
        _pivot(0, "100", PivotType.LOW),
        _pivot(1, "120", PivotType.HIGH),
        _pivot(2, "100", PivotType.LOW),
        _pivot(3, "130", PivotType.HIGH),
        _pivot(4, "115", PivotType.LOW),
        _pivot(5, "140", PivotType.HIGH),
    ]
    counts = enumerate_counts(pivots, degree=2, templates=_ld_only_templates(), top_k=5)
    assert all(c.pattern != "leading_diagonal" for c in counts), (
        f"LD should be rejected on full W2 retrace; got: {[c.pattern for c in counts]}"
    )
```

Also at the top of the file, add `PatternTemplate` to the existing import line so `_ld_only_templates` resolves:

```python
from wave_alpha.elliott.templates import PatternTemplate, load_bundled_templates
```

(That import already exists from Task 1 — verify nothing changes if it's present.)

- [ ] **Step 2: Run tests to verify they pass**

```bash
uv run pytest tests/elliott/test_leading_diagonal.py -v
```

Expected: PASS — the engine should already reject these via the YAML constraints from Task 1. If a test fails, the YAML constraint is misspelled or the operator semantics differ from expectation; fix the YAML, not the test.

- [ ] **Step 3: Commit**

```bash
git add tests/elliott/test_leading_diagonal.py
git commit -m "$(cat <<'EOF'
test(elliott): leading_diagonal hard-constraint rejection coverage

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: LD vs ED disambiguation tests (soft-constraint scoring)

**Files:**
- Modify: `tests/elliott/test_leading_diagonal.py`

The core engineering claim of this spec is that LD wins on deep-W2 windows and ED wins on shallow-W2 windows. This task locks that contract.

- [ ] **Step 1: Append the failing tests**

Append to `tests/elliott/test_leading_diagonal.py`:

```python
def _ld_and_ed_templates():
    return [
        PatternTemplate.load_bundled("leading_diagonal"),
        PatternTemplate.load_bundled("ending_diagonal"),
    ]


def _score_for(counts, pattern_name: str) -> float | None:
    for c in counts:
        if c.pattern == pattern_name:
            return c.score
    return None


def test_ld_outscores_ed_on_deep_w2():
    """W2 retrace approx 0.7 should trigger LD's wave_2_deep_retrace soft
    AND avoid wave_2_not_excessive; ED has no such soft, so LD scores higher."""
    # Up-direction LD shape with deep W2:
    #   W1: 100(L) -> 120(H)   length=20
    #   W2: 120(H) -> 106(L)   retrace=0.70   <- LD-favoring
    #   W3: 106(L) -> 128(H)   length=22  <= 1.618*20=32.36 (LD's wave_3_not_extended OK)
    #   W4: 128(H) -> 112(L)   overlap with W1 OK for both LD and ED
    #   W5: 112(L) -> 130(H)   length=18
    pivots = [
        _pivot(0, "100", PivotType.LOW),
        _pivot(1, "120", PivotType.HIGH),
        _pivot(2, "106", PivotType.LOW),
        _pivot(3, "128", PivotType.HIGH),
        _pivot(4, "112", PivotType.LOW),
        _pivot(5, "130", PivotType.HIGH),
    ]
    counts = enumerate_counts(pivots, degree=2, templates=_ld_and_ed_templates(), top_k=5)
    ld_score = _score_for(counts, "leading_diagonal")
    ed_score = _score_for(counts, "ending_diagonal")
    assert ld_score is not None, f"LD missing from {[c.pattern for c in counts]}"
    assert ed_score is not None, f"ED missing from {[c.pattern for c in counts]}"
    assert ld_score > ed_score, (
        f"deep-W2 window: LD must outscore ED. got LD={ld_score:.4f} ED={ed_score:.4f}"
    )


def test_ed_outscores_ld_on_shallow_w2():
    """W2 retrace approx 0.38 fires NEITHER of LD's deep-W2 softs, so LD
    accrues two extra soft violations vs. ED -> ED scores higher."""
    # Up-direction with shallow W2:
    #   W1: 100(L) -> 120(H)   length=20
    #   W2: 120(H) -> 112.4(L) retrace=0.38   <- ED-favoring (LD wave_2_deep_retrace violated)
    #   W3: 112.4(L) -> 128(H) length=15.6
    #   W4: 128(H) -> 118(L)   overlap with W1 (118 > 100 and < 120)
    #   W5: 118(L) -> 132(H)   length=14
    pivots = [
        _pivot(0, "100", PivotType.LOW),
        _pivot(1, "120", PivotType.HIGH),
        _pivot(2, "112.4", PivotType.LOW),
        _pivot(3, "128", PivotType.HIGH),
        _pivot(4, "118", PivotType.LOW),
        _pivot(5, "132", PivotType.HIGH),
    ]
    counts = enumerate_counts(pivots, degree=2, templates=_ld_and_ed_templates(), top_k=5)
    ld_score = _score_for(counts, "leading_diagonal")
    ed_score = _score_for(counts, "ending_diagonal")
    assert ld_score is not None, f"LD missing from {[c.pattern for c in counts]}"
    assert ed_score is not None, f"ED missing from {[c.pattern for c in counts]}"
    assert ed_score > ld_score, (
        f"shallow-W2 window: ED must outscore LD. got LD={ld_score:.4f} ED={ed_score:.4f}"
    )


def test_ld_dedupes_per_template_across_windows():
    """Multiple LD-eligible windows in a longer pivot list collapse to the
    highest-scoring one before the global top-K cut. Mirrors existing
    best_per_template behavior; this test exists so future enumerator
    changes that break dedupe also fail here."""
    # Eight pivots: two overlapping 6-pivot LD-shaped windows.
    pivots = [
        _pivot(0, "100", PivotType.LOW),
        _pivot(1, "120", PivotType.HIGH),
        _pivot(2, "106", PivotType.LOW),
        _pivot(3, "128", PivotType.HIGH),
        _pivot(4, "112", PivotType.LOW),
        _pivot(5, "130", PivotType.HIGH),
        _pivot(6, "118", PivotType.LOW),
        _pivot(7, "135", PivotType.HIGH),
    ]
    counts = enumerate_counts(pivots, degree=2, templates=_ld_only_templates(), top_k=5)
    ld_counts = [c for c in counts if c.pattern == "leading_diagonal"]
    assert len(ld_counts) == 1, (
        f"per-template dedupe broken: got {len(ld_counts)} LD entries"
    )
```

- [ ] **Step 2: Run the tests**

```bash
uv run pytest tests/elliott/test_leading_diagonal.py -v
```

Expected: ALL PASS. If the deep-vs-shallow tests fail because LD and ED tie or order inverts, the soft-constraint weights need tuning. **Do not** weaken the test thresholds; instead, inspect which softs fired on each count (`count.soft_violations`) and adjust the YAML thresholds. The goal is for the score formula `_score` (in `enumerator.py`) to produce a strict ordering on these two windows — if it can't, the differentiator softs aren't strong enough and the spec's claim doesn't hold.

If they still don't separate after tuning, **stop and surface to the user** before continuing — this is a real design-validity signal, not a test-writing problem.

- [ ] **Step 3: Commit**

```bash
git add tests/elliott/test_leading_diagonal.py
git commit -m "$(cat <<'EOF'
test(elliott): LD vs ED disambiguation contract via soft-constraint scoring

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: LD survives where impulse fails (overlap regression guard)

**Files:**
- Modify: `tests/elliott/test_leading_diagonal.py`

Impulse has a hard `wave_4_no_overlap` constraint; LD does not. Locking this behavior so a future enumerator change can't accidentally exclude LD from diagonal-shaped windows.

- [ ] **Step 1: Append the failing test**

Append to `tests/elliott/test_leading_diagonal.py`:

```python
def _ld_impulse_ed_templates():
    return [
        PatternTemplate.load_bundled("leading_diagonal"),
        PatternTemplate.load_bundled("impulse"),
        PatternTemplate.load_bundled("ending_diagonal"),
    ]


def test_ld_survives_where_impulse_dies_on_w4_overlap():
    """A 5-wave window where W4 overlaps W1: impulse's hard wave_4_no_overlap
    rejects it, but LD and ED do not. The enumerated output must include
    leading_diagonal and exclude impulse."""
    # Deep-W2 LD shape (also valid for ED) with W4 overlapping W1:
    #   W1: 100(L) -> 120(H)
    #   W2: 120(H) -> 106(L)   retrace=0.70 (LD-favoring)
    #   W3: 106(L) -> 128(H)
    #   W4: 128(H) -> 112(L)   overlap with W1 (112 > 100 and < 120)
    #   W5: 112(L) -> 130(H)
    pivots = [
        _pivot(0, "100", PivotType.LOW),
        _pivot(1, "120", PivotType.HIGH),
        _pivot(2, "106", PivotType.LOW),
        _pivot(3, "128", PivotType.HIGH),
        _pivot(4, "112", PivotType.LOW),
        _pivot(5, "130", PivotType.HIGH),
    ]
    counts = enumerate_counts(
        pivots, degree=2, templates=_ld_impulse_ed_templates(), top_k=5
    )
    patterns = {c.pattern for c in counts}
    assert "leading_diagonal" in patterns, f"LD missing from {patterns}"
    assert "impulse" not in patterns, (
        f"impulse should be rejected on W4 overlap; got: {patterns}"
    )
```

- [ ] **Step 2: Run the test**

```bash
uv run pytest tests/elliott/test_leading_diagonal.py::test_ld_survives_where_impulse_dies_on_w4_overlap -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/elliott/test_leading_diagonal.py
git commit -m "$(cat <<'EOF'
test(elliott): LD survives W4-overlap windows that reject impulse

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Layer 4 pipeline integration test

**Files:**
- Modify: `tests/test_pipeline.py`

End-to-end check: synthetic bars run through `run_analysis` produce an LD entry in `result.counts_daily`. Reuses the linear-interpolation pivot bar builder pattern from the wxy spec (Task 8 of `2026-05-10-complex-corrections.md`).

- [ ] **Step 1: Inspect existing fake-provider patterns**

```bash
grep -n "class _Fake\|class Fake.*Provider\|class _Stub\|class Stub" tests/test_pipeline.py
```

Expected: at least one existing fake `Provider` class. **Reuse it** if compatible; do not invent a new one. If the existing fakes have a different `fetch` signature, mimic the closest match.

- [ ] **Step 2: Write the failing integration test**

Append to `tests/test_pipeline.py`:

```python
def test_run_analysis_emits_leading_diagonal_when_pivot_structure_matches():
    """End-to-end: synthetic bars engineered to produce a degree-2 leading
    diagonal must surface a `leading_diagonal` WaveCount in counts_daily."""
    from datetime import date, timedelta
    from decimal import Decimal

    from wave_alpha.domain import Bar, Interval
    from wave_alpha.pipeline import AnalyzeRequest, run_analysis

    bars = _build_bars_for_leading_diagonal_at_degree_2()
    as_of = bars[-1].timestamp

    class _FakeProvider:
        def fetch(self, ticker, interval, *, end):
            return list(bars)

    req = AnalyzeRequest(ticker="TEST", as_of=as_of)
    result = run_analysis(req, provider=_FakeProvider())
    lds = [c for c in result.counts_daily if c.pattern == "leading_diagonal"]
    assert lds, (
        "expected leading_diagonal in counts_daily; got patterns: "
        f"{[c.pattern for c in result.counts_daily]}"
    )
    assert lds[0].degree == 2


def _build_bars_for_leading_diagonal_at_degree_2() -> list:
    """Build bars whose multi-degree zigzag produces a degree-2 LD shape:
    deep W2, W4 overlapping W1, W3 not extended past 1.618*W1. Linear
    interpolation between hand-chosen pivot prices; one bar per day."""
    from datetime import date, timedelta
    from decimal import Decimal

    from wave_alpha.domain import Bar, Interval

    # Pivot prices designed to match the unit-test deep-W2 LD shape:
    #   day 0:  100 (L)
    #   day 10: 120 (H)   W1 = +20
    #   day 20: 106 (L)   W2 retrace ~ 0.70
    #   day 30: 128 (H)   W3 = +22 (< 1.618*W1)
    #   day 40: 112 (L)   W4 overlaps W1
    #   day 50: 130 (H)   W5 = +18
    pivot_days_prices = [
        (0, "100"),
        (10, "120"),
        (20, "106"),
        (30, "128"),
        (40, "112"),
        (50, "130"),
    ]
    bars: list[Bar] = []
    for i in range(len(pivot_days_prices) - 1):
        d0, p0 = pivot_days_prices[i]
        d1, p1 = pivot_days_prices[i + 1]
        span = d1 - d0
        for k in range(span):
            day = d0 + k
            t = k / span
            price = Decimal(p0) + (Decimal(p1) - Decimal(p0)) * Decimal(t)
            bars.append(
                Bar(
                    timestamp=date(2026, 1, 1) + timedelta(days=day),
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=1000,
                    interval=Interval.DAILY,
                )
            )
    final_day, final_price = pivot_days_prices[-1]
    bars.append(
        Bar(
            timestamp=date(2026, 1, 1) + timedelta(days=final_day),
            open=Decimal(final_price),
            high=Decimal(final_price),
            low=Decimal(final_price),
            close=Decimal(final_price),
            volume=1000,
            interval=Interval.DAILY,
        )
    )
    return bars
```

- [ ] **Step 3: Run the integration test**

```bash
uv run pytest tests/test_pipeline.py::test_run_analysis_emits_leading_diagonal_when_pivot_structure_matches -v
```

Expected: PASS. If it fails because `detect_multi_degree` smooths the bars into different pivot points, tune the bar fixture (extend extremes, vary the price step) until degree 2 produces a 5-segment LD-shape window. Do not weaken the test assertion.

- [ ] **Step 4: Commit**

```bash
git add tests/test_pipeline.py
git commit -m "$(cat <<'EOF'
test(pipeline): leading_diagonal end-to-end integration

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Layer 2 golden-fixture loader + 2 synthetic seeds

**Files:**
- Create: `tests/elliott/fixtures/leading_diagonal_golden/README.md`
- Create: `tests/elliott/fixtures/leading_diagonal_golden/synthetic_ld_positive.json`
- Create: `tests/elliott/fixtures/leading_diagonal_golden/synthetic_ed_negative.json`
- Create: `tests/elliott/test_leading_diagonal_golden.py`

Mirrors `tests/elliott/test_wxy_golden.py`. The synthetic seeds use the same builders from Task 5; real-chart fixtures are added incrementally by an analyst until the spec's "≥ 3 positives + 2 negatives" bar is met.

- [ ] **Step 1: Create the fixture-format documentation**

`tests/elliott/fixtures/leading_diagonal_golden/README.md`:

````markdown
# Leading diagonal golden fixtures

Each fixture is a JSON file with the schema:

```json
{
  "name": "human-readable label",
  "ticker": "AAPL",
  "as_of": "2024-03-15",
  "bars": [
    {"timestamp": "2024-01-02", "open": "184.5", "high": "186.0", "low": "183.0", "close": "185.5", "volume": 12345, "interval": "1d"}
  ],
  "expected_top_pattern_at_degree_2": "leading_diagonal",
  "expected_runner_up_in_topk": "ending_diagonal"
}
```

`expected_top_pattern_at_degree_2` is one of:
- `"leading_diagonal"` - positive: LD must be #1 at degree 2.
- `"ending_diagonal"` / `"impulse"` - negative: LD must NOT be #1.
- `"primitive"` - any non-LD pattern; regression guard.

`expected_runner_up_in_topk` is optional. When set, the named pattern
must appear in the degree-2 top-K (not necessarily at position 2).

If `bars` is the literal string `"_use_helper_in_test_module_"`, the
loader calls a builder function in
`tests/elliott/test_leading_diagonal_golden.py` keyed by the fixture's
`name`. Real-chart fixtures use full bars arrays.

Acceptance: spec §5.2 requires >= 5 fixtures (>= 3 positives + 2
negatives) before merge. Synthetic seeds count toward the loader test
but not toward the acceptance bar — at least 3 of the 5 must be
real-chart fixtures.
````

- [ ] **Step 2: Land the 2 synthetic seed fixtures**

`tests/elliott/fixtures/leading_diagonal_golden/synthetic_ld_positive.json`:

```json
{
  "name": "synthetic_ld_positive",
  "ticker": "SYNTH",
  "as_of": "2026-02-20",
  "bars": "_use_helper_in_test_module_",
  "expected_top_pattern_at_degree_2": "leading_diagonal",
  "expected_runner_up_in_topk": "ending_diagonal"
}
```

`tests/elliott/fixtures/leading_diagonal_golden/synthetic_ed_negative.json`:

```json
{
  "name": "synthetic_ed_negative",
  "ticker": "SYNTH",
  "as_of": "2026-02-20",
  "bars": "_use_helper_in_test_module_",
  "expected_top_pattern_at_degree_2": "ending_diagonal"
}
```

- [ ] **Step 3: Create the loader test module**

`tests/elliott/test_leading_diagonal_golden.py`:

```python
"""Golden tests for leading_diagonal classification.

Each fixture in ``tests/elliott/fixtures/leading_diagonal_golden/*.json``
is loaded and run through ``run_analysis`` (or, for synthetic fixtures,
a builder function in this module). The test asserts the expected
top-of-top-K pattern at degree 2.

Add real-chart fixtures by dropping a JSON file in the fixtures dir
and filling in the ``bars`` array. Spec §5.2 requires >= 5 fixtures
(>= 3 positives + 2 negatives) before merge; analyst contributes
incrementally.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from wave_alpha.domain import Bar, Interval
from wave_alpha.pipeline import AnalyzeRequest, run_analysis

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "leading_diagonal_golden"


def _bars_synthetic_ld_positive() -> list[Bar]:
    """Reuse the same bar builder as the pipeline integration test."""
    from tests.test_pipeline import _build_bars_for_leading_diagonal_at_degree_2

    return _build_bars_for_leading_diagonal_at_degree_2()


def _bars_synthetic_ed_negative() -> list[Bar]:
    """Shallow-W2 5-wave window that LD should LOSE to ED.
    Pivot prices match the unit-test shallow-W2 fixture in
    tests/elliott/test_leading_diagonal.py::test_ed_outscores_ld_on_shallow_w2."""
    pivot_days_prices = [
        (0, "100"),
        (10, "120"),
        (20, "112.4"),
        (30, "128"),
        (40, "118"),
        (50, "132"),
    ]
    bars: list[Bar] = []
    for i in range(len(pivot_days_prices) - 1):
        d0, p0 = pivot_days_prices[i]
        d1, p1 = pivot_days_prices[i + 1]
        span = d1 - d0
        for k in range(span):
            day = d0 + k
            t = k / span
            price = Decimal(p0) + (Decimal(p1) - Decimal(p0)) * Decimal(t)
            bars.append(
                Bar(
                    timestamp=date(2026, 1, 1) + timedelta(days=day),
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=1000,
                    interval=Interval.DAILY,
                )
            )
    final_day, final_price = pivot_days_prices[-1]
    bars.append(
        Bar(
            timestamp=date(2026, 1, 1) + timedelta(days=final_day),
            open=Decimal(final_price),
            high=Decimal(final_price),
            low=Decimal(final_price),
            close=Decimal(final_price),
            volume=1000,
            interval=Interval.DAILY,
        )
    )
    return bars


_SYNTHETIC_BUILDERS = {
    "synthetic_ld_positive": _bars_synthetic_ld_positive,
    "synthetic_ed_negative": _bars_synthetic_ed_negative,
}


def _load_bars_from_fixture(fx: dict) -> list[Bar]:
    if fx["bars"] == "_use_helper_in_test_module_":
        builder = _SYNTHETIC_BUILDERS[fx["name"]]
        return builder()
    out: list[Bar] = []
    for b in fx["bars"]:
        out.append(
            Bar(
                timestamp=date.fromisoformat(b["timestamp"]),
                open=Decimal(b["open"]),
                high=Decimal(b["high"]),
                low=Decimal(b["low"]),
                close=Decimal(b["close"]),
                volume=int(b["volume"]),
                interval=Interval(b["interval"]),
            )
        )
    return out


def _fixture_files() -> list[Path]:
    return sorted(_FIXTURE_DIR.glob("*.json"))


@pytest.mark.parametrize("fixture_path", _fixture_files(), ids=lambda p: p.stem)
def test_leading_diagonal_golden(fixture_path: Path):
    fx = json.loads(fixture_path.read_text())
    bars = _load_bars_from_fixture(fx)
    as_of = date.fromisoformat(fx["as_of"])

    class _FakeProvider:
        def fetch(self, ticker, interval, *, end):
            return list(bars)

    req = AnalyzeRequest(ticker=fx["ticker"], as_of=as_of)
    result = run_analysis(req, provider=_FakeProvider())

    degree_2_counts = [c for c in result.counts_daily if c.degree == 2]
    if not degree_2_counts:
        pytest.fail(f"{fx['name']}: no degree-2 candidates produced")
    top = degree_2_counts[0]
    patterns_in_topk = {c.pattern for c in degree_2_counts}

    expected = fx["expected_top_pattern_at_degree_2"]
    if expected == "leading_diagonal":
        assert top.pattern == "leading_diagonal", (
            f"{fx['name']}: expected LD at top of degree-2; got {top.pattern}"
        )
    elif expected in {"ending_diagonal", "impulse"}:
        assert top.pattern != "leading_diagonal", (
            f"{fx['name']}: LD must NOT be #1 (expected {expected}); "
            f"got {top.pattern}"
        )
    elif expected == "primitive":
        assert top.pattern != "leading_diagonal", (
            f"{fx['name']}: LD must NOT be #1 - primitive negative violated"
        )
    else:
        pytest.fail(f"{fx['name']}: unrecognized expected: {expected}")

    runner_up = fx.get("expected_runner_up_in_topk")
    if runner_up is not None:
        assert runner_up in patterns_in_topk, (
            f"{fx['name']}: expected {runner_up} in top-K; got {patterns_in_topk}"
        )
```

- [ ] **Step 4: Run the golden tests**

```bash
uv run pytest tests/elliott/test_leading_diagonal_golden.py -v
```

Expected: 2 PASS (positive seed + negative seed). The test parametrizes over fixture files; once an analyst adds more JSONs, they auto-register.

- [ ] **Step 5: Commit**

```bash
git add tests/elliott/fixtures/leading_diagonal_golden/ tests/elliott/test_leading_diagonal_golden.py
git commit -m "$(cat <<'EOF'
test(elliott): leading_diagonal golden fixture loader + 2 synthetic seeds

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Bump `test_lean_install.py` template-count assertion

**Files:**
- Modify: `tests/test_lean_install.py:66`

- [ ] **Step 1: Open the file and locate the assertion**

```bash
grep -n "expected >=" tests/test_lean_install.py
```

Expected output:

```
66:            "assert len(ts) >= 6, f'expected >=6 templates, got {len(ts)}'; "
```

- [ ] **Step 2: Apply the edit**

In `tests/test_lean_install.py`, change the line:

```python
"assert len(ts) >= 6, f'expected >=6 templates, got {len(ts)}'; "
```

to:

```python
"assert len(ts) >= 8, f'expected >=8 templates, got {len(ts)}'; "
```

(Current bundled count after LD lands: `impulse`, `zigzag`, `flat`, `expanded_flat`, `contracting_triangle`, `ending_diagonal`, `wxy`, `leading_diagonal` = 8.)

- [ ] **Step 3: Run the lean-install test**

```bash
uv run pytest tests/test_lean_install.py -v
```

Expected: PASS. (This builds a wheel and installs it in a temp venv — slow, ~30s. If it fails because the wheel doesn't ship the new YAML, check that `pyproject.toml`'s `[tool.hatch.build.targets.wheel]` packages declaration still globs `src/wave_alpha/` — adding the YAML should be automatic per CLAUDE.md.)

- [ ] **Step 4: Commit**

```bash
git add tests/test_lean_install.py
git commit -m "$(cat <<'EOF'
test(lean-install): bump template-count threshold for leading_diagonal

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Layer 3 count-layer backtest sanity gate (before + after)

**Files:**
- Create: `reports/leading_diagonal_count_before.md`
- Create: `reports/leading_diagonal_count_after.md`

This is the merge gate. It must run on the **baseline** (the branch point of `main`, before any LD commits) and on the **feature branch** with all prior tasks merged. The diff between the two outputs is what determines whether the merge proceeds.

**Important:** This task is not TDD-style; it produces evidence (markdown reports) rather than code. Run it after Tasks 1–7 are committed.

- [ ] **Step 1: Identify the baseline commit**

```bash
git log --oneline -1
```

Note the current HEAD (e.g., `c8ac674`). The baseline is the **parent of the first LD commit** — typically the commit just before Task 1's commit. List recent commits to confirm:

```bash
git log --oneline -10
```

The baseline is the most recent commit on `main` that is **not** an LD task commit (e.g., the spec-only commit `c8ac674` if the LD tasks branched from there).

- [ ] **Step 2: Generate the baseline report**

Check out the baseline commit in a worktree (do not modify the current branch). If you're not already in a worktree, create one:

```bash
git worktree add ../wave_alpha-baseline c8ac674   # use actual baseline SHA
cd ../wave_alpha-baseline
uv sync --extra dev
mkdir -p reports
time uv run wave-alpha backtest count \
  --universe data/universe_curated.txt \
  --start 2020-01-01 \
  --end 2024-12-31 \
  --step 5 \
  --out reports/leading_diagonal_count_before.md
```

Record the elapsed time printed by `time` — needed for the runtime regression check in Step 4.

If you're already in a worktree, just check out the baseline SHA inline (without switching the parent worktree):

```bash
git stash --include-untracked  # if any work in progress
git checkout c8ac674
uv run wave-alpha backtest count \
  --universe data/universe_curated.txt \
  --start 2020-01-01 \
  --end 2024-12-31 \
  --step 5 \
  --out reports/leading_diagonal_count_before.md
```

**DO NOT** switch branches if you're a subagent — surface a BLOCKED status to the parent and let the parent resolve it. (Per the user's standing instruction to never switch branches from inside a subagent.)

- [ ] **Step 3: Generate the after-LD report**

Return to the LD branch (`git checkout <ld-branch>` if you stashed; or back to the worktree HEAD).

```bash
mkdir -p reports
time uv run wave-alpha backtest count \
  --universe data/universe_curated.txt \
  --start 2020-01-01 \
  --end 2024-12-31 \
  --step 5 \
  --out reports/leading_diagonal_count_after.md
```

Record the elapsed time.

- [ ] **Step 4: Diff the reports and verify acceptance thresholds**

Open both markdown files. Extract the **overall** `top1_hit_rate`, `top3_hit_rate`, and `sample_size` fields (rendered at the top of the report by `render_count_markdown`).

Apply the spec §5.3 hard-fail thresholds:

| Metric | Before | After | Δ | Threshold | Status |
|---|---|---|---|---|---|
| `top1_hit_rate` | X.XX | Y.YY | Y−X | ≥ −0.02 (abs) | PASS/FAIL |
| `top3_hit_rate` | X.XX | Y.YY | Y−X | ≥ −0.02 (abs) | PASS/FAIL |
| `sample_size` | NNN | MMM | (M−N)/N | ≥ −5% | PASS/FAIL |
| Runtime (seconds) | S | T | (T−S)/S | ≤ +10% | PASS/FAIL |

If **any** metric fails, STOP. Open the per-coherence-bucket and per-year tables in both reports. Diagnose:
- If `full`-bucket `top1_hit_rate` dropped sharply → LD is winning where the engine was previously confident (R1 from the spec).
- If a single year regressed → regime-specific misclassification.
- Tighten the soft constraints in `leading_diagonal.yaml` (e.g., lower `wave_2_not_excessive` cap from 0.9 to 0.85; add a `length(W5) <= length(W3)` soft for contracting bias) and re-run from Step 3.

Do **not** weaken the spec thresholds to make the gate pass. If after tightening the gate still fails, surface to the user — the spec's central claim does not hold on real data and may need revisiting.

- [ ] **Step 5: Commit the reports**

```bash
git add reports/leading_diagonal_count_before.md reports/leading_diagonal_count_after.md
git commit -m "$(cat <<'EOF'
chore(reports): count-layer backtest before/after leading_diagonal

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Final verification

**Files:** (none modified — verification only)

- [ ] **Step 1: Run the full test suite**

```bash
uv run pytest
```

Expected: all green. If any pre-existing test fails because `leading_diagonal` now appears in its expected results, fix the test — but only if it asserts an exact pattern list. Generic "len(counts) > 0" assertions should be unaffected.

- [ ] **Step 2: Run ruff lint**

```bash
uv run ruff check .
```

Expected: clean.

- [ ] **Step 3: Run ruff format (idempotent)**

```bash
uv run ruff format .
```

Expected: zero files reformatted.

- [ ] **Step 4: Manual analyze sanity check**

```bash
uv run wave-alpha analyze AAPL --as-of 2024-12-31 --json | python -c "import json, sys; d = json.load(sys.stdin); print([c['pattern'] for c in d.get('counts_daily', [])])"
```

Expected: command completes, JSON parses, output is a list of pattern names. `leading_diagonal` may or may not appear — depends on AAPL's daily structure on that date — but the command must not raise.

- [ ] **Step 5: Update GitNexus index**

```bash
npx gitnexus analyze
```

Per CLAUDE.md — keeps the knowledge graph fresh for impact-analysis queries in the next session.

- [ ] **Step 6: Final no-op safety-net commit**

If Steps 1–3 produced any cleanup edits:

```bash
git add -A
git commit -m "$(cat <<'EOF'
chore(elliott): post-leading_diagonal verification cleanup

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

If already clean, skip.

---

## Spec coverage check (self-review before handoff)

| Spec section | Implementation task |
|---|---|
| §2 — `leading_diagonal.yaml` ships, primitive flow handles it | Task 1 |
| §3 — Full YAML template content | Task 1 (Step 3) |
| §4 — `tests/test_lean_install.py:66` template-count bump | Task 7 |
| §4 — All other "no change" rows respected | Tasks 1–9 do not touch them; verification in Task 9 |
| §5.1 — Layer 1 unit tests | Tasks 1, 2, 3, 4 |
| §5.2 — Layer 2 golden loader + ≥ 5 fixtures (≥ 3 pos + 2 neg) | Task 6 lands loader + 2 synthetic seeds; 3 real-chart fixtures contributed before merge (analyst, separate commits) |
| §5.3 — Layer 3 count-layer backtest before/after gate | Task 8 |
| §5.4 — Layer 4 pipeline integration test | Task 5 |
| §6 R3 — `direction_alternates` verified on expanding diagonals | Implicit in Task 3 (deep-W2 LD with W3 < 1.618*W1 covers contracting; analyst-added fixtures cover expanding) |
| §8 — Acceptance checklist (all 9 items) | Tasks 1–9; final boxes ticked in Task 9 |
