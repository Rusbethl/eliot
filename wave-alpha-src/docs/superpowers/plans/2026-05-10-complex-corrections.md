# Complex corrections (W-X-Y) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the W-X-Y double-three corrective template to the Elliott engine with strict sub-count enforcement at degree N−1, scoped to the new template only. Primitive templates remain flat. Targets count quality in sideways markets, not new trade signals.

**Architecture:** A new `wxy.yaml` template ships alongside existing primitives. `enumerate_counts` gains an optional `sub_pivots` parameter. When fitting a composite template (gated by `_COMPOSITE_TEMPLATE_NAMES`), the enumerator slices `sub_pivots` between the parent segment's start/end pivots (matched by `(timestamp, type)` via the existing `multi_degree` subset invariant) and recursively calls `enumerate_counts` restricted to `PRIMITIVE_CORRECTIVES`. Sub-counts populate `WaveCount.sub_counts`; sub-count `soft_violations` propagate to the parent with a `<label>:` prefix. Composite candidates compete in the same top-K as primitives.

**Tech Stack:** Python 3.11+, `uv`, Pydantic v2, `decimal.Decimal` for prices, `pytest`, `ruff`, YAML templates loaded by `PatternTemplate.from_yaml_file`, no new dependencies.

**Spec:** [`docs/superpowers/specs/2026-05-10-complex-corrections-design.md`](../specs/2026-05-10-complex-corrections-design.md)

---

## File map

**Created:**
- `src/wave_alpha/elliott/templates_data/wxy.yaml` — the new template
- `tests/elliott/test_wxy_template.py` — template loads, validates, allowlist correct
- `tests/elliott/test_wxy_enumeration.py` — Layer 1 unit tests; contains a fixture-builder used across the file
- `tests/elliott/fixtures/wxy_golden/` — Layer 2 golden fixtures (JSON)
- `tests/elliott/fixtures/wxy_golden/README.md` — documents fixture format
- `tests/elliott/test_wxy_golden.py` — Layer 2 golden test loader

**Modified:**
- `src/wave_alpha/elliott/enumerator.py` — add `sub_pivots` param, `_COMPOSITE_TEMPLATE_NAMES` and `_PRIMITIVE_CORRECTIVE_NAMES` constants, `_slice_sub_pivots` and `_fit_sub_counts` helpers, composite branch in main loop
- `src/wave_alpha/pipeline.py` — `_enumerate_for` passes `sub_pivots=by_degree.get(degree-1)`
- `tests/test_pipeline.py` — Layer 3 integration test asserting wxy emerges through the full pipeline

**Not modified (intentionally):**
- `src/wave_alpha/elliott/templates.py` — `allowed_sub_patterns` field already present
- `src/wave_alpha/elliott/validator.py` — sub-count enforcement lives in enumerator, not validator
- `src/wave_alpha/elliott/dsl.py` — no new operators
- `src/wave_alpha/elliott/models.py` — `sub_counts` field already present
- `src/wave_alpha/elliott/fib_features.py` — `wxy` falls through to legacy single-ratio path
- `src/wave_alpha/right_edge/assessment.py`, `right_edge/training.py`, `backtest/count_layer.py` — call `enumerate_counts` without `sub_pivots`; composite branch is silently skipped (graceful degrade)

---

### Task 1: Ship the wxy.yaml template + load smoke test

**Files:**
- Create: `src/wave_alpha/elliott/templates_data/wxy.yaml`
- Create: `tests/elliott/test_wxy_template.py`

- [ ] **Step 1: Write the failing test**

`tests/elliott/test_wxy_template.py`:

```python
from wave_alpha.elliott.templates import PatternTemplate, load_bundled_templates


def test_wxy_template_loads_from_bundled_dir():
    tpl = PatternTemplate.load_bundled("wxy")
    assert tpl.name == "wxy"
    assert tpl.waves == ["W", "X", "Y"]
    assert tpl.direction_alternates is True


def test_wxy_template_appears_in_load_bundled_templates():
    names = {t.name for t in load_bundled_templates()}
    assert "wxy" in names


def test_wxy_template_has_expected_constraints():
    tpl = PatternTemplate.load_bundled("wxy")
    names = {c.name for c in tpl.constraints}
    assert names == {
        "x_shorter_than_w",
        "x_shorter_than_y",
        "y_at_least_0618_w",
        "y_at_most_1618_w",
        "x_max_retrace_of_w",
    }
    severities = {c.name: c.severity for c in tpl.constraints}
    assert severities["x_shorter_than_w"] == "hard"
    assert severities["x_shorter_than_y"] == "hard"
    assert severities["y_at_least_0618_w"] == "soft"
    assert severities["y_at_most_1618_w"] == "soft"
    assert severities["x_max_retrace_of_w"] == "soft"


def test_wxy_template_has_corrective_sub_pattern_allowlist():
    tpl = PatternTemplate.load_bundled("wxy")
    expected = ["zigzag", "flat", "expanded_flat", "contracting_triangle"]
    assert tpl.allowed_sub_patterns["W"] == expected
    assert tpl.allowed_sub_patterns["X"] == expected
    assert tpl.allowed_sub_patterns["Y"] == expected
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/elliott/test_wxy_template.py -v
```

Expected: FAIL — file `wxy.yaml` not found.

- [ ] **Step 3: Create the YAML template**

`src/wave_alpha/elliott/templates_data/wxy.yaml`:

```yaml
name: wxy
direction_alternates: true
waves: ["W", "X", "Y"]
constraints:
  - name: x_shorter_than_w
    severity: hard
    expr: "length(WX) < length(WW)"
    rationale: "Connector X is shorter than the corrective W it joins."

  - name: x_shorter_than_y
    severity: hard
    expr: "length(WX) < length(WY)"
    rationale: "Connector X is shorter than the corrective Y it joins."

  - name: y_at_least_0618_w
    severity: soft
    expr: "length(WY) >= Decimal('0.618') * length(WW)"
    rationale: "Y typically reaches at least 0.618x W."

  - name: y_at_most_1618_w
    severity: soft
    expr: "length(WY) <= Decimal('1.618') * length(WW)"
    rationale: "Y rarely extends more than 1.618x W; outliers are usually misclassified."

  - name: x_max_retrace_of_w
    severity: soft
    expr: "retrace(WX, WW) <= Decimal('0.786')"
    rationale: "X usually retraces W partially, not fully."

allowed_sub_patterns:
  "W": ["zigzag", "flat", "expanded_flat", "contracting_triangle"]
  "X": ["zigzag", "flat", "expanded_flat", "contracting_triangle"]
  "Y": ["zigzag", "flat", "expanded_flat", "contracting_triangle"]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/elliott/test_wxy_template.py -v
```

Expected: 4 PASS.

- [ ] **Step 5: Run full pytest to ensure no existing test regresses**

```bash
uv run pytest -x
```

Expected: all green. The new template now appears in `load_bundled_templates()`; any test that asserts an exact template count or list will need adjusting — search for `load_bundled_templates` in tests and assert counts. If a test fails on count, fix it (the new template is real and bundled). Re-run.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/elliott/templates_data/wxy.yaml tests/elliott/test_wxy_template.py
git commit -m "feat(elliott): add wxy template (W-X-Y double-three corrective)"
```

---

### Task 2: Add `sub_pivots` parameter and `_COMPOSITE_TEMPLATE_NAMES` (graceful degrade path)

**Files:**
- Modify: `src/wave_alpha/elliott/enumerator.py:18-59`
- Create: `tests/elliott/test_wxy_enumeration.py`

This task wires the new parameter and the composite-name gate so that **without** `sub_pivots` the wxy template is silently skipped (graceful degrade), preserving every existing call site. No sub-count fitting yet — that arrives in later tasks.

- [ ] **Step 1: Write the failing test**

`tests/elliott/test_wxy_enumeration.py` (new file — fixture-builder shared with later tasks):

```python
from datetime import date, timedelta
from decimal import Decimal

from wave_alpha.domain import Interval
from wave_alpha.elliott.enumerator import enumerate_counts
from wave_alpha.pivots.models import PivotEvent, PivotState, PivotType


def _pivot(day: int, price: str, kind: PivotType, *, degree: int) -> PivotEvent:
    """Build a PivotEvent at day 2026-01-01 + day with the given price."""
    return PivotEvent(
        interval=Interval.DAILY,
        degree=degree,
        timestamp=date(2026, 1, 1) + timedelta(days=day),
        price=Decimal(price),
        type=kind,
        state=PivotState.CONFIRMED,
        confirmed_at=None,
    )


# Reusable parent + sub-pivot fixture used across many tests in this file.
# Parent (degree=2): four pivots forming a W-X-Y outline.
#   W: 100 (H) -> 80 (L)        downward corrective
#   X: 80 (L)  -> 85 (H)        connector up
#   Y: 85 (H)  -> 65 (L)        downward corrective
# Sub (degree=1): subset invariant — every parent pivot also appears in sub_pivots.
#   W subdivides as a zigzag: 100(H) -> 90(L) -> 95(H) -> 80(L)
#   X is a single segment (degenerate): 80(L) -> 85(H)
#   Y subdivides as a zigzag: 85(H) -> 75(L) -> 80(H) -> 65(L)
def _wxy_zigzag_zigzag_fixture() -> tuple[list[PivotEvent], list[PivotEvent]]:
    parent = [
        _pivot(0, "100", PivotType.HIGH, degree=2),
        _pivot(3, "80", PivotType.LOW, degree=2),
        _pivot(4, "85", PivotType.HIGH, degree=2),
        _pivot(7, "65", PivotType.LOW, degree=2),
    ]
    sub = [
        _pivot(0, "100", PivotType.HIGH, degree=1),
        _pivot(1, "90", PivotType.LOW, degree=1),
        _pivot(2, "95", PivotType.HIGH, degree=1),
        _pivot(3, "80", PivotType.LOW, degree=1),
        _pivot(4, "85", PivotType.HIGH, degree=1),
        _pivot(5, "75", PivotType.LOW, degree=1),
        _pivot(6, "80", PivotType.HIGH, degree=1),
        _pivot(7, "65", PivotType.LOW, degree=1),
    ]
    return parent, sub


def test_wxy_silently_skipped_when_sub_pivots_none():
    """Composite candidates must be skipped (not raised) for callers that
    don't supply lower-degree pivots. Preserves backward compat for
    right_edge.assessment, right_edge.training, backtest.count_layer, and
    every test that builds pivots by hand."""
    parent, _sub = _wxy_zigzag_zigzag_fixture()
    counts = enumerate_counts(parent, degree=2, sub_pivots=None, top_k=5)
    assert all(c.pattern != "wxy" for c in counts), (
        "wxy must not appear when sub_pivots is None"
    )


def test_existing_callers_unchanged_without_kwarg():
    """Existing positional callers must continue to work — the new
    sub_pivots arg is keyword-only with a default of None."""
    parent, _sub = _wxy_zigzag_zigzag_fixture()
    counts = enumerate_counts(parent, degree=2, top_k=5)
    assert isinstance(counts, list)
    assert all(c.pattern != "wxy" for c in counts)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/elliott/test_wxy_enumeration.py -v
```

Expected: FAIL — `enumerate_counts()` does not accept `sub_pivots` keyword argument.

- [ ] **Step 3: Modify `enumerator.py` — add the parameter and composite-name gate**

Edit `src/wave_alpha/elliott/enumerator.py`. Add the two module-level constants near the existing weight constants and update the `enumerate_counts` signature + main loop:

```python
# Composite templates require sub_pivots and recurse into degree N-1 to fit
# primitive corrective sub-counts. See spec 2026-05-10-complex-corrections-design.md §3.
_COMPOSITE_TEMPLATE_NAMES: frozenset[str] = frozenset({"wxy"})

# Primitive corrective templates eligible to fill a composite slot (W, X, Y).
# Excludes impulse and ending_diagonal (motive structures, semantically wrong
# inside a corrective combination).
_PRIMITIVE_CORRECTIVE_NAMES: frozenset[str] = frozenset(
    {"zigzag", "flat", "expanded_flat", "contracting_triangle"}
)


def enumerate_counts(
    pivots: list[PivotEvent],
    *,
    degree: int,
    top_k: int = 5,
    templates: list[PatternTemplate] | None = None,
    sub_pivots: list[PivotEvent] | None = None,
) -> list[WaveCount]:
    """Try each template against pivot windows; return top_k by score.

    A swing-trader-friendly ranker: each candidate's score blends template fit,
    fib confluence at W2's retrace, and recency (how close the candidate's last
    pivot is to the latest input pivot). Multiple windows of the same template
    are deduplicated to the highest-scoring one before the global top_k cut, so
    one template family cannot crowd out alternates.

    Composite templates (those whose name is in _COMPOSITE_TEMPLATE_NAMES) are
    skipped when ``sub_pivots`` is None — graceful degrade for callers that
    operate on a single degree (right_edge feature extractor, count-layer
    backtest, hand-built test fixtures).
    """
    if not pivots:
        return []
    templates = templates or load_bundled_templates()
    latest_idx = len(pivots) - 1

    best_per_template: dict[str, WaveCount] = {}
    for tpl in templates:
        if tpl.name in _COMPOSITE_TEMPLATE_NAMES and sub_pivots is None:
            continue  # composite needs lower-degree pivots; nothing to do here
        if tpl.name in _COMPOSITE_TEMPLATE_NAMES:
            continue  # full composite branch lands in Task 6; for now, skip
        n_required = len(tpl.waves) + 1  # waves -> n segments need n+1 pivots
        if len(pivots) < n_required:
            continue
        for end_idx in range(len(pivots), n_required - 1, -1):
            window = pivots[end_idx - n_required : end_idx]
            if not _alternates_ok(window):
                continue
            count = WaveCount(pattern=tpl.name, degree=degree, pivots=window)
            count = validate(count, tpl)
            if count.hard_violations:
                continue
            score = _score(count, tpl, last_window_idx=end_idx - 1, latest_idx=latest_idx)
            count = count.model_copy(update={"score": score})

            current = best_per_template.get(tpl.name)
            if current is None or score > current.score:
                best_per_template[tpl.name] = count

    candidates = sorted(best_per_template.values(), key=lambda c: c.score, reverse=True)
    return candidates[:top_k]
```

The double-`continue` for composites is intentional and temporary: it keeps Task 2 minimal. The full composite branch replaces the inner `continue` in Task 6.

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/elliott/test_wxy_enumeration.py -v
```

Expected: 2 PASS.

- [ ] **Step 5: Run full pytest to confirm no regressions**

```bash
uv run pytest -x
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/elliott/enumerator.py tests/elliott/test_wxy_enumeration.py
git commit -m "feat(elliott): wire sub_pivots arg and composite-name gate"
```

---

### Task 3: Add `_slice_sub_pivots` helper

**Files:**
- Modify: `src/wave_alpha/elliott/enumerator.py`
- Modify: `tests/elliott/test_wxy_enumeration.py`

Helper that, given a `sub_pivots` list and a parent segment's start/end pivots, returns the inclusive slice between them by `(timestamp, type)` matching. Returns `None` if either anchor is missing (corrupted pipeline state — should not occur given the subset invariant).

- [ ] **Step 1: Write the failing test**

Append to `tests/elliott/test_wxy_enumeration.py`:

```python
from wave_alpha.elliott.enumerator import _slice_sub_pivots


def test_slice_sub_pivots_inclusive_slice_full_y():
    parent, sub = _wxy_zigzag_zigzag_fixture()
    # Parent Y segment is parent[2] -> parent[3] (day 4 -> day 7)
    seg_start, seg_end = parent[2], parent[3]
    result = _slice_sub_pivots(sub, seg_start, seg_end)
    assert result is not None
    # Y subdivides as 4 sub-pivots at degree=1: 85(H), 75(L), 80(H), 65(L)
    assert [p.timestamp for p in result] == [
        date(2026, 1, 1) + timedelta(days=d) for d in (4, 5, 6, 7)
    ]
    assert result[0].timestamp == seg_start.timestamp
    assert result[-1].timestamp == seg_end.timestamp


def test_slice_sub_pivots_returns_short_slice_for_degenerate_x():
    parent, sub = _wxy_zigzag_zigzag_fixture()
    # Parent X segment is parent[1] -> parent[2] (day 3 -> day 4)
    seg_start, seg_end = parent[1], parent[2]
    result = _slice_sub_pivots(sub, seg_start, seg_end)
    assert result is not None
    # X has no intermediate sub-pivots in the fixture: just [80(L), 85(H)]
    assert len(result) == 2


def test_slice_sub_pivots_returns_none_when_anchor_missing():
    parent, sub = _wxy_zigzag_zigzag_fixture()
    # Build a fake parent pivot that is NOT in sub_pivots
    bogus = _pivot(99, "50", PivotType.LOW, degree=2)
    result = _slice_sub_pivots(sub, parent[0], bogus)
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/elliott/test_wxy_enumeration.py::test_slice_sub_pivots_inclusive_slice_full_y -v
```

Expected: FAIL — `_slice_sub_pivots` not defined.

- [ ] **Step 3: Add the helper to `enumerator.py`**

Append at the bottom of `src/wave_alpha/elliott/enumerator.py`:

```python
def _slice_sub_pivots(
    sub_pivots: list[PivotEvent],
    seg_start: PivotEvent,
    seg_end: PivotEvent,
) -> list[PivotEvent] | None:
    """Return the inclusive slice of ``sub_pivots`` between two parent
    boundary pivots, matched by ``(timestamp, type)``.

    The multi_degree subset invariant guarantees that every degree-N parent
    pivot is also present in degree-(N-1) sub_pivots with the same
    ``(timestamp, type)``. A None return indicates a corrupted pipeline state
    (e.g. caller passed sub_pivots from a different bar set); the parent
    composite candidate should be rejected.
    """
    start_key = (seg_start.timestamp, seg_start.type)
    end_key = (seg_end.timestamp, seg_end.type)
    start_idx: int | None = None
    end_idx: int | None = None
    for i, p in enumerate(sub_pivots):
        key = (p.timestamp, p.type)
        if key == start_key and start_idx is None:
            start_idx = i
        elif key == end_key and start_idx is not None:
            end_idx = i
            break
    if start_idx is None or end_idx is None:
        return None
    return sub_pivots[start_idx : end_idx + 1]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/elliott/test_wxy_enumeration.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/elliott/enumerator.py tests/elliott/test_wxy_enumeration.py
git commit -m "feat(elliott): add _slice_sub_pivots helper for composite enumeration"
```

---

### Task 4: Add `_fit_sub_counts` (strict path, no degeneracy yet)

**Files:**
- Modify: `src/wave_alpha/elliott/enumerator.py`
- Modify: `tests/elliott/test_wxy_enumeration.py`

This task implements strict sub-count fitting for slots with ≥ 4 sub-pivots. The degeneracy relaxation (X-wave with < 4 sub-pivots) lands in Task 5.

- [ ] **Step 1: Write the failing test**

Append to `tests/elliott/test_wxy_enumeration.py`:

```python
from wave_alpha.elliott.enumerator import _fit_sub_counts
from wave_alpha.elliott.models import WaveCount
from wave_alpha.elliott.templates import PatternTemplate


def test_fit_sub_counts_finds_zigzag_in_w_slot():
    parent, sub = _wxy_zigzag_zigzag_fixture()
    tpl = PatternTemplate.load_bundled("wxy")
    parent_count = WaveCount(pattern="wxy", degree=2, pivots=parent)

    sub_counts, sub_softs = _fit_sub_counts(
        parent_count=parent_count,
        tpl=tpl,
        sub_pivots=sub,
    )
    # W slot must produce a zigzag sub-count
    assert sub_counts is not None  # not rejected
    assert "W" in sub_counts
    assert sub_counts["W"] is not None
    assert sub_counts["W"].pattern == "zigzag"
    # Y slot must produce a zigzag sub-count
    assert sub_counts["Y"] is not None
    assert sub_counts["Y"].pattern == "zigzag"
    # Sub-count pivots must span the full slot (boundary alignment)
    assert sub_counts["W"].pivots[0].timestamp == parent[0].timestamp
    assert sub_counts["W"].pivots[-1].timestamp == parent[1].timestamp


def test_fit_sub_counts_rejects_when_w_slot_has_no_valid_primitive():
    """If the W slot's sub-window contains 4+ pivots but none of the
    primitive correctives validates cleanly, the parent must be rejected."""
    # Build a pathological fixture: W slot has 4 sub-pivots that don't form
    # a valid corrective (e.g., all going the same direction — impossible to
    # alternate). Use direction-monotone sub-pivots that violate _alternates_ok
    # inside the recursive call, so no primitive validates.
    parent = [
        _pivot(0, "100", PivotType.HIGH, degree=2),
        _pivot(3, "80", PivotType.LOW, degree=2),
        _pivot(4, "85", PivotType.HIGH, degree=2),
        _pivot(7, "65", PivotType.LOW, degree=2),
    ]
    # W has 4 sub-pivots, but not alternating — no primitive will validate.
    # We construct this by reusing degree-2 pivots in the W range with all
    # the same type; since PivotEvent is frozen we just hand-build them.
    sub = [
        _pivot(0, "100", PivotType.HIGH, degree=1),
        _pivot(1, "99", PivotType.HIGH, degree=1),  # not alternating (H, H)
        _pivot(2, "98", PivotType.HIGH, degree=1),  # not alternating
        _pivot(3, "80", PivotType.LOW, degree=1),
        _pivot(4, "85", PivotType.HIGH, degree=1),
        _pivot(5, "75", PivotType.LOW, degree=1),
        _pivot(6, "80", PivotType.HIGH, degree=1),
        _pivot(7, "65", PivotType.LOW, degree=1),
    ]
    tpl = PatternTemplate.load_bundled("wxy")
    parent_count = WaveCount(pattern="wxy", degree=2, pivots=parent)

    sub_counts, _sub_softs = _fit_sub_counts(
        parent_count=parent_count, tpl=tpl, sub_pivots=sub
    )
    assert sub_counts is None  # parent must be rejected
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/elliott/test_wxy_enumeration.py::test_fit_sub_counts_finds_zigzag_in_w_slot -v
```

Expected: FAIL — `_fit_sub_counts` not defined.

- [ ] **Step 3: Add the helper to `enumerator.py`**

Append at the bottom of `src/wave_alpha/elliott/enumerator.py`:

```python
def _fit_sub_counts(
    parent_count: WaveCount,
    tpl: PatternTemplate,
    sub_pivots: list[PivotEvent],
) -> tuple[dict[str, WaveCount | None] | None, list[str]]:
    """Fit a primitive corrective sub-count into each label slot of a
    composite template.

    Returns ``(sub_counts, prefixed_soft_violations)``. A None first element
    signals rejection (a slot's sub-window failed to produce a valid primitive
    sub-count, or boundary alignment failed).

    Strict path only — does not yet handle degenerate slots (sub-window of
    < 4 pivots). Task 5 adds the degeneracy relaxation.
    """
    # Lazy import to avoid circularity if enumerator is imported before
    # bundled templates are needed elsewhere.
    primitive_templates = [
        t for t in load_bundled_templates() if t.name in _PRIMITIVE_CORRECTIVE_NAMES
    ]

    sub_counts: dict[str, WaveCount | None] = {}
    prefixed_softs: list[str] = []
    for label, seg in zip(tpl.waves, parent_count.segments(), strict=True):
        sub_window = _slice_sub_pivots(sub_pivots, seg.start, seg.end)
        if sub_window is None:
            return None, []  # corrupted pipeline state — reject
        if len(sub_window) < 4:
            # Degeneracy path — handled in Task 5. For now reject.
            return None, []
        candidates = enumerate_counts(
            sub_window,
            degree=parent_count.degree - 1,
            templates=primitive_templates,
            top_k=1,
            sub_pivots=None,  # one level deep only
        )
        # Filter to candidates that span the full slot.
        spanning = [
            c
            for c in candidates
            if c.pivots[0].timestamp == seg.start.timestamp
            and c.pivots[0].type == seg.start.type
            and c.pivots[-1].timestamp == seg.end.timestamp
            and c.pivots[-1].type == seg.end.type
        ]
        if not spanning:
            return None, []
        chosen = spanning[0]
        allowed = tpl.allowed_sub_patterns.get(label, [])
        if chosen.pattern not in allowed:
            return None, []
        sub_counts[label] = chosen
        for soft in chosen.soft_violations:
            prefixed_softs.append(f"{label}:{soft}")
    return sub_counts, prefixed_softs
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/elliott/test_wxy_enumeration.py -v
```

Expected: all PASS. The strict-fitting tests pass; the rejection test also passes.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/elliott/enumerator.py tests/elliott/test_wxy_enumeration.py
git commit -m "feat(elliott): add _fit_sub_counts strict path"
```

---

### Task 5: Degeneracy relaxation for slots with < 4 sub-pivots

**Files:**
- Modify: `src/wave_alpha/elliott/enumerator.py:_fit_sub_counts`
- Modify: `tests/elliott/test_wxy_enumeration.py`

Replace the "reject on degeneracy" branch with: accept the slot as `sub_counts[label] = None`, append `<label>:no_subdivision` to soft violations.

- [ ] **Step 1: Write the failing test**

Append to `tests/elliott/test_wxy_enumeration.py`:

```python
def test_fit_sub_counts_x_degenerate_accepted_with_soft_violation():
    """When X has only 2 sub-pivots (no internal subdivision), the slot is
    accepted with sub_counts['X'] = None and 'X:no_subdivision' added to
    propagated soft violations. W and Y still must validate strictly."""
    parent, sub = _wxy_zigzag_zigzag_fixture()
    tpl = PatternTemplate.load_bundled("wxy")
    parent_count = WaveCount(pattern="wxy", degree=2, pivots=parent)

    sub_counts, sub_softs = _fit_sub_counts(
        parent_count=parent_count, tpl=tpl, sub_pivots=sub
    )
    assert sub_counts is not None
    # X is degenerate (2 sub-pivots: day 3 -> day 4)
    assert "X" in sub_counts
    assert sub_counts["X"] is None
    assert "X:no_subdivision" in sub_softs
    # W and Y still resolved strictly
    assert sub_counts["W"] is not None and sub_counts["W"].pattern == "zigzag"
    assert sub_counts["Y"] is not None and sub_counts["Y"].pattern == "zigzag"


def test_fit_sub_counts_strict_when_slot_has_4plus_pivots_but_no_primitive():
    """Degeneracy relaxation applies only to short sub-windows. A slot with
    >= 4 sub-pivots that fits no primitive must still reject."""
    # Reuse the rejection fixture from Task 4 — W has 4 non-alternating pivots.
    parent = [
        _pivot(0, "100", PivotType.HIGH, degree=2),
        _pivot(3, "80", PivotType.LOW, degree=2),
        _pivot(4, "85", PivotType.HIGH, degree=2),
        _pivot(7, "65", PivotType.LOW, degree=2),
    ]
    sub = [
        _pivot(0, "100", PivotType.HIGH, degree=1),
        _pivot(1, "99", PivotType.HIGH, degree=1),
        _pivot(2, "98", PivotType.HIGH, degree=1),
        _pivot(3, "80", PivotType.LOW, degree=1),
        _pivot(4, "85", PivotType.HIGH, degree=1),
        _pivot(5, "75", PivotType.LOW, degree=1),
        _pivot(6, "80", PivotType.HIGH, degree=1),
        _pivot(7, "65", PivotType.LOW, degree=1),
    ]
    tpl = PatternTemplate.load_bundled("wxy")
    parent_count = WaveCount(pattern="wxy", degree=2, pivots=parent)

    sub_counts, _sub_softs = _fit_sub_counts(
        parent_count=parent_count, tpl=tpl, sub_pivots=sub
    )
    assert sub_counts is None  # still rejected — strictness preserved on full slots
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/elliott/test_wxy_enumeration.py::test_fit_sub_counts_x_degenerate_accepted_with_soft_violation -v
```

Expected: FAIL — current implementation rejects degenerate slots.

- [ ] **Step 3: Modify `_fit_sub_counts` to accept degeneracy**

In `src/wave_alpha/elliott/enumerator.py`, replace the degenerate-rejection branch:

```python
        if len(sub_window) < 4:
            # Degeneracy path — handled in Task 5. For now reject.
            return None, []
```

with:

```python
        if len(sub_window) < 4:
            # Degenerate slot: no primitive corrective fits in fewer than 4
            # sub-pivots (3 segments). Common for the connector X-wave.
            # Accept the slot with sub_counts[label] = None and emit a
            # synthetic soft violation. Spec §3.3 step 3.
            sub_counts[label] = None
            prefixed_softs.append(f"{label}:no_subdivision")
            continue
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/elliott/test_wxy_enumeration.py -v
```

Expected: all PASS, including the new test and the strict-rejection regression guard.

- [ ] **Step 5: Commit**

```bash
git add src/wave_alpha/elliott/enumerator.py tests/elliott/test_wxy_enumeration.py
git commit -m "feat(elliott): degeneracy relaxation for slots with <4 sub-pivots"
```

---

### Task 6: Wire composite branch end-to-end in `enumerate_counts`

**Files:**
- Modify: `src/wave_alpha/elliott/enumerator.py:enumerate_counts`
- Modify: `tests/elliott/test_wxy_enumeration.py`

Replace the placeholder `continue` for composite templates from Task 2 with the real composite branch: iterate windows, validate parent constraints, fit sub-counts, attach to the `WaveCount`, score, dedupe.

- [ ] **Step 1: Write the failing test**

Append to `tests/elliott/test_wxy_enumeration.py`:

```python
def test_wxy_emitted_with_populated_subcounts_when_sub_pivots_provided():
    parent, sub = _wxy_zigzag_zigzag_fixture()
    counts = enumerate_counts(parent, degree=2, sub_pivots=sub, top_k=10)
    wxys = [c for c in counts if c.pattern == "wxy"]
    assert len(wxys) == 1
    wxy = wxys[0]
    assert wxy.degree == 2
    # W and Y populated with zigzags; X is degenerate (no_subdivision)
    assert wxy.sub_counts["W"] is not None
    assert wxy.sub_counts["W"].pattern == "zigzag"
    assert wxy.sub_counts["W"].degree == 1
    assert wxy.sub_counts.get("X") is None
    assert wxy.sub_counts["Y"] is not None
    assert wxy.sub_counts["Y"].pattern == "zigzag"
    assert wxy.sub_counts["Y"].degree == 1


def test_wxy_competes_in_topk_with_primitives():
    """Composite candidates must appear in the same top-K list as primitives —
    no parallel field, no automatic discount. Spec §3.5."""
    parent, sub = _wxy_zigzag_zigzag_fixture()
    counts = enumerate_counts(parent, degree=2, sub_pivots=sub, top_k=10)
    pattern_set = {c.pattern for c in counts}
    # The 4-pivot parent could match a zigzag (3 segments) too; both are valid alternates.
    assert "wxy" in pattern_set
    # No duplicate wxy entries (per-template dedupe).
    assert sum(1 for c in counts if c.pattern == "wxy") == 1


def test_wxy_no_self_recursion_one_level_deep_only():
    """Sub-counts emitted for W/X/Y must be primitive correctives — never
    another wxy. Verified via _PRIMITIVE_CORRECTIVE_NAMES restriction."""
    from wave_alpha.elliott.enumerator import _PRIMITIVE_CORRECTIVE_NAMES

    parent, sub = _wxy_zigzag_zigzag_fixture()
    counts = enumerate_counts(parent, degree=2, sub_pivots=sub, top_k=10)
    wxy = next(c for c in counts if c.pattern == "wxy")
    for label, sub_count in wxy.sub_counts.items():
        if sub_count is not None:
            assert sub_count.pattern in _PRIMITIVE_CORRECTIVE_NAMES, label
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/elliott/test_wxy_enumeration.py::test_wxy_emitted_with_populated_subcounts_when_sub_pivots_provided -v
```

Expected: FAIL — composite branch still skips the template.

- [ ] **Step 3: Replace the placeholder composite skip with the real branch**

In `src/wave_alpha/elliott/enumerator.py:enumerate_counts`, find the composite skip block from Task 2:

```python
        if tpl.name in _COMPOSITE_TEMPLATE_NAMES and sub_pivots is None:
            continue  # composite needs lower-degree pivots; nothing to do here
        if tpl.name in _COMPOSITE_TEMPLATE_NAMES:
            continue  # full composite branch lands in Task 6; for now, skip
        n_required = len(tpl.waves) + 1  # waves -> n segments need n+1 pivots
```

Replace with:

```python
        if tpl.name in _COMPOSITE_TEMPLATE_NAMES and sub_pivots is None:
            continue  # composite needs lower-degree pivots; graceful degrade
        n_required = len(tpl.waves) + 1  # waves -> n segments need n+1 pivots
```

Then, immediately after the existing primitive scoring loop body, before the `current = best_per_template.get(tpl.name)` line, add a composite-only step that fits sub-counts and merges propagated soft violations. The full inner loop becomes:

```python
        if len(pivots) < n_required:
            continue
        for end_idx in range(len(pivots), n_required - 1, -1):
            window = pivots[end_idx - n_required : end_idx]
            if not _alternates_ok(window):
                continue
            count = WaveCount(pattern=tpl.name, degree=degree, pivots=window)
            count = validate(count, tpl)
            if count.hard_violations:
                continue
            if tpl.name in _COMPOSITE_TEMPLATE_NAMES:
                assert sub_pivots is not None  # gated above
                sub_counts, sub_softs = _fit_sub_counts(
                    parent_count=count, tpl=tpl, sub_pivots=sub_pivots
                )
                if sub_counts is None:
                    continue  # sub-count fitting rejected the parent
                count = count.model_copy(
                    update={
                        "sub_counts": {k: v for k, v in sub_counts.items() if v is not None},
                        "soft_violations": count.soft_violations + sub_softs,
                    }
                )
            score = _score(count, tpl, last_window_idx=end_idx - 1, latest_idx=latest_idx)
            count = count.model_copy(update={"score": score})

            current = best_per_template.get(tpl.name)
            if current is None or score > current.score:
                best_per_template[tpl.name] = count
```

Note the `{k: v for k, v in sub_counts.items() if v is not None}` filter: `WaveCount.sub_counts` is typed `dict[str, WaveCount]`, so `None` placeholders for degenerate slots cannot be stored there. The fact that a slot is degenerate is recorded entirely via the `<label>:no_subdivision` soft violation.

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/elliott/test_wxy_enumeration.py -v
```

Expected: all PASS.

- [ ] **Step 5: Run full pytest**

```bash
uv run pytest -x
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/wave_alpha/elliott/enumerator.py tests/elliott/test_wxy_enumeration.py
git commit -m "feat(elliott): wire composite branch in enumerate_counts"
```

---

### Task 7: Sub-count violation propagation explicit test + score impact assertion

**Files:**
- Modify: `tests/elliott/test_wxy_enumeration.py`

Sub-count soft violations already propagate (the `_fit_sub_counts` helper builds `prefixed_softs`, and Task 6 merges them into the parent). This task adds an explicit test that the score impact matches the existing `0.50 * (1 - 0.10 * len(soft_violations))` formula.

- [ ] **Step 1: Write the failing test**

Append to `tests/elliott/test_wxy_enumeration.py`:

```python
def test_subcount_soft_violations_propagate_to_parent_with_label_prefix():
    """A sub-zigzag with soft violations causes the parent's
    soft_violations to contain '<label>:<name>' entries. The parent's score
    drops by 0.10 * 0.50 = 0.05 per propagated violation, matching the
    existing _score formula (base weight 0.50, soft penalty 0.10/violation)."""
    parent, sub = _wxy_zigzag_zigzag_fixture()
    counts = enumerate_counts(parent, degree=2, sub_pivots=sub, top_k=10)
    wxy = next(c for c in counts if c.pattern == "wxy")

    # Inspect propagated softs: each must be either '<label>:<orig_name>' or
    # 'X:no_subdivision' (the synthetic degeneracy soft).
    propagated = [s for s in wxy.soft_violations if ":" in s]
    assert propagated, "expected propagated sub-count soft violations"
    for entry in propagated:
        label, _, name = entry.partition(":")
        assert label in {"W", "X", "Y"}
        assert name  # non-empty


def test_score_decrement_matches_soft_violation_count():
    """Direct assertion that the parent's base score is
    1 - 0.10 * len(soft_violations) — no special-casing for propagated softs."""
    parent, sub = _wxy_zigzag_zigzag_fixture()
    counts = enumerate_counts(parent, degree=2, sub_pivots=sub, top_k=10)
    wxy = next(c for c in counts if c.pattern == "wxy")

    n_softs = len(wxy.soft_violations)
    expected_base = max(0.0, 1.0 - 0.10 * n_softs)
    # _score = 0.50 * base + 0.20 * confluence + 0.30 * recency
    # We can't pin confluence/recency exactly without re-running the helpers,
    # but score must be no greater than 0.50 * 1.0 + 0.20 * 1.0 + 0.30 * 1.0 == 1.0
    # and the base contribution is bounded by 0.50 * expected_base.
    assert wxy.score <= 0.50 * expected_base + 0.20 + 0.30 + 1e-9
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/elliott/test_wxy_enumeration.py::test_subcount_soft_violations_propagate_to_parent_with_label_prefix \
              tests/elliott/test_wxy_enumeration.py::test_score_decrement_matches_soft_violation_count -v
```

Expected: PASS — propagation is already implemented; this task just locks the contract with explicit tests.

- [ ] **Step 3: Commit**

```bash
git add tests/elliott/test_wxy_enumeration.py
git commit -m "test(elliott): assert wxy soft-violation propagation contract"
```

---

### Task 8: Pipeline wiring + Layer 3 integration test

**Files:**
- Modify: `src/wave_alpha/pipeline.py:_enumerate_for`
- Modify: `tests/test_pipeline.py`

`_enumerate_for` currently calls `enumerate_counts(pivots, degree=degree, top_k=_TOP_K_PER_DEGREE)`. Wire `sub_pivots=by_degree.get(degree-1)` so composite templates activate in production. Then add a Layer 3 integration test using a fake provider that emits bars producing a wxy at degree 2.

- [ ] **Step 1: Read existing test for fake-provider patterns**

```bash
uv run python -c "import inspect; from tests import test_pipeline; print(inspect.getsourcefile(test_pipeline))"
```

Open `tests/test_pipeline.py` and locate the existing fake-provider helper used by other tests (`grep -n "class.*Provider" tests/test_pipeline.py`). The new integration test reuses or copies that pattern. **Do not invent a new fake-provider class if one exists** — the codebase already has at least one.

- [ ] **Step 2: Write the failing integration test**

Append to `tests/test_pipeline.py`:

```python
def test_run_analysis_emits_wxy_when_pivot_structure_matches(monkeypatch):
    """End-to-end: synthetic bars engineered to produce a degree-2 wxy with
    valid degree-1 sub-counts must surface a wxy WaveCount in the daily
    counts list. Asserts §3.6 of the complex-corrections spec — pipeline
    wiring delivers sub_pivots to the enumerator."""
    from datetime import date
    from decimal import Decimal

    from wave_alpha.domain import Bar, Interval
    from wave_alpha.pipeline import AnalyzeRequest, run_analysis

    # Build a bar series whose ATR-scaled multi-degree zigzag produces:
    #   degree=1 pivots subdividing each parent segment
    #   degree=2 pivots forming a 4-pivot W-X-Y outline
    # The simplest construction: tall outer swings dominated by smaller
    # inner swings — see helper below. If existing test_pipeline.py has a
    # `_make_bars_from_pivots` or similar, reuse it.
    bars = _build_bars_for_wxy_at_degree_2()
    as_of = bars[-1].timestamp

    class _FakeProvider:
        def fetch(self, ticker, interval, *, end):
            # Same shape across all intervals — multi-degree fixture is
            # interval-agnostic for this test.
            return list(bars)

    req = AnalyzeRequest(ticker="TEST", as_of=as_of)
    result = run_analysis(req, provider=_FakeProvider())
    wxys = [c for c in result.counts_daily if c.pattern == "wxy"]
    assert wxys, (
        f"expected wxy in counts_daily; got patterns: "
        f"{[c.pattern for c in result.counts_daily]}"
    )
    wxy = wxys[0]
    assert wxy.degree == 2
    assert wxy.sub_counts.get("W") is not None
    assert wxy.sub_counts["W"].degree == 1


def _build_bars_for_wxy_at_degree_2() -> list:
    """Build a bar series that, when run through detect_multi_degree, yields
    degree-1 and degree-2 pivots forming a textbook W-X-Y. Bar prices align
    with the eight pivot prices used in test_wxy_enumeration's fixture:
    100 -> 90 -> 95 -> 80 -> 85 -> 75 -> 80 -> 65, with intermediate filler
    bars to give detect_multi_degree something to work with.

    Each pivot becomes a single bar where high == low == close == that
    price; intermediate bars walk linearly between pivots. ATR is small so
    even modest swings cross degree-1 / degree-2 thresholds.
    """
    from datetime import date, timedelta
    from decimal import Decimal

    from wave_alpha.domain import Bar, Interval

    pivot_days_prices = [
        (0, "100"),
        (5, "90"),
        (10, "95"),
        (15, "80"),
        (20, "85"),
        (25, "75"),
        (30, "80"),
        (35, "65"),
    ]
    bars: list[Bar] = []
    for i in range(len(pivot_days_prices) - 1):
        d0, p0 = pivot_days_prices[i]
        d1, p1 = pivot_days_prices[i + 1]
        span = d1 - d0
        for k in range(span):
            day = d0 + k
            # Linear interpolation gives smooth bars between pivots.
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
    # Append the final pivot day as one bar.
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

If the linearly-interpolated bars don't yield the expected multi-degree pivots (because ATR-scaled thresholds smooth them out), adjust by either: (a) using `thresholds=` instead of `atr_multiples=` in a custom analyze call, or (b) bumping the price extremes outward. The integration test's job is to exercise the pipeline wiring; if the synthetic bars need tuning, do it inline rather than abandoning the test.

- [ ] **Step 3: Run test to verify it fails**

```bash
uv run pytest tests/test_pipeline.py::test_run_analysis_emits_wxy_when_pivot_structure_matches -v
```

Expected: FAIL — pipeline does not yet pass `sub_pivots`, so wxy is silently skipped.

- [ ] **Step 4: Wire `sub_pivots` in `_enumerate_for`**

In `src/wave_alpha/pipeline.py`, modify `_enumerate_for`:

```python
def _enumerate_for(bars: list[Bar], interval: Interval) -> list[WaveCount]:
    """Enumerate candidates across all configured degrees and pool the
    results.  Each degree contributes up to `_TOP_K_PER_DEGREE` candidates;
    degrees with no pivots contribute nothing.

    Per-degree top-K is preferred over a global top-K so that one degree's
    score distribution cannot crowd out the others (degree-2 has a
    calibrated score baseline from the 2026-05-07 EBT fit; degrees 1 and 3
    do not).  See spec 2026-05-08-multi-degree-enumeration-design.md §3.1.

    For composite templates (wxy) the lower-degree pivot list at degree-1
    is passed via ``sub_pivots`` so the enumerator can fit primitive
    corrective sub-counts.  See spec 2026-05-10-complex-corrections-design.md §3.6.
    """
    if not bars:
        return []
    by_degree = detect_multi_degree(bars, interval=interval, atr_multiples=_DEFAULT_ATR_MULTIPLES)
    candidates: list[WaveCount] = []
    for degree in _ENUMERATED_DEGREES:
        pivots = by_degree.get(degree, [])
        sub_pivots = by_degree.get(degree - 1) if degree > 0 else None
        candidates.extend(
            enumerate_counts(
                pivots,
                degree=degree,
                top_k=_TOP_K_PER_DEGREE,
                sub_pivots=sub_pivots,
            )
        )
    return candidates
```

- [ ] **Step 5: Run integration test**

```bash
uv run pytest tests/test_pipeline.py::test_run_analysis_emits_wxy_when_pivot_structure_matches -v
```

Expected: PASS. If the test still fails because the synthetic bars don't produce degree-2 pivots in the expected shape, tune the bar fixture (extend the price extremes, change ATR multiples, etc.) — see Step 2's note.

- [ ] **Step 6: Run full pytest**

```bash
uv run pytest -x
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/wave_alpha/pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): pass sub_pivots through _enumerate_for"
```

---

### Task 9: Layer 2 golden-fixture loader + initial fixtures

**Files:**
- Create: `tests/elliott/fixtures/wxy_golden/README.md`
- Create: `tests/elliott/fixtures/wxy_golden/<your_fixtures>.json` (≥ 2 to start)
- Create: `tests/elliott/test_wxy_golden.py`

The acceptance bar requires "≥ 5 Layer-2 golden fixtures land before merge" (spec §6.2). Land the loader and 2 starter fixtures here; the spec-acceptance threshold is satisfied as additional fixtures are contributed by the analyst across follow-up commits.

- [ ] **Step 1: Create fixture format documentation**

`tests/elliott/fixtures/wxy_golden/README.md`:

```markdown
# W-X-Y golden fixtures

Each fixture is a JSON file with the schema:

```json
{
  "name": "human-readable label",
  "ticker": "AAPL",
  "as_of": "2024-03-15",
  "bars": [
    {"timestamp": "2024-01-02", "open": "184.5", "high": "186.0", "low": "183.0", "close": "185.5", "volume": 12345, "interval": "1d"}
  ],
  "expected_top_pattern_at_degree_2": "wxy",
  "expected_subcounts": {
    "W": "zigzag",
    "Y": "flat"
  }
}
```

`expected_top_pattern_at_degree_2` is one of:
- `"wxy"`        — W-X-Y is expected at top of degree-2 top-K (positive case)
- `"primitive"`  — any non-wxy pattern; asserts wxy must NOT appear at top
                   (regression guard against false positives)

`expected_subcounts` is optional and only consulted when the expected top
pattern is `"wxy"`. Missing labels are not asserted — useful when X is
degenerate or the analyst hasn't committed to a specific Y subdivision.
```

- [ ] **Step 2: Land 2 starter fixtures**

The implementer should not invent these — they require analyst-labeled real ticker windows. As placeholders for the test loader, land two synthetic fixtures derived from the unit-test bar generator:

`tests/elliott/fixtures/wxy_golden/synthetic_positive.json`:

```json
{
  "name": "synthetic_positive",
  "ticker": "SYNTH",
  "as_of": "2026-02-05",
  "bars": "_use_helper_in_test_module_",
  "expected_top_pattern_at_degree_2": "wxy",
  "expected_subcounts": {"W": "zigzag", "Y": "zigzag"}
}
```

`tests/elliott/fixtures/wxy_golden/synthetic_negative.json`:

```json
{
  "name": "synthetic_negative_zigzag",
  "ticker": "SYNTH",
  "as_of": "2026-02-05",
  "bars": "_use_helper_in_test_module_",
  "expected_top_pattern_at_degree_2": "primitive",
  "expected_subcounts": {}
}
```

The string `"_use_helper_in_test_module_"` signals the loader to call a builder function in the test module by the fixture's `name`. Real ticker fixtures use full bars arrays; synthetic ones leverage helpers so the test file stays self-contained.

- [ ] **Step 3: Write the loader test module**

`tests/elliott/test_wxy_golden.py`:

```python
"""Golden tests for W-X-Y classification.

Each fixture in ``tests/elliott/fixtures/wxy_golden/*.json`` is loaded and
run through ``run_analysis`` (or, for synthetic fixtures, a builder
function in this module). The test asserts the expected top-of-top-K
pattern at degree 2.

Add real-chart fixtures by dropping a JSON file in the fixtures dir and
filling in the ``bars`` array. Spec §6.2 requires >= 5 fixtures
(positive + negative) before merge; analyst contributes incrementally.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from wave_alpha.domain import Bar, Interval
from wave_alpha.pipeline import AnalyzeRequest, run_analysis

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "wxy_golden"


def _bars_synthetic_positive() -> list[Bar]:
    """Reuses the same construction as
    test_pipeline._build_bars_for_wxy_at_degree_2."""
    from tests.test_pipeline import _build_bars_for_wxy_at_degree_2

    return _build_bars_for_wxy_at_degree_2()


def _bars_synthetic_negative_zigzag() -> list[Bar]:
    """Single zigzag at degree 2: 100 -> 80 -> 90 -> 75. Three segments,
    not enough to trigger a wxy (which needs 3 segments at parent + at
    least 4 sub-pivots in W and Y slots)."""
    from datetime import timedelta

    pivot_days_prices = [(0, "100"), (10, "80"), (20, "90"), (30, "75")]
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
    "synthetic_positive": _bars_synthetic_positive,
    "synthetic_negative_zigzag": _bars_synthetic_negative_zigzag,
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
def test_wxy_golden(fixture_path: Path):
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

    expected = fx["expected_top_pattern_at_degree_2"]
    if expected == "wxy":
        assert top.pattern == "wxy", (
            f"{fx['name']}: expected wxy at top of degree-2; got {top.pattern}"
        )
        for label, sub_pat in fx.get("expected_subcounts", {}).items():
            sub = top.sub_counts.get(label)
            assert sub is not None, f"{fx['name']}: sub_counts['{label}'] is None"
            assert sub.pattern == sub_pat, (
                f"{fx['name']}: expected {label}={sub_pat}, got {sub.pattern}"
            )
    elif expected == "primitive":
        assert top.pattern != "wxy", (
            f"{fx['name']}: wxy must NOT appear at top of degree-2 — "
            f"primitive negative case violated"
        )
    else:
        pytest.fail(f"{fx['name']}: unrecognized expected: {expected}")
```

- [ ] **Step 4: Run golden tests**

```bash
uv run pytest tests/elliott/test_wxy_golden.py -v
```

Expected: 2 PASS (positive + negative). The test parametrizes over fixture files; once the analyst adds more fixtures, they auto-register.

- [ ] **Step 5: Commit**

```bash
git add tests/elliott/fixtures/wxy_golden/ tests/elliott/test_wxy_golden.py
git commit -m "test(elliott): wxy golden fixture loader + 2 synthetic seeds"
```

---

### Task 10: Final verification — full suite, lint, manual analyze sanity

**Files:**
- (none modified — verification only)

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest
```

Expected: all green. If any pre-existing test fails because `wxy` now appears in its expected results, update the test to acknowledge the new template — but only if the test was asserting an exact pattern list. Generic "len(counts) > 0" assertions should be unaffected.

- [ ] **Step 2: Run linter**

```bash
uv run ruff check .
```

Expected: clean. Fix any unused-import or line-length issues that surfaced from the edits.

- [ ] **Step 3: Run formatter (idempotent)**

```bash
uv run ruff format .
```

Expected: zero files reformatted (already clean from edits done with care).

- [ ] **Step 4: Manual analyze sanity check**

Run an `analyze` against a real ticker that historically had sideways action. The point is not to assert a specific output, just to confirm the CLI still works and `wxy` may surface naturally:

```bash
uv run wave-alpha analyze AAPL --as-of 2024-12-31 --json | head -100
```

Expected: command completes, JSON parses. If `wxy` appears in `counts_daily`, the implementation is producing real output. If it doesn't, that's also fine — depends on the bar history.

- [ ] **Step 5: Final commit (no-op safety net)**

If steps 1–3 produced any cleanup edits:

```bash
git add -A
git commit -m "chore(elliott): post-wxy verification cleanup"
```

If they were already clean, skip.

- [ ] **Step 6: Update GitNexus index**

```bash
npx gitnexus analyze
```

The PostToolUse hook normally handles this; running explicitly here ensures the post-implementation graph is fresh for the next session's impact-analysis queries (per CLAUDE.md).

---

## Spec coverage check (self-review before handoff)

| Spec section | Implementation task |
|---|---|
| §2 — wxy template ships | Task 1 |
| §3.1 — `enumerate_counts` widened with `sub_pivots` | Task 2 |
| §3.1 — `_COMPOSITE_TEMPLATE_NAMES` gate | Task 2 |
| §3.1 — recursion restricted to `_PRIMITIVE_CORRECTIVE_NAMES` | Task 4 (`_fit_sub_counts`) |
| §3.1 — composite branch produces `WaveCount` with sub_counts | Task 6 |
| §3.2 — `_slice_sub_pivots` helper | Task 3 |
| §3.3 step 3 — degeneracy relaxation for slots <4 sub-pivots | Task 5 |
| §3.3 step 4 — strict fitting + spanning filter + allowlist check | Task 4 |
| §3.4 — soft-violation propagation via `<label>:` prefix | Tasks 4, 6 (mechanism); Task 7 (explicit assertion) |
| §3.5 — composite competes in same top-K | Task 6 (test) |
| §3.6 — graceful degrade when sub_pivots None | Task 2 |
| §4 — module change list | Tasks 1–8 cover all rows; "not modified" rows are not touched |
| §5 — wxy.yaml content | Task 1 |
| §6.1 — Layer 1 unit tests | Tasks 2, 3, 4, 5, 6, 7 |
| §6.2 — Layer 2 golden tests with loader and starter fixtures | Task 9 |
| §6.3 — Layer 3 pipeline integration test | Task 8 |
| §9 — acceptance checklist | Task 10 |
