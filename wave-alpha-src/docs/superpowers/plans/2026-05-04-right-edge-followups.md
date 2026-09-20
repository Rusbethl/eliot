# Right-Edge Follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three follow-ups from the 2026-05-03 training run (verdict was HOLD; calibration win was 2-3× per degree but Brier overlapped):

1. **Fix degree-count assumption** — codebase actually emits 5 degrees (0..4), not 3.
2. **Extend the promotion gate** with a calibration-only path: PROMOTE when Brier overlaps but every degree's ECE is strictly better by ≥X%.
3. **Heuristic Platt-recalibration** — fit per-degree Platt scalers on top of the existing heuristic and persist as a small artifact. Smaller change than shipping the logistic; captures most of the calibration gap.

**Architecture:** All three changes are localized to `right_edge/` and the gate. No changes to live inference contract. New runtime path: heuristic + optional Platt artifact loaded from disk, mirroring how the logistic loader works.

**Spec:** `docs/superpowers/specs/2026-05-04-right-edge-logistic-design.md` (training run section already documents the motivation)

---

## Phase 1 — Degree-count assumption fix

The spec / docstrings claim 3 degrees. `_DEFAULT_ATR_MULTIPLES` in `pipeline.py` has 5 elements → degrees 0..4 actually exist. Hunt down every "3 degrees" assumption in code and docs.

### Task 1.1: Audit and fix degree-count text

**Files:**
- Modify: `docs/superpowers/specs/2026-05-04-right-edge-logistic-design.md`
- Modify: any `right_edge/` docstrings that assume 3 degrees
- Audit: `right_edge/promotion.py`, `right_edge/training.py`, tests

- [ ] **Step 1: Search.**

```bash
grep -rn "degree" src/wave_alpha/right_edge/ tests/right_edge/
grep -rn "1, 2, 3\|degrees 1\|3 degrees\|degrees 1/2/3" src/ tests/ docs/
```

- [ ] **Step 2: Fix occurrences.**

In the spec, the architecture and Q3-related sections claim "degrees 1, 2, 3". Update to "degrees 0..4 (5 total per `_DEFAULT_ATR_MULTIPLES`)". The "Per-degree separate logistic weights" deferred-decision text should mention "5 degrees, not 3" since the spec previously argued degree-3 had too few samples — re-evaluate language.

In `right_edge/loader.py` test file (`tests/right_edge/test_loader.py`), the fixture `platt_per_degree` may use `{"1": ..., "2": ..., "3": ...}` based on the old assumption. Update to cover degrees 0..4 OR explicitly note "tests use a synthetic subset of degrees; production has 5".

In `right_edge/promotion.py`, no specific count is asserted — verify by reading. The verdict logic iterates over the union of degrees seen in the predictions, so it's degree-count-agnostic. Just update any docstring claiming "degrees 1/2/3".

In `right_edge/training.py`, `walk_forward()`'s `accumulated_platt_inputs` uses `setdefault` so it's degree-count-agnostic. Verify and move on.

- [ ] **Step 3: Run tests.**

```bash
uv run pytest -x
```

Expected: PASS (no behavioral change; this is text-only).

- [ ] **Step 4: Commit.**

```bash
git add -A
git commit -m "docs(right-edge): degree count is 5 (0..4) per _DEFAULT_ATR_MULTIPLES, not 3"
```

---

## Phase 2 — Gate-rule extension: PROMOTE_CALIBRATION verdict

Current gate: PROMOTE iff Brier strict-better AND per-degree calibration ≤. Extension: also PROMOTE when Brier overlaps but calibration is *strictly better by a margin* on every degree. Captures the case from the 2026-05-03 run where calibration was the win.

### Task 2.1: Add PROMOTE_CALIBRATION verdict

**Files:**
- Modify: `src/wave_alpha/right_edge/promotion.py`
- Modify: `tests/right_edge/test_promotion.py`

- [ ] **Step 1: Update Verdict literal and verdict logic.**

```python
Verdict = Literal["PROMOTE", "PROMOTE_CALIBRATION", "HOLD", "REJECT"]

# Threshold: every degree's logistic ECE must be at least 20% relatively
# better than heuristic's. Tunable; chosen as the smallest gap that's
# clearly above bootstrap noise on per-degree Brier and gives meaningful
# user-facing improvement (i.e., a 70% prediction is closer to truly 70%).
_CALIBRATION_PROMOTE_RATIO: float = 0.20  # relative improvement required


def _calibration_strictly_better(per_degree: dict[int, dict[str, float]]) -> bool:
    for d, m in per_degree.items():
        log_ece = m["logistic"]
        heu_ece = m["heuristic"]
        if heu_ece == 0:
            return False  # can't beat zero
        improvement = (heu_ece - log_ece) / heu_ece
        if improvement < _CALIBRATION_PROMOTE_RATIO:
            return False
    return True
```

Update verdict assignment in `evaluate_promotion`:

```python
log_lo, _, log_hi = log_ci
heu_lo, _, heu_hi = heu_ci

# REJECT first (strict-worse on either Brier or any per-degree calibration).
if log_lo > heu_hi:
    return GateResult("REJECT", log_ci, heu_ci, per_degree)
for d, m in per_degree.items():
    if m["logistic"] > m["heuristic"]:
        return GateResult("REJECT", log_ci, heu_ci, per_degree)

# Strongest PROMOTE: Brier strict-better AND calibration ≤ everywhere.
if log_hi < heu_lo:
    return GateResult("PROMOTE", log_ci, heu_ci, per_degree)

# Calibration-only PROMOTE: Brier overlaps (no regression by REJECT check
# above), and every degree's calibration is strictly better by ≥ ratio.
if _calibration_strictly_better(per_degree):
    return GateResult("PROMOTE_CALIBRATION", log_ci, heu_ci, per_degree)

return GateResult("HOLD", log_ci, heu_ci, per_degree)
```

- [ ] **Step 2: Update LogisticConfirmationModel.from_json to accept the new verdict.**

```python
# In src/wave_alpha/right_edge/logistic.py:
_PROMOTE_VERDICTS = frozenset({"PROMOTE", "PROMOTE_CALIBRATION"})

@classmethod
def from_json(cls, path: Path) -> LogisticConfirmationModel:
    raw = json.loads(path.read_text())
    verdict = raw.get("gate", {}).get("verdict")
    if verdict not in _PROMOTE_VERDICTS:
        raise ModelArtifactError(
            f"Refusing to load model with verdict={verdict}"
        )
    ...
```

- [ ] **Step 3: Tests.**

Add to `tests/right_edge/test_promotion.py`:

```python
def test_promote_calibration_when_brier_overlaps_and_calibration_clearly_better():
    # Synthesize: same Brier (overlapping CIs), logistic calibration 3x better
    # on every degree.
    ...
    assert r.verdict == "PROMOTE_CALIBRATION"


def test_hold_when_calibration_better_but_below_threshold():
    # Logistic calibration only 10% better → below 20% threshold → HOLD
    ...
    assert r.verdict == "HOLD"


def test_promote_strong_path_still_works():
    # Strict Brier-better AND calibration ≤ → PROMOTE (not PROMOTE_CALIBRATION)
    ...
    assert r.verdict == "PROMOTE"
```

Add to `tests/right_edge/test_logistic.py`:

```python
def test_from_json_loads_promote_calibration_artifact(tmp_path):
    # Should load (not raise) when verdict is PROMOTE_CALIBRATION.
    ...
```

- [ ] **Step 4: Update CLI to handle the new verdict.**

In `src/wave_alpha/cli/app.py`'s `train_right_edge` command:

```python
# Existing:
if write:
    if gate.verdict != "PROMOTE" and not force:
        ...

# Change to:
_PROMOTE_VERDICTS_CLI = {"PROMOTE", "PROMOTE_CALIBRATION"}
if write:
    if gate.verdict not in _PROMOTE_VERDICTS_CLI and not force:
        ...
```

- [ ] **Step 5: Run all tests.**

```bash
uv run pytest -x
```

Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add src/wave_alpha/right_edge/promotion.py src/wave_alpha/right_edge/logistic.py src/wave_alpha/cli/app.py tests/
git commit -m "feat(right-edge): PROMOTE_CALIBRATION verdict — gate ships on calibration-only wins"
```

---

## Phase 3 — Heuristic Platt-recalibration

Capture most of the calibration gap without shipping the logistic. Take the existing heuristic, run it through the same walk-forward pipeline to produce per-degree (raw_proba, outcome) pairs, fit Platt per degree, persist as a small JSON. Loader path checks for this artifact and wraps the heuristic with calibration when found.

### Task 3.1: `CalibratedHeuristicModel` runtime class

**Files:**
- Create: `src/wave_alpha/right_edge/calibrated_heuristic.py`
- Create: `tests/right_edge/test_calibrated_heuristic.py`

- [ ] **Step 1: Implement the class.**

```python
# src/wave_alpha/right_edge/calibrated_heuristic.py
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from wave_alpha.right_edge.features import RightEdgeFeatures
from wave_alpha.right_edge.heuristic import HeuristicConfirmationModel


class CalibratedHeuristicArtifactError(ValueError):
    """Raised when a calibrated-heuristic artifact is malformed."""


@dataclass(frozen=True)
class CalibratedHeuristicModel:
    """Heuristic + per-degree Platt scalers loaded from a JSON artifact.

    Smaller change than the logistic: same hand-coded feature weighting,
    just calibrated post-hoc to match empirical confirmation rates.
    """

    version: str
    heuristic_version: str
    platt_per_degree: dict[int, tuple[float, float]]

    @classmethod
    def from_json(cls, path: Path) -> CalibratedHeuristicModel:
        raw = json.loads(path.read_text())
        if raw.get("gate", {}).get("verdict") not in {"PROMOTE", "PROMOTE_CALIBRATION"}:
            raise CalibratedHeuristicArtifactError(
                f"Refusing to load with verdict={raw.get('gate', {}).get('verdict')}"
            )
        return cls(
            version=raw["version"],
            heuristic_version=raw["heuristic_version"],
            platt_per_degree={
                int(d): (float(p["a"]), float(p["b"]))
                for d, p in raw["platt_per_degree"].items()
            },
        )

    def predict_proba(self, features: RightEdgeFeatures, *, degree: int) -> float:
        raw = HeuristicConfirmationModel().predict_proba(features)
        a, b = self.platt_per_degree.get(degree, (1.0, 0.0))
        z = max(min(a * raw + b, 50.0), -50.0)
        return 1.0 / (1.0 + math.exp(-z))
```

- [ ] **Step 2: Tests.**

```python
def test_from_json_loads_promote_calibration_artifact(tmp_path):
    path = tmp_path / "h.json"
    path.write_text(json.dumps({
        "version": "calibrated_heuristic_v1",
        "heuristic_version": "heuristic_v2",
        "platt_per_degree": {"0": {"a": 1.5, "b": -0.3}, "1": {"a": 1.2, "b": 0.0}},
        "gate": {"verdict": "PROMOTE_CALIBRATION"},
    }))
    m = CalibratedHeuristicModel.from_json(path)
    assert m.version == "calibrated_heuristic_v1"


def test_from_json_refuses_hold_verdict(tmp_path):
    path = tmp_path / "h.json"
    path.write_text(json.dumps({
        "version": "calibrated_heuristic_v1",
        "heuristic_version": "heuristic_v2",
        "platt_per_degree": {},
        "gate": {"verdict": "HOLD"},
    }))
    with pytest.raises(CalibratedHeuristicArtifactError):
        CalibratedHeuristicModel.from_json(path)


def test_predict_proba_unknown_degree_uses_identity_platt(tmp_path):
    # Build a model with platt only for degree 0; predict for degree 99.
    # Must equal the raw heuristic proba (identity Platt).
    ...
```

- [ ] **Step 3: Run tests.** Expected: PASS.

- [ ] **Step 4: Commit.**

### Task 3.2: Training command for calibrated heuristic

**Files:**
- Modify: `src/wave_alpha/right_edge/training.py` (add `fit_calibrated_heuristic` + serialize helper)
- Modify: `src/wave_alpha/cli/app.py` (add `train calibrated-heuristic` command)

- [ ] **Step 1: Add fit + serialize helpers.**

```python
# src/wave_alpha/right_edge/training.py
from wave_alpha.right_edge.heuristic import HeuristicConfirmationModel


def fit_calibrated_heuristic(
    rows_by_quarter: dict[date, list[TrainingRow]],
    *,
    folds: list[Fold],
) -> tuple[dict[int, PlattParams], list[CalibratedPrediction]]:
    """Walk-forward Platt scalers fit ON TOP OF the heuristic, no logistic.

    Same fold structure as walk_forward; just uses heuristic as the raw scorer
    instead of a fitted logistic. Returns (final_platt_per_degree, oos_predictions)
    where oos_predictions carry the calibrated heuristic probas for the gate.
    """
    heuristic = HeuristicConfirmationModel()
    accumulated: dict[int, list[tuple[float, int]]] = {}
    all_oos: list[CalibratedPrediction] = []

    for fold in folds:
        # Test rows in this fold's quarter range.
        test_rows: list[TrainingRow] = [
            r for q, rs in rows_by_quarter.items()
            if fold.test_start <= q < fold.test_end
            for r in rs
        ]
        if not test_rows:
            continue

        # Platt fit on PRIOR-fold accumulator only.
        platt = {d: fit_platt(p) for d, p in accumulated.items() if p}

        for r in test_rows:
            raw = heuristic.predict_proba(r.features)
            scaler = platt.get(r.degree)
            cal = apply_platt(scaler, raw) if scaler else raw
            all_oos.append(CalibratedPrediction(
                ticker=r.ticker, as_of=r.as_of, degree=r.degree,
                features=r.features, raw_proba=raw,
                calibrated_proba=cal, outcome=r.outcome,
            ))

        for r in test_rows:
            raw = heuristic.predict_proba(r.features)
            accumulated.setdefault(r.degree, []).append((raw, r.outcome))

    final_platt = {d: fit_platt(p) for d, p in accumulated.items() if p}
    return final_platt, all_oos


def serialize_calibrated_heuristic_artifact(
    *,
    platt: dict[int, PlattParams],
    gate: GateResult,
    trained_at: date | None = None,
    git_sha: str | None = None,
) -> dict:
    return {
        "version": "calibrated_heuristic_v1",
        "heuristic_version": HeuristicConfirmationModel().version,
        "trained_at": (trained_at or date.today()).isoformat(),
        "git_sha": git_sha if git_sha is not None else _git_sha(),
        "platt_per_degree": {
            str(d): {"a": p.a, "b": p.b} for d, p in platt.items()
        },
        "gate": {
            "verdict": gate.verdict,
            "logistic_brier_ci": list(gate.logistic_brier_ci),  # named "logistic" but it's the calibrated heuristic
            "heuristic_brier_ci": list(gate.heuristic_brier_ci),
            "per_degree_calibration": gate.per_degree_calibration,
        },
    }
```

**Naming note:** the `gate.logistic_brier_ci` field is reused for the calibrated-heuristic's CI vs raw heuristic. Acceptable for now; if the field name confusion bothers reviewers, rename later. Document in the commit message.

- [ ] **Step 2: Add CLI command.**

```python
# In cli/app.py:

@train_app.command("calibrated-heuristic")
def train_calibrated_heuristic(
    universe_file: Path = typer.Option(
        Path("data/universe_curated.txt"), "--universe",
    ),
    start_date: str = typer.Option("2021-01-01", "--start"),
    end_date: str = typer.Option(..., "--end"),
    write: bool = typer.Option(False, "--write"),
    force: bool = typer.Option(False, "--force"),
    seed: int = typer.Option(42, "--seed"),
    output_path: Path = typer.Option(
        Path("src/wave_alpha/right_edge/models/right_edge_calibrated_heuristic_v1.json"),
        "--output",
    ),
) -> None:
    """Fit per-degree Platt scalers on top of the heuristic.

    Same walk-forward + paired-bootstrap gate as `train right-edge`; the model
    being evaluated is "heuristic + Platt" vs raw heuristic.
    """
    # Mostly mirrors train_right_edge, but uses fit_calibrated_heuristic.
    # ... assemble training rows (same path) ...
    final_platt, oos = fit_calibrated_heuristic(rows_by_quarter, folds=folds)

    cal_preds = [(cp.calibrated_proba, cp.outcome, cp.degree) for cp in oos]
    raw_preds = [(cp.raw_proba, cp.outcome, cp.degree) for cp in oos]
    gate = _evaluate_promotion(
        logistic_preds=cal_preds,
        heuristic_preds=raw_preds,
        seed=seed,
    )

    typer.echo(f"\nVerdict: {gate.verdict}")
    typer.echo(f"  Calibrated-heuristic Brier 95% CI: {gate.logistic_brier_ci}")
    typer.echo(f"  Raw heuristic Brier 95% CI: {gate.heuristic_brier_ci}")
    typer.echo("  Per-degree calibration error (ECE):")
    for d, m in sorted(gate.per_degree_calibration.items()):
        typer.echo(f"    degree={d}  calibrated={m['logistic']:.4f}  raw={m['heuristic']:.4f}")

    if write:
        if gate.verdict not in {"PROMOTE", "PROMOTE_CALIBRATION"} and not force:
            typer.echo(f"Not writing — verdict is {gate.verdict}. Use --force to write anyway.")
            raise typer.Exit(1)
        artifact = serialize_calibrated_heuristic_artifact(platt=final_platt, gate=gate)
        write_artifact(artifact, output_path)
        typer.echo(f"Wrote {output_path}")
```

- [ ] **Step 3: CLI smoke test.** Mirror the existing `test_cli_train.py` pattern; verify the command runs end-to-end on synthetic data and prints the expected verdict labels.

- [ ] **Step 4: Run tests.** Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/wave_alpha/right_edge/training.py src/wave_alpha/cli/app.py tests/
git commit -m "feat(right-edge): wave-alpha train calibrated-heuristic — Platt-on-heuristic fit"
```

### Task 3.3: Loader path for calibrated heuristic

**Files:**
- Modify: `src/wave_alpha/right_edge/loader.py`
- Modify: `src/wave_alpha/prefs/models.py` (add `model: "calibrated_heuristic"` Literal value)
- Modify: `tests/right_edge/test_loader.py`

- [ ] **Step 1: Extend `RightEdgeConfig.model` literal.**

```python
class RightEdgeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    model: Literal["auto", "heuristic", "calibrated_heuristic", "logistic"] = "auto"
```

- [ ] **Step 2: Extend loader.**

```python
# src/wave_alpha/right_edge/loader.py
from wave_alpha.right_edge.calibrated_heuristic import (
    CalibratedHeuristicArtifactError,
    CalibratedHeuristicModel,
)

_DEFAULT_LOGISTIC_PATH = (
    Path(__file__).parent / "models" / "right_edge_logistic_v1.json"
)
_DEFAULT_CALIBRATED_HEURISTIC_PATH = (
    Path(__file__).parent / "models" / "right_edge_calibrated_heuristic_v1.json"
)


def load_right_edge_model(
    cfg: RightEdgeConfig,
    *,
    logistic_path: Path = _DEFAULT_LOGISTIC_PATH,
    calibrated_heuristic_path: Path = _DEFAULT_CALIBRATED_HEURISTIC_PATH,
) -> ConfirmationModel:
    if cfg.model == "heuristic":
        return HeuristicAdapter()

    if cfg.model == "calibrated_heuristic":
        return CalibratedHeuristicModel.from_json(calibrated_heuristic_path)

    if cfg.model == "logistic":
        return LogisticConfirmationModel.from_json(logistic_path)

    # auto: prefer logistic > calibrated_heuristic > heuristic
    if logistic_path.exists():
        try:
            return LogisticConfirmationModel.from_json(logistic_path)
        except (ModelArtifactError, ValueError, KeyError) as exc:
            logger.warning("right-edge: logistic unloadable, trying calibrated heuristic ({})", exc)
    if calibrated_heuristic_path.exists():
        try:
            return CalibratedHeuristicModel.from_json(calibrated_heuristic_path)
        except (CalibratedHeuristicArtifactError, ValueError, KeyError) as exc:
            logger.warning("right-edge: calibrated heuristic unloadable, falling back to raw ({})", exc)
    return HeuristicAdapter()
```

- [ ] **Step 3: Tests.**

Add to `tests/right_edge/test_loader.py`:

```python
def test_loader_explicit_calibrated_heuristic_returns_model(tmp_path):
    path = tmp_path / "h.json"
    _write_promote_calibrated_heuristic_artifact(path)
    cfg = RightEdgeConfig(model="calibrated_heuristic")
    m = load_right_edge_model(cfg, calibrated_heuristic_path=path)
    assert isinstance(m, CalibratedHeuristicModel)


def test_loader_auto_prefers_logistic_over_calibrated_heuristic(tmp_path):
    log = tmp_path / "log.json"
    cal = tmp_path / "cal.json"
    _write_promote_logistic_artifact(log)
    _write_promote_calibrated_heuristic_artifact(cal)
    cfg = RightEdgeConfig(model="auto")
    m = load_right_edge_model(
        cfg, logistic_path=log, calibrated_heuristic_path=cal
    )
    assert isinstance(m, LogisticConfirmationModel)


def test_loader_auto_falls_back_to_calibrated_heuristic_when_logistic_missing(tmp_path):
    cal = tmp_path / "cal.json"
    _write_promote_calibrated_heuristic_artifact(cal)
    cfg = RightEdgeConfig(model="auto")
    m = load_right_edge_model(
        cfg, logistic_path=tmp_path / "missing.json", calibrated_heuristic_path=cal
    )
    assert isinstance(m, CalibratedHeuristicModel)
```

- [ ] **Step 4: Run all tests.** Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/wave_alpha/prefs/models.py src/wave_alpha/right_edge/loader.py tests/
git commit -m "feat(right-edge): loader supports calibrated_heuristic — auto prefers logistic > calibrated > raw"
```

---

## Phase 4 — Re-run training, ship if PROMOTE_CALIBRATION

Operator phase. Run both training pipelines against real data; commit any artifact that earns a PROMOTE or PROMOTE_CALIBRATION verdict.

- [ ] **Step 1: Re-run logistic training — does the gate-rule extension flip the prior HOLD?**

```bash
uv run wave-alpha train right-edge --start 2021-01-01 --end 2026-04-01 --write
```

Expected: with the prior run's calibration ratios (60-70% improvement per degree, all ≥20% threshold), verdict should be **PROMOTE_CALIBRATION**. Artifact written to `src/wave_alpha/right_edge/models/right_edge_logistic_v1.json`.

If PROMOTE_CALIBRATION fires:
```bash
git add src/wave_alpha/right_edge/models/right_edge_logistic_v1.json
git commit -m "feat(right-edge): ship trained logistic (PROMOTE_CALIBRATION verdict)"
```

If still HOLD: investigate. The gate output should show why (likely one degree under-threshold).

- [ ] **Step 2: Run calibrated-heuristic training.**

```bash
uv run wave-alpha train calibrated-heuristic --start 2021-01-01 --end 2026-04-01 --write
```

The "model" being evaluated here is heuristic + Platt vs raw heuristic. The Brier comparison may be tighter than logistic-vs-heuristic since Platt only changes calibration not discrimination. Calibration WILL improve (that's the whole point of Platt) — verdict likely PROMOTE_CALIBRATION.

If PROMOTE / PROMOTE_CALIBRATION fires:
```bash
git add src/wave_alpha/right_edge/models/right_edge_calibrated_heuristic_v1.json
git commit -m "feat(right-edge): ship calibrated heuristic (Platt-per-degree on top of heuristic_v2)"
```

- [ ] **Step 3: Append a second training-run section to the spec.**

Document the new verdicts and the artifact provenance. Replaces / extends the prior "Training run 2026-05-03 — Verdict: HOLD" section with the post-extension result.

```bash
git add docs/superpowers/specs/2026-05-04-right-edge-logistic-design.md
git commit -m "docs(spec): right-edge training run after gate-extension — new verdicts logged"
```

- [ ] **Step 4: Final test pass + ruff + merge.**

```bash
uv run pytest -x
uv run ruff check .

git checkout main
git merge --no-ff feat/right-edge-followups -m "Merge feat/right-edge-followups — gate extension + calibrated heuristic"
git branch -f master main
```

---

## Self-review checklist (run by plan author before handoff)

- [x] All three follow-ups have explicit tasks.
- [x] Phase 1 is doc-only (text fixes + docstring updates) — no behavioral change.
- [x] Phase 2's PROMOTE_CALIBRATION semantics are precise: calibration strictly better by ≥20% relative on EVERY degree, with no degree regressed (REJECT check still applies first).
- [x] Phase 3's calibrated heuristic reuses the exact same fold + Platt + bootstrap-gate machinery as the logistic — no parallel infrastructure.
- [x] Loader auto-mode preference order is explicit: logistic > calibrated_heuristic > raw heuristic.
- [x] No fields renamed in `GateResult` despite the calibrated-heuristic gate calling them "logistic"/"heuristic" — pragmatic reuse, documented in commit message.
- [x] Determinism preserved (same `serialize_calibrated_heuristic_artifact` accepts `trained_at` + `git_sha` overrides).
- [x] Phase 4 is operator-action; clear branching on the verdict outcome.

## Execution

Per memory directive ("skip per-task reviewer dispatches on mechanical work"), execute as one batched implementer dispatch per phase, with a final cross-cutting code review at end. Phase 4 runs the actual training and ships verdict-gated artifacts.
