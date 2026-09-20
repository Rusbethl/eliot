---
related:
  - docs/superpowers/specs/2026-05-06-ablation-grid-design.md  # G7
  - docs/superpowers/specs/2026-05-06-atr-multiple-wiring-design.md  # mirror
  - docs/superpowers/specs/2026-05-04-trade-sim-layer-design.md  # §5.1
---

# `target_source` wiring — design

## 1. Motivation

The 2026-05-06 ablation grid Phase 2 fill discovered (G7) that
`TradeRules.target_source = "fixed_R"` is not wired into
`derive_signal`. The schema accepts the value, but `_target_price`
ignores `rules.target_source` entirely — it always projects a 1.618
extension of W1 from `anchor`, on the trade-direction side. The
`_target_price_fixed_r` unit is implemented and direction-correct,
but never called from the production path.

This is the exact mirror of S5 (the atr_multiple wiring gap fixed in
spec `2026-05-06-atr-multiple-wiring-design.md`). The Phase 2 grid
sweep included `target_source` as an axis but the axis was degenerate
— every cell produced bit-identical targets — so the verdict could
not actually rank `count_target` vs. `fib_extension` vs. `fixed_R`.

This spec ships the wiring. It is deliberately narrow: no new
`TradeRules` fields, no behavior change to the existing fib-extension
path, no re-running of the grid.

## 2. Goals and non-goals

**Goals**

- `derive_signal` produces a fixed-R target when
  `rules.target_source == "fixed_R"`, on the profit side of the entry
  anchor, with magnitude `target_R × planned_risk`.
- `count_target` and `fib_extension` are explicit aliases — both
  dispatch to the existing 1.618×|W1| projection. The grid spec's
  `target_source=count_target` cells continue to produce the same
  targets they always have. (Renaming or splitting the two semantics
  is out of scope; the schema's two-name redundancy stays for
  back-compat.)
- The schema-stated option matches the runtime behavior. No silent
  fallback when `target_source="fixed_R"`.
- When `target_source="fixed_R"` is set without a `target_R` value,
  `derive_signal` raises `ValueError`. The pipeline catches at the
  per-candidate call site (already present from the atr_multiple
  wiring) so one mis-configured candidate does not lose its ranked
  siblings.
- All existing call paths that use the default
  (`target_source="count_target"`, `target_R=None`) keep working
  unchanged. No call-site signature changes outside `derive.py`.

**Non-goals**

- Introducing a real difference between `count_target` and
  `fib_extension` (e.g. count's W3 projection vs. W1 extension).
  They remain aliases. Splitting them is a future spec, not this
  one.
- Sweeping `target_R` across {1.5, 2.0, 2.5, 3.0} on the curated
  universe to compare against `fib_extension`. That is a follow-up
  grid spec, gated on this wiring.
- Changing `_target_price_fixed_r` itself — it is unit-tested and
  direction-correct.
- Adding cross-field validation in `TradeRules.__post_init__` that
  rejects `target_source="fixed_R"` + `target_R is None` at config
  time. Runtime raise is sufficient and matches the atr_multiple
  pattern (W4 of that spec).
- Making `target_R` required when `target_source="fixed_R"` via the
  schema (would force every other call site to learn a new field).
  Default `None` + runtime raise wins on minimal blast radius.

## 3. Architecture

### 3.1 API changes

`_target_price` (in `src/wave_alpha/trades/derive.py`):

```python
def _target_price(
    count: WaveCount,
    direction: str,
    rules: TradeRules,
    *,
    anchor: Decimal,
    stop: Decimal,        # NEW — required when target_source="fixed_R"
) -> Decimal:
    if rules.target_source == "fixed_R":
        if rules.target_R is None:
            raise ValueError(
                "fixed_R target_source requires target_R to be set"
            )
        return _target_price_fixed_r(
            anchor=anchor,
            stop=stop,
            direction=direction,  # type: ignore[arg-type]
            target_r=Decimal(str(rules.target_R)),
        )
    # count_target and fib_extension are aliases — both use the
    # existing 1.618 × |W1| projection on the trade-direction side.
    segs = count.segments()
    if not segs:
        return anchor
    w1 = segs[0]
    span = abs(w1.end.price - w1.start.price)
    delta = Decimal("1.618") * span
    return anchor + delta if direction == "long" else anchor - delta
```

The `*` separator makes `anchor` keyword-only (matching `_stop_price`'s
style); the existing call site already passes `anchor` as a kwarg so
this is back-compat. `stop` is the only new parameter.

`derive_signal` in the same file:

```python
stop = _stop_price(count, direction, rules, bars=bars, anchor=current_price)
if stop == current_price:
    return None  # zero-risk degenerate
target = _target_price(
    count, direction, rules, anchor=current_price, stop=stop,  # NEW: stop
)
```

`stop` is already in scope from the prior line. No new variable
needed.

### 3.2 Pipeline integration

`pipeline.run_analysis` already wraps the per-candidate
`derive_signal` call in `try/except ValueError` (added by the
atr_multiple wiring spec, §3.2). That same except already catches the
new `ValueError("fixed_R target_source requires target_R to be set")`
without modification. No pipeline change required.

`derive_signal`'s docstring gets one extra clause noting that
`rules.target_R` is required when `rules.target_source == "fixed_R"`.

### 3.3 `Decimal(str(rules.target_R))` conversion

`TradeRules.target_R` is `float | None` on the schema. To preserve
exact decimal arithmetic in `_target_price_fixed_r`, the wiring
converts via `Decimal(str(...))` rather than `Decimal(float)`. The
`str` round-trip avoids the binary-float representation noise (e.g.
`Decimal(2.0)` produces `Decimal("2")` but `Decimal(2.5)` produces
`Decimal("2.5")` — the conversion is fine for round numbers but
`str()` is the project-wide convention and matches every other
`Decimal(str(...))` usage in the engine.

### 3.4 Lookahead safety

No bars are involved in target derivation. The wiring touches only
count.pivots, anchor (latest close), stop (count-pivot or ATR-derived,
both already point-in-time-safe), and rules. No new leak surface.

## 4. Tests

Five new tests, each pinning one invariant. TDD — failing test first,
then implementation, then green. New tests live in
`tests/trades/test_derive.py` alongside the existing
`test_atr_multiple_*` suite (analogous structure).

### 4.1 `test_target_source_fib_extension_explicit_long`

Pin that explicit `target_source="fib_extension"` produces the same
target the implicit default has always produced. Uses the existing
`_bullish_w5_setup()` fixture.

```python
sig_default = derive_signal(
    count=_bullish_w5_setup(),
    ticker="AAPL",
    as_of=date(2024, 1, 19),
    current_price=Decimal("120"),
    rules=TradeRules(stop_source="count_invalidation"),
)
sig_explicit = derive_signal(
    count=_bullish_w5_setup(),
    ticker="AAPL",
    as_of=date(2024, 1, 19),
    current_price=Decimal("120"),
    rules=TradeRules(
        stop_source="count_invalidation",
        target_source="fib_extension",
    ),
)
assert sig_default.target_price == sig_explicit.target_price
```

### 4.2 `test_target_source_count_target_aliases_fib_extension`

Pin that `count_target` and `fib_extension` are byte-identical. This
is the alias contract.

```python
sig_a = derive_signal(..., rules=TradeRules(target_source="count_target", ...))
sig_b = derive_signal(..., rules=TradeRules(target_source="fib_extension", ...))
assert sig_a.target_price == sig_b.target_price
```

### 4.3 `test_target_source_fixed_R_long`

For a long setup with `target_R=2.0`:

```python
sig = derive_signal(
    count=_bullish_w5_setup(),
    ticker="AAPL",
    as_of=date(2024, 1, 19),
    current_price=Decimal("120"),
    rules=TradeRules(
        stop_source="count_invalidation",
        target_source="fixed_R",
        target_R=2.0,
    ),
)
assert sig is not None
assert sig.direction == "long"
risk = abs(sig.entry_price - sig.stop_price)
assert sig.target_price == sig.entry_price + Decimal("2") * risk
assert sig.target_price > sig.entry_price
```

### 4.4 `test_target_source_fixed_R_short`

Mirror of 4.3 for the 5-seg W5-complete short setup. Assert:

```python
risk = abs(sig.entry_price - sig.stop_price)
assert sig.target_price == sig.entry_price - Decimal("2") * risk
assert sig.target_price < sig.entry_price
```

### 4.5 `test_target_source_fixed_R_raises_without_target_R`

```python
with pytest.raises(ValueError, match="fixed_R"):
    derive_signal(
        count=_bullish_w5_setup(),
        ticker="AAPL",
        as_of=date(2024, 1, 19),
        current_price=Decimal("120"),
        rules=TradeRules(
            stop_source="count_invalidation",
            target_source="fixed_R",
            # target_R omitted (defaults to None)
        ),
    )
```

The pipeline-level skip behavior is already pinned by
`test_pipeline_skips_bad_atr_candidate_keeps_others` from the
atr_multiple wiring spec — its `try/except ValueError` is the same
boundary that catches the fixed_R raise. No new pipeline test needed
because the path is identical.

## 5. File structure

**Modified:**

- `src/wave_alpha/trades/derive.py` — `_target_price` signature +
  dispatch; `derive_signal` call site updated to pass `stop`.
- `tests/trades/test_derive.py` — tests 4.1–4.5.

**Not modified:**

- `src/wave_alpha/trades/models.py` — no schema change; `target_R`
  default stays `None`.
- `src/wave_alpha/pipeline.py` — `try/except ValueError` already in
  place from the atr_multiple wiring.
- `_target_price_fixed_r` — direction-correct already.
- `tests/test_pipeline.py` — existing `test_pipeline_skips_bad_*`
  test already covers the per-candidate skip semantics.
- Any test that calls `derive_signal` with the default
  `target_source` — unchanged behavior, all green.

## 6. Lookahead and other invariants

- No bars touch `_target_price`. No leak surface introduced.
- `_target_price_fixed_r` raises `ValueError("zero risk: anchor and
  stop are equal")` when given a zero-risk anchor/stop pair, but
  `derive_signal` already short-circuits with `if stop == current_price:
  return None` on the prior line. The raise is unreachable from
  `derive_signal`; it remains as a defensive check for the unit's
  direct callers.
- `count_target` ↔ `fib_extension` alias contract is covered by
  test 4.2. Adding behavioral divergence in a future spec would
  break that test deliberately.

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| A user sets `target_source="fixed_R"` without `target_R` | New `ValueError` surfaces at runtime. Pipeline's existing per-candidate `try/except` skips that candidate. CLI/web direct callers see a clear error message naming the missing field. |
| `target_R=0.0` produces a target equal to entry → zero-reward signal | `_target_price_fixed_r` itself doesn't reject zero `target_r`; the resulting signal would have `risk_per_share > 0` but `reward_per_share == 0` and `rr == 0.0`. The `TradeSignal` schema's `_check_risk` only forbids zero *risk*, not zero *reward*, so the signal is constructible. This is a configuration footgun, not a correctness bug; out of scope here. Document in §8 W1. |
| `count_target` and `fib_extension` aliasing locks in current behavior — if a future spec splits them, the alias test (4.2) must be updated deliberately | Documented in §6. The test is the contract; deliberate breakage is the point. |
| Backtest configs that set `target_source="fixed_R"` but don't set `target_R` will start failing | Documented goal — they were silently producing fib_extension targets. The `configs/grid_phase2.yaml` Phase 2 cells using `target_source="fixed_R"` should be audited for `target_R` presence on merge. |
| `Decimal(str(rules.target_R))` round-trip on float values like `target_R=0.1` produces `Decimal("0.1")` exactly, but `target_R=2.5000001` retains its float noise via `str()` | Acceptable — `target_R` is a user-set tuning knob, not a precision-critical field. `Decimal(str(...))` is the project's standard conversion. |

## 8. Open issues / deferred decisions

| # | Question | Defer to | Reason |
|---|---|---|---|
| W1 | Should `_target_price_fixed_r` reject `target_r <= 0` to avoid zero-reward signals? | Future polish | A `target_R` value that produces a zero-reward signal is a configuration error, not a runtime correctness issue. Schema-level constraint (`target_R: PositiveFloat | None`) is the natural place if we want this enforced. |
| W2 | Should `count_target` actually project off the count's W3 (or wave-3 extension) rather than aliasing W1's fib extension? | Future spec | The current behavior is what the engine has shipped since v0.5.0 — silently calling it "count_target" while computing fib_extension. A divergence would require a curated-universe sweep to verify it's not a regression on the +0.218R baseline. Out of scope for a wiring spec. |
| W3 | Should `target_R` be a `Decimal` on the schema instead of `float`? | Future polish | Symmetric with `commission_per_share: Decimal`. Would remove the `Decimal(str(...))` conversion at the call site. Not blocking. |
| W4 | Should the pipeline log a warning when it skips a fixed_R candidate due to missing `target_R`? | Future polish | Same answer as the atr_multiple W2 — adds a project-wide logging convention question. The grid spec can introduce a skip-rate counter on `TradeLayerResult` if needed. |

## 9. Roadmap impact

This spec lands as v0.5.8 → v0.5.9 (minor bump iff observable
behavior changes — and it does, by design, for any prior user of
`target_source="fixed_R"`). Once merged, a follow-up `target_R` grid
sweep can rank `fixed_R` cells against `fib_extension` cells
properly. That sweep is out of scope here; this spec only fixes the
underlying degenerate-axis bug.

The §8 deferred decisions (W1 zero-reward guard, W2 count_target
divergence, W3 Decimal target_R, W4 skip logging) are not blocking
for the v0.5.9 ship.
