# CHANGELOG



## v0.14.3 (2026-05-12)

### Fix

* fix(web): eliminate slow page-load on deepdive and home

deepdive: replace functools.lru_cache with a thread-safe single-flight
memoizer so the 6+ concurrent HTMX fragments share one run_analysis
call instead of each computing it. lru_cache only locks the dict, not
the function body, so parallel cache misses each executed the full
~10s pipeline and contended for cache.sqlite. Cold page load ~73s → ~11s.

home (and every non-chart page): move lightweight-charts (176 KB) and
deepdive.js out of base.html into a {% block page_scripts %} that only
deepdive.html overrides; tie asset_v to __version__ so browser cache
survives uvicorn restarts. DOMContentLoaded on cold load ~169 ms → ~23 ms.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`d5764e6`](https://github.com/chen-star/wave-alpha/commit/d5764e6df81449807d400885517535c43eb30f54))


## v0.14.2 (2026-05-12)

### Chore

* chore(release): 0.14.2 ([`7e09d3a`](https://github.com/chen-star/wave-alpha/commit/7e09d3a345a33200f769ac235793f06035bd6870))

### Fix

* fix(data): enable WAL + single-tx writes to prevent &#34;database is locked&#34;

The deep-dive page fans out ~8 concurrent HTMX fragments that each call
deps.analyze on cold-cache loads, racing to write cache.sqlite. The
default rollback-journal mode + autocommit executemany (1 transaction
per row) blew past the 5s busy-wait under contention.

Enable WAL, synchronous=NORMAL, 30s busy_timeout, and drop autocommit
so OHLCVCache.put becomes a single transaction. Apply the same pragmas
to LLMResponseCache and history.store. Add a regression test that
spawns 8 concurrent writers.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`9b27c4a`](https://github.com/chen-star/wave-alpha/commit/9b27c4ad61779650955500e176e814146a7b02ab))


## v0.14.1 (2026-05-12)

### Chore

* chore(release): 0.14.1 ([`33937ef`](https://github.com/chen-star/wave-alpha/commit/33937ef7cfc2c8ab8cbe89440b1befa11871d059))

### Fix

* fix(web): load popover.js before alpine.min.js

Alpine initialized before popover.js registered window.popover, so
x-data=&#34;popover()&#34; threw ReferenceError. Without a data context,
x-show and x-cloak couldn&#39;t hide the popover cards — every KPI on the
home page rendered the popover headline (&#34;WHAT &#39;SIGNALS&#39; COUNTS&#34;,
etc.) jammed between the value and delta lines.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`3253ac4`](https://github.com/chen-star/wave-alpha/commit/3253ac444af0564cbaa414381a95d354daad08fd))


## v0.14.0 (2026-05-12)

### Chore

* chore(release): 0.14.0 ([`c17d45c`](https://github.com/chen-star/wave-alpha/commit/c17d45ca7a4b616c4078f167b44199981b879400))

### Feature

* feat(engine_ranker): outcome-trained ranker module + pipeline (#7)

* docs(specs): outcome-trained engine ranker design

A new logistic re-ranker applied at pipeline level (not inside enumerator)
that takes the four agreed features template_fit / fib / recency /
coherence and outputs calibrated P(outcome=target). Folds coherence
into the logistic instead of leaving it as an outside multiplier on
the LLM blend. Mirrors right_edge/ module layout and reuses the
outcome-training eval harness for the promotion gate.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* docs(plans): outcome-trained engine ranker implementation plan

Ten task plan: decompose engine_score components on WaveCount; new
engine_ranker module (features, heuristic, logistic, loader, training,
promotion); prefs config; pipeline integration with the new probabilistic
blend; SnapshotCount engine_prob field; CLI; rollout note. Every task
ships with its own failing-test-first cycle and a commit boundary.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* feat(elliott): decompose engine_score into component sub-scores

Adds template_fit_component / fib_component / recency_component fields
to WaveCount; populated by the enumerator&#39;s scorer alongside the
existing scalar score. Enables a pipeline-level re-ranker to read the
components without recomputation, in service of the outcome-trained
engine ranker. Purely additive: fields default to None for hand-built
counts; legacy score field is unchanged bit-for-bit.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* fix(elliott): address T1 review findings — stale refs + bound docs

Code review surfaced two stale references to the deleted `_score()`
helper in docstrings (models.py field comment, test module docstring)
and one unrealistic lower-bound assertion on template_fit_component
(can dip below zero for counts with &gt; 10 soft violations; the scalar
`score` is what gets clamped). Documentation updated to reflect
reality; lower-bound assertion removed. No arithmetic change.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* feat(engine_ranker): module skeleton with features + heuristic baseline

New engine_ranker module mirrors right_edge/ layout. EngineRankerFeatures
holds (template_fit, fib, recency, coherence, degree) extracted from a
scored WaveCount. HeuristicEngineRanker reproduces enumerator legacy
scoring to 1e-12 and is the no-artifact fallback / promotion baseline.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* refactor(elliott,engine_ranker): public weight constants + heuristic version

Code review on T2 flagged that engine_ranker.heuristic imports the
underscore-prefixed _WEIGHT_* constants from elliott.enumerator, a
cross-module private-symbol dependency. Promote them to public
WEIGHT_BASE / WEIGHT_CONFLUENCE / WEIGHT_RECENCY (same values, same
arithmetic) so the lockstep relationship is explicit. Add a
version=&#34;heuristic_v1&#34; attribute to HeuristicEngineRanker mirroring
the right_edge heuristic for diagnostic provenance. No behavior
change.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* feat(engine_ranker): logistic model + auto-fallback loader

LogisticEngineRanker reads JSON artifacts produced by training; refuses
non-PROMOTE verdicts and unknown feature names. Shared coefficient
vector + per-degree intercept; unseen degrees fall back to median
intercept.

load_engine_ranker mirrors right_edge.loader: auto &gt; logistic &gt;
heuristic, silent fallback on missing or malformed artifact. Minimal
EngineRankerConfig added to prefs.models (kind only); expanded in
Task 4.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* fix(engine_ranker): T3 review polish — warnings + docs

Two important fixes from code review:
  - LogisticEngineRanker.predict_proba now logs a WARNING when a
    candidate&#39;s degree is unseen in the trained intercepts (spec §6.2
    requires this; previously silent).
  - load_engine_ranker&#39;s auto-fallback now logs at WARNING (not INFO)
    when the logistic artifact is unloadable, matching
    right_edge.loader and the operational signal a degraded model
    deserves.

Plus three minor polish items:
  - Hide _median_intercept from the dataclass repr.
  - Comment _PROMOTE_VERDICTS to explain why PROMOTE_CALIBRATION is
    not a valid verdict for this model.
  - Document on the Protocol why the signature omits *, degree.

No behavior change to predictions, blend formula, or fallback chain;
purely observability and documentation.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* feat(prefs): EngineRankerConfig + outcome-training sub-block

Wires engine_ranker into AppConfig with a kind selector (auto/heuristic/
logistic) and an outcome_training sub-block holding the walk-forward
split, class-balance band, time-stop handling, and the per-degree row
gate that training and promotion read.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* feat(pipeline): integrate engine_ranker into _rank + blend_scores

blend_scores gains a logistic path: final = alpha * engine_prob +
(1-alpha) * llm_prob, no coh_multiplier (coherence is a feature of the
logistic). Legacy heuristic path is preserved bit-for-bit when
engine_ranker is the heuristic.

RankedCount carries optional engine_prob, populated only when the
active model is logistic. run_analysis accepts an optional engine_ranker
kwarg; when None, builds it from AppConfig.engine_ranker via load_config.
Default &#39;auto&#39; kind falls back to heuristic when no artifact is present,
so behavior at this commit is unchanged for stock installs.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* docs(pipeline): T5 review polish — typed _rank + scale caveat

Add EngineRankerModel type annotation to _rank&#39;s engine_ranker
parameter (resolves at type-check time via the existing TYPE_CHECKING
guard).

Expand the comment on the per-candidate try/except so the next reader
sees the design implication: a fallback row mid-batch sorts together
with logistic-scale siblings on a different scale. The trigger is
unreachable in production (enumerate_counts always populates
components) but the comment makes the latent risk visible.

No behavior change.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* feat(history): SnapshotCount.engine_prob optional field

Carries the calibrated probability from the logistic engine ranker into
the persisted snapshot when active. Defaults to None so older payloads
and heuristic-path runs load unchanged. _to_snapshot_count populates
the field from RankedCount.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* feat(engine_ranker): outcome-dataset builder + walk-forward training

build_outcome_dataset reads resolved snapshots from history.sqlite,
replays the pipeline (LLM disabled) at each (ticker, as_of) against
cached bars, and recomputes EngineRankerFeatures for the top-1
candidate.

train_logistic fits a shared-coef + per-degree-intercept logistic via
NumPy batch gradient descent on the walk-forward train split (mirroring
right_edge/training.fit_logistic — no sklearn). Per-degree intercepts
are encoded as one-hot columns. Class weights kick in when the
observed target rate falls outside [class_balance_low,
class_balance_high]. Deterministic for a fixed random_state.

candidate_to_loadable converts a CandidateArtifact into a usable
LogisticEngineRanker without going through JSON, for in-process
promotion evaluation.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* fix(engine_ranker): T7 review polish — safety + ergonomics

Code review surfaced several real issues:
  - ZeroDivisionError when train split is 100% positive or negative
    (target_rate at the {0,1} extreme). Now clamps to (1e-6, 1-1e-6)
    before computing class-balance weights.
  - build_outcome_dataset silently swallowed pipeline exceptions and
    only incremented a counter. Now logs a WARNING per skipped row
    with ticker, as_of, and the exception text so a corrupted cache
    or pipeline regression is debuggable.
  - walk_forward_split docstring now describes boundary behavior
    (split=0.0, split=1.0, empty input) explicitly.
  - _select_resolved_snapshots gained its missing return type
    annotation.
  - The internal _split_fn alias was unused-purpose noise; replaced
    with a direct call.
  - Artifact metadata now records walk_forward_cutoff so post-hoc
    debugging can reconstruct the train/holdout split.
  - serialize_artifact now raises ValueError when &#39;extra&#39; keys would
    overwrite core artifact keys (silent override hazard).

No behavior change for healthy datasets; only safety, observability,
and self-documentation.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* feat(engine_ranker): promotion gate (paired-bootstrap Brier + R)

PROMOTE requires lower 95% CI bound of (Brier_baseline - Brier_candidate)
and (R_candidate - R_baseline) both non-negative across 10k bootstrap
resamples. INSUFFICIENT_DATA if any observed degree has fewer than
min_resolved_per_degree holdout rows. Synthetic always-better/always-
worse fixtures verify the verdict logic; the proxy R uses a coarse
sign-of-prediction rule because the real backtest harness runs
separately.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* feat(cli): wave-alpha train engine-ranker

Manual training command: builds outcome dataset from history.sqlite,
fits walk-forward logistic, runs promotion gate, optionally writes the
JSON artifact. --write is gated on verdict=PROMOTE unless --force is
passed; with --force, the actual verdict is recorded in the artifact&#39;s
gate field so the loader&#39;s PROMOTE-only check still refuses to
auto-load it. --dry-run reports the row counts without training.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* chore: T10 final verification — engine ranker branch complete

All 765+ non-web tests pass; ruff clean; wave-alpha train engine-ranker
visible in --help; heuristic-path smoke produces expected behavior on
the analyze command. The branch is ready for review/merge.

CHANGELOG generation is left to the project&#39;s semantic-release pipeline
on merge to main.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* fix(engine_ranker): pre-merge polish from final review

Important:
  - build_outcome_dataset now passes engine_ranker=HeuristicEngineRanker()
    explicitly when replaying through run_analysis. Without this, once a
    logistic artifact ships the training replay would use the trained
    model to extract features for the next round — a self-feedback loop
    that breaks reproducibility across training runs.

Minor:
  - Tightened provider and candidate_to_loadable type hints from Any
    to OHLCVProvider and LogisticEngineRanker respectively.
  - Aligned the artifact JSON metadata key with the spec wording.

Docs:
  - Spec §5.5 and §6.3 updated to acknowledge that the v1 promotion
    gate uses a coarse +1/-1/0 R proxy (not derive_signal-based) and
    does not yet reuse the right_edge.outcome_eval harness. Both
    deferred to a follow-up PR; an in-source TODO marks the proxy.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

---------

Co-authored-by: Alex Chen &lt;jiaxinc31@gmail.com&gt;
Co-authored-by: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`5a3ad16`](https://github.com/chen-star/wave-alpha/commit/5a3ad164ba27c11df65c4ae4469cb643c2582554))

### Unknown

* Worktree outcome data bootstrap (#6)

* feat(history): add replay_snapshot helper for backfill/densify

Re-runs run_analysis at a given (ticker, as_of) against the supplied
provider, upserts the resulting snapshot, and runs the resolver — but
deliberately bypasses record_snapshot&#39;s auto-prune so backfill of old
as_of dates does not delete sibling rows.

* feat(cli): wave-alpha history backfill-signals

Replays run_analysis at every existing (ticker, as_of) and upserts the
snapshot under the current schema. Concretely, this upgrades pre-
max_holding_bars rows so the outcome resolver can act on them. LLM is
forced disabled and auto-prune is bypassed via replay_snapshot.

* refactor(cli): extract _distinct_tickers helper

Replaces backfill-signals&#39; misuse of recent_across_all (a UI-card
helper, capped at 10k rows) and prune&#39;s inline SELECT DISTINCT with
a single _distinct_tickers helper. Same SQL, clearer intent, no
silent-cap footgun.

* feat(cli): wave-alpha history densify

Generates snapshots over (universe × quarterly cadence) using cached
bars. LLM forced disabled, auto-prune bypassed. Silently skips tickers
without cached bars at the requested as_of (universe drift is normal).

* docs(plans): outcome-data bootstrap plan

Captures decisions (quarterly cadence, curated universe, silent-skip
for missing bars, replay_snapshot bypasses auto-prune) and the staged
follow-up work after PR merge: backfill, densify, train, evaluate.

---------

Co-authored-by: Alex Chen &lt;jiaxinc31@gmail.com&gt; ([`881f110`](https://github.com/chen-star/wave-alpha/commit/881f11084b8da2e7873031b6dadad1cd7a03e83d))


## v0.13.0 (2026-05-12)

### Chore

* chore(release): 0.13.0 ([`3b7a106`](https://github.com/chen-star/wave-alpha/commit/3b7a1060f9946decc7933620b6fbd180b6eb4310))

### Feature

* feat: outcome-training evaluation harness (#5)

* docs: spec + plan for outcome-training evaluation harness

Read-only CLI harness to empirically measure whether v0.10.0&#39;s
outcome-driven right-edge training (--use-outcomes) actually
improves model quality vs synthetic-only training. Splits resolved
outcomes by exit_date, trains two arms, scores both on the same
held-out slice, reports per-degree Brier/ECE and a paired bootstrap
Δ Brier CI.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* feat(right-edge): add exit_date field to OutcomeRow

Required by the outcome-training eval harness for temporal split logic.
Populated in build_outcome_dataset from snapshot.outcome.exit_date.
See docs/superpowers/specs/2026-05-11-outcome-training-eval-harness-design.md.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* refactor(right-edge): raise ValueError instead of assert for outcome.exit_date

Asserts get stripped under python -O; a data-contract violation from
the resolver should surface as a real exception. Also trims the
redundant direct-construction half of test_build_outcome_dataset_
populates_exit_date — the dataclass-machinery check adds no coverage
of the production path.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* feat(right-edge): outcome-eval data models

Adds OutcomeEvalConfig, ArmMetrics, OutcomeEvalResult dataclasses for the
outcome-training evaluation harness.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* feat(right-edge): _split_outcomes_temporal with tie-pushing

Implements the temporal split described in spec §4: rank outcomes by
exit_date, hold-out most-recent fraction, boundary ties stay on the
training side. Empty input and zero-holdout cases raise.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* feat(right-edge): _score_arm_from_predictions (Brier + ECE + log-loss)

Per-arm scoring on (calibrated_proba, label, degree) triples. Pure
function — reuses backtest.brier primitives for parity with the
existing promotion gate.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* feat(right-edge): paired bootstrap CI on Brier delta (B - A)

Mirrors the resampling structure of promotion._paired_bootstrap_brier
but operates on a fixed paired hold-out (both arms scored on the same
rows) rather than two independent samples.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* feat(right-edge): verdict from Brier delta CI

improves / no_effect / degrades based on whether the 95% CI excludes
zero on the appropriate side.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* feat(right-edge): run_outcome_eval orchestration

Wires arm A (walk_forward synthetic-only) + arm B (train_blended_final
with training-portion outcomes only) + holdout scoring + paired
bootstrap delta CI + verdict. assign_weights runs AFTER the split so
the per-degree ramp reflects the training count.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* feat(right-edge): format_markdown_report for outcome-eval result

Renders OutcomeEvalResult as a markdown report with config, row
counts, per-arm metrics, delta CI, and verdict sections.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* feat(cli): wave-alpha train outcome-eval

New read-only CLI command that empirically measures whether outcome-
driven right-edge training (--use-outcomes) actually improves model
quality. Writes a markdown report to reports/outcome_eval_&lt;end&gt;.md.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

* style(right-edge): tighten pytest.raises in frozen-dataclass test (B017)

Pre-existing in Task 2; deferred to Task 10&#39;s final lint sweep per
the plan. Also any other ruff cleanups from the final sweep.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt;

---------

Co-authored-by: Alex Chen &lt;jiaxinc31@gmail.com&gt;
Co-authored-by: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`246be53`](https://github.com/chen-star/wave-alpha/commit/246be53e64f7a24ad8ea8f1ffad15247db86bf1b))


## v0.12.0 (2026-05-12)

### Chore

* chore(release): 0.12.0 ([`59f40ae`](https://github.com/chen-star/wave-alpha/commit/59f40aec11ee1b777a0f67ee2fa3a434a697b57a))

### Documentation

* docs: enumerator constraint-eval cache implementation plan

8 tasks covering: Constraint.parsed cached AST (Task 1), evaluate_parsed
companion (Task 2), validator eval_cache kwarg (Task 3), label-convention
invariant test (Task 4), window-major loop restructure with per-window
cache (Task 5), LD opt-in byte-equivalence smoke (Task 6), count-layer
backtest perf gate with byte-identical metrics + &gt;=10% runtime (Task 7),
final verification (Task 8).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`65cffaf`](https://github.com/chen-star/wave-alpha/commit/65cffaf38b5c685ec22f3260e27112d0b0f121ab))

* docs: enumerator constraint-eval cache design spec

Behavior-preserving runtime optimization: pre-parse constraint AST
at template load + per-window evaluation cache shared across
templates within the same (n_required, waves-labels) group.

Targets the +25% per-template runtime cost discovered when adding
leading_diagonal in v0.11.0 (see project memory + 2026-05-10-leading-
diagonal-design.md §10). Layer-3 gate requires byte-identical
count-layer backtest metrics and a &gt;=10% runtime improvement.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`e687354`](https://github.com/chen-star/wave-alpha/commit/e6873543a08b7ea656a6419b47ce3fc3a52f0c5f))

### Feature

* feat(elliott): validator.validate accepts optional eval_cache

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`767583f`](https://github.com/chen-star/wave-alpha/commit/767583f2db0bf236f94e41c841e57d470211a5f6))

* feat(elliott): evaluate_parsed for pre-parsed constraint AST

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`bf23284`](https://github.com/chen-star/wave-alpha/commit/bf232842643b042dded790a073083358770de4d7))

* feat(elliott): Constraint.parsed cached AST property

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`2ffa619`](https://github.com/chen-star/wave-alpha/commit/2ffa619e8f4acd47663f1b72159756928d9cfa47))

### Performance

* perf(elliott): window-major enumerate_counts with per-window eval cache

Restructures the (template × window) outer-product into a
window-major loop grouped by (n_required, tuple(waves)). Within
each group, a fresh per-window dict[str, bool] cache is passed to
validator.validate so shared constraints (e.g., wave_3_not_shortest
on impulse + leading_diagonal + ending_diagonal) evaluate exactly
once per window across all templates in the group.

Behavior-preserving: every count emitted before is emitted with the
same hard_violations, soft_violations, and engine_score.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`e1bfbb5`](https://github.com/chen-star/wave-alpha/commit/e1bfbb58e169a09d14a75e0f96538383a82d658b))

### Style

* style: ruff format + consolidate test imports (RUF100, E402)

ruff format applied to dsl.py, enumerator.py, validator.py, and
the new test_enumerator_eval_cache.py (line wrapping). Test file
imports consolidated to the top to satisfy E402 — eliminates the
mid-file &#39;from wave_alpha.elliott.{models,validator,enumerator}&#39;
imports that had accumulated as tasks were appended.

Also removes a stale &#39;# noqa: BLE001&#39; in dsl.py.evaluate_parsed
(RUF100 — BLE001 is not in the configured ruleset).

uv run ruff check . clean; uv run pytest -q passes 808/808 (port-
8765 flake deselected); ruff format --check clean.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`859fef9`](https://github.com/chen-star/wave-alpha/commit/859fef99266a31208c1891f1bbc454830c6485d4))

### Test

* test(elliott): lock template grouping invariant (n_required, waves)

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`224f1b7`](https://github.com/chen-star/wave-alpha/commit/224f1b702a7f484042604b4bc4278416f9e6d4c3))

### Unknown

* Merge pull request #4 from chen-star/feat/enumerator-eval-cache

perf(elliott): enumerator constraint-eval cache (-28% backtest runtime) ([`205248f`](https://github.com/chen-star/wave-alpha/commit/205248ff84abbbf4e9316e35e3f6ab6f3c9ec290))


## v0.11.0 (2026-05-11)

### Chore

* chore(release): 0.11.0 ([`fae9851`](https://github.com/chen-star/wave-alpha/commit/fae98510693c5851f7bcfa15183a9c9ea23dca3d))

### Documentation

* docs: leading_diagonal implementation plan + spec backtest metrics fix

Spec §5.3 thresholds corrected to use actual CountLayerResult
metrics (top1_hit_rate, top3_hit_rate, sample_size) rather than
the previously-guessed engine_score / total_candidates.

Plan covers 9 tasks: YAML template + load tests (1), hard-constraint
rejection (2), LD-vs-ED scoring disambiguation (3), LD-survives-impulse
overlap guard (4), pipeline integration (5), golden fixture loader +
2 synthetic seeds (6), lean-install bump (7), count-layer backtest
before/after gate (8), final verification (9).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`9ad7f07`](https://github.com/chen-star/wave-alpha/commit/9ad7f07f762c159efdbaa5afd2accc0ed31bbd5c))

* docs: leading diagonal template design spec

Adds leading_diagonal as a peer primitive motive template,
differentiated from ending_diagonal by soft-constraint scoring
(W2 retrace depth, W3 extension). No enumerator/validator/DSL
changes; merge-gated by count-layer backtest neutrality on the
curated 50-ticker universe.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`c8ac674`](https://github.com/chen-star/wave-alpha/commit/c8ac67458ff6bffc6782a7759b2410ac4d3f47fa))

### Feature

* feat(elliott,prefs): ship leading_diagonal opt-in via TemplatesConfig

The Layer-3 count-layer backtest gate failed twice: with the
originally-shipped permissive constraints (top-3 -6pp, runtime +23%)
and with the v1-iteration tightened constraints (top-3 -4pp,
runtime +26%). Diagnosis: LD displaces corrective candidates in the
top-3, costing direction diversity on corrective moves; runtime
regression is structural per-template dispatch cost, not per-window
constraint work.

Resolution: ship LD bundled but opt-in via config, default off.

- New TemplatesConfig Pydantic model with enable_leading_diagonal:
  bool = False; exposes enabled_opt_in_names() -&gt; frozenset[str].
- AppConfig.templates: TemplatesConfig field, default off.
- elliott.templates: new OPT_IN_TEMPLATE_NAMES = {&#34;leading_diagonal&#34;}
  and load_default_templates(enabled_opt_in=...) filters bundled
  templates against the opt-in registry.
- enumerate_counts gains enabled_opt_in_templates: frozenset[str]
  parameter; default loads the filtered (LD-excluded) template set.
- run_analysis and _enumerate_for thread the parameter through.
- scan.orchestrator.run_scan and _scan_one thread it through.
- CLI &#39;analyze&#39; and &#39;scan&#39; commands read cfg.templates.
  enabled_opt_in_names() and pass it in.
- web.deps._run_analysis_uncached reads cfg.templates and passes it.

With LD opt-in off (default), the production engine path is byte-
identical to baseline 9ad7f07; the Layer-3 gate passes trivially
because nothing about the default path changed.

Tests:
- tests/elliott/test_templates.py: new tests for OPT_IN_TEMPLATE_NAMES,
  load_default_templates exclude/include semantics, and unknown opt-in
  names being ignored.
- tests/prefs/test_models.py: TemplatesConfig defaults, override via
  model_validate, default-off contract.
- tests/test_pipeline.py: new test_run_analysis_does_not_emit_leading
  _diagonal_by_default; existing LD pipeline test updated to pass
  enabled_opt_in_templates={&#34;leading_diagonal&#34;}.
- tests/elliott/test_leading_diagonal_golden.py: golden loader passes
  the opt-in (these fixtures specifically exercise LD).

Spec updated with §10 (iteration history + opt-in pivot rationale)
and §4 module-changes table.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`e95f4a8`](https://github.com/chen-star/wave-alpha/commit/e95f4a8b890d40ab9edccc2845e8fe05c5c35d74))

* feat(elliott): add leading_diagonal template (motive primitive)

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`6341d43`](https://github.com/chen-star/wave-alpha/commit/6341d437c4b7e6456b1923f05da72c00aced3fad))

### Fix

* fix(elliott,spec): v1 iteration on leading_diagonal — tighter hard constraints

Layer-3 backtest gate failed on the originally-shipped LD template:
top-3 hit rate -6 pp (0.78 -&gt; 0.72) and runtime +23%. LD displaced
corrective candidates (zigzag/flat/wxy) in the top-3, concentrating
direction predictions on the motive side; the runtime hit came from
LD&#39;s full constraint suite running on every 5-pivot window.

Iteration:
- Promote wave_4_overlaps_wave_1 from soft to hard. Classical EW treats
  W4 overlap as definitional for diagonals; without overlap, the window
  is either an impulse (already validates) or not a 5-wave motive.
- Add wave_2_min_retrace &gt;= 0.3 as hard. A W2 retrace below 0.3 is too
  shallow to be a diagonal corrective — the window is structurally an
  impulse with a tiny pullback.

Both are cheap early-exit filters that keep LD enumeration off most
non-diagonal windows, addressing both the top-3 displacement and the
runtime regression. Adds two regression-guard tests
(test_ld_hard_rejects_w2_too_shallow, test_ld_hard_rejects_when_w4
_does_not_overlap_w1) and updates the spec&#39;s section 3 + 5.1 to
reflect the new constraint set and document the iteration history.

13 unit tests pass; the LD-vs-ED disambiguation tests from Task 3
still hold because their pivot fixtures satisfy the new hard
constraints by construction.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`8b11560`](https://github.com/chen-star/wave-alpha/commit/8b11560e9541361a3372fd03e1b7836038cacb7f))

* fix(spec,ed): symmetric W2 soft on ending_diagonal for LD disambiguation

The original leading_diagonal spec assumed satisfying LD&#39;s
wave_2_deep_retrace soft would raise its score above ED&#39;s on
deep-W2 windows. The engine&#39;s _score formula only penalizes
soft violations and does not reward passes, so LD and ED tied
on those windows (both at zero violations).

Adding a symmetric wave_2_shallow_retrace_preferred soft to
ending_diagonal.yaml penalizes ED on deep-W2 (retrace &gt;= 0.6),
which produces the spec&#39;s intended LD-wins-deep / ED-wins-shallow
ordering through the engine&#39;s actual mechanics.

Spec updated to (a) document the required ED companion change,
(b) add ending_diagonal.yaml to the §4 modules table, and
(c) correct the lean-install template-count bump (&gt;= 8, not &gt;= 7).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`b8b0361`](https://github.com/chen-star/wave-alpha/commit/b8b03614198420968ba8b2199fe5120921ac6e77))

### Style

* style: ruff format + drop unused imports + RUF002 unicode cleanup

ruff format applied to the 5 leading_diagonal-related test files
(line wrapping). Also removes 5 unused imports inside the LD pipeline
integration test (datetime.date/timedelta, Decimal, Bar, Interval —
leftover from the Task 5 bar-builder helper, which is the actual
user of those imports), and replaces 5 ambiguous unicode characters
(× -&gt; x, – -&gt; -) in the docstring of
_build_bars_for_leading_diagonal_at_degree_2 to satisfy RUF002.

uv run ruff check . clean.
uv run pytest -q clean (797 passed, port-8765 flake deselected).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`3b7590b`](https://github.com/chen-star/wave-alpha/commit/3b7590b4a0e14c5c345e361ec0a32c579fdf1629))

### Test

* test(lean-install): bump template-count threshold for leading_diagonal

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`fb916b5`](https://github.com/chen-star/wave-alpha/commit/fb916b5928fd71adc6b43d43d32ba6bc855899a9))

* test(elliott): leading_diagonal golden fixture loader + 2 synthetic seeds

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`4092614`](https://github.com/chen-star/wave-alpha/commit/4092614c8f5b56fd0165bc9380e19f29881e841e))

* test(pipeline): leading_diagonal end-to-end integration

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`fa62eec`](https://github.com/chen-star/wave-alpha/commit/fa62eece47650c0287f02e60b62e017785682dab))

* test(elliott): LD survives W4-overlap windows that reject impulse

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`d4d69bb`](https://github.com/chen-star/wave-alpha/commit/d4d69bbff37ac088166b6aeb6d4f2916f36ef42a))

* test(elliott): LD vs ED disambiguation contract via soft-constraint scoring

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`d4d7aa6`](https://github.com/chen-star/wave-alpha/commit/d4d7aa6819dd926c74bf10b18d3a9da9a0824ac8))

* test(elliott): leading_diagonal hard-constraint rejection coverage

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`35a82e7`](https://github.com/chen-star/wave-alpha/commit/35a82e74fa8fefa0272aa4c99cd050b67571cd07))

### Unknown

* Merge pull request #3 from chen-star/feat/leading-diagonal-template

feat(elliott): add leading_diagonal template (opt-in) ([`1b0bbbb`](https://github.com/chen-star/wave-alpha/commit/1b0bbbbd67ae0ad6c8d35c1a755ef8c7bcff4711))


## v0.10.0 (2026-05-11)

### Chore

* chore(release): 0.10.0 ([`b1f4937`](https://github.com/chen-star/wave-alpha/commit/b1f4937d5cc556f4cefeecfd791f0ddb1aaa173f))

* chore: refresh GitNexus symbol counts after outcome-tracking reindex

5992 symbols | 8169 edges | 53 flows

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`c4360ac`](https://github.com/chen-star/wave-alpha/commit/c4360accfed181e393a438c3c2079d3e31234c2a))

### Documentation

* docs: CLAUDE.md notes outcome-driven training + history retention

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`317398e`](https://github.com/chen-star/wave-alpha/commit/317398ed9ef3d995e9c1125800f5b4044ebbd7a2))

* docs: implementation plan for outcome-driven right-edge training

11-task TDD plan covering: history.sqlite auto-prune (3 tasks), outcome
dataset construction with recompute-at-train-time (3 tasks), sample-weight
plumbing through fit_logistic/fit_platt (1 task), blended final refit
(1 task), prefs + CLI surface (2 tasks), and CLAUDE.md update.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`b5064c4`](https://github.com/chen-star/wave-alpha/commit/b5064c4d26085402b6e8420d53690894d2f35ab2))

* docs: spec outcome-driven right-edge training with bounded history

Adds the design for blending outcome-labeled examples from history.sqlite
with the existing synthetic dataset for right-edge logistic training,
plus an auto-prune retention policy that keeps history.sqlite bounded.
Features are recomputed at train time from cache.sqlite bars — zero
extra storage in history.sqlite.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`e0d8b41`](https://github.com/chen-star/wave-alpha/commit/e0d8b41b272107a70a780d7b2f6c6d53d1deac39))

### Feature

* feat(cli): &#39;train right-edge --use-outcomes&#39; blends outcomes into final refit

Add --use-outcomes, --outcome-weight-target, --outcome-limit, and
--drop-time-stops flags to `train right-edge`; the blended-final block
runs after walk_forward (OOS estimator stays synthetic-only) and splices
the blended logistic + Platt back into the WalkForwardResult before
serialize_artifact writes the JSON. Extend serialize_artifact with an
optional outcome_training metadata dict so the provenance is preserved
in the artifact without changing the default artifact schema.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`52e0add`](https://github.com/chen-star/wave-alpha/commit/52e0add90bb50fa57da5fde7a5cb8170172f1bd3))

* feat(prefs): add RightEdgeOutcomeTrainingConfig

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`6c974d5`](https://github.com/chen-star/wave-alpha/commit/6c974d59e23766034ee88a6c0ea6e7c133de22e8))

* feat(right_edge): train_blended_final refits on synthetic + outcomes

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`6721bba`](https://github.com/chen-star/wave-alpha/commit/6721bbaf0c36efc278efd50f7984feeff657c7a2))

* feat(right_edge): fit_logistic / fit_platt accept optional sample weights

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`5013428`](https://github.com/chen-star/wave-alpha/commit/5013428914afb231a4bfa469ae88b93904b1d234))

* feat(right_edge): build_outcome_dataset — recompute features at train time

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`9925fd4`](https://github.com/chen-star/wave-alpha/commit/9925fd48c1ca92d5430c6830e7959b3b7c0106d2))

* feat(right_edge): outcome-dataset data models + per-degree weight ramp

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`9e9f05c`](https://github.com/chen-star/wave-alpha/commit/9e9f05c380c0d9f94da224bcbd1e6518f070e78e))

* feat(cli): add &#39;wave-alpha history&#39; sub-app (stats, prune)

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`317786a`](https://github.com/chen-star/wave-alpha/commit/317786a6863fbd2fcdee787e0f0f9e46a0c9aa7b))

* feat(history): auto-prune snapshots older than retention_days

Per-ticker prune inside record_snapshot after the resolver pass. Best-effort
— failures log at WARNING and never propagate. Default retention is 730 days
(configurable; 0 disables). ([`b3eb200`](https://github.com/chen-star/wave-alpha/commit/b3eb2000a2ff3c03782c061868688e0f033045bc))

* feat(prefs): add HistoryConfig.retention_days (default 730)

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`41671e6`](https://github.com/chen-star/wave-alpha/commit/41671e6cd5ed058752ddc43f74dd002517f87c4c))

* feat(history): add prune_older_than SQL primitive

Adds a per-ticker DELETE function to history/store.py that removes
snapshots with as_of &lt; cutoff and returns the row count; missing DB
returns 0 (idempotent). Groundwork for bounded retention in Task 3
(record_snapshot) and the wave-alpha history prune CLI (Task 4).

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`c472a70`](https://github.com/chen-star/wave-alpha/commit/c472a70078696276ddfa957810e13a8b656a2c55))

### Fix

* fix: wire outcome_training config to CLI + surface wrong-degree skips

- Change --outcome-weight-target default to None so config.yaml values
  are respected; CLI flag overrides when explicitly passed.
- Load right_edge.outcome_training from config inside train_right_edge,
  applying all three fields (n_target_per_degree, include_time_stops,
  time_stop_label) as base defaults before applying CLI overrides.
- Add skipped_wrong_degree counter to OutcomeDataset and
  build_outcome_dataset; reported in CLI output and outcome_metadata.
- Update all test fixtures (test_training_blended.py,
  test_train_right_edge_use_outcomes.py) to pass skipped_wrong_degree=0.
- Add test_build_outcome_dataset_counts_wrong_degree_skips.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`a4406af`](https://github.com/chen-star/wave-alpha/commit/a4406afc429bbfaeb61c88dba3188d6c1d116595))

* fix(right_edge): symmetric sample_weights shape guard in fit_platt

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`3ab81d1`](https://github.com/chen-star/wave-alpha/commit/3ab81d1673ca0163821cc25c5171d5c40d51f1f7))

### Refactor

* refactor(right_edge): tidy outcome_dataset post-Task-6 nits

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`b22e029`](https://github.com/chen-star/wave-alpha/commit/b22e02938d4d05f9eb378dd730bd420bc03553a8))

### Style

* style: apply ruff format across new files

CI&#39;s ruff format --check is stricter than the ruff check the implementers
ran. Auto-formatting via `uv run ruff format .` resolves the 14 files
flagged.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`9b8a0a8`](https://github.com/chen-star/wave-alpha/commit/9b8a0a814441f2bbb39b8d5d9b95cc782e4fcd63))

* style(history): hoist timedelta import + pin caplog level

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`211804a`](https://github.com/chen-star/wave-alpha/commit/211804a949b2428e1455a9e3365bb4a462cc882b))

### Test

* test(right_edge): cover label_for_status + guard n_target_per_degree&gt;0

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`da1fe2a`](https://github.com/chen-star/wave-alpha/commit/da1fe2addf60376b863ec37c5c51eb31fdb97608))

* test(cli): tighten history-prune deletion assertion

Replace the weak `len(aapl_rows) + len(msft_rows) &lt;= 3` check with
`len(aapl_rows) == 0` and `len(msft_rows) == 0` so the test fails if
prune is a no-op, not just when it deletes too many rows.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`9e95103`](https://github.com/chen-star/wave-alpha/commit/9e9510302c175b8d17c4cb583f944ab4c3170b71))

* test(prefs): lock HistoryConfig ge=0 constraint ([`0451b38`](https://github.com/chen-star/wave-alpha/commit/0451b38fc62b6f2b3a5533de6af74ffbe4c50733))

* test(history): lock in exclusive-cutoff semantics for prune_older_than

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`1210b89`](https://github.com/chen-star/wave-alpha/commit/1210b890c7f7f871bd944acab23687c3979f4f1f))

### Unknown

* Merge pull request #2 from chen-star/feat/outcome-driven-right-edge-training

feat: outcome-driven right-edge training + bounded history retention ([`e5a1e6e`](https://github.com/chen-star/wave-alpha/commit/e5a1e6e05051025b61c01af68d29839f764b1796))


## v0.9.0 (2026-05-11)

### Chore

* chore(release): 0.9.0 ([`6823ed3`](https://github.com/chen-star/wave-alpha/commit/6823ed330a72f958250f0863ffb6a3bd9596e9bf))

* chore: sync uv.lock to wave-alpha v0.8.0

Reflects the semantic-release version bump merged on origin/main during
this branch&#39;s lifetime.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`a2cb0ae`](https://github.com/chen-star/wave-alpha/commit/a2cb0ae99a732a64b631ecf2a17e592193655fed))

* chore: refresh GitNexus symbol counts after outcome-tracking reindex

5979 symbols | 8155 edges | 53 flows

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`10eb372`](https://github.com/chen-star/wave-alpha/commit/10eb37230157d6a4d8c63bf0116ac18dcb86884d))

### Ci

* ci: pin uv run pytest to the matrix python version

`uv sync --python 3.12 …` installs into a 3.12 venv, but the next step
`uv run pytest` without --python falls back to the project&#39;s lower
requires-python bound (3.11), recreates the venv, and loses pytest.

Pin the run command to the same matrix python so the 3.12 / 3.13 matrix
entries actually exercise those versions. Pre-existing breakage; surfaced
now that lint passes and the test job runs to completion.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`a39ff7f`](https://github.com/chen-star/wave-alpha/commit/a39ff7f8448f7ac907545d4d790b979b86ee43d2))

### Documentation

* docs(plan): outcome-tracking implementation plan

15 tasks. Foundation: pure resolve_outcome + Outcome dataclass; backtest
adapter refactor with golden parity. History layer: SnapshotOutcome,
additive Snapshot.outcome, idempotent outcome_status column migration,
unresolved_for query, resolve_pending_for, eager hook in record_snapshot.
Surface: compute_outcome_badge, deepdive context wiring, _history.html
chip + badge, wave-alpha resolve CLI.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`0b23a56`](https://github.com/chen-star/wave-alpha/commit/0b23a56e3f74c5d08d8789138ff26607b75394fe))

* docs(spec): outcome tracking on history snapshots (v2)

Layers per-snapshot outcome resolution onto the v1 wave-count history.
Adds Snapshot.outcome (additive payload field) + outcome_status column,
shared resolver extracted from backtest/trade_layer._walk_to_exit, eager
hook in record_snapshot, wave-alpha resolve CLI for batch backfill, and
per-row outcome chip + outcome badge in the deepdive history card.

R is raw engine accuracy (no slippage/commission/borrow) — the deepdive
measures whether the engine was right, not what the user&#39;s P&amp;L would
have been with their TradeRules. Backtest path keeps slippage/commission
in a thin adapter on top of the shared resolver.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`a4d7f2b`](https://github.com/chen-star/wave-alpha/commit/a4d7f2b356a6c4653ae2ffb2be22a75e6e919e5d))

### Feature

* feat(cli): wave-alpha resolve [--ticker|--all] [--json]

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`ee578b2`](https://github.com/chen-star/wave-alpha/commit/ee578b2cf712e8ca78235c201ea8868d186f54db))

* feat(web): outcome chip + outcome badge in history card

Renders per-row outcome chips (✓/✗/⊘/⊙ with R values) and a summary
outcome badge line in the History card header. CSS uses existing palette
tokens (--bull, --bear, --warn, --cyan, --fg-muted) — no new neon.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`e0a9af7`](https://github.com/chen-star/wave-alpha/commit/e0a9af79040887e627775402f435fb857eb68e5f))

* feat(web): pass outcome_badge into history fragment context

Compute outcome_badge via history_service.compute_outcome_badge inside
history_fragment and add it to the TemplateResponse context dict alongside
the existing stability badge. Failures default outcome_badge to None
(same guard as badge). Adds web test verifying the route returns 200
with a resolved-outcome snapshot in the DB.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`6e6f9ee`](https://github.com/chen-star/wave-alpha/commit/6e6f9ee62ca973037ddf49c099989baecc214aca))

* feat(history): compute_outcome_badge for deepdive header

Add OutcomeBadge frozen dataclass and compute_outcome_badge pure function
that aggregates resolved/pending snapshot outcomes into a summary badge
(resolved_count, target_count, hit_rate, avg_R, in_flight_count) for the
deepdive UI header (Tasks 12/13).

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`43e9e22`](https://github.com/chen-star/wave-alpha/commit/43e9e22817369ef84924e1741f2c418e66d52a76))

* feat(history): trigger resolver from record_snapshot; populate new SnapshotSignal fields

Wire `resolver.resolve_pending_for` into `record_snapshot` so prior
unresolved snapshots are resolved whenever a new snapshot is written.
Extend `_to_snapshot_signal` to populate all fields the resolver needs
(count_id, count_pattern, wave_label, degree, confidence,
expected_bars_to_target, max_holding_bars). The resolver call shares the
same try/except as the upsert so a resolver failure never breaks the
write path.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`eb66825`](https://github.com/chen-star/wave-alpha/commit/eb668256ac89eb548c1d04489d6f3ca59d1e83f9))

* feat(history): resolve_pending_for walks unresolved snapshots forward

Adds history/resolver.py with resolve_pending_for(ticker, daily_bars, *, db_path, today)
that loads NULL/pending rows via store.unresolved_for, converts SnapshotSignal to
TradeSignal, delegates to trades.outcome.resolve_outcome, and upserts the result.
v1 signals missing max_holding_bars are skipped. Covered by 5 new end-to-end tests.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`6af28ef`](https://github.com/chen-star/wave-alpha/commit/6af28ef6392310e6430d52b8c90579fb3a750e05))

* feat(history): outcome_status column + unresolved_for query + idempotent migration

Adds `outcome_status` denormalized column to the snapshots table (idempotent
ALTER TABLE migration for v1 DBs), updates `upsert` to write it, adds a
supporting index, and introduces `unresolved_for(ticker)` returning NULL +
pending rows ascending by as_of for the resolver to walk.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`9a9f06b`](https://github.com/chen-star/wave-alpha/commit/9a9f06bd946f80be8de6fd86e35eac02b6e36d50))

* feat(history): SnapshotOutcome model + additive Snapshot.outcome field ([`e5bd52d`](https://github.com/chen-star/wave-alpha/commit/e5bd52db01b384436bf30dd6486757161301bce5))

* feat(history): extend SnapshotSignal with optional resolver fields

Add count_id, count_pattern, wave_label, degree, confidence,
expected_bars_to_target, max_holding_bars as optional (None default)
fields so v1 snapshots round-trip without migration.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`eaca3c8`](https://github.com/chen-star/wave-alpha/commit/eaca3c8186bd4d0048ea8eadc79038d81ff484d0))

* feat(pipeline): expose AnalyzeResult.daily_bars for outcome resolver

Adds daily_bars field (list[Bar], default []) to AnalyzeResult and
populates it from the existing daily local in run_analysis — no second
fetch. Downstream consumers (Tasks 9-10 outcome resolver) can access
lookahead-safe daily bars without re-querying the provider. ([`3f687cb`](https://github.com/chen-star/wave-alpha/commit/3f687cbe471db0eae519eb1c96bac71cd491a568))

* feat(trades): pure resolve_outcome for target-hit happy path ([`4cc5e09`](https://github.com/chen-star/wave-alpha/commit/4cc5e09ff51afcc585d419c83df1651e01a2c353))

### Fix

* fix(tests): strip ANSI escapes before help-text substring assertions

Typer&#39;s rich help panel embeds ANSI color codes inside flag names when
the runner&#39;s stdout is detected as color-capable (CI&#39;s pytest env). That
breaks the `assert &#34;--json&#34; in res.stdout` substring checks because the
flag name renders as e.g. `\x1b[1m--json\x1b[0m`.

Strip ANSI codes via a small per-file regex before asserting. Pre-existing
breakage exposed for the first time now that lint passes and the test job
runs to completion under the PR workflow.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`6b52184`](https://github.com/chen-star/wave-alpha/commit/6b52184a81ca03b3a2f57da9e69123d6db8e6a2d))

* fix(history): isolate per-row failures in resolve_pending_for

Widen the try/except to cover the full per-row processing block so that
a failure on one row (e.g. a bad resolve_outcome call) logs a warning
and skips that row rather than aborting the entire batch for the ticker.
Adds a regression test that monkeypatches resolve_outcome to raise on
the first row and verifies the second row is still resolved.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`5f6d41b`](https://github.com/chen-star/wave-alpha/commit/5f6d41b5533dbc3a9494e45e97ee0cff285096f7))

### Refactor

* refactor(backtest): delegate _walk_to_exit to shared trades.outcome.resolve_outcome

Adapter layers backtest-specific concerns on the resolver:
- Gap-aware adverse stop fills on non-fill bars (preserve legacy intent).
- +1 time-stop boundary so the resolver&#39;s 1-indexed bars_held &gt;= max_holding
  fires on the same bar the legacy offset &gt;= max_hold (0-indexed) did.
- bars_held adjusted -1 to preserve the existing 0-indexed TradeRecord schema.
- Slippage / commission / borrow continue to layer in _make_record unchanged.

All 37 existing trade-layer tests pass with no behavioral change. The shared
resolver remains pure (raw engine accuracy semantic for live history). ([`cd8fd6c`](https://github.com/chen-star/wave-alpha/commit/cd8fd6c6f62addae1c1f64410e86d63b0e38b008))

### Style

* style: ruff format pass

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`557a5a2`](https://github.com/chen-star/wave-alpha/commit/557a5a2275a9cd8e263666cf4fef249b37008b8e))

* style(trades): ruff format outcome.py and tests ([`7e152c9`](https://github.com/chen-star/wave-alpha/commit/7e152c90db38cfc7b09daf69f8ac3b65ab3237dc))

### Test

* test(trades): MFE/MAE coverage on intraday extremes

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`a3405ec`](https://github.com/chen-star/wave-alpha/commit/a3405ecb0c6aaa83fcdc28fdb0c3f71e2b088c30))

* test(trades): resolver coverage for stop/time_stop/pending/short/edges

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`aecc039`](https://github.com/chen-star/wave-alpha/commit/aecc039f3c7dbf6570a645361142ea0deec6792f))

### Unknown

* Merge pull request #1 from chen-star/worktree-feat+outcome-tracking

feat: outcome tracking on history snapshots (v2) ([`517dcaf`](https://github.com/chen-star/wave-alpha/commit/517dcafff1b7c02471fd1678bb943734d1a45068))


## v0.8.0 (2026-05-10)

### Chore

* chore(release): 0.8.0 ([`e7b9a37`](https://github.com/chen-star/wave-alpha/commit/e7b9a37c2785432742e29e57200a21396756ceb0))

* chore: refresh GitNexus symbol counts after wxy reindex

5534 → 5694 symbols, 7542 → 7755 relationships, 51 → 54 flows reflect
the wxy template + helpers + tests now indexed.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`236d741`](https://github.com/chen-star/wave-alpha/commit/236d741a7cc7b3d18e21cd7c25e17f48422dccbf))

* chore: sync uv.lock to wave-alpha v0.7.0

Project version moved to 0.7.0 in 388fdbb but the lockfile stayed at
0.6.0. Picked up by uv sync during the wxy implementation session.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`b391d2c`](https://github.com/chen-star/wave-alpha/commit/b391d2c6161103918fd33ea7c9de31a2bdb7f967))

* chore: gitignore .claude/ local agent tooling

Untrack all skill files under .claude/; this directory holds
machine-local Claude Code config and skill bundles, not project code.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`cd3f5b7`](https://github.com/chen-star/wave-alpha/commit/cd3f5b77534c6f90fee1860c2e4e90f425515deb))

### Documentation

* docs(test): clarify wxy golden fixture README

Document wxy_anywhere expected_subcounts behavior; list all three synthetic
seeds; explain the synthetic_positive naming/assertion mismatch.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`f4647dd`](https://github.com/chen-star/wave-alpha/commit/f4647ddd2a02fa09b988a2a22fea4d13c1ace38c))

* docs(elliott): update _fit_sub_counts docstring after degeneracy relaxation

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`ba28de2`](https://github.com/chen-star/wave-alpha/commit/ba28de201caedf30aed4269a5882724f493c27e5))

* docs(plan): complex corrections (W-X-Y) — 10-task implementation plan

Maps every spec section to a TDD task: template + smoke test, sub_pivots
parameter widening with graceful degrade, helper functions, strict and
degenerate sub-count fitting paths, end-to-end composite branch wiring,
soft-violation propagation, pipeline integration, golden fixture loader.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`03478bc`](https://github.com/chen-star/wave-alpha/commit/03478bc245e369b91ea60a86a419aa1ebd3c3c61))

* docs(spec): complex corrections (W-X-Y) — engine classification design

Adds wxy.yaml with sub-count enforcement scoped to the new template.
Recursive primitive-only fitting at degree N-1 leverages the existing
multi_degree subset invariant; primitive templates remain flat. v1
targets count quality in sideways markets, not new signal types.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`583f43f`](https://github.com/chen-star/wave-alpha/commit/583f43f4c87b8d4a705840e0e23cab27d4d42db1))

### Feature

* feat(pipeline): pass sub_pivots through _enumerate_for

Wire degree-(N-1) pivots into enumerate_counts for each enumerated degree
so composite templates (wxy) receive the sub-pivot list they need to fit
primitive corrective sub-counts.  Before this change, _enumerate_for called
enumerate_counts without sub_pivots, causing the enumerator to silently skip
wxy candidates (graceful degrade path in enumerator.py).

Adds Layer 3 integration test (test_run_analysis_emits_wxy_when_pivot_structure_matches)
with a purpose-built 135-bar fixture that stratifies degree-1 / degree-2 pivots
via ATR-scaled thresholds (4% intrabar range, sub-swings 8-10 pts vs major
swings 25-28 pts).  Asserts §3.6 of spec 2026-05-10-complex-corrections-design.md. ([`740289b`](https://github.com/chen-star/wave-alpha/commit/740289b32c3d3ae479c24008163ab36a7ffb3ccb))

* feat(elliott): wire composite branch in enumerate_counts

Replace placeholder double-continue with real composite sub-count fitting block.
Composite templates (wxy) now fit and attach sub-counts when sub_pivots is
provided; degenerate slots (X-wave) accepted with no_subdivision soft violation.
Three new end-to-end tests verify populated sub_counts, top-K competition, and
no-self-recursion invariant (11 total in test_wxy_enumeration.py).

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`c0f7842`](https://github.com/chen-star/wave-alpha/commit/c0f7842521b84aa6eb5067418bac69fa5b738808))

* feat(elliott): degeneracy relaxation for slots with &lt;4 sub-pivots

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`559520e`](https://github.com/chen-star/wave-alpha/commit/559520e732cf95660f8c6fdb296a0d13631e1859))

* feat(elliott): add _fit_sub_counts strict path

Appends _fit_sub_counts helper to enumerator.py that iterates over each
composite template slot, slices sub_pivots to the slot&#39;s boundary,
rejects if sub-window has &lt;4 pivots (degenerate path deferred to Task 5),
and delegates to recursive enumerate_counts with primitive_templates only.
Returns None to signal rejection when no spanning count validates or the
pattern is not in allowed_sub_patterns for that slot.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`9380d67`](https://github.com/chen-star/wave-alpha/commit/9380d6788c36deb8d232e9e92fea2edff6fe2d97))

* feat(elliott): add _slice_sub_pivots helper for composite enumeration ([`9c4529f`](https://github.com/chen-star/wave-alpha/commit/9c4529f76b2cbc841bd070bcf18e424ca7b469de))

* feat(elliott): wire sub_pivots arg and composite-name gate ([`ae6b4df`](https://github.com/chen-star/wave-alpha/commit/ae6b4dfdb0d016fac9a78b7831cb6a096552afdb))

* feat(elliott): add wxy template (W-X-Y double-three corrective) ([`ee8e863`](https://github.com/chen-star/wave-alpha/commit/ee8e863a14d44bb303c712f0e203a8917fe67c26))

### Style

* style(elliott): ruff format test_wxy_enumeration.py

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`a990dc6`](https://github.com/chen-star/wave-alpha/commit/a990dc684c6228054f24f40c36eb95e22ba9f354))

* style(elliott): match wxy yaml format and test conventions

Drop blank lines between constraint entries to match sibling templates;
use set comparison for allowed_sub_patterns to avoid order-sensitivity.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`9f71f06`](https://github.com/chen-star/wave-alpha/commit/9f71f061726782a1721e799937b5730417ef7be4))

### Test

* test(elliott): real-chart wxy golden fixtures (MSFT 2018, AAPL 2022)

Adds two 200-bar fixtures sourced from local cache:
- MSFT 2018-12-31 — wxy at rank 0 of degree-2 (score 0.949), all three
  sub-counts populated (W=zigzag, X=expanded_flat, Y=zigzag); strongest
  positive case with no degenerate slot.
- AAPL 2022-10-15 — wxy at rank 1 of degree-2 (zigzag wins 0.993),
  wxy_anywhere assertion verifies the engine still produces a valid wxy
  candidate when a primitive scores higher.

Brings total Layer-2 golden fixtures to 5 (3 synthetic + 2 real-chart),
satisfying spec §6.2 acceptance bar.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`2335f64`](https://github.com/chen-star/wave-alpha/commit/2335f6498ee163aa9905002db52e24a8e9bde452))

* test(elliott): wxy golden fixture loader + 2 synthetic seeds

Adds a fixture-driven golden test loader (tests/elliott/test_wxy_golden.py)
that parametrizes over JSON fixtures in tests/elliott/fixtures/wxy_golden/.
Ships 3 synthetic seed fixtures: synthetic_positive (primitive guard),
synthetic_positive_strict (wxy_anywhere assertion confirming wxy is produced
in degree-2 candidates), and synthetic_negative_zigzag (regression guard).
Extends the loader with a wxy_anywhere expected value per the acceptable
resolution path when zigzag scores higher than wxy at rank-0.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`5feac79`](https://github.com/chen-star/wave-alpha/commit/5feac79e03d05ea36d531ff29e2735380c21a734))

* test(elliott): assert wxy soft-violation propagation contract ([`93a6379`](https://github.com/chen-star/wave-alpha/commit/93a6379f7ae37313145b847a89a016319ee26008))


## v0.7.0 (2026-05-10)

### Chore

* chore(release): 0.7.0 ([`388fdbb`](https://github.com/chen-star/wave-alpha/commit/388fdbbb035ade2fad9e53f59e6ebbf2d2598b34))

* chore(skills): vendor ui-ux-pro-max design intelligence skill

Adds 28 files (SKILL.md + scripts + CSV reference data for styles,
palettes, font pairings, charts, and stack-specific guidelines).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`8a68218`](https://github.com/chen-star/wave-alpha/commit/8a6821809e32dd23e2fccc694ee6e29cba97bcf8))

* chore: gitignore .superpowers/ ephemeral session state

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`7739345`](https://github.com/chen-star/wave-alpha/commit/7739345939188e1406be59599054665924ed547c))

* chore: refresh GitNexus symbol counts after reindex

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`8767ee2`](https://github.com/chen-star/wave-alpha/commit/8767ee2cb81961d182975ed65fdda7c11118272a))

* chore: refresh GitNexus symbol counts after Stooq indexing

Updates AGENTS.md and CLAUDE.md preamble with the post-reindex counts
(5524 nodes, 7533 edges, 51 flows). Generated by `npx gitnexus analyze`
in Task 13 of the Stooq provider plan.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`9e8f287`](https://github.com/chen-star/wave-alpha/commit/9e8f287f49b0cad7ec715e38d856c3ac4991916a))

### Documentation

* docs(claude): replace force-include refs with packages directive

force-include was dropped from pyproject.toml in c8f27d6; CLAUDE.md
still pointed at it in the templates and web sections. Updated both to
describe the current packaging mechanism (`packages = [&#34;src/wave_alpha&#34;]`
includes every file under that tree, Python and non-Python).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`66d314a`](https://github.com/chen-star/wave-alpha/commit/66d314a55cdd09264b7599d3ee6c06fbcbfec80d))

* docs(changelog): note Stooq OHLCV fallback under [Unreleased]

Includes the 2026-05 note that the endpoint now requires a free,
captcha-issued API key. Without one, the chain silently uses Yahoo only.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`7167141`](https://github.com/chen-star/wave-alpha/commit/716714136b4ba9d06e0532c8afdbd5abe96fa2a2))

### Feature

* feat(data): plumb Stooq API key (config + env + StooqProvider)

Stooq&#39;s CSV endpoint started requiring a captcha-issued API key. The
fallback chain silently returned [] in production. Pivot:

- AppConfig.data.stooq_api_key (frozen Pydantic, parallel to LLMConfig.api_key).
- resolve_stooq_api_key(cfg) — STOOQ_API_KEY env var beats config.
- StooqProvider(api_key=...) — early-return [] when missing or empty;
  &amp;apikey=... appended to URL when set.
- Three production wiring sites (cli/app.py, web/deps.py, web/routes/deepdive.py)
  resolve and pass the key.
- Existing test_stooq tests updated to construct StooqProvider with a fake key.
- New tests for the no-key/empty-key short-circuit and the URL formatting.
- Live smoke test now skips cleanly when STOOQ_API_KEY env var is unset.

The reframe: feature is no longer &#39;free, automatic Yahoo derisk&#39;. It&#39;s
&#39;optional resilience for users who get a free Stooq key (one captcha)&#39;.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`b47c53a`](https://github.com/chen-star/wave-alpha/commit/b47c53a5e18ea96fc5ebf6aa83d0ce60adf40a85))

* feat: wire Stooq fallback into CLI and web providers

cli/app.py:_default_provider and web/deps.py:_run_analysis_uncached
now construct CachedProvider(FallbackProvider([Yahoo, Stooq])). Yahoo
stays primary; Stooq covers daily/weekly when Yahoo fails or returns
empty. 4h stays Yahoo-only via interval-aware skip.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`dc2aeb3`](https://github.com/chen-star/wave-alpha/commit/dc2aeb3aefbe582e64f60f8456240d48fb1a51f8))

* feat(data): add FallbackProvider chain decorator

Tries providers in order, skipping any whose supported_intervals
exclude the requested interval, and falling through on exception or
empty result. Returns [] if every provider skips, fails, or yields
empty (no exception ever propagates).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`3e2ccf8`](https://github.com/chen-star/wave-alpha/commit/3e2ccf89ff105ab6605c5ac65b5e4a55184ee3ec))

* feat(data): add StooqProvider class with HTTP fetch

Composes the symbol mapper and CSV parser behind an OHLCVProvider with
supported_intervals={DAILY, WEEKLY}. Uses stdlib urllib.request through
a small _http_get indirection that tests monkeypatch. No retries inside
the provider; transport errors propagate for FallbackProvider to catch.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`f81cf2e`](https://github.com/chen-star/wave-alpha/commit/f81cf2ea4852ca3d8947700e1d7bbf3d1efe8f3a))

* feat(data): add Stooq CSV parser

Parses Stooq&#39;s CSV body to ascending-by-date Bars. Handles the &#39;No data&#39;
sentinel, empty bodies, halted-day &#39;-&#39; rows, and out-of-order inputs.
Decimal precision matches the existing yfinance parser (4dp).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`76b906c`](https://github.com/chen-star/wave-alpha/commit/76b906c79ec90a4a6af42a67089bc90a97eba16b))

* feat(data): add Stooq symbol mapping

US-equity tickers map to Stooq&#39;s grammar: lowercase with class-share
dots replaced by dashes plus `.us` suffix. Non-US symbols deferred.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`f3b9b56`](https://github.com/chen-star/wave-alpha/commit/f3b9b56cb912c406645fd15121333761cdd55325))

* feat(data): add supported_intervals declaration to OHLCVProvider

Backward-compatible class attribute defaulting to all Intervals. Lets
FallbackProvider skip providers that don&#39;t support a requested interval
(e.g. Stooq has no 4h data).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`9537f60`](https://github.com/chen-star/wave-alpha/commit/9537f607bac7e43d185203e825c7e1116e9572bd))

### Fix

* fix(web): wire Stooq fallback into deepdive chart-bars path

The Task 8 wiring missed `web/routes/deepdive.py:_load_bars`, which
constructed CachedProvider(YahooProvider()) directly for chart rendering.
Now it uses FallbackProvider([Yahoo, Stooq]) like _default_provider and
_run_analysis_uncached, so the chart-bars path picks up the Stooq fallback.

Found by code-quality review of 379a141; spec §8.3 inventory was incomplete.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`b9480e3`](https://github.com/chen-star/wave-alpha/commit/b9480e3ddfe6ef87475784b4a5ab6bfc8178a8ba))

### Test

* test(data): end-to-end simulated Yahoo failure

Two tests exercising the full production stack (CachedProvider +
FallbackProvider + Yahoo + Stooq) with Yahoo monkeypatched to raise.
Verifies (a) daily/weekly fall through to Stooq&#39;s bars and (b) 4h
correctly returns [] without consulting Stooq (interval-skipped).

Stooq is constructed with a fake api_key=TESTKEY so the no-key
short-circuit (added in Task 10b) doesn&#39;t pre-empt the fallback path.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`6d90d0f`](https://github.com/chen-star/wave-alpha/commit/6d90d0fcf18c32c55df0def281fa38ce7b272456))

* test(data): add opt-in live-Stooq smoke test

Two tests behind @pytest.mark.network that verify a real fetch of
AAPL daily and weekly bars succeeds against stooq.com. Skipped by
default; run with `pytest -m network`. Catches breakage in Stooq&#39;s
CSV format or endpoint availability.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`af03700`](https://github.com/chen-star/wave-alpha/commit/af0370082e6cc91b66a89001ffec0c292d97b0ea))

* test: register network pytest marker and skip by default

Adds a `network` marker for tests that hit live external endpoints
(opt in with `-m network`) and updates default `addopts` with
`-m &#39;not network&#39;` so the live-network tests added in Task 10 are
deselected by default. Strict-markers stays in effect.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`76e0dd1`](https://github.com/chen-star/wave-alpha/commit/76e0dd19dbbe7698a16b6668d4c695740aedc177))

* test(data): integration test for CachedProvider wrapping FallbackProvider

Verifies cache hit/miss/persist semantics work when upstream is the
fallback chain. Covers: secondary supplies bars (cache persists them),
chain empty falls back to cache, fully-skipped chain produces [].

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`e162ebc`](https://github.com/chen-star/wave-alpha/commit/e162ebcf98ad29ad8c0841f41c365769e152170d))

* test(data): add Stooq CSV fixtures for parser tests

Five daily bars and five Friday-anchored weekly bars matching Stooq&#39;s
real CSV header. Hand-curated illustrative values; tests assert
structure, not specific prices.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`21e7389`](https://github.com/chen-star/wave-alpha/commit/21e73891e8d7837c7dd1d1b74986427a4e6c9f32))


## v0.6.0 (2026-05-11)

### Build

* build: drop redundant force-include from wheel build

hatchling&#39;s `packages = [&#34;src/wave_alpha&#34;]` already pulls in every file
under that directory (Python and non-Python). The separate force-include
list re-added templates, static assets, and py.typed, which produced
duplicate entries in the wheel&#39;s zip local headers. PyPI&#39;s warehouse
validator rejected those wheels with HTTP 400 &#34;Invalid distribution
file. Duplicate filename in local headers.&#34; Removing force-include
keeps every shipped file present (verified: 138 files, twine check
passes) and the wheel uploads cleanly.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`c8f27d6`](https://github.com/chen-star/wave-alpha/commit/c8f27d60245d16a6c51e9e68634ac1c505a23706))

### Chore

* chore(release): 0.1.0 ([`dd070bb`](https://github.com/chen-star/wave-alpha/commit/dd070bb8b04baab75b8849625f3b4e6619a06c1d))

* chore: automate releases via python-semantic-release

Adds a version-bump workflow driven by Conventional Commits and tightens
publish.yml so feature-branch tags can&#39;t ship to PyPI.

- .github/workflows/version.yml: on push to main, semantic-release reads
  feat/fix/BREAKING commits since the last tag, bumps version in
  pyproject.toml + src/wave_alpha/__init__.py, commits, tags, and pushes.
  workflow_dispatch supports forced patch/minor/major bumps.
- .github/workflows/publish.yml: refuse tags whose commit isn&#39;t an
  ancestor of origin/main; switch GitHub Release notes to --generate-notes.
- pyproject.toml: [tool.semantic_release] config (version locations,
  branch=main, tag v{version}, commit &#34;chore(release): {version}&#34;,
  changelog mode=update so existing CHANGELOG narrative is preserved).
- RELEASING.md: rewritten for the automated flow + one-time setup
  (PyPI Trusted Publisher, pypi GitHub Environment, RELEASE_TOKEN PAT).
- CONTRIBUTING.md: Conventional Commits guidance + relaxed CHANGELOG rule.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`5e61d42`](https://github.com/chen-star/wave-alpha/commit/5e61d42e6e0c936a3f49fb34efb1049b8d55e1ce))

* chore(release): regenerate uv.lock for 0.6.0

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`7ea6ddd`](https://github.com/chen-star/wave-alpha/commit/7ea6dddee1c21133442b06c801d06730aa288a63))

* chore(release): 0.6.0

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`a40ef40`](https://github.com/chen-star/wave-alpha/commit/a40ef4017547d149da801cbcdd5f183d324e0748))

* chore(ruff): exclude .claude/ from lint scope

The .claude/ directory contains user-machine-specific Claude Code plugin
files that are not part of the wave-alpha source. Their lint state is
out of scope for this project&#39;s CI.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`43d21b3`](https://github.com/chen-star/wave-alpha/commit/43d21b3fa628da6ec00e144f885265fcb936db40))

* chore(typing): add py.typed marker (PEP 561)

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`a1c184d`](https://github.com/chen-star/wave-alpha/commit/a1c184d31a678d68e3bc613480666a414f737140))

* chore(packaging): regenerate uv.lock for [llm] extra

Reflects pyproject change in 713c2f0 — anthropic moved from base deps
to the optional [llm] extra. Functional install set is unchanged when
syncing with --extra llm.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`530fde9`](https://github.com/chen-star/wave-alpha/commit/530fde982f087c75090e718f8721050d6d502160))

* chore(packaging): add license, classifiers, keywords, project URLs

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`9e503d4`](https://github.com/chen-star/wave-alpha/commit/9e503d4953ad5602e8168fd684c0a82bd41d0bff))

* chore: add MIT LICENSE

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`a26c818`](https://github.com/chen-star/wave-alpha/commit/a26c81829835bc581131847b46f08a648433bb03))

* chore: gitignore .claude/scheduled_tasks.lock runtime file ([`15a52f6`](https://github.com/chen-star/wave-alpha/commit/15a52f6909093aa902b4ecf0cadf8b0b9ab1708e))

* chore: sync uv.lock and gitnexus blocks

uv.lock catches up to v0.5.9 (would have shipped with 4a900b6).
AGENTS.md / CLAUDE.md gitnexus blocks updated by the most recent
`npx gitnexus analyze` run — project name `wave_alpha` → `wave-alpha`
plus refreshed symbol/relationship/flow counts.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`3fe7797`](https://github.com/chen-star/wave-alpha/commit/3fe77973860c6064956815d227a7de8a1b2c6178))

* chore(release): bump to v0.5.7

EBT empirical fit ships: degree-2 _DEFAULT_BARS_PER_DEGREE entry
replaced with the empirical median (17 bars) from the 2026-05-05
sanity run.  max_holding_bars decoupled from the × 1.5 multiplier;
degree-2 now uses the empirical p75 (29).  Validated against the
ablation grid Phase 2 winner cell — see
docs/superpowers/specs/2026-05-07-ebt-empirical-fit-design.md §6.

Verdict: MARGINAL.  |expectancy delta| = 0.0535R (slightly above the
0.05R PASS threshold).  Refit ships because the empirical median is
the truthful default for the user-facing expected_bars_to_target
signal field.  Caveat: the grid-spec recommended-defaults absolute
expectancy is now +0.218R, not +0.272R.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`aa394cb`](https://github.com/chen-star/wave-alpha/commit/aa394cbab66f385b4f62ee8d29b244fb1c1a87f1))

* chore(release): bump to v0.5.6

Ablation grid sweep ships: tiered Phase 1 (16 cells) → Phase 2 (32
cells) harness with cross-cell aggregation, GO/NO-GO trigger,
markdown + JSON + CSV artifacts, and a backtest grid CLI subcommand.

Phase 2 verdict (§6.3 + §6.4): GO with stop_source=atr_multiple as
the dominant lever (+0.116R sensitivity over count_invalidation).
Best cell expectancy +0.272R, PF 1.44, hit rate 0.40, n=1818. The
TradeRules() default change to stop_source=atr_multiple lands as a
separate commit on main post-merge. New deferred decision G7 logs
the target_source wiring gap discovered during Phase 2.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`5b922ae`](https://github.com/chen-star/wave-alpha/commit/5b922aeebbcd93e25019664868245491fbb3f3da))

* chore(configs): pin Phase 2 direction_modes=[long] from Phase 1 winner

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`38a4c54`](https://github.com/chen-star/wave-alpha/commit/38a4c54df770807bccfac6fa28d8f40a82cc7f4e))

* chore(release): bump to v0.5.5

atr_multiple stop_source is now wired (was a silent fallback to
last_pivot before). Any prior user of stop_source=&#34;atr_multiple&#34;
gets new behavior — this is the documented goal, not a regression.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`6973607`](https://github.com/chen-star/wave-alpha/commit/69736070920000bd274628a1eda92f9c75fd0484))

* chore(release): sync __version__ string to v0.5.4

The runtime version string in src/wave_alpha/__init__.py is what the
CLI and web UI surface; pyproject.toml drives the build. Both must
move together.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`d20e884`](https://github.com/chen-star/wave-alpha/commit/d20e8848a2e094e4d8057e4d08e48a570ff8c04a))

* chore(release): bump to v0.5.4

derive.py target+stop direction fixes (commits 308407e via merge
b67dcf8..2cfc011) change observable behavior of any short-signal
path — analyze, scan, web UI, backtest. Spec §9 S4 condition met.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`76ad0ed`](https://github.com/chen-star/wave-alpha/commit/76ad0ed86917a185ecb5be6bbe7c899b6e84f9d5))

* chore(gitignore): exclude reports/ and .gitnexus from version control

reports/ holds large reproducible sanity-run artifacts (durable record
lives in the spec). .gitnexus/ holds the GitNexus index binary (was
already untracked in the working tree).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`b4a28bd`](https://github.com/chen-star/wave-alpha/commit/b4a28bde11a467675db8df1af65e7e3efbe08677))

* chore(release): bump to v0.5.3 ([`7f25979`](https://github.com/chen-star/wave-alpha/commit/7f2597924f9df6c8859197671fa68718ebe91bb6))

* chore(release): bump to v0.5.1

Polish release covering four follow-ups from v0.4 + v0.5 reviews:
- CachedProvider tail-staleness invalidation (real bug fix)
- deps.analyze memoization across HTMX fragments (perf, ~4x)
- Coherence tri-badge per spec §11 #3
- Three CR cleanups: SYMBOL_RE consolidation, exception class in
  scan errors, sort_by(reverse=...) for ticker key

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`a419d8b`](https://github.com/chen-star/wave-alpha/commit/a419d8bb9fb307bc3513ec0f6d8d88d7d2f8d92c))

* chore(scan,watchlist): three cleanup items from v0.5 review

- Consolidate the SYMBOL_RE regex into watchlist/symbol.py; store and
  parsers now import it instead of carrying near-identical copies.
- Preserve the exception class name in scan error messages
  (&#34;RuntimeError: provider down&#34; vs &#34;provider down&#34;) — distinguishes
  silent yfinance returns from HTTPErrors at a glance.
- sort_by(key=&#34;ticker&#34;, reverse=True) now honors the reverse flag
  (previously silently ignored); scan route defaults to ascending for
  ticker sort, which is the expected UI behavior.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`14c5eac`](https://github.com/chen-star/wave-alpha/commit/14c5eac6ac6465412e5989c062568af1c1298ff2))

* chore(scan,web): apply review fixes — public predicted_direction + form HTMX + honest cache claim

- coherence/score.py: rename _predicted_direction → predicted_direction
  (drop the underscore — three external consumers were importing it
  with a type:ignore). Update call sites in scan/orchestrator and
  backtest/count_layer.
- web/templates/scan.html: replace four independent buttons (each
  losing the others&#39; state) with a single hx-trigger=change form whose
  hx-include semantics ship sort + signals_only together. Adds a
  &#34;coherence&#34; sort option for parity with sort_by&#39;s supported keys.
- README.md: correct the v0.5 nightly-batch claim — CachedProvider is
  permissive and does NOT refetch when the tail is stale; document the
  manual cache-clear workaround and call out staleness invalidation as
  a v0.5.x follow-up.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`1cee613`](https://github.com/chen-star/wave-alpha/commit/1cee6135cc844af3359363bbb255c247f36e69a2))

* chore(release): bump to v0.5.0; document watchlist + scan

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`29c2ce5`](https://github.com/chen-star/wave-alpha/commit/29c2ce59d0f6a6f6706062d285e8ca607cf0cb6c))

* chore(backtest): apply review fixes — docstring + caveat + help strings

- count_layer.py: rewrite CountLayerEvaluator docstring to drop the
  misleading &#34;invokes the engine&#34; wording and accurately describe the
  inlined pivot-detect + enumerate path.
- report.py: add a caveat under the right-edge calibration section
  noting v0.4 features are placeholder constants; the headline numbers
  reflect a near-uniform prior and will become meaningful once the
  v0.4.x logistic model replaces the heuristic.
- cli/app.py: add help= strings to the pivot subcommand options for
  parity with the count subcommand.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`bcc96f4`](https://github.com/chen-star/wave-alpha/commit/bcc96f400343e1da84d075ab930a3dc435e4e7a3))

* chore(release): bump to v0.4.0; document backtest harness

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`3ddb96f`](https://github.com/chen-star/wave-alpha/commit/3ddb96ffd969a2caee87edcad80e1c369a7be4ce))

* chore(deps): refresh uv.lock for v0.3.0 wave-alpha version

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`f64205d`](https://github.com/chen-star/wave-alpha/commit/f64205d57fbba6064fc182b2673c7766c2e68818))

* chore(tests): apply ruff format to new test files

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`02c8db3`](https://github.com/chen-star/wave-alpha/commit/02c8db3f84b07528c0118a2da8f6647d67f56a04))

* chore(web): drop dead .gitkeep files in non-empty directories

templates/, static/, and static/vendor/ all contain real files now.
The placeholder .gitkeep files inflate the wheel unnecessarily.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`489bc29`](https://github.com/chen-star/wave-alpha/commit/489bc2970ebab77a49b21d4b859b8937b5cbf8bf))

* chore(web): clarify scenarios &#34;last pivot&#34; badge wording

Replace misleading &#34;invalidates at&#34; label with &#34;last pivot&#34; — the
displayed price is the final pivot of the alternate count, not a
structural invalidation level. Add a comment noting that per-count
invalidation logic is deferred.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`be5f8da`](https://github.com/chen-star/wave-alpha/commit/be5f8da5a1bcf3b098d140d5a1583d9631315e69))

* chore(release): bump to v0.3.0; document Web UI

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`9232b17`](https://github.com/chen-star/wave-alpha/commit/9232b17546bf2f2e7b254def9ba6031a19365189))

* chore(build): force-include web/templates and web/static in wheel

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`f8c0e86`](https://github.com/chen-star/wave-alpha/commit/f8c0e8670f28b529a7eb002a718b150b586fbfd9))

* chore(vendor): Alpine.js v3.13.10 (MIT)

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`f0291ca`](https://github.com/chen-star/wave-alpha/commit/f0291ca3f08641fc991b887c21db046bac6ad1c3))

* chore(vendor): HTMX v1.9.12 (BSD)

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`52e136d`](https://github.com/chen-star/wave-alpha/commit/52e136dba600ffc15cfcb7c1cbff8c0a51e11ee5))

* chore(vendor): TradingView Lightweight Charts v5.0.3 (MIT)

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`d1ed688`](https://github.com/chen-star/wave-alpha/commit/d1ed6881b5012b956dc9d881521d6562b4f4d519))

* chore(web): empty package scaffolding

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`a71f139`](https://github.com/chen-star/wave-alpha/commit/a71f139f88260c2fc9e945ad2f563c82e2163e64))

* chore(deps): add [ui] optional extra (fastapi, uvicorn, jinja2)

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`2397edd`](https://github.com/chen-star/wave-alpha/commit/2397edd3a7c2f23036cf08c5112fc8a3bb8aea5d))

* chore(prefs,llm): drop unused config fields and re-export llm public API

Remove AppConfig.default_period_years and AppConfig.default_degree — neither
is read anywhere on this branch and they clutter &#39;config show&#39;. Update the
test accordingly.

Populate llm/__init__.py with a full re-export list (matching the pattern
established by prefs/__init__.py) so callers can import from wave_alpha.llm
rather than reaching into submodules.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`7c254e7`](https://github.com/chen-star/wave-alpha/commit/7c254e7a3b4f1838688b54d9a28fcd78f41ad4b5))

* chore(release): bump to v0.2.0; document LLM flags

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`799f671`](https://github.com/chen-star/wave-alpha/commit/799f671f0ebf67c6e8124fcad4f876a27735e070))

* chore: initialize pyproject and pin Python 3.11

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`29b99f5`](https://github.com/chen-star/wave-alpha/commit/29b99f5155821588f59bf4f91585a55da6b00bc6))

### Ci

* ci: harden version-bump skip guard and document empty-commit gotcha

The previous guard used contains(head_commit.message, &#39;chore(release):&#39;),
which false-matched commits whose body merely mentioned the literal
string. Switched to startsWith() so only the actual release-commit
subject is filtered.

Also added a note in RELEASING.md that paths-ignore plus an empty
commit means version.yml will not fire — first-run smoke tests should
use the workflow_dispatch button instead.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`b31f173`](https://github.com/chen-star/wave-alpha/commit/b31f17362054fc52e215b4a85df410779c26ca51))

* ci: add Trusted Publisher workflow for PyPI releases

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`b0b6433`](https://github.com/chen-star/wave-alpha/commit/b0b64338ae552a663fccc0ed21e4cc3d70c92e91))

* ci: add lint + pytest matrix workflow

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`077da70`](https://github.com/chen-star/wave-alpha/commit/077da7091078b5674319a9acf91cc2947083bbbf))

### Documentation

* docs: add PyPI version and lifetime downloads badges to README

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`647d455`](https://github.com/chen-star/wave-alpha/commit/647d455e4d0191162469e4b1ca72ca9534ce8850))

* docs(plan): implementation plan for Stooq OHLCV provider

13 tasks across provider abstraction, StooqProvider, FallbackProvider,
production wiring, network-test discipline, and end-to-end simulation.
TDD throughout. References docs/superpowers/specs/2026-05-10-stooq-provider-design.md.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`bb3f15e`](https://github.com/chen-star/wave-alpha/commit/bb3f15e47bc0c5ef37e17991346acded8871b8a5))

* docs: spec for Stooq OHLCV provider as Yahoo fallback

Derisks the single-OHLCV-source fragility shipped in 0.6.0 by adding
Stooq behind a new FallbackProvider decorator. Yahoo stays primary;
Stooq covers daily/weekly only (no intraday). 4h remains Yahoo-only.

Deferred from docs/superpowers/plans/2026-05-09-pypi-public-release.md.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`770c03f`](https://github.com/chen-star/wave-alpha/commit/770c03f374ee011e11db65d1c852d050c4f7caab))

* docs(changelog): note trust popovers + /about page under [Unreleased]

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`82eb13c`](https://github.com/chen-star/wave-alpha/commit/82eb13c46e44c240d1175e043bfce33460088fb0))

* docs: trust-popups spec + implementation plan

Adds the design spec and 12-task implementation plan for click-pinned
trust popovers and the new /about page that documents the engine
pipeline end-to-end.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`8864025`](https://github.com/chen-star/wave-alpha/commit/886402585a42c24df23eabff248c24889d3264e3))

* docs(changelog): note web UI redesign for 0.6.0

Adds a Changed-section bullet for the web UI redesign and links to the
2026-05-09 design spec. Also rolls in a small ruff-format cleanup of
tests/web/test_static_assets.py picked up during the final lint pass. ([`29fda6d`](https://github.com/chen-star/wave-alpha/commit/29fda6dc3150f8203bc592a30367aed5b4ef25e1))

* docs(plan): add web UI redesign implementation plan

15 bite-sized TDD tasks: 5 backend (ranking filters, watchlist
enrichment, Home view, route registration), 10 frontend (CSS rewrite,
macros, base layout, page templates, fragment partials, chart theme).
Final task is a manual smoke walk + CHANGELOG entry.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`35878eb`](https://github.com/chen-star/wave-alpha/commit/35878eb9c4e723b24700f0d2c6ddb5f15a3b3330))

* docs(web): drop &#34;Heat&#34; KPI from redesign spec

User asked what Heat actually was — it was decoration without a precise
metric definition. Removing rather than carrying a vague v1.1 obligation;
Home/Scan ship with the four concrete KPIs (Signals · Top R:R ·
Watchlist hits · Stable picks).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`9a7b927`](https://github.com/chen-star/wave-alpha/commit/9a7b927bebc542df20f531e1c08ce818b5793a08))

* docs(web): add design spec for cyber-spectrum UI redesign

Captures decisions from the brainstorm session: dark hybrid + neon
spectrum direction, new Home dashboard at /, progressive-disclosure
microcopy, pattern/wave color coding, and locked classic green/red
candlestick colors. Ready for writing-plans.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`59d64a9`](https://github.com/chen-star/wave-alpha/commit/59d64a96a8ee9e4fdf6e1e2bce3f50734a6dad20))

* docs: add RELEASING.md describing tag-driven publish workflow

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`95c757c`](https://github.com/chen-star/wave-alpha/commit/95c757c7d3110ab7450d398e0e9703d74992d4c6))

* docs: add CONTRIBUTING.md with dev loop and PR checklist

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`22b730b`](https://github.com/chen-star/wave-alpha/commit/22b730b582337547d85f37f4d8bbcafbba74130f))

* docs: add SECURITY.md with private vulnerability reporting channel

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`e0ba99c`](https://github.com/chen-star/wave-alpha/commit/e0ba99cda5b0ebecb697dea728672ac5ff8f669c))

* docs: add CHANGELOG.md seeded for 0.6.0 public release

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`08abfe9`](https://github.com/chen-star/wave-alpha/commit/08abfe9c31bf268fedca114eb32258b11b463d7c))

* docs(plan): add 2026-05-09 PyPI public release plan

Captures legal/packaging blockers, release hygiene docs, CI/CD via
Trusted Publisher, and the 0.6.0 release ritual in 14 tasks across
4 phases.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`5d0cb50`](https://github.com/chen-star/wave-alpha/commit/5d0cb50424e7e4b6878b6d042a8254d281e9da1c))

* docs(readme): restructure features as capability-grouped sections

Replaces the flat feature bullet list with a &#34;What it does&#34; section: an
at-a-glance table of six capabilities (wave detection, trade signals,
scanning, history, backtests, optional LLM) each linked to its detail
section, followed by per-capability subsections with tight bullets.
Reads top-down from what&#39;s possible to how each piece works.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`62dfffd`](https://github.com/chen-star/wave-alpha/commit/62dfffd4b1c8a4b8255e703a3ec1e62fdadf9bd8))

* docs(readme): add TOC, prerequisites, CLI table, and history section

Restructures the README for clarity: adds a table of contents, splits
prerequisites and installation from quick start, introduces a CLI
command reference table, and surfaces the history snapshot feature
along with `history.sqlite` in the persistence list.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`4071114`](https://github.com/chen-star/wave-alpha/commit/4071114fc629d5afdcd3a955ca28f4328a9ca389))

* docs(plan): Claude Code Max LLM spike implementation plan

Two-task plan: TDD-build ClaudeCodeClient (subprocess transport to
`claude -p`), then a throwaway scripts/spike_claude_code_compare.py
that runs the engine once, calls both clients on the same
LLMRequest, and dumps reports/claude_code_spike.json for go/no-go.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`f2c6b84`](https://github.com/chen-star/wave-alpha/commit/f2c6b843a3348b6407fe0d0120b863e73b497eab))

* docs(spec): Claude Code Max as --llm-mode live backend (spike)

Throwaway spike to test whether shelling out to `claude -p` can
serve as the LLM transport for users on a Max plan, eliminating
the need for an Anthropic API key. Side-by-side compare on one
ticker, dumps reports/claude_code_spike.json, hard go/no-go on
functional equivalence + latency + auth.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`ce44241`](https://github.com/chen-star/wave-alpha/commit/ce44241861ff54d22c24e6588aceef4855d4a56d))

* docs(plan): EBT refit for degrees 1 and 3 implementation plan

8 tasks: yaml + smoke (T1) → long backtest (T2) → inspect script (T3)
→ run + cross-check (T4) → derive.py + tests update if SHIP (T5) →
verdict file (T6) → version bump if SHIP (T7) → merge (T8).

T5/T7 conditional on per-degree threshold (≥100 winners).  Both-HOLD
outcome merges spec-only without code change.

Spec: docs/superpowers/specs/2026-05-08-ebt-refit-1-and-3-design.md.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`5c33c63`](https://github.com/chen-star/wave-alpha/commit/5c33c63fab9f8c16f25cea390831b421d972ccae))

* docs(spec): EBT refit for degrees 1 and 3 — design

Two-phase spec: (A) run multi-degree backtest to collect per-degree
trade outcomes, (B) fit empirical median+p75 for degrees that meet
≥100-winner threshold, update _EBT_MEDIAN_BY_DEGREE and
_EBT_P75_BY_DEGREE.

Closes the calibration gap S6 deliberately deferred (§9 W1).
Mirrors 2026-05-07 degree-2 EBT methodology; criterion simplified
because heuristic→empirical comparison has no rigorous baseline.

Lands as v0.5.11 if at least one degree ships; no version bump if
both fail threshold.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`5a972e7`](https://github.com/chen-star/wave-alpha/commit/5a972e7135d36a8cc624c8c963cd7593eccebbbe))

* docs(plan): multi-degree enumeration implementation plan

8 tasks: 3 failing tests → RED verify → minimal pipeline change
→ GREEN verify + regression sweep → conditional test audit →
lint → version bump 0.5.10 → merge.

Key choice: §4.3 per-degree EBT test lives at derive_signal unit
level (not full pipeline) for determinism — pipeline&#39;s score-driven
ranked[:3] may not surface all 3 degrees as signals.

Spec: docs/superpowers/specs/2026-05-08-multi-degree-enumeration-design.md.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`9937351`](https://github.com/chen-star/wave-alpha/commit/9937351b23fdb3604f7b4c87cb96f43d85eb8d26))

* docs(spec): multi-degree enumeration design — closes S6

Make pipeline._enumerate_for emit candidates across degrees [1, 2, 3]
instead of only degree 2.  Pivot detector and enumerator are already
parameterized; change is at the pipeline assembly layer.

Per-degree top-K = 2 (6 candidates max per analyze).  Each candidate
carries count.degree; downstream derive_signal, EBT lookup, LLM
rerank, web UI all key on it without modification.

Heuristic EBT for degrees 1/3 (10/15 and 60/90) stays as-is — the
empirical refit is a deferred follow-up that needs multi-degree
trade data, which requires this ship first.

Closes S6 deferred from 2026-05-05 sanity run §9.  Lands as v0.5.10.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`088a91b`](https://github.com/chen-star/wave-alpha/commit/088a91b5d9eb81217af36d1ac06a1b67cc4135fe))

* docs(spec): target_R sweep §6 verdict — MARGINAL Δ=-0.013R

Sweep on curated universe 2020-2024:
- baseline (fib_extension): +0.215R, n=2242
- best fixed_R (target_R=3.0): +0.202R, n=2331
- Δ = -0.013R (within ±0.05R MARGINAL band per spec §4)

Curve monotonically increasing (1.5 &lt; 2.0 &lt; 2.5 &lt; 3.0) with
diminishing returns; fib_extension appears as a soft upper bound
under current rules and time-stop. fixed_R remains a documented
opt-in. v0.5.9 stays the head; no version bump.

Adds CLAUDE.md domain-conventions bullet noting the verdict.
Includes incidental AGENTS.md/CLAUDE.md gitnexus stat refresh.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`8368e02`](https://github.com/chen-star/wave-alpha/commit/8368e0273b0be51e7ddc0faac72dfe796def3fe4))

* docs(plan): target_R sweep implementation plan

9 tasks: 2 yaml configs + smoke validation, 2 long-running grid
runs (baseline first, then sweep), verdict authoring, conditional
GO-path default flip + version bump, merge.

Spec: docs/superpowers/specs/2026-05-08-target-r-sweep-design.md.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`8983343`](https://github.com/chen-star/wave-alpha/commit/89833432984b61372c3efd8d9c26064110d4edd9))

* docs(spec): target_R sweep design

Single-axis sweep ranking target_R ∈ {1.5, 2.0, 2.5, 3.0} against
the fib_extension baseline on the curated 50-ticker universe over
2020-2024.  Closes the question Phase 2 couldn&#39;t answer because the
target_source axis was degenerate pre-G7.

Two-yaml shape (baseline 1-cell + sweep 4-cell) avoids the
8-cell Cartesian-product redundancy that would waste ~60% of
runtime on duplicate fib_extension cells.

0.05R three-tier verdict (matches Phase 2 sensitivity threshold).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`beab93f`](https://github.com/chen-star/wave-alpha/commit/beab93f5039556cc9e27eb583eb5c1a4e5ad09fe))

* docs(spec): EBT empirical fit §6 validation — MARGINAL verdict

Single-cell validation against the ablation grid Phase 2 winner shows
post-refit expectancy +0.218R vs pre-refit +0.272R, |delta|=0.0535R.
Verdict: MARGINAL (just above the 0.05R PASS threshold).

Mechanism: tighter time-stop (max_holding 37→29 bars) produces +189
trades and +2.5pp hit rate but −0.054R per-trade expectancy.
Aggregate profit −11.5% post-refit.

Refit ships because the empirical median is the truthful default for
the user-facing expected_bars_to_target signal field.  Caveat logged:
the grid-spec recommended-defaults absolute expectancy is now +0.218R,
not +0.272R.  Companion artifact:
reports/ebt-empirical-fit-2026-05-08/comparison.md (gitignored).

Validation YAML: configs/ebt_validation_winner.yaml.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`a319757`](https://github.com/chen-star/wave-alpha/commit/a319757791778fbdebf76ec07a25d69a80eb6942))

* docs(specs): close S2 + G4 deferrals — EBT refit ships separately

Both prior specs deferred the EBT empirical refit to a separate
follow-up.  The follow-up landed at
docs/superpowers/specs/2026-05-07-ebt-empirical-fit-design.md.
Update the §9 deferred-decisions tables to reference the resolution
spec.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`a0eceb4`](https://github.com/chen-star/wave-alpha/commit/a0eceb44b9ae7b6308b2d30006084d011bcc0212))

* docs(plan): EBT empirical fit — 5 tasks, ~5 commits

Phase A (immediate): T1 refit derive.py with empirical lookups +
2 new tests (TDD). T2 close §9 S2 + §9 G4 in sibling specs.

Phase B (post-grid-merge, sequenced): T3 single-cell validation
re-run vs Phase 2 winner. T4 fill spec §6 with comparison + verdict.
T5 final review + v0.5.7 bump (skipped on FAIL verdict).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`a9e1a19`](https://github.com/chen-star/wave-alpha/commit/a9e1a19856f5e44131319b2556061b7375bb3a89))

* docs(spec): EBT empirical fit — refit degree-2 from sanity run

Closes 2026-05-05-trade-sim-sanity-run.md §9 S2 and
2026-05-06-ablation-grid-design.md §9 G4. Replaces the heuristic
_DEFAULT_BARS_PER_DEGREE[2]=25 with the empirical median (17 bars,
n=148 winners), and decouples max_holding_bars from the hardcoded
× 1.5 multiplier by adding a separate _EBT_P75_BY_DEGREE lookup
(degree 2 = 29, the empirical p75 of winners). Other degrees
fall back to the existing × 1.5 ratio until S6 multi-degree
enumeration ships.

Validation: re-run the ablation grid&#39;s Phase 2 winner cell
post-refit; pass criterion |expectancy delta| ≤ 0.05R.

Sequencing: chronologically gated on the ablation grid spec
landing v0.5.6 to main. This branch is parallel-track work and
cannot merge until the grid does.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`b2dcefb`](https://github.com/chen-star/wave-alpha/commit/b2dcefb102a0c7739051bfb59cb291e28223c6ed))

* docs(spec): ablation grid Phase 2 results — §6.3 + §6.4 verdict

Phase 2 ran 32 cells over 2020-2024 in 15h 55min (warm cache).
Best cell: entry-immediate_stop-atr-multiple_target-fib-extension_minconf-0.45,
expectancy +0.272R, PF 1.44, hit rate 0.40, n=1818.

Three findings:
1. stop_source=atr_multiple is the dominant Phase 2 lever (+0.116R
   sensitivity over count_invalidation). Combined with Phase 1&#39;s
   direction lift (+0.25R), the full recommended config is +0.4R
   above the sanity-run centroid.
2. atr_multiple cells have dramatically higher hit rates (0.40-0.48
   vs 0.24-0.33) — vol-scaled stops avoid noise-trip pivots.
3. target_source axis is degenerate. _target_price ignores
   rules.target_source; _target_price_fixed_r exists but is never
   called. 16 of 32 cells duplicated the same path. Logged as G7;
   doesn&#39;t invalidate the stop_source verdict.

Verdict: Defaults updated (partial). Ship stop_source=atr_multiple as
new TradeRules() default with the v0.5.6 release. direction_modes
recommendation goes in docs only (long-only default conflicts with
the main design&#39;s bidirectional principle). entry_trigger and
min_confidence defaults unchanged (axes essentially flat).

§9 G7 added: wire target_source (mirror of atr_multiple S5 work).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`9411ec3`](https://github.com/chen-star/wave-alpha/commit/9411ec332e36774769a46f25c5e714d6a109c516))

* docs(spec): ablation grid Phase 1 results — §6.1 + §6.2 GO

Phase 1 ran 16 cells over 2020-2024 on the curated 50-ticker universe
in 7h 29min wall-clock. Best cell:
minconf-0.55_dir-long_entry-immediate, expectancy +0.174R, PF 1.39,
n=2151. Verdict: GO — six long-only cells cleared the trigger;
direction_modes is the dominant axis (+0.25R sensitivity gap, 8x
any other axis); min_confidence and entry_trigger are essentially
flat. Pin direction_modes=[long] for Phase 2.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`815dbfb`](https://github.com/chen-star/wave-alpha/commit/815dbfb1223d24d315f034363242f2718387cf2f))

* docs(plan): ablation grid sweep — 10 tasks, ~10 commits

Phase A (build harness): T1 GridConfig + cartesian product, T2 GridResult
aggregation, T3 phase1_trigger, T4 run_grid orchestration, T5
render_grid_markdown, T6 YAML configs, T7 CLI subcommand. Phase B
(operational): T8 Phase 1 run + spec fill, T9 conditional Phase 2 run
+ spec fill, T10 version bump. T1-T5 + T7 are TDD; T6/T8/T9/T10 are
configuration/execution/metadata.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`15d40e8`](https://github.com/chen-star/wave-alpha/commit/15d40e82da36bd71dfbb4f1c43bc4fd5bb9c7b4d))

* docs(spec): tiered ablation grid sweep — Phase 1 + conditional Phase 2

Closes the v0.5 finishing-step commitment from main design §8 and the
trade-sim layer §13 roadmap. Tiered structure motivated by sanity-run
findings: a 16-cell screening grid (min_confidence × direction_modes
× entry_trigger) screens for a positive-expectancy region cheaply,
and a 32-cell validation grid (entry × stop × target × min_confidence,
direction pinned at Phase 1 winner) only runs if the trigger fires
(best cell expectancy ≥ +0.05R OR PF ≥ 1.05, closed_trades ≥ 200).

NO-GO branch names explicit engine-work next-steps (S6 multi-degree
enumeration, per-pattern min_confidence, coherence recalibration) —
no silent retries with new axes. Phase 2 honors the S5 closure by
exercising atr_multiple head-to-head against count_invalidation, and
prunes the redundant target_source axis (count_target == fib_extension
today per da9b0d5).

Architecture: new src/wave_alpha/backtest/grid.py (orchestration over
existing run_trade_layer), grid_report.py for cross-cell summary, and
a backtest grid CLI subcommand. 8 tests pin shape, determinism, and
aggregation correctness; engine numerics remain the trade-sim layer&#39;s
contract.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`7462986`](https://github.com/chen-star/wave-alpha/commit/746298667857e827997ce5c79b05faccc8d4c8b7))

* docs(plan): atr_multiple stop wiring — 3 tasks, ~4 commits

Task 1 wires the dispatch in _stop_price and threads bars through
derive_signal (TDD, 5 invariant tests). Task 2 integrates with
pipeline.run_analysis (TDD, 1 per-candidate-skip integration test).
Task 3 final review + v0.5.5 bump.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`d0c1832`](https://github.com/chen-star/wave-alpha/commit/d0c1832be117fcef047e4f95b1547626a925b451))

* docs(spec): atr_multiple stop_source wiring

Wires _stop_price_atr_multiple into derive_signal so
TradeRules.stop_source=&#39;atr_multiple&#39; produces an ATR-based stop
instead of silently falling through to last_pivot.

Narrow scope: no new TradeRules fields (deferred to grid spec per
trade-sim layer §5.1 commitment), strict ValueError raise on missing
or insufficient bars, pipeline.run_analysis catches at the
per-candidate boundary so one bad candidate doesn&#39;t lose its ranked
siblings. Six TDD tests pin the invariants. Closes 2026-05-05 sanity
run S5.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`83166a8`](https://github.com/chen-star/wave-alpha/commit/83166a8efecf9823969524113b47dfdaecbebedb))

* docs(spec): trade-sim sanity run §7+§10 — fix ledger + GO verdict

§7: 3 fix commits ledgered (1 prewarm tooling, 2 engine bugs in
derive.py covering target direction + count_invalidation 5-seg short
stop, 1 inspector C5 degenerate-skip). The two engine bugs in row 2
affect any short-signal path, not just this sanity run.

§10.1: GO. All §5.1 correctness pass (C5 skipped per S6). E4/E5
reverse-monotonicity is a documented finding (large samples, both
TF-count slices agree, plausible drivers — direction skew + threshold
calibration — that the ablation grid will disentangle). Spec follow-ups
listed: sweep min_confidence, slice coherence-by-direction, track EBT
empirical median, add atr_multiple after S5 wires it.

Lint cleanup: removed unused fib_extension import, replaced ambiguous
× with * in derive docstring.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`2cfc011`](https://github.com/chen-star/wave-alpha/commit/2cfc01142559cc6adece1188cb53a3e0671dd85b))

* docs(spec): trade-sim sanity run §6 results filled

Phase E iter 1 results: 4577 closed trades, hit rate 0.142, expectancy
-0.130R, PF 0.71, 28% 4h coverage. All §5.1 correctness PASS (incl C5
skipped per S6). E4/E5 reverse-monotonic — full coherence performs
worse than partial in both 2-TF and 3-TF slices. Documented as a
finding for the ablation grid, not a sim bug: large samples, both
slices agree, two plausible drivers (direction skew + threshold
calibration) both grist for the grid.

Empirical EBT distribution for degree 2 winners: n=148, p25=7.5,
median=17, p75=29. Feeds T7 follow-up.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`30ae985`](https://github.com/chen-star/wave-alpha/commit/30ae98561ea8beaa29ad26ff359d771585dc3eac))

* docs(branding): README facelift with SVG logo, AGENTS, GitNexus skills

- Rewrite README around features, quickstart, web UI, watchlist/scan,
  optional LLM, backtest harness, architecture, and persistence; switch
  from version-tagged section headers to a feature-first layout with
  GitHub admonitions for the privacy contract and 4h-history caveat.
- Add docs/assets/{logo-light,logo-dark,icon}.svg — banner wordmark
  with the iconic 5-wave Elliott impulse + a square app icon, served
  via &lt;picture&gt; for light/dark mode auto-switching.
- Add AGENTS.md and append the GitNexus block to CLAUDE.md so
  MCP-aware agents pick up the impact-analysis-before-edit rules.
- Vendor .claude/skills/{create-readme, gitnexus/*} for in-repo
  skill discovery (settings.local.json intentionally excluded).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`2285cf5`](https://github.com/chen-star/wave-alpha/commit/2285cf535d3957bcaf5bc7e4cffa374d9edfbd93))

* docs(plan): trade-sim sanity run — 6 phases, ~9 baseline commits

Phase A setup (gitignore + config), Phase B pre-run instrumentation
(degree + EBT on signal + record, TDD), Phase C tooling (prewarm +
B-bar inspection scripts), Phase D execute, Phase E iterate fixes
(conditional, capped at 5), Phase F persist + verdict.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`00e6420`](https://github.com/chen-star/wave-alpha/commit/00e6420e12fe1d503f1c2c4adcce468a5fb27f52))

* docs(spec): trade-sim sanity run on curated 2020-2024 universe

Single-config sim run gating the §8 ablation grid spec. Defines the
centroid TradeRules, 4h-where-available coverage, B-bar pass criteria
(correctness + plausible economics), fix-as-you-go workflow, and a
post-run results template that becomes the durable record.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`b67dcf8`](https://github.com/chen-star/wave-alpha/commit/b67dcf88d9f7e8524bac09e1031da41d62c1e049))

* docs(readme): explain 4h-history limit in backtest section ([`9e5b8d4`](https://github.com/chen-star/wave-alpha/commit/9e5b8d4797c427f35a7e240d5628d83427543686))

* docs(data): clarify CachedProvider hit semantics and dual-filter path ([`e0d278e`](https://github.com/chen-star/wave-alpha/commit/e0d278e7d46706438d96196118c73c974a90bf22))

* docs(plan): 4h cache strategy — 9 tasks across 4 phases, TDD per task

Phase A: cache mechanics fix (CachedProvider hit predicate + YahooProvider end-filter shift)
Phase B: trade-layer surfacing (tf_count on TradeRecord, coverage_4h/by_tf_count on result, report + sidecars)
Phase C: CLI warning + README docs
Phase D: v0.5.3 bump

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`3e2576d`](https://github.com/chen-star/wave-alpha/commit/3e2576d4a98ce4e54ac8b10842a5ec1567108ae0))

* docs(spec): 4h cache strategy under yfinance&#39;s 730d intraday limit

S1 fixes the cache mechanics that burn one yfinance call per backtest
as_of when end &lt; today-730d. S2 surfaces 4h coverage in trade-layer
reports for ablation interpretability. S3 adds a CLI warning when a
backtest window crosses the 4h availability boundary.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`91e84ec`](https://github.com/chen-star/wave-alpha/commit/91e84ece7a572af1dc8b577487be64e4ab4764f9))

* docs(plan): trade-sim backtest layer — 17 tasks, TDD per task

Implements docs/superpowers/specs/2026-05-04-trade-sim-layer-design.md.

Key sequencing: extract coherence_bucket helper first (T1), then ATR/fixed-R
helpers (T2-T3), then build TradeRecord/Result types (T4-T6) before any sim
state machine (T7-T10). Lookahead guard (T11), runner (T12), report (T13),
CLI (T14), README + version bump (T15), smoke run (T16), final sweep (T17).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`64bf1c5`](https://github.com/chen-star/wave-alpha/commit/64bf1c58c2476e64fc86d3813f72f9f493ae48a7))

* docs(spec): clarify count_target vs fib_extension — equivalent today, distinct in API

Pinned-target templates don&#39;t exist yet; both target_source values fall through
to fib 1.618 in v0.5.x. The API split is preserved so a future template change
(e.g., wave-5 projection) lights up count_target without TradeRules churn.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`da9b0d5`](https://github.com/chen-star/wave-alpha/commit/da9b0d55a27f28218ddf004bcd1d2dd5e62e752c))

* docs(spec): trade-sim backtest layer — single-config sim before ablation grid

A third backtest layer alongside count + pivot. Takes a TradeRules config
and reports §8 trade-economics (hit rate, avg R, expectancy, profit factor,
max drawdown) with breakdowns by direction, pattern, coherence bucket, year.

Layered with the eventual v0.5 ablation grid: this spec covers single-config
sim only. The 48-cell sweep lands as a separate spec on top of run_trade_layer.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`5413aa1`](https://github.com/chen-star/wave-alpha/commit/5413aa1d5b0657e1eb969190ce10ee785376a2a1))

* docs(claude-md): project guidance for Claude Code sessions

Tooling, common commands, architecture overview, lookahead enforcement
boundary, YAML wave templates, right-edge model selection, LLM modes,
provider stack, persistence locations, CLI structure, web UI conventions,
and the plans/specs workflow.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`1afd65a`](https://github.com/chen-star/wave-alpha/commit/1afd65a01c87b7271f8aecf4f92a6457971d1bd9))

* docs(plan): fib structural features — 22-task plan in 3 phases

Phase 1 (Tasks 1-9) ships engine_score Fib confluence independently.
Phase 2 (Tasks 10-17) extends RightEdgeFeatures with safe defaults and
refactors logistic _vector to read from feature_order — old artifacts
keep loading without retrain. Phase 3 (Tasks 18-22) is a gated retrain
pass; calibrated_heuristic is unaffected (it doesn&#39;t use _vector).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`680ba93`](https://github.com/chen-star/wave-alpha/commit/680ba93d92db0875d8daab0517e15e29395df161))

* docs(spec): fib structural features — pattern-aware multi-ratio confluence

Pattern-specific Fib bands feed both engine_score (replacing the single
W2:W1 generic confluence) and the right-edge model&#39;s feature vector.
Impulse + zigzag get curated bands; other patterns fall back to the
existing generic scorer. Forces a right-edge model retrain — captured
as a training-run task in the plan.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`682f364`](https://github.com/chen-star/wave-alpha/commit/682f364702b476ebeda89bd22333c736a080b5a8))

* docs(spec): training-run results after follow-ups — both artifacts ship

Logistic: HOLD → PROMOTE_CALIBRATION (gate extension fired as expected).
Calibrated heuristic: full PROMOTE — Brier strictly better than both raw
heuristic AND logistic (CIs don&#39;t overlap), calibration 2-4× better per
degree. Per-degree calibration vs logistic is mixed.

Loader preference order kept as logistic &gt; calibrated &gt; raw for now;
flipping to calibrated &gt; logistic noted as an open design question.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`a11cc0f`](https://github.com/chen-star/wave-alpha/commit/a11cc0f449442ddc099664638216cc38bdf289ff))

* docs(right-edge): degree count is 5 (0..4) per _DEFAULT_ATR_MULTIPLES, not 3

Update spec JSON examples (platt_per_degree, per_degree_calibration) to show
all 5 degrees. Fix non-goals note and deferred-decision text to reference 5
degrees. Add note to test_loader.py fixture clarifying tests use a synthetic
subset; production artifacts cover degrees 0..4.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`fbcf791`](https://github.com/chen-star/wave-alpha/commit/fbcf79109142144856df8595439d539b12d15749))

* docs(plan): right-edge follow-ups — degree fix, gate extension, calibrated heuristic

3 follow-ups from the 2026-05-03 HOLD verdict:
1. Fix degree-count assumption (5 not 3) in spec + docstrings.
2. Add PROMOTE_CALIBRATION verdict — gate ships when calibration is strictly
   better by ≥20% per degree, even if Brier overlaps.
3. New CalibratedHeuristicModel — heuristic + per-degree Platt loaded from JSON
   artifact; smaller change than the logistic, captures most of the calibration
   gap. Loader auto-prefers logistic &gt; calibrated &gt; raw.
4. Re-run training with the new gate; ship any artifact that earns PROMOTE
   or PROMOTE_CALIBRATION.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`867e9cb`](https://github.com/chen-star/wave-alpha/commit/867e9cbb9a1ff8f5b2bbdd6155b27ce55975002d))

* docs(spec): right-edge training run 2026-05-03 — verdict HOLD

4998 train rows / 4061 OOS predictions across 49 tickers × 21 quarters.
Logistic Brier marginally better than heuristic (CIs overlap), but calibration
2-3x better on every degree. Gate&#39;s strict &#34;Brier AND calibration&#34; rule kept
heuristic in production. Logged follow-ups: gate-rule extension for
calibration-only PROMOTE, heuristic recalibration as a smaller alternative,
update degree count assumption (5 not 3).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`c6c878a`](https://github.com/chen-star/wave-alpha/commit/c6c878acab4369a9fd4d377a968e65dedb6d305d))

* docs(plan): right-edge logistic implementation plan (7 phases)

Phase 1 fixes the placeholder-features bug + introduces FeatureExtractor.
Phases 2-4 build training, gating, and JSON-persisted model.
Phases 5-6 wire CLI + inference fallback.
Phase 7 is operator-run training + verdict-gated artifact commit.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`dd70a57`](https://github.com/chen-star/wave-alpha/commit/dd70a57ef646635e9b15a729a7c2ad881ae81503))

* docs(spec): right-edge logistic confirmation model — v0.4.x design

Closes spec §5.4 promotion gate. Trains a calibrated logistic on backtest-derived labels, gated by bootstrap-CI Brier + per-degree calibration. Bundles fix for placeholder-features bug in pivot_layer.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`9039157`](https://github.com/chen-star/wave-alpha/commit/903915704907b8ade8493b1a13eff503b7632d16))

* docs(plan): v0.5.1 polish plan

Targeted follow-ups from v0.4 + v0.5 reviews:
- CachedProvider tail-staleness invalidation (real bug)
- deps.analyze memoization across HTMX fragments (perf)
- Coherence tri-badge W ✓ D ✓ 4h ⚠ (spec §11 #3 deferred)
- CR cleanups bundle (SYMBOL_RE consolidation, exception class in
  scan errors, sort_by reverse for ticker)

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`014e7af`](https://github.com/chen-star/wave-alpha/commit/014e7af0cf8c0a1004ebc54f5013711008c90f69))

* docs(plan): v0.4 backtest harness + v0.5 watchlist scanner plans

Two implementation plans covering the next two roadmap phases:

- v0.4: Walk-forward backtest harness (pivot + count layers) over a
  curated 50-ticker universe, with hard lookahead guards and a
  --llm-mode disabled headline.
- v0.5: Multi-ticker watchlist + scan dashboard (CRUD, CSV/TV-TXT
  import, JSON-archived scan reports, sortable web table).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`f485496`](https://github.com/chen-star/wave-alpha/commit/f485496437d8ccfcb0f984c70ec02d5939769d0d))

* docs(plan): track v0.3 spec §11 deferrals

Document three items from spec §11 that did not ship in v0.3: toolbar
period selector + degree slider, multi-TF coherence tri-badge, and LLM
coherence explanation paragraph. Gives v0.3.1/v0.4 a clear todo list.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`c5869b3`](https://github.com/chen-star/wave-alpha/commit/c5869b3e7428d813f779d78d34cd77e1d31b639b))

* docs(vendor): correct lightweight-charts license to Apache 2.0

The d1ed688 commit message labeled it MIT; the upstream bundle ships
under Apache License 2.0 per the embedded license header.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`e9e3723`](https://github.com/chen-star/wave-alpha/commit/e9e372328fb66569404ab66bc2bbc44f1e21388e))

* docs(plans): v0.2 LLM contract and v0.3 web UI deep-dive plans

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`9a97fe1`](https://github.com/chen-star/wave-alpha/commit/9a97fe1db34fbf0be03d23761e6dd249ab1c5ab0))

* docs(coherence,trades,readme): annotate v0.2 follow-ups; align versioning

I2: document the spec/plan divergence in coherence_score&#39;s docstring. v0.1
collapses nesting+direction onto one boolean (+0.5) because actual sub-wave
nesting isn&#39;t implemented yet. Spec §7&#39;s 4-dimension breakdown reconverges
once nested counts ship in v0.2.

M3: README now uses v0.2 / v0.3 versioning consistent with the design doc
roadmap. The &#34;Plan N&#34; / &#34;v0.N&#34; terminology was drifting.

M6: re-export the public API (CoherenceInputs, CoherenceResult,
coherence_score, coherence_multiplier, three_way) from wave_alpha.coherence
so callers don&#39;t need to reach into the .score submodule.

M7: add a code comment on _DEFAULT_BARS_PER_DEGREE pointing to spec §9&#39;s
&#34;degree-derived from historical median × 1.5&#34; requirement and the v0.5
backtest harness as the wiring milestone.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`07004b3`](https://github.com/chen-star/wave-alpha/commit/07004b3901a5bf759334db9ad05338c30abf5ab3))

* docs: minimal README for v0.1 engine core

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`df41f42`](https://github.com/chen-star/wave-alpha/commit/df41f42c919f34a1ae8b42fbd3c9c454252752c4))

* docs(plan): wave-alpha v0.1 engine core implementation plan

26-task TDD plan covering: project scaffolding (Phase A), data layer
with PointInTimeView (Phase B), multi-degree ATR-ZigZag pivots with
subset invariant (Phase C), Elliott templates and rule validator and
count enumerator (Phase D), right-edge heuristic confirmation model
(Phase E), three-way multi-TF coherence (Phase F), trade-signal
derivation (Phase G), CLI analyze command and live smoke test (Phase H).

LLM (Plan 2) and web UI (Plan 3) explicitly out of scope.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`5496e90`](https://github.com/chen-star/wave-alpha/commit/5496e90056d843cc168e7965464c5359bdc27709))

* docs(design): wave-alpha v0.1 design spec

Initial design spec consolidating all 12 brainstorm decisions:
forward-outcome accuracy with multi-TF coherence and rule floor;
multi-degree ATR-ZigZag with hierarchical subset invariant; strict
point-in-time backtest plus tentative live pivots with scenario
branches; tree-of-templates count encoding via YAML; engine-as-
enumerator/LLM-as-judge with prompt caching; weekly+daily+4h
coherence as a confidence multiplier; phased right-edge probability
model (heuristic v1, logistic on backtest labels later); three-layer
backtest harness with PointInTimeView lookahead guard; bidirectional
trade signals with count-derived stop/target and ablation-validated
defaults; local-first feedback loop with opt-in trade-outcomes-only
pooling; v0.1 ships single-ticker deep-dive web UI.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`9238ee1`](https://github.com/chen-star/wave-alpha/commit/9238ee1986bad9f34c8d018c8a43b990cf70d5bd))

### Feature

* feat(web): fill /about page with full pipeline narrative + diagrams ([`08c316c`](https://github.com/chen-star/wave-alpha/commit/08c316c2a1de6d6e85566779e3acc1c63e727456))

* feat(web): add α slider trust popover with /about deep link ([`6426e17`](https://github.com/chen-star/wave-alpha/commit/6426e1791da09ea4c907851470fe5b0e157a2868))

* feat(web): add home KPI trust popovers ([`aa7263f`](https://github.com/chen-star/wave-alpha/commit/aa7263f9872a0348a20b42f658d7d9b8ac3bbf93))

* feat(web): add scan-table column-header trust popovers ([`56ac6d8`](https://github.com/chen-star/wave-alpha/commit/56ac6d8c91a37db5198ed069212716312421d064))

* feat(web): add hero KPI trust popovers (confidence + R:R) ([`a706f7a`](https://github.com/chen-star/wave-alpha/commit/a706f7a989112244f0f44f36efc32eb089d09b52))

* feat(web): add per-candidate trust popovers in counts list ([`4137102`](https://github.com/chen-star/wave-alpha/commit/4137102afe87eaf608f53363d1f37c5dc62d93b5))

* feat(web): add trade plan trust popover ([`1b28908`](https://github.com/chen-star/wave-alpha/commit/1b28908c0c02150ac721af1584ea968667c1a70a))

* feat(web): add right-edge card trust popover ([`68ed102`](https://github.com/chen-star/wave-alpha/commit/68ed102ec8da3e806f02a422c325e623d484820c))

* feat(web): add coherence card trust popover ([`eb1e529`](https://github.com/chen-star/wave-alpha/commit/eb1e529384115cd9537ddfde52dc1a1df2aac7e4))

* feat(web): add /about page scaffold with anchor sections + nav link ([`3ee5fee`](https://github.com/chen-star/wave-alpha/commit/3ee5fee6f12f88eb07ca18fe3f647ab96b95edb0))

* feat(web): add popover macro + Alpine component for trust UI

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`3c87a9a`](https://github.com/chen-star/wave-alpha/commit/3c87a9a9a99f3cb60b2dc9fb50349510b9328938))

* feat(web/chart): theme chart for cyber-spectrum (locked classic candles) ([`633a8c0`](https://github.com/chen-star/wave-alpha/commit/633a8c014679609f10df3df314d25e69796cd589))

* feat(web/deepdive): redesign all fragment partials with new tokens ([`ddaf2cf`](https://github.com/chen-star/wave-alpha/commit/ddaf2cff25de77932c0c4370f101fc29fddc93cb))

* feat(web/deepdive): redesign page with hero card + skeleton loading

Replaces the old toolbar-driven shell with a hero card (mono-cyan
ticker + LLM mode pill + inline Reload form), a two-column body
(chart + plan/coherence/right-edge stack), a 2-up row of wave counts
and scenarios, and a stability card at the bottom. All fragment
sections load via HTMX with skeleton placeholders and themed card
titles. Glossary tooltips on coherence and right-edge titles.

Removes _toolbar.html — its controls now live in the global top nav
(search + as-of) and the per-page Reload form on the hero card. ([`37b6937`](https://github.com/chen-star/wave-alpha/commit/37b69372c2047f40f9914d19e8662e4075e9dd2b))

* feat(web/settings): redesign with help text, α slider, glossary tooltip

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`f11794f`](https://github.com/chen-star/wave-alpha/commit/f11794f3ff1c233904895ff2d82bca3e447fd05f))

* feat(web/watchlist): redesign with enriched rows showing last-known signal

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`e726fbb`](https://github.com/chen-star/wave-alpha/commit/e726fbbc633f15add089562fd5e7a57f0ee5db93))

* feat(web/scan): redesign scan page with KPI strip, filter tabs, pattern badges

Replace the old scan.html (select dropdown + plain table) with the new layout:
empty-state with CLI codeblocks, recent-scan pill row, KPI strip
(Signals/Errors/Duration/Coverage), and a single Alpine-driven filter card
(All/Signals/Long/Short tabs + Sort/Min-RR selects) that lazy-loads
#scan-table via HTMX with Alpine state read through hx-vals.

Replace _scan_table.html with the .dt table that uses pat() and pill()
macros, a scorebar for the score column, and color-coded entry/stop/target.

Update test_scan_routes assertions: header is now &#34;Right-edge&#34; (lowercase
e) and /scan/{id} renders the HTMX table mount point instead of the rows
inline (rows are now fetched via /scan/{id}/table).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`890a635`](https://github.com/chen-star/wave-alpha/commit/890a6350177f9ed19ccd5dc319efd94051d80849))

* feat(web/home): full styled Home dashboard with KPIs, top signals, watchlist ([`634b20f`](https://github.com/chen-star/wave-alpha/commit/634b20fe4bf8b3aeb9a0989d4d6ff7059b1be76c))

* feat(web/templates): redesign base layout with top nav, macros, global ⌘K ([`02a255c`](https://github.com/chen-star/wave-alpha/commit/02a255c486b89a85e81324601cebfa53d3fa1cb8))

* feat(web/css): rewrite app.css around cyber-spectrum design tokens ([`8b56f9d`](https://github.com/chen-star/wave-alpha/commit/8b56f9d639b04cdadaa5fe5ace36502df9f6cf06))

* feat(web): add Home dashboard at / and drop /settings redirect ([`e539336`](https://github.com/chen-star/wave-alpha/commit/e539336af1ead0548c1f5b77c8865f85420ba8d0))

* feat(web/watchlist): surface latest-scan signal per entry to template

Wire the latest scan report into the watchlist page via watchlist_enrich.enrich
so each row can show its last-known top pattern. The route now also passes
`latest_scan_id` for a &#34;last refreshed&#34; subline. The existing `entries` key is
kept in the context for backward compatibility. Render a Pattern column in the
partial template (em-dash when no scan covers the symbol). Test fixture now
monkeypatches both `_watchlist_path` and `_scan_dir` so watchlist tests share
the scan-dir indirection used by scan tests.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`d8f80d1`](https://github.com/chen-star/wave-alpha/commit/d8f80d1d3cf649ecd38aaa19958f516ef2527918))

* feat(web): add watchlist_enrich helper to join entries against latest scan ([`20eee4f`](https://github.com/chen-star/wave-alpha/commit/20eee4fecfb3fcde6d98a220d11fe3edfd23bdc6))

* feat(web/scan): accept bias and min_rr query params on table fragment ([`a36444c`](https://github.com/chen-star/wave-alpha/commit/a36444ce7a72bf3ece390f3697b2366f51785a2a))

* feat(scan): add filter_by_bias and filter_by_min_rr to ranking ([`b9f5385`](https://github.com/chen-star/wave-alpha/commit/b9f53855cef582a9eb6211a8de5df5a5dce4aa22))

* feat(cli): add DISCLAIMER and first-run CLI banner

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`e291442`](https://github.com/chen-star/wave-alpha/commit/e291442d3b2e90d972b95f3c8ebcb8328f020f74))

* feat(packaging): move anthropic into [llm] optional extra

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`713c2f0`](https://github.com/chen-star/wave-alpha/commit/713c2f057459005107d37c33a38f0af332195beb))

* feat(web): styles for history section and stability badge

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`a00f200`](https://github.com/chen-star/wave-alpha/commit/a00f200271743c62587915b135a20529f09ec491))

* feat(cli): analyze writes history snapshot

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`5acf9f5`](https://github.com/chen-star/wave-alpha/commit/5acf9f576f9562f14c52989a351ddfb278573a66))

* feat(web): wire history section + counts_fragment write hook

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`ed6a453`](https://github.com/chen-star/wave-alpha/commit/ed6a4535fec4d85c34912e50d1059b6f58ad149c))

* feat(web): /deepdive/&lt;ticker&gt;/history route + template

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`99d24b0`](https://github.com/chen-star/wave-alpha/commit/99d24b088ab75e1a915db92be65ce56ea356f08d))

* feat(history): get_recent_with_badge read API ([`1c90999`](https://github.com/chen-star/wave-alpha/commit/1c90999d02b31832f23d7bcbd41a7ee39b8c4d88))

* feat(history): _compute_badge stability metrics ([`6116a7a`](https://github.com/chen-star/wave-alpha/commit/6116a7acb221ff366146a90ed4d25b302d5c13df))

* feat(history): record_snapshot service (write path)

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`d552f9d`](https://github.com/chen-star/wave-alpha/commit/d552f9d3dac112788e3b46b4bd61523671489442))

* feat(history): SQLite store with upsert and recent ([`4a88073`](https://github.com/chen-star/wave-alpha/commit/4a880731187845008a76d1554f38df2c3e8f634f))

* feat(history): Snapshot, StabilityBadge, RecentHistory models ([`d9f938b`](https://github.com/chen-star/wave-alpha/commit/d9f938b3ed4adbb5351e7cf881f4e98b1c30776e))

* feat(history): top_count_hash for stability identity

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`80bc3bc`](https://github.com/chen-star/wave-alpha/commit/80bc3bc5304f442261caf1a4ff39fd358d19c671))

* feat(trades): empirical EBT refit for degree 1 — closes §9 W1

Per the 2026-05-08 EBT refit verdict (§6 of spec):
- degree 1: SHIP empirical 8/11 (was heuristic 10/15), n=119 winners
- degree 3: HOLD heuristic 60/90, n=31 winners &lt; 100-winner threshold

Methodology mirrors 2026-05-07 degree-2 EBT spec: median →
expected_bars_to_target, p75 → max_holding_bars; ≥100-winner
threshold gate per degree.

Phase A backtest produced 2,739 trades on the curated universe
(22% more than v0.5.9 single-degree baseline of 2,242, reflecting
multi-degree fan-out).  198 target-hit winners total.

Bug fix in scripts/inspect_ebt_refit.py: filter on
exit_reason == &#34;target&#34; (the actual ExitReason literal value), not
&#34;target_hit&#34; (which never matches).

v0.5.10 → v0.5.11.

See: docs/superpowers/specs/2026-05-08-ebt-refit-1-and-3-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`cf5118e`](https://github.com/chen-star/wave-alpha/commit/cf5118ee094eb5ceef9ce2806ae797364ed58575))

* feat(trades): empirical EBT refit for degree 1 — closes §9 W1

Per the 2026-05-08 EBT refit verdict (§6 of spec):
- degree 1: SHIP empirical 8/11 (was heuristic 10/15), n=119 winners
- degree 3: HOLD heuristic 60/90, n=31 winners &lt; 100-winner threshold

Methodology mirrors 2026-05-07 degree-2 EBT spec: median →
expected_bars_to_target, p75 → max_holding_bars; ≥100-winner
threshold gate per degree.

Phase A backtest produced 2,739 trades on the curated universe
(22% more than v0.5.9 single-degree baseline of 2,242, reflecting
multi-degree fan-out).  198 target-hit winners total.

Bug fix in scripts/inspect_ebt_refit.py: filter on
exit_reason == &#34;target&#34; (the actual ExitReason literal value), not
&#34;target_hit&#34; (which never matches).

v0.5.10 → v0.5.11.

See: docs/superpowers/specs/2026-05-08-ebt-refit-1-and-3-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`0db7d4d`](https://github.com/chen-star/wave-alpha/commit/0db7d4d685c5f3b4a5742192c047a7e9575c0b2c))

* feat(scripts): add Claude Code vs API side-by-side spike script

Runs the engine once on a fixed ticker (AAPL @ 2024-12-31) with LLM
disabled to obtain real candidates, builds the same LLMRequest that
_maybe_run_scan would build, then calls AnthropicClient and
ClaudeCodeClient on the identical request. Dumps raw + parsed
responses and wall-clock latency for each to reports/claude_code_spike.json.

Throwaway harness — not registered in CLI. Output decides the
go/no-go on the full feature per the spike spec §3 / §9.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`ab7a7be`](https://github.com/chen-star/wave-alpha/commit/ab7a7bec844ef21f89fdd093fcc2d8da417368a2))

* feat(llm): add ClaudeCodeClient (spike) — subprocess transport for Max plan

Shells out to `claude -p` with the user&#39;s existing CLI auth, no API
key required. Throwaway transport for the side-by-side comparison
against AnthropicClient; see the spike spec.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`8feb5fb`](https://github.com/chen-star/wave-alpha/commit/8feb5fb415f013e6a5b115831b62f512d4a7e338))

* feat(backtest): add EBT refit Phase B inspect script ([`01ff11a`](https://github.com/chen-star/wave-alpha/commit/01ff11a80c47d43a45d307cfd29d463db73a7821))

* feat(backtest): add EBT refit Phase A config ([`89fd7dd`](https://github.com/chen-star/wave-alpha/commit/89fd7dd4f6ac13dc8cdfaebc7f4d22fff634abea))

* feat(engine): multi-degree enumeration — closes S6

pipeline._enumerate_for now emits candidates across degrees [1, 2, 3]
instead of only degree 2.  Pivot detector and elliott.enumerator are
already degree-parameterized; the change is at the assembly layer.

Per-degree top-K = 2 (6 candidates max per analyze, was 5 single-
degree).  Each candidate carries count.degree; downstream derive_signal
keys on it for the empirical degree-2 EBT (17/29) and the heuristic
EBTs for degrees 1 (10/15) and 3 (60/90).

`_DEFAULT_DEGREE_FOR_COUNT = 2` retained for downstream consumers
(right_edge.assessment, count_layer backtest, web deepdive default)
that operate on a single calibrated degree.

Empirical EBT refit for degrees 1 and 3 is a deferred follow-up spec —
needs multi-degree trade data, which requires this ship first.

Closes S6 from 2026-05-05 sanity run §9.  v0.5.9 → v0.5.10.

See: docs/superpowers/specs/2026-05-08-multi-degree-enumeration-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`fc83787`](https://github.com/chen-star/wave-alpha/commit/fc837874e4e3755243ccb727dd0d9c0c28ba1211))

* feat(backtest): add target_R fixed_R sweep grid config ([`fbeac37`](https://github.com/chen-star/wave-alpha/commit/fbeac37993e23fac45412dcd1f96b94ea88cc1d1))

* feat(backtest): add target_R baseline grid config + smoke universe ([`2949791`](https://github.com/chen-star/wave-alpha/commit/2949791494bf50ba80ab60787fb87b892dcaa3e6))

* feat(trades): wire target_source dispatch — closes G7

`_target_price` now honors `rules.target_source` instead of always
projecting fib_extension off W1.  `count_target` and `fib_extension`
are explicit aliases (current behavior preserved); `fixed_R` dispatches
to `_target_price_fixed_r` with `target_R * planned_risk` on the profit
side.  Missing `target_R` raises ValueError — pipeline&#39;s existing
per-candidate try/except (from atr_multiple wiring) catches without
modification.

Closes G7 from the ablation grid spec — Phase 2&#39;s `target_source` axis
was degenerate because the wiring did not exist.  v0.5.8 → v0.5.9.

See: docs/superpowers/specs/2026-05-08-target-source-wiring-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`4a900b6`](https://github.com/chen-star/wave-alpha/commit/4a900b68be9677eec37256251aad70f34614c65d))

* feat(trades): default stop_source flips to atr_multiple

Implements the §6.4 verdict from the ablation grid spec
(2026-05-06-ablation-grid-design.md): TradeRules.stop_source default
changes from &#34;count_invalidation&#34; to &#34;atr_multiple&#34;.  The grid
established a +0.116R sensitivity gap on the curated 50-ticker
universe over 2020-2024; shipping atr_multiple as the default
reflects the validated empirical winner.

Test migration: 7 sites in tests/trades/test_derive.py that had
relied on the implicit count_invalidation default now pass it
explicitly (all of them assert on count-derived stop behavior or
return early before stop derivation).  Test docstring for
test_count_invalidation_works_without_bars updated to reflect
the post-flip semantics.

Existing analyze/scan/web/CLI surfaces that historically called
derive_signal without bars must now either:
1. Pass `stop_source=&#34;count_invalidation&#34;` explicitly to keep old
   no-bars behavior, OR
2. Pass `bars=` so the new atr_multiple default can compute ATR(14).

The pipeline (run_analysis) already passes bars per the
2026-05-06-atr-multiple-wiring spec, so backtest paths are
unaffected.  Direct callers in non-pipeline contexts may need updating;
none discovered in src/.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`a092e0e`](https://github.com/chen-star/wave-alpha/commit/a092e0e3d0726536325cd71aa3151e960891190b))

* feat(derive): EBT empirical fit — refit degree-2 from sanity run

Replaces _DEFAULT_BARS_PER_DEGREE[2]=25 with the empirical median
(17 bars, n=148 winners from the 2026-05-05 sanity run on the curated
50-ticker universe over 2020-2024).  Decouples max_holding_bars from
the hardcoded × 1.5 multiplier by adding a separate
_EBT_P75_BY_DEGREE lookup (degree 2 = 29, the empirical p75).  Other
degrees fall back to median * 1.5 (preserving today&#39;s behavior) until
multi-degree enumeration ships and produces empirical data for them.

Spec: docs/superpowers/specs/2026-05-07-ebt-empirical-fit-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`287f75f`](https://github.com/chen-star/wave-alpha/commit/287f75fc586857586e25fb184fc1ed72cc000263))

* feat(backtest): n_atr_skipped counter on TradeLayerResult + AnalyzeResult

Closes the atr_multiple wiring spec §8 W2 deferred decision. Surfaces
the silent skip rate of derive_signal ValueError catches (typically
atr_multiple stop_source with insufficient bars for ATR(14)) so they
become visible during grid runs and post-hoc analysis.

Wiring:
- AnalyzeResult.n_atr_skipped: int — incremented in pipeline.run_analysis
  whenever a ranked candidate raises ValueError. The counter is reset
  per AnalyzeRequest (no cross-call leakage).
- TradeLayerResult.n_atr_skipped: int — accumulated by
  TradeLayerEvaluator across all _signal_at calls (per-ticker total).
- run_trade_layer aggregates n_atr_skipped across the universe into
  the returned TradeLayerResult.
- render_trade_markdown surfaces a one-line note when &gt; 0; CLI JSON
  sidecars include the count in the headline; backtest grid summary.csv
  adds an n_atr_skipped column.

Four tests pin the wiring: AnalyzeResult-level (zero default + flaky
candidate increments), evaluator-level (sums per-call), and aggregator
(across-universe sum).

Spec: docs/superpowers/specs/2026-05-06-atr-multiple-wiring-design.md §8 W2.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`abf19b3`](https://github.com/chen-star/wave-alpha/commit/abf19b3ea43fb09501efe89e472435f4b60ae8c5))

* feat(cli): backtest grid subcommand

Wires GridConfig + run_grid + render_grid_markdown into the existing
`wave-alpha backtest` sub-app. Loads YAML grid config via --grid;
writes top-level grid.md + summary.csv + per-cell {report.md,
result.json, trades.csv} under --out. Per-cell shape mirrors the
existing `backtest trade` artifacts so single-cell inspection is
identical to a sanity run.

Spec: docs/superpowers/specs/2026-05-06-ablation-grid-design.md §3.7.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`8b25adb`](https://github.com/chen-star/wave-alpha/commit/8b25adbd76bd9d1d3f45639b5d95ac11d6fbdc8f))

* feat(configs): grid_phase1 + grid_phase2 axis definitions

Phase 1: 16 cells (min_confidence × direction_modes × entry_trigger).
Phase 2: 32 cells (entry × stop × target × min_confidence), with
direction_modes placeholder ready for the Phase 1 winner.

Spec: docs/superpowers/specs/2026-05-06-ablation-grid-design.md §3.2, §3.4.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`41acf32`](https://github.com/chen-star/wave-alpha/commit/41acf3299b77e6edacbad5a8a4d95ad2ca309f81))

* feat(backtest): grid_report markdown renderer

render_grid_markdown produces a cross-cell summary with sections:
header (phase name + axis dimensions + fixed values), per-cell
ranking table, axis sensitivity table (marginal means per level),
winner block, and (Phase 1 only) GO/NO-GO trigger verdict.

Spec: docs/superpowers/specs/2026-05-06-ablation-grid-design.md §3.8.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`6a91f4a`](https://github.com/chen-star/wave-alpha/commit/6a91f4a33fd3e99d4937307b8c30b0887eb011e7))

* feat(backtest): run_grid orchestration over run_trade_layer

Iterates GridConfig.cells() in row-major order, builds TradeRules
from each cell&#39;s axis_values + fixed values, calls run_trade_layer,
collects results into a GridResult. Pure orchestration; numerical
correctness is the trade-sim layer&#39;s contract.

Spec: docs/superpowers/specs/2026-05-06-ablation-grid-design.md §3.5.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`e91c737`](https://github.com/chen-star/wave-alpha/commit/e91c7370adfbdd0a18868611a619f2ac2c20fe8d))

* feat(backtest): phase1_trigger GO/NO-GO function

GO iff at least one cell has (expectancy &gt;= +0.05R OR PF &gt;= 1.05) AND
closed_trades &gt;= 200. PF=inf qualifies (zero-loss cell — passes by
definition; surfaced explicitly rather than silently coerced). Returns
(verdict, human-readable reason) for spec §6.2 fill. NO-GO branches:
all-undersample (no cell met floor) or all-below-threshold (best
eligible failed both criteria).

Spec: docs/superpowers/specs/2026-05-06-ablation-grid-design.md §3.3.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`58fb817`](https://github.com/chen-star/wave-alpha/commit/58fb81765b1d0e2a55b5802fcf67a04df56e1f29))

* feat(backtest): GridResult aggregation — ranked, winner, sensitivity

GridResult holds one row per cell with its TradeLayerResult.
ranked_by_expectancy filters by closed_trades &gt;= 200 (sample-size
floor matching the sanity-run E6 invariant) and sorts descending.
winner returns the top ranked row or None. axis_sensitivity computes
marginal mean expectancy / profit_factor / closed_trades per axis
level — exposes which knob moves the metric most.

Also adds GridConfig.__post_init__ validator that asserts axis names
are unique and disjoint from fixed keys (defensive hygiene from T1
review).

Spec: docs/superpowers/specs/2026-05-06-ablation-grid-design.md §3.6.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`0e98c27`](https://github.com/chen-star/wave-alpha/commit/0e98c271a793a24ac805a06d769869a55c310c78))

* feat(backtest): GridConfig + cartesian-product cell iteration

Defines GridAxis (one sweep dimension), GridConfig (axes + fixed
values), and GridCell (one cell = axis_values overlaid on fixed).
GridConfig.cells() yields the cartesian product in row-major axis
order. Cell IDs are deterministic from axis values for reproducible
artifact paths.

Spec: docs/superpowers/specs/2026-05-06-ablation-grid-design.md §3.1.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`251c628`](https://github.com/chen-star/wave-alpha/commit/251c628daa01621f13e9f8e7f603a996f2dc450a))

* feat(pipeline): pass bars to derive_signal, catch per-candidate ValueError

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

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`15ab2bf`](https://github.com/chen-star/wave-alpha/commit/15ab2bf05d43a67c9d262c71fe07d01ea859548e))

* feat(derive): wire atr_multiple stop_source into derive_signal

Previously TradeRules.stop_source=&#34;atr_multiple&#34; silently fell through
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

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`28db48a`](https://github.com/chen-star/wave-alpha/commit/28db48abc2aa5ad62a77e51d28a8d163b347eede))

* feat(scripts): inspect_sanity_run — B-bar verifier for trade-sim run

Implements the 16 criteria from spec §5: 7 correctness (hard-fail),
6 economics, 3 coverage. Loads result.json + trades.csv, prints a
verdict table, exits non-zero on any §5.1 violation.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`e770aa0`](https://github.com/chen-star/wave-alpha/commit/e770aa02b76bf9fffe30d79ea15ba814b3fdfb11))

* feat(scripts): prewarm yfinance cache for sanity-run

One-shot pre-warm to populate ~/.wave_alpha/cache.sqlite with
weekly+daily+4h bars through 2024-12-31 for the curated universe.
Avoids yfinance fetch latency dominating the trade-sim wall time.
4h failures for delisted tickers are tolerated; weekly/daily failures
exit 1.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`b82ba17`](https://github.com/chen-star/wave-alpha/commit/b82ba175c20b9d2854137562a6c97b02fc4c3cbb))

* feat(backtest): TradeRecord carries degree + expected_bars_to_target

Required by trade-sim sanity run spec §6.6 — per-degree empirical
distribution of realized bars-to-exit on winning trades. Both fields
populated from signal at the closed-exit and open_at_end construction
sites; CSV sidecar picks up the new columns automatically via the
existing fields(TradeRecord) traversal in cli/app.py.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`75ba69e`](https://github.com/chen-star/wave-alpha/commit/75ba69ea406eef241b2ddaab768edb0eef1f4779))

* feat(trades): TradeSignal carries degree from source WaveCount

Required by trade-sim sanity run spec §6.6 (per-degree empirical
expected-bars-to-target distribution). Test cases that build
TradeSignal directly are updated with degree=2.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`16ad8b0`](https://github.com/chen-star/wave-alpha/commit/16ad8b00e3db54d48596c540f51c5d50460f3fe2))

* feat(configs): centroid TradeRules for trade-sim sanity run

atr_multiple / fib_extension / 5bps / step=5. Three deviations from
TradeRules() defaults documented in spec §3.1.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`8bb9a5f`](https://github.com/chen-star/wave-alpha/commit/8bb9a5f8689529ccd0baaf4dd3c605fcf6318a5c))

* feat(cli): warn when backtest window crosses 4h availability boundary

Stderr warning fires from backtest_count, backtest_pivot, and
backtest_trade when start &lt; today - 730d, naming the cutoff date and
explaining the 2-timeframe degradation. ([`811d1cc`](https://github.com/chen-star/wave-alpha/commit/811d1ccacec2c802de28f71531fecd2bbb92d292))

* feat(cli): backtest trade sidecars expose 4h coverage and tf_count

JSON sidecar gains coverage_4h on headline and a top-level by_tf_count
breakdown. CSV ledger picks up tf_count as the trailing column via the
existing asdict(record).keys() flow. ([`9bc4b40`](https://github.com/chen-star/wave-alpha/commit/9bc4b40719ce65c4b350fead4de2c6380e7c36c8))

* feat(backtest): trade-layer report shows 4h coverage in header

Surfaces the fraction of closed trades whose signal-time coherence had
all three timeframes available, so backtests reaching past yfinance&#39;s
730-day 4h limit are honestly interpretable. ([`87fbc51`](https://github.com/chen-star/wave-alpha/commit/87fbc51c12a7b858345693d787df9f94060d7e52))

* feat(backtest): coverage_4h property and by_tf_count breakdown

coverage_4h reports the fraction of closed trades whose signal-time
coherence used all three timeframes; by_tf_count slices the standard
metrics block by 2-TF vs 3-TF for ablation analysis. ([`50c0da6`](https://github.com/chen-star/wave-alpha/commit/50c0da64c8061cced52cf0b393fe5ab90208367f))

* feat(backtest): TradeRecord carries tf_count for coverage analysis

tf_count is 3 when coherence had W+D+4h, 2 when 4h was missing
(coherence.fallback_used == &#34;no_4h_history&#34;). Threaded through the
trade-layer evaluator&#39;s existing _last_coherence_score side-channel. ([`67e25c9`](https://github.com/chen-star/wave-alpha/commit/67e25c9f1645212d01b32e5f3dffa3093d53239c))

* feat(backtest): trade-sim layer — wave-alpha backtest trade

Adds a third backtest layer alongside count_layer and pivot_layer that
takes a TradeRules config and reports hit rate, avg R, expectancy,
profit factor, and max drawdown over the curated universe, with
breakdowns by direction, pattern, coherence bucket, and year.

Pipeline (per ticker, two cadences):
  - Signal generation at --step (calls pipeline.run_analysis with LLM
    disabled) → top-1 signal accepted.
  - Exit walk every business day with stop &gt; target &gt; time-stop
    precedence, gap-aware stop fills, same-bar-stop-wins rule. Long /
    short slippage + commission + daily-prorated short borrow in
    Decimal arithmetic. R denominator = abs(fill_price - stop_price)
    (realized basis).

Entry triggers:
  - immediate (default): fill at T+1 open.
  - confirmation: T+1 close in signal direction → fill at T+2 open;
    otherwise drop.

Surface:
  - wave-alpha backtest trade --universe ... --start ... --end ...
    --rules rules.yaml --out trade.md [--json] [--trades-csv]
  - TradeLayerEvaluator, run_trade_layer, render_trade_markdown
    follow the count/pivot conventions.
  - TradeRecord (frozen dataclass) and TradeLayerResult with 7
    properties + 4 breakdown methods. Profit_factor renders as ∞
    when there are no losses.
  - Markdown report mirrors render_count_markdown / render_pivot_markdown
    style; JSON sidecar serializes inf as null.

Pre-built helpers in trades/derive.py for the upcoming ablation grid:
  - _stop_price_atr_multiple — ATR-anchored stop at 2× ATR(14).
  - _target_price_fixed_r — target at target_R × planned risk.
Both are unit-tested but not yet wired into derive_signal — that
wiring lands in the ablation-grid spec.

Cleanups bundled in:
  - Extracted backtest/coherence_bucket.py shared by count_layer and
    trade_layer (single source of truth for full/partial/disagreement).
  - TradeRules now sets extra=&#34;forbid&#34; — unknown YAML keys raise
    instead of silently defaulting (spec §3 enforcement).

Lookahead is enforced through PointInTimeView via pipeline.run_analysis;
tests/backtest/test_trade_layer_lookahead.py plants an unsorted bar and
verifies the assertion propagates.

Bumps version to v0.5.2. README documents the new command. Spec at
docs/superpowers/specs/2026-05-04-trade-sim-layer-design.md; plan at
docs/superpowers/plans/2026-05-04-trade-sim-layer.md.

Smoke-run on AAPL/MSFT/NVDA × H1-2024 surfaced a pre-existing engine
bug in trades/derive.py:_target_price (uses fib_extension regardless
of direction, putting target on wrong side of entry for many shorts).
The trade layer is shipping correctly; the engine fix is the natural
next step before the ablation grid.

20 commits squashed. 30+ new tests; full suite at 447 passing, ruff
clean.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`e2ddf06`](https://github.com/chen-star/wave-alpha/commit/e2ddf06da4164638969c5fde6ec19e8096845b22))

* feat(right-edge): trainer supports fib features; v2 artifact: HOLD vs v1

Trainer&#39;s _row_to_vector and predict_logistic now read field names
dynamically from feature_order, mirroring LogisticConfirmationModel._vector.
Public constants LEGACY_FEATURE_ORDER and FIB_FEATURE_ORDER plus a
feature_order(with_fib=) helper expose the schema.
assemble_training_rows runs enumerate_counts and feeds the top-1 count
into FeatureExtractor — matches what assess_from_bars does at serve
time, so train/serve see the same fib slots.

CLI: wave-alpha train right-edge --with-fib-features opts the trainer
into the 19-field schema. Default is the 5-field legacy schema; CI and
existing scripts unchanged.

Training run on data/universe_curated.txt (50 tickers,
2021-01-01..2024-12-31, seed 42, 3806 rows, 2869 OOS predictions)
produced verdict PROMOTE_CALIBRATION vs heuristic — but the v2 (19-feat)
artifact is mildly worse than v1 (5-feat) on aggregate Brier (+0.0070
mean) with uniformly weak fib coefficients (top fib |coef| = 0.057 vs
retracement_progress at 1.24). The model treats fib features as noise
on this dataset.

Decision: keep v1 as the bundled logistic artifact. v2 committed
alongside at right_edge_logistic_v2.json for reproducibility but not
loaded by default; _DEFAULT_LOGISTIC_PATH unchanged.

Verdict + caveats + follow-up hypotheses captured in
docs/superpowers/specs/2026-05-04-fib-features-training-run.md.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`78a3bf3`](https://github.com/chen-star/wave-alpha/commit/78a3bf3a0e8d849c0c1f32438f4422426e19bbe8))

* feat(right-edge): plumbing for fib features in confirmation model

RightEdgeFeatures gains 14 new fields (7 raw fib ratios + 7 presence
indicators), all defaulted to 0.0 — old artifacts continue to load
because their feature_order lists exactly the legacy 5 fields and
nothing depends on the new defaults.

LogisticConfirmationModel._vector refactored to read field names
dynamically from self.feature_order via getattr, with bars_since_candidate
rescaled by /20.0 as before. Old 5-field artifacts produce identical
vectors. Loader validates that every name in feature_order exists as a
RightEdgeFeatures field — refuses unknown names with ModelArtifactError
rather than silently misaligning predictions.

extract_features and FeatureExtractor.extract accept an optional
top_count: WaveCount kwarg; when supplied, the 14 fib slots populate
from fib_features(count) (None → (0.0, 0.0); float → (value, 1.0)).
FeatureExtractor is now a thin delegator over extract_features —
duplicated _volume_decline removed.

assess_from_bars runs enumerate_counts(snap.pivots, degree=degree,
top_k=1) and threads the top-1 count through to the extractor, so
right-edge inference now sees structural ratios.

CalibratedHeuristicModel and HeuristicConfirmationModel are unchanged —
neither uses _vector. Pipeline.py is unchanged — it doesn&#39;t compute
right-edge probability.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`637a011`](https://github.com/chen-star/wave-alpha/commit/637a01163b2a91966c4a78432d5357a753aab12e))

* feat(elliott): pattern-aware Fib structural features for engine_score

Replace the single-ratio Fib confluence in elliott/enumerator with
pattern-aware multi-ratio scoring for impulse and zigzag counts. Two new
pure modules:

  - fib_bands.py — canonical band constants per (pattern, ratio_name) plus
    a trapezoid scorer (1.0 inside ideal band, linear decay across
    acceptable, 0 outside).
  - fib_features.py — FibFeatures dataclass + named ratio extraction
    (5 ratios for impulse, 2 for zigzag, none otherwise) +
    confluence_score with weight redistribution: ratios that are None
    are excluded from numerator AND denominator, so a textbook count
    still scores 1.0 even with a missing ratio.

enumerator._confluence_bonus dispatches on pattern: curated patterns use
the new multi-ratio scorer, others fall back to the legacy generic
fib_confluence_score(W2, reference=W1). Score weight structure
(0.50 base / 0.20 confluence / 0.30 recency) is preserved — no
re-ranking surprise on existing scans.

Spec: docs/superpowers/specs/2026-05-03-fib-structural-features-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`998c814`](https://github.com/chen-star/wave-alpha/commit/998c814741da7d843215ec8216e31c2e2b990a74))

* feat(right-edge): surface confirmation probability in deepdive + scan views

Wires the trained right-edge model into the two live web surfaces it had
been absent from. Deepdive renders a &#34;Right-Edge Confirmation&#34; card with
the probability for the most recent tentative degree-2 pivot; scan grows
a sortable Right-Edge column populated at scan time so archived reports
carry the field.

- right_edge/assessment.py: pure assess_from_bars helper (shared web + scan)
- web/right_edge.py: lazy ConfirmationModel cache + provider-fed wrapper
- web/routes/deepdive.py: /deepdive/{ticker}/right-edge HTMX fragment
- scan/models.py: ScanRow.right_edge_proba (None-default; archive-compatible)
- scan/orchestrator.py: run_scan(right_edge_model=...); errors swallowed
- scan/ranking.py: &#34;right_edge&#34; sort key (None last)
- cli/app.py scan: loads model via load_right_edge_model(cfg.right_edge)

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`d8b537d`](https://github.com/chen-star/wave-alpha/commit/d8b537dcbb18448d8ec67a21974eff9c3fe58d9c))

* feat(right-edge): ship calibrated heuristic model (PROMOTE verdict)

Heuristic + per-degree Platt scalers fitted via walk-forward across the
same 49-ticker × 21-quarter universe.

Brier 95% CI: [0.2198, 0.2240, 0.2279] — strictly better than raw heuristic
[0.2290, 0.2366, 0.2448] AND strictly better than logistic [0.2282, 0.2318,
0.2350] (CIs barely don&#39;t overlap).

Per-degree ECE 2-4× better than raw heuristic on every degree:
  deg 0  ECE: 0.077 (was 0.186)
  deg 1  ECE: 0.030 (was 0.127)
  deg 2  ECE: 0.057 (was 0.119)
  deg 3  ECE: 0.072 (was 0.109)
  deg 4  deg 4  ECE: 0.067 (was 0.153)

Loader auto mode currently prefers logistic; the calibrated heuristic is
available as an explicit `right_edge.model: calibrated_heuristic` config or
loaded automatically only when the logistic artifact is missing. Whether to
flip the preference order is a design question (the simpler model has tighter
Brier; the logistic has tighter ECE in some regimes).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`09fa647`](https://github.com/chen-star/wave-alpha/commit/09fa64751d983dcaf5a6b8f23184df0fcfde1672))

* feat(right-edge): ship trained logistic model (PROMOTE_CALIBRATION verdict)

4998 train rows / 4061 OOS predictions across 49 tickers × 21 quarters.
Brier essentially even with heuristic (CIs overlap), but per-degree
calibration is 2-3× better on every degree:

  degree 0  ECE: 0.060 (was 0.186)  3.1× better
  degree 1  ECE: 0.049 (was 0.127)  2.6× better
  degree 2  ECE: 0.055 (was 0.119)  2.2× better
  degree 3  ECE: 0.040 (was 0.109)  2.7× better
  degree 4  ECE: 0.047 (was 0.153)  3.2× better

Live inference (auto mode) now uses the logistic — stated probabilities
match empirical confirmation rates much more closely than the heuristic.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`f7d20f3`](https://github.com/chen-star/wave-alpha/commit/f7d20f318dfe0374d62aa0e0e7aa8e96c5b0a43d))

* feat(right-edge): loader supports calibrated_heuristic — auto prefers logistic &gt; calibrated &gt; raw

- prefs/models.py: add &#34;calibrated_heuristic&#34; to RightEdgeConfig.model literal.
- loader.py: extend load_right_edge_model() to handle calibrated_heuristic mode
  (explicit) and auto mode (logistic &gt; calibrated_heuristic &gt; heuristic preference
  order). Imports CalibratedHeuristicModel from calibrated_heuristic.py. Backward-
  compatible via path= alias for older callers. Adds _DEFAULT_CALIBRATED_HEURISTIC_PATH.
- tests: add 8 new loader tests covering: explicit calibrated_heuristic mode,
  auto preferring logistic over calibrated heuristic, auto fallback to calibrated
  heuristic when logistic missing, fallback chain to raw heuristic, FileNotFoundError
  for missing explicit artifact, legacy path= kwarg backward compat.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`11f5348`](https://github.com/chen-star/wave-alpha/commit/11f5348efabd8cc3fa1151891bacddf9af3e7a68))

* feat(right-edge): wave-alpha train calibrated-heuristic — Platt-on-heuristic fit

- training.py: add fit_calibrated_heuristic() — walk-forward Platt scalers on top
  of the heuristic, same fold structure and leak-fixed Platt order as walk_forward().
  Also add serialize_calibrated_heuristic_artifact(). Note: gate.logistic_brier_ci
  is reused for the calibrated-heuristic vs raw-heuristic Brier CI — pragmatic field
  name reuse documented in docstring.
- cli/app.py: extract _load_rows_by_quarter() private helper shared by both train
  commands (universe load, boundary computation, row assembly, bucketing by quarter).
  Add train calibrated-heuristic command. --write gate accepts PROMOTE and
  PROMOTE_CALIBRATION verdicts. Output labels &#34;Calibrated-heuristic&#34; vs &#34;Raw heuristic&#34;.
- tests: add 5 CLI smoke tests for calibrated-heuristic (help, end-to-end, write-gate,
  force-write, promote-calibration-writes-artifact).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`4cf42fb`](https://github.com/chen-star/wave-alpha/commit/4cf42fbbaed4ba32f69db4ee66050d6b61e4473c))

* feat(right-edge): CalibratedHeuristicModel — heuristic + per-degree Platt runtime class

Add calibrated_heuristic.py with CalibratedHeuristicModel dataclass:
- from_json loads JSON artifact, accepts PROMOTE or PROMOTE_CALIBRATION verdicts.
- predict_proba applies per-degree Platt scaler on top of the stateless heuristic.
- Identity scaler (a=1, b=0) is the fallback for degrees absent from the artifact.
- Sigmoid clipped to [-50, 50] to prevent OverflowError on pathological weights.
- CalibratedHeuristicArtifactError raised for non-promotable verdicts.

Tests cover: load PROMOTE/PROMOTE_CALIBRATION, refuse HOLD/REJECT/missing-gate,
predict_proba in [0,1], identity fallback for unknown degree, correct Platt math,
no overflow on extreme weights.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`6de10d0`](https://github.com/chen-star/wave-alpha/commit/6de10d0ec252c6e9e5d721d1b5c4787fbc06bd5b))

* feat(right-edge): PROMOTE_CALIBRATION verdict — gate ships on calibration-only wins

- promotion.py: add Verdict literal PROMOTE_CALIBRATION, _CALIBRATION_PROMOTE_RATIO=0.20,
  and _calibration_strictly_better() helper. Verdict evaluation order: REJECT → PROMOTE
  → PROMOTE_CALIBRATION → HOLD. Threshold chosen as smallest gap clearly above bootstrap
  noise on per-degree calibration.
- logistic.py: add _PROMOTE_VERDICTS frozenset; from_json now accepts both &#34;PROMOTE&#34;
  and &#34;PROMOTE_CALIBRATION&#34; instead of only &#34;PROMOTE&#34;.
- cli/app.py: --write gate updated to accept both promote verdicts.
- tests: add test_promote_calibration_when_brier_overlaps_and_calibration_clearly_better,
  test_hold_when_calibration_better_but_below_threshold, test_promote_strong_path_still_works,
  and test_from_json_loads_promote_calibration_artifact. Updated test_hold_when_close to use
  identical preds (zero calibration delta) to avoid PROMOTE_CALIBRATION false-positive.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`f602e8a`](https://github.com/chen-star/wave-alpha/commit/f602e8acfc7ab4a3c5a1cdb3ae859bce547c5738))

* feat(right-edge): wire ConfirmationModel into pipeline + web + pivot_layer (Task 6.2)

PivotLayerEvaluator gains a model: ConfirmationModel field with _HeuristicAdapter()
default factory so existing callers get the heuristic unchanged; CLI/web callers
can pass a logistic model loaded from config. predict_proba call updated to pass
degree=degree. train right-edge command updated to use _HeuristicAdapter for the
baseline comparison (uniformity: all predict_proba calls now take degree kwarg).
326 tests passing.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`0bfd90c`](https://github.com/chen-star/wave-alpha/commit/0bfd90c3284319630c2637c476ffd441aed4fa76))

* feat(right-edge): RightEdgeConfig + load_right_edge_model loader (Task 6.1)

Add RightEdgeConfig (auto/heuristic/logistic) to AppConfig in prefs/models.py.
Implement load_right_edge_model() in right_edge/loader.py: heuristic returns
_HeuristicAdapter, logistic raises FileNotFoundError on missing artifact,
auto falls back silently to _HeuristicAdapter if artifact absent or non-PROMOTE.
7 passing tests in tests/right_edge/test_loader.py.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`2fbb629`](https://github.com/chen-star/wave-alpha/commit/2fbb629ff3368c08928ef053973c9479e612b8ce))

* feat(cli): wave-alpha train right-edge — walk-forward + gate

Adds a `train` Typer subgroup and a `right-edge` command that runs the
full walk-forward training pipeline, scores the logistic against the
heuristic baseline via bootstrap-CI promotion gate, prints the verdict
with Brier CIs and per-degree calibration, and optionally writes the
JSON artifact (gated by PROMOTE verdict; --force bypasses the gate).

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`3585d84`](https://github.com/chen-star/wave-alpha/commit/3585d84b9da09ff71a6b492d5b4d2d7508ee0e4b))

* feat(right-edge): JSON-persisted LogisticConfirmationModel + artifact serializer

Adds Task 4.2 round-trip tests for serialize_artifact, write_artifact,
and _git_sha (already in training.py). Tests cover: artifact key shape,
string keys in platt_per_degree (JSON constraint), file creation,
full serialize→write→from_json→predict_proba round-trip (confirms no
train/serve skew), and _git_sha fallback to &#34;unknown&#34; on failure.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`40c24a5`](https://github.com/chen-star/wave-alpha/commit/40c24a5423670182460f14ec29b36f7ce18ad1be))

* feat(right-edge): LogisticConfirmationModel — JSON-loadable runtime model

Adds logistic.py with ModelArtifactError and LogisticConfirmationModel
(frozen dataclass). from_json refuses non-PROMOTE artifacts; predict_proba
applies per-degree Platt calibration with identity fallback for unknown
degrees. _vector mirrors _row_to_vector in training.py exactly (same field
order, same bars_since/20 rescaling) — locked in by a round-trip test.

Tests: from_json PROMOTE/HOLD/REJECT/missing-gate; predict_proba unit interval;
round-trip skew invariant; unknown-degree identity fallback.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`e1a7e25`](https://github.com/chen-star/wave-alpha/commit/e1a7e25277f6c5ecd9c2506593bcfc9a31528f72))

* feat(right-edge): bootstrap-CI promotion gate (PROMOTE/HOLD/REJECT)

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`8de5631`](https://github.com/chen-star/wave-alpha/commit/8de5631006dfcfe90ea42b05481fd354d1da9dbd))

* feat(right-edge): walk-forward training pipeline (logistic + Platt-per-degree)

Implements training.py in one file across all 4 Phase-2 tasks:
- Task 2.1: TrainingRow + Fold dataclasses + quarter_folds() expanding-window helper
- Task 2.2: LogisticParams, PlattParams, fit_logistic (batch GD, deterministic), fit_platt, predict_logistic, apply_platt
- Task 2.3: CalibratedPrediction, WalkForwardResult, walk_forward() single-pass OOS loop
- Task 2.4: assemble_training_rows() bridging OHLCVProvider + FeatureExtractor to TrainingRow stream

Also includes serialize_artifact / write_artifact helpers (used by Phase 4 CLI).
22 new tests all passing; total suite 295 tests green.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`11fe9b0`](https://github.com/chen-star/wave-alpha/commit/11fe9b08d0aa5c337c6c24431efa906fb950bc45))

* feat(right-edge): FeatureExtractor — shared point-in-time feature computation

New FeatureExtractor class in right_edge/extractor.py serves as the
single source of truth for feature computation, eliminating train/serve
skew. Covers HIGH and LOW pivot types; raises on empty bars. Tested with
high/low pivot fixtures and empty-bars guard.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`7fa7f43`](https://github.com/chen-star/wave-alpha/commit/7fa7f43404e607e3f7d5fa07b851afbb641e24c1))

* feat(pivots): expose ATR + required-move per degree via DegreeSnapshot

Add DegreeSnapshot dataclass and detect_multi_degree_with_snapshots()
as a sibling to detect_multi_degree(). The existing function is
unchanged; the new one augments each degree&#39;s pivot list with
atr_at_last_bar and required_move so callers (right-edge feature
extractor, pivot_layer) no longer need to re-derive these values.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`61ea832`](https://github.com/chen-star/wave-alpha/commit/61ea832f0f98e38d5f68cbf2a263710e8b448885))

* feat(web): coherence tri-badge W ✓ D ✓ 4h ⚠ per spec §11

build_tri_badge derives per-timeframe status from CoherenceResult:
- ok  when pairwise score &gt;= 0.6 against the daily trunk
- warn when present but below threshold
- missing when the timeframe has no counts at all

Threshold 0.6 matches the existing &#34;partial agreement&#34; floor used in
the count-layer backtest coherence buckets.

Rendered above the existing pairwise breakdown so users see at-a-glance
agreement, with the underlying numbers still available for drill-in.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`7b4a380`](https://github.com/chen-star/wave-alpha/commit/7b4a38014f7ad5aeabb72d2ddf06c9820d3e890d))

* feat(web): nav strip linking Scan, Watchlist, Settings

Single horizontal nav in the base template. /deepdive/{ticker} pages
inherit it through the {% extends %} chain without per-page changes.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`3085218`](https://github.com/chen-star/wave-alpha/commit/3085218d918c33aa11e529bdd6b68d7c9059b669))

* feat(web): /scan dashboard reads JSON archives

GET /scan lists recent scan IDs (newest first) with a selector.
GET /scan/{id} renders the most recent scan or the selected one with a
sortable table. GET /scan/{id}/table?sort=...&amp;signals_only=... is the
HTMX fragment for in-place sort and filter.

Each ticker links to its deep-dive page with the scan&#39;s as_of date so
the &#34;what was the system seeing&#34; view stays consistent.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`1d66caf`](https://github.com/chen-star/wave-alpha/commit/1d66cafbe2468776a0cb0e14533c6eeaf27f725c))

* feat(web): /watchlist page with add/remove forms

POST-redirect-GET pattern (303) for add/rm so refresh doesn&#39;t double-
submit. Symbols link to /deepdive/{symbol} for one-click drill-in.

Bulk import is intentionally CLI-only — keeps the web page focused and
avoids file-upload complexity for a power-user feature.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`e32b503`](https://github.com/chen-star/wave-alpha/commit/e32b503bd20a3434ca1f6f52fcd7602d1686502f))

* feat(cli): wave-alpha scan command — multi-ticker scan + persist

Defaults to scanning the watchlist; override with --symbols A,B,C.
Reuses the existing _build_runner so LLM modes work the same as
analyze. Persists a JSON report to ~/.wave_alpha/scans/&lt;scan_id&gt;.json
and prints a one-line summary per ticker.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`7930d42`](https://github.com/chen-star/wave-alpha/commit/7930d42bd455987fcee39d0fbb3f95d706bfc9a9))

* feat(scan): pure-function ranking helpers (sort_by, filter_signals)

sort_by accepts &#34;score&#34; | &#34;rr&#34; | &#34;ticker&#34; | &#34;coherence&#34;. Numeric sorts
push None-valued rows to the end regardless of direction. ticker sort
is alphabetical ascending (ignores reverse flag — UX choice).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`808897d`](https://github.com/chen-star/wave-alpha/commit/808897d379af11d93b750ff94c408481fbd8a2f1))

* feat(scan): JSON-backed scan report archive

save_report writes ~/.wave_alpha/scans/&lt;scan_id&gt;.json (Pydantic
model_dump_json). load_report returns None on missing. list_reports
returns scan_ids newest-first (ISO timestamp lex sort).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`f0bec8c`](https://github.com/chen-star/wave-alpha/commit/f0bec8c4c11f0a7f301035fd9ee77508bec172f4))

* feat(scan): run_scan orchestrator — one ScanRow per ticker

Walks the symbol list, runs analyze per ticker, captures errors
per-ticker (a bad symbol does not abort the scan). LLM-disabled by
default; opt-in via llm_mode + llm_runner.

ScanRow fields cover engine outputs (top pattern, score, direction,
coherence) and trade signal (entry/stop/target/rr) when present.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`d376206`](https://github.com/chen-star/wave-alpha/commit/d376206ee81fb55120057225e2a41961d08e4d1d))

* feat(scan): ScanRow + ScanReport frozen Pydantic models

ScanRow distinguishes &#34;engine didn&#39;t produce&#34; (None on field) from
&#34;hard error&#34; (error set). has_signal / has_error are read-only props.

ScanReport tracks scan_id, start/end timestamps, as_of, rows, and
exposes duration + summary counts.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`900d005`](https://github.com/chen-star/wave-alpha/commit/900d0052c357cf55efe5b05d76ba954f6f417a74))

* feat(cli): wave-alpha watchlist add|rm|ls|import subcommands

Manages the YAML-backed watchlist at ~/.wave_alpha/watchlist.yaml.
Import accepts both CSV (first-column symbols, header detected) and
TradingView TXT exports (NASDAQ:AAPL with section markers).

Tests use a `_watchlist_path` indirection so monkeypatch can point at
tmp_path — same pattern as `_config_path` for prefs. Import counting
uses a pre-loop initial_set snapshot rather than double-loading per symbol.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`0cf246f`](https://github.com/chen-star/wave-alpha/commit/0cf246fc0b08e853c006afa5670c1f6dd95bbcd1))

* feat(watchlist): CSV and TradingView TXT import parsers

parse_csv_symbols reads the first column, skips Symbol/Ticker header
rows, dedupes, lenient on invalid cells.

parse_tv_txt_symbols handles the TV export format: comma-separated
EXCHANGE:SYMBOL tokens, bare symbols, and ###Group### section markers
(skipped). Strips exchange prefixes (NASDAQ:, NYSE:, etc).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`6f4182e`](https://github.com/chen-star/wave-alpha/commit/6f4182e6f0086e4ae2c01b4528a3c889bea705f1))

* feat(watchlist): WatchlistEntry model + YAML-backed store

Persists at ~/.wave_alpha/watchlist.yaml. add_entry is idempotent
(existing symbol returns the original entry, preserving added_on);
remove_entry returns bool so callers can distinguish &#34;removed&#34; from
&#34;wasn&#39;t there&#34;.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`07237f5`](https://github.com/chen-star/wave-alpha/commit/07237f55d849d7160dbf635720f24b60a78496a2))

* feat(cli): wave-alpha backtest count|pivot subcommands

Two subcommands under `wave-alpha backtest`, both writing markdown
reports to a user-specified path:

- count: walks (universe x business days) at configurable subsample;
  writes top-1/top-3 hit rates, coherence buckets, yearly breakdown.
- pivot: takes (initial, final) snapshot per ticker; writes per-degree
  confirmation rate, Brier score, calibration error.

Backtest is --llm-mode disabled by construction (no LLM client is
instantiated in either runner) so headline numbers reproduce without
an API key.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`42e1699`](https://github.com/chen-star/wave-alpha/commit/42e1699ad5fccda6778c4162f0cc8432b0056e98))

* feat(backtest): markdown report renderers for count + pivot layers

Count report: headline top-1/top-3 hit rates, coherence-bucket
breakdown (full/partial/disagreement), per-year breakdown for regime
diagnostics.

Pivot report: per-degree confirmation rate, Brier score, 10-bin
reliability calibration error.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`c97d76f`](https://github.com/chen-star/wave-alpha/commit/c97d76f3e897a4834fb444b9a324189b13e52d7d))

* feat(backtest): BacktestRunner orchestrates universe × dates

run_count_layer iterates business days × universe, returning a single
aggregated CountLayerResult — explicitly does not instantiate any LLM
client (test asserts this) so backtest stays reproducible without a key.

run_pivot_layer takes one (initial, final) pair per ticker; walk-forward
extension across multiple snapshots ships in v0.4.x once the v0.4 shape
is validated.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`d3d96b9`](https://github.com/chen-star/wave-alpha/commit/d3d96b976816c4c1eda546aadde0ab73327dd830))

* feat(backtest): PivotLayerEvaluator walks initial→final snapshots

For each TENTATIVE pivot in the initial snapshot, runs the heuristic
right-edge model to produce a predicted confirmation probability and
compares to pivot_confirmation_label against the final snapshot. The
heuristic uses placeholder feature values for v0.4 — the harness shape
is the deliverable; richer features land in v0.4.x with the logistic
model.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`74cd4b6`](https://github.com/chen-star/wave-alpha/commit/74cd4b6dfbb4ab238bb90edaa22b5e8eafcccadc))

* feat(backtest): PivotEvaluation + PivotLayerResult with Brier integration

PivotEvaluation captures one tentative-pivot outcome at a given degree,
storing the heuristic-predicted confirmation probability and the
realized confirmed/invalidated outcome.

PivotLayerResult exposes per-degree confirmation rate (spec §8 metric),
Brier score, and reliability calibration error.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`8bcd4d7`](https://github.com/chen-star/wave-alpha/commit/8bcd4d7d784a6675277cab330081c524fbaebef8))

* feat(backtest): CountLayerEvaluator walks (ticker x as_ofs) reusing engine

Bypasses pipeline.run_analysis to skip the LLM seam — count-layer
backtest is reproducible without an API key. Reuses pivot detection +
enumeration directly with the same defaults pipeline uses.

For each as_of, derives top-1 and top-3 predicted directions and
compares to forward_direction_label at n_bars_forward (default 20).
Skips samples with insufficient forward history or empty counts.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`96027a9`](https://github.com/chen-star/wave-alpha/commit/96027a9797ac76b8f75ea768b5e234deaaa23f32))

* feat(backtest): coherence-bucket and yearly count-layer breakdowns

Adds CountLayerResult.by_coherence_bucket() returning sample size and
hit rates for full (&gt;0.7), partial (0.4-0.7), and disagreement (&lt;0.4)
buckets per spec §8 sample report.

Adds CountLayerResult.by_year() returning the same per-year for
regime-aware diagnostics — 2020 vol spike, 2022 bear, etc.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`0805493`](https://github.com/chen-star/wave-alpha/commit/0805493b59f78fdafa010de5aa6a8bf890ecdff8))

* feat(backtest): CountEvaluation + CountLayerResult models

CountEvaluation captures one (ticker, as_of) sample with predicted
direction (top-1), actual forward direction, coherence at signal
time, and per-rank hit flags.

CountLayerResult aggregates samples and exposes top1/top3 hit rates.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`cfa40fa`](https://github.com/chen-star/wave-alpha/commit/cfa40fa1c31b9f17c010916e290916823e9a5651))

* feat(backtest): Brier score, reliability bins, calibration error

Pure functions over (predicted_probability, actual_outcome) pairs:
- brier_score: mean squared error, lower is better, range [0, 1]
- reliability_bins: groups predictions into N equal-width buckets
- calibration_error: weighted mean gap between predicted and actual
  per bin; sample-size weighted

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`852af14`](https://github.com/chen-star/wave-alpha/commit/852af148190740d175d8e872a73154500b3eac93))

* feat(backtest): pure-function labelers for direction + pivot outcome

forward_direction_label compares close at as_of vs close n bars later,
returning &#39;up&#39;/&#39;down&#39; or None when forward history is short. Flat close
labels as &#39;down&#39; (no bullish edge convention).

pivot_confirmation_label compares a tentative pivot to a later pivot
snapshot at the same degree, returning &#39;confirmed&#39; / &#39;invalidated&#39; /
None (still pending).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`ef05c89`](https://github.com/chen-star/wave-alpha/commit/ef05c89e28988063f44ec6811894d4933ee48fce))

* feat(backtest): walk-forward business-day iterator

iter_business_days(start, end, step) yields Mon-Fri dates inclusive,
optionally subsampled by step (e.g. step=5 = weekly). Holidays are not
filtered — non-trading days produce empty provider results downstream
which the evaluators skip cleanly.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`b557139`](https://github.com/chen-star/wave-alpha/commit/b557139598ac1f3bc902e59753751bb754416de9))

* feat(backtest): universe loader + curated 50-ticker file

Adds wave_alpha.backtest.universe.load_universe — reads one symbol per
line, skips blanks/comments, uppercases, dedupes, validates symbol shape.

The curated 50-ticker list spans mega-cap tech, defensives, cyclicals,
financials, energy, biotech, and recent IPOs to span vol regimes for
backtest validation.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`6aa62bb`](https://github.com/chen-star/wave-alpha/commit/6aa62bb1b2c60a4ca561328dab36d6b5673f3521))

* feat(cli): wave-alpha ui launches local FastAPI UI with port discovery

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`2ef15f5`](https://github.com/chen-star/wave-alpha/commit/2ef15f5e558559d2bc514a55b665153b258aee9e))

* feat(web): /settings page reads/writes ~/.wave_alpha/config.yaml

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`12dcbd3`](https://github.com/chen-star/wave-alpha/commit/12dcbd3d88e7cc6bdf22ec6febf685cd3adafb15))

* feat(web): right-edge scenarios fragment surfaces alt counts with invalidation

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`b40ad10`](https://github.com/chen-star/wave-alpha/commit/b40ad10fb9e5c4c8a1a32828113cdc3a99ebd8cb))

* feat(web): trade plan fragment with entry/stop/target/RR card

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`b1bf66f`](https://github.com/chen-star/wave-alpha/commit/b1bf66f4956d71f69ecfb628a020e75c68da5cf3))

* feat(web): counts fragment with engine, llm, and final scores plus narratives

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`18d5c29`](https://github.com/chen-star/wave-alpha/commit/18d5c2958a744c9852426eec248a50425c52af75))

* feat(web): coherence fragment with score, multiplier, and pairwise breakdown

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`05d08d0`](https://github.com/chen-star/wave-alpha/commit/05d08d09b46381b923492a1ea2d8e28812298a08))

* feat(web): chart fragment with Lightweight Charts pivot overlay

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`c5c73b7`](https://github.com/chen-star/wave-alpha/commit/c5c73b7f6e3af9a6199931340e6f18f2f96b82ad))

* feat(web): GET /deepdive/{ticker} renders shell with HTMX targets

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`4ee74a2`](https://github.com/chen-star/wave-alpha/commit/4ee74a2d442042f93eb9f776069ee750c1fea26d))

* feat(web): base and deepdive templates with HTMX fragment loaders

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`378ed5a`](https://github.com/chen-star/wave-alpha/commit/378ed5a4280644c2f648d95e858d0fe3fdc7f553))

* feat(web): deps.analyze seam composes provider and LLMRunner

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`4a43357`](https://github.com/chen-star/wave-alpha/commit/4a433574e8254a5526147678d5b5cf6521d13b1b))

* feat(web): chart_data builder converts engine output to Lightweight Charts JSON

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`8842645`](https://github.com/chen-star/wave-alpha/commit/8842645ceb60ddd9a2e069a16aeac4ddc6ee835b))

* feat(web): /healthz, / redirect, /quit system routes

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`0df9992`](https://github.com/chen-star/wave-alpha/commit/0df99924b54c918b76b715866b5ece8b2a6ed85f))

* feat(web): FastAPI app factory with templates, static, and disclaimer

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`547f876`](https://github.com/chen-star/wave-alpha/commit/547f8761f655f599fc931575e7c3b67c7aad6b13))

* feat(web): hand-written app.css for v0.3 deep-dive UI

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`b3f1e7b`](https://github.com/chen-star/wave-alpha/commit/b3f1e7bed26a3ee9cacea73968c77766858af29b))

* feat(cli): config show and config set commands

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`0d555a9`](https://github.com/chen-star/wave-alpha/commit/0d555a9a8d9c400e3ab46af10c30595e2fa5107b))

* feat(cli): analyze --llm-mode and --alpha; print ranked_daily

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`a9873af`](https://github.com/chen-star/wave-alpha/commit/a9873aff9618be0f3f1362047ba031da10675a01))

* feat(pipeline): wire LLM scan, ranking, and blended final score

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`ac03766`](https://github.com/chen-star/wave-alpha/commit/ac0376650087090c3c9f666e87b092040fa31ff1))

* feat(llm): blend_scores per spec §6 final ranking formula

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`fdb248e`](https://github.com/chen-star/wave-alpha/commit/fdb248e8e4aeb57284d07901978d6396f3768ff0))

* feat(llm): LLMRunner orchestrates cache + client per mode

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`7ec2e16`](https://github.com/chen-star/wave-alpha/commit/7ec2e1622f78a518d42f1fce69d0e00c25e6aa06))

* feat(llm): SQLite response cache keyed by (model_id, prompt_hash)

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`b894eb0`](https://github.com/chen-star/wave-alpha/commit/b894eb0fdfc0c7eed46fddf3f53d70d810fdab43))

* feat(llm): AnthropicClient wrapper with prompt-cache breakpoint

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`ff674a3`](https://github.com/chen-star/wave-alpha/commit/ff674a33438e1cae9a8f05b6bfa5c9849931c42c))

* feat(llm): pydantic response models and strict parsers

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`1460960`](https://github.com/chen-star/wave-alpha/commit/1460960f21a6b777d9078cf403f6dfa928893d3e))

* feat(llm): scan and deep-dive system prompts plus user-prompt builders

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`04ebf28`](https://github.com/chen-star/wave-alpha/commit/04ebf28b0a59c3c38f5db3d671005a31c754caac))

* feat(llm): compact text serialization of WaveCount candidates

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`0e4c5fd`](https://github.com/chen-star/wave-alpha/commit/0e4c5fde24e9046821948d43c9e934bcd63c57bb))

* feat(llm): LLMMode enum (live/cached/disabled)

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`8103118`](https://github.com/chen-star/wave-alpha/commit/81031182d923d8176f79da59bae39e605a863c8f))

* feat(prefs): YAML round-trip and API key resolver

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`bef1f94`](https://github.com/chen-star/wave-alpha/commit/bef1f94e1dea76fb8324bdc205424bcaec721c0b))

* feat(prefs): AppConfig and LLMConfig pydantic models

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`194dfe6`](https://github.com/chen-star/wave-alpha/commit/194dfe66e487a084333c117b7165f38f78b7f71e))

* feat(data,cli): sorted-bars assertion, CachedProvider, lazy as_of

I4: PointInTimeView&#39;s __post_init__ now hard-asserts sorted ascending bars
via a new UnsortedBarsError. The lookahead invariant relied on monotonic
ordering — yfinance happens to return ascending order today, but a future
provider could violate it silently. Better to fail loud at construction.

I5: new CachedProvider decorator wraps an upstream OHLCVProvider with the
existing OHLCVCache. The CLI&#39;s _default_provider now stacks
CachedProvider(YahooProvider) at ~/.wave_alpha/cache.sqlite so repeated
analyze runs hit yfinance only when the cache is cold for that
(ticker, interval) pair. Three new tests cover hit/miss/empty paths.

M2: as_of CLI default is now resolved at command invocation (None → today)
instead of at module import time.

M4: test_version_command now asserts equality with __version__ instead of
just dot-counting — survives a hypothetical 1.0 release.

Bonus: tests/test_cli.py used to carry the same year-only-shift fixture
that the broken pipeline test had; the new sorted-bars assertion exposed
it. Replaced with the SyntheticImpulseProvider from test_pipeline.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`5835afd`](https://github.com/chen-star/wave-alpha/commit/5835afd8718336dc56a81d6a66d70b5fe6289532))

* feat(cli): wave-alpha analyze TICKER with --json output

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`576e417`](https://github.com/chen-star/wave-alpha/commit/576e417a2a0e1162ca29af07ccb7b79b796db856))

* feat(pipeline): orchestrator wiring data, pivots, elliott, coherence, trades

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`fdbc36a`](https://github.com/chen-star/wave-alpha/commit/fdbc36a0cbe22075ff3061b8d19a23630c760c19))

* feat(trades): derive signal from WaveCount per TradeRules

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`aecf3c3`](https://github.com/chen-star/wave-alpha/commit/aecf3c3ba5b60e58753f242aef6eb4412ac58ee6))

* feat(trades): TradeRules and TradeSignal Pydantic models

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`33b4df2`](https://github.com/chen-star/wave-alpha/commit/33b4df2d09ca793a67cc35a380f384771e6b4c2d))

* feat(coherence): pairwise + three-way blend with insufficient-history fallback

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`166c786`](https://github.com/chen-star/wave-alpha/commit/166c786f009cf0152a81100b3b1fd24edfd0375a))

* feat(right_edge): HeuristicConfirmationModel v1

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`1a4ab83`](https://github.com/chen-star/wave-alpha/commit/1a4ab83ffb6802f4e4ae2cfe172cf006c53b3de5))

* feat(right_edge): RightEdgeFeatures with point-in-time extraction

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`f21892a`](https://github.com/chen-star/wave-alpha/commit/f21892a29a5110addb64b6588ec881d050f88079))

* feat(elliott): count enumerator with template fit and fib confluence scoring

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`0f88abf`](https://github.com/chen-star/wave-alpha/commit/0f88abf7a6833929617bb33713a7ecf31a227f8a))

* feat(elliott): fib retracement, extension, and confluence scoring

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`42f2d84`](https://github.com/chen-star/wave-alpha/commit/42f2d84abdc4a80eee597d68e59df350ee3daba4))

* feat(elliott): rule validator with hard/soft violation lists

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`363fe9f`](https://github.com/chen-star/wave-alpha/commit/363fe9f1819ff64c4f0b7f8068dd499e3b36ae3d))

* feat(elliott): YAML pattern template loader with v0.1 starter set

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`5e4cbce`](https://github.com/chen-star/wave-alpha/commit/5e4cbce5473b792803d3ff3a57c28aacdfd46a89))

* feat(elliott): constraint DSL with length/retrace/overlaps helpers

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`35d8133`](https://github.com/chen-star/wave-alpha/commit/35d81336c2bcba3c4379560895ce4c9d03b5ba52))

* feat(elliott): WaveSegment and WaveCount domain models

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`07092cc`](https://github.com/chen-star/wave-alpha/commit/07092cc5d40fb2007c9966a212739a6733d86a15))

* feat(pivots): multi-degree orchestrator enforcing subset invariant

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`a550db5`](https://github.com/chen-star/wave-alpha/commit/a550db5bfa730ffec6fab2555d372d2da6fa13f3))

* feat(pivots): single-threshold ATR-ZigZag with confirmed/tentative tail

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`4f67528`](https://github.com/chen-star/wave-alpha/commit/4f67528b277924e4b4a7225e06c1d57b7876f5be))

* feat(pivots): Wilder ATR computation

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`91833c3`](https://github.com/chen-star/wave-alpha/commit/91833c31799f55f7fccc9674b1b9b3f37fef8ce1))

* feat(data): SQLite-backed OHLCVCache with Decimal-safe storage

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`94a7309`](https://github.com/chen-star/wave-alpha/commit/94a73096e063eeea1840034182f27e5fbba744be))

* feat(data): YahooProvider with 4h client-side resampling

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`22b2a17`](https://github.com/chen-star/wave-alpha/commit/22b2a1713cc93038913d3d57792adaec09a1e7c5))

* feat(data): OHLCVProvider ABC

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`5c3282f`](https://github.com/chen-star/wave-alpha/commit/5c3282f052848fc6327e2b332d9f9c375b82e709))

* feat(data): PointInTimeView with hard lookahead assertions

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`78897a4`](https://github.com/chen-star/wave-alpha/commit/78897a4fe326f7d20bff7e3befc448fb000346dc))

* feat(domain): Bar and Interval domain models

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`321e850`](https://github.com/chen-star/wave-alpha/commit/321e8501c6f7269c8b0b42dfddd2287cdf37b55b))

* feat(cli): minimal typer app exposing version

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`70e7566`](https://github.com/chen-star/wave-alpha/commit/70e756635d51020623ecc70511305814bf7de6eb))

### Fix

* fix: trigger first automated release ([`1c7b408`](https://github.com/chen-star/wave-alpha/commit/1c7b40823d720f9a2fe055fee12651a35df00ac5))

* fix(web): align α popover with actual 0.6 default + hoist out of &lt;label&gt; ([`fca8e34`](https://github.com/chen-star/wave-alpha/commit/fca8e344fdec2c157dd93b5704b84d2ba15444c3))

* fix(web): hoist coherence popover out of &lt;p&gt; + document placement rule ([`8127415`](https://github.com/chen-star/wave-alpha/commit/8127415df0ec38e275d91d86494e981166c48e26))

* fix(web): improve popover a11y (aria-modal/labelledby) + clean up macro ([`780a1b5`](https://github.com/chen-star/wave-alpha/commit/780a1b5ec0a4f2b3510d9ddfada0c2a7fd3a77fa))

* fix(web): apply final-review fixes (scan filter v3, gloss a11y, hero, recent, paths)

- scan.html: switch hx-vals from Alpine v2 (__x.$data) to Alpine v3
  (Alpine.$data) so filter tabs / sort / min-RR actually submit values
- Add a keyboard-accessible gloss(term, tip) macro (tabindex=0, role,
  aria-label) and replace 3 inline &lt;span class=&#34;gloss&#34;&gt; usages in
  deepdive.html and settings.html
- New /deepdive/{ticker}/hero HTMX fragment + _hero.html: bias pill,
  pattern badge, wave label, and Last close / Confidence / R:R KPIs;
  loaded after the shell renders so first paint stays fast
- Home: add HomeView.recently_analyzed (with new RecentlyAnalyzed
  dataclass) populated from history store recent_across_all helper;
  add HomeView.watchlist_total so the &#34;of N watched&#34; denominator
  reflects the full list, not just the top-5 visible card
- settings.html: add a yellow Storage paths card listing the 4 user
  state files (read-only) for transparency
- Tests: hero fragment present + empty states; HomeView recently
  analyzed and watchlist_total fields; recent_across_all newest-first,
  one-row-per-ticker, limit, and missing-db cases

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`8836e84`](https://github.com/chen-star/wave-alpha/commit/8836e84a3fa2dc589530e4e19bb190492fbc1395))

* fix(cli): banner alignment, write-error tolerance, and discoverability

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`130925e`](https://github.com/chen-star/wave-alpha/commit/130925e0e7c93215f38f3453b738f3dde6ec0b61))

* fix(packaging): broaden ImportError msg + alphabetize extras

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`c76a936`](https://github.com/chen-star/wave-alpha/commit/c76a9367a508f91601fd6c18249e01978f9ebaa9))

* fix(scripts): make spike&#39;s API baseline optional when ANTHROPIC_API_KEY missing

Original guard rejected runs without an API key, but the spike&#39;s whole
point is validating the keyless ClaudeCodeClient path. Now skips the
AnthropicClient leg gracefully if no key is set; ClaudeCodeClient still
runs and the artifact records the skip.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`ed24ee2`](https://github.com/chen-star/wave-alpha/commit/ed24ee20dcc7a8b9a380cb3cf32cd316343212aa))

* fix(backtest): grid axis_sensitivity surfaces inf for all-degenerate levels

Latent bug from T2: when every cell at an axis level had zero losses
(profit_factor == inf), the inf-exclusion filter produced an empty
list, _mean returned 0.0, and the report rendered the level&#39;s PF as
&#34;0.00&#34; — falsely implying catastrophic losses when the truth is
&#34;uniformly profitable, no losses at all.&#34;

Extract _pf_mean helper that:
- Averages finite-PF cells when any are present (existing behavior).
- Surfaces float(&#34;inf&#34;) when ALL cells are degenerate-positive (new).
- Returns 0.0 only on truly empty input (defensive).

render_grid_markdown&#39;s axis-sensitivity table now translates inf to
&#34;∞&#34;, matching the per-cell table convention. Two tests pin the
new semantics: existing test asserts the all-inf level emits inf,
new test asserts finite-only levels still average correctly.

Surfaced by T5 code review.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`31cbe14`](https://github.com/chen-star/wave-alpha/commit/31cbe145e8bff4505bcac28b80902ee1dbda453d))

* fix(backtest): move grid.py runner/provider/models imports to top

T4 (commit e91c737) added the run_grid imports at the bottom of
grid.py with # noqa: E402 suppressions, breaking the existing
top-of-file import convention. Move them to the top and drop the
suppressions. No functional change.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`0f99c45`](https://github.com/chen-star/wave-alpha/commit/0f99c4599430ba6792b82187ceeca22ff2e66a4d))

* fix(backtest): grid axis_sensitivity handles degenerate PF cells

Two review-flagged issues from code review of 0e98c27:

1. _pf_or_zero coerced PF=inf cells to 0 in axis_sensitivity averages,
   biasing the level mean downward — a gain-only cell looked like the
   worst possible cell. Fix: exclude PF=inf rows from the per-level PF
   mean rather than coercing. Helper _pf_or_zero is now unused; deleted.
   Test test_grid_axis_sensitivity_excludes_degenerate_pf_cells pins the
   new behavior with a controllable n_wins/n_losses synthetic.

2. axis_sensitivity inferred axis names from rows[0] only, silently
   mis-aggregating when rows come from different configs. Fix: defensive
   assertion that all rows share the same axis set; raises ValueError
   naming the offending row index on mismatch.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`f7c8c3e`](https://github.com/chen-star/wave-alpha/commit/f7c8c3ed7f78c8f720c56e3ffd2b4b1a84045eca))

* fix(scripts): C5 EBT diversity skips single-degree case

C5 expects ≥5 distinct EBT values per degree, but EBT is constant per
degree (_DEFAULT_BARS_PER_DEGREE lookup) — a one-degree run can never
satisfy it by design. Skip the check when only one degree is present
and become meaningful again once multi-degree enumeration ships. See
spec §9 S6.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`c00f4eb`](https://github.com/chen-star/wave-alpha/commit/c00f4ebc1f21eea4cff65a20ce04e6c0a334f4a6))

* fix(derive): target + count_invalidation stop honor trade direction

Two long-standing invariant violations surfaced by the trade-sim sanity
run (12245 trades, 0% hit rate):

(1) _target_price ignored direction and projected fib_extension off W1&#39;s
    price direction. For W5-complete shorts (W1 up, trade direction
    down), the target landed above anchor — wrong side. The sim&#39;s target
    check (bar.low &lt;= target_price) then fired immediately on day 0,
    exiting shorts at a price far above entry and labeling it &#34;target&#34;.
    Fix: project |W1| × 1.618 from anchor, on the trade-direction side.

(2) _stop_price for count_invalidation returned segs[3].end.price (W4)
    for any 4-or-more segment impulse. For a 5-segment (W5-complete)
    short, the W4 end is on the trade-direction side (below entry),
    making the &#34;stop&#34; a profit target. Fix: for 5+ seg impulses use the
    last pivot (W5 extremum); 4-seg case keeps W4-end as before.

Test: test_short_signal_target_below_entry pins both invariants
(target &lt; entry, stop &gt; entry) for a W5-complete short.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`308407e`](https://github.com/chen-star/wave-alpha/commit/308407efc80c1698c63fc49d328540ea3edb3c25))

* fix(scripts): prewarm passes db_path to CachedProvider

CachedProvider requires db_path; the v1 of this script omitted it and
crashed at startup. Use the canonical ~/.wave_alpha/cache.sqlite per
CLAUDE.md.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`0fb4833`](https://github.com/chen-star/wave-alpha/commit/0fb48331f66a0b49d7149cf408253d2dbec06d78))

* fix(data): yahoo provider returns unfiltered window

End-filtering is now a CachedProvider responsibility, applied uniformly
to both cache-hit and cache-miss return paths. yfinance&#39;s 730-day
intraday payload now lands in the cache verbatim. ([`b01d579`](https://github.com/chen-star/wave-alpha/commit/b01d57984eaf213fa2d609075f476b40cf887743))

* fix(data): cache hit predicate ignores empty filtered slice

Old-end backtest fetches now short-circuit on cache freshness alone,
returning the (often empty) filtered slice without re-hitting upstream.
Eliminates O(N) wasted yfinance calls on backtests reaching back past
yfinance&#39;s 730-day intraday window. ([`c612a54`](https://github.com/chen-star/wave-alpha/commit/c612a549bf1385984a9063bc0a796d48a6749c53))

* fix(right-edge): clip sigmoid input to prevent OverflowError on pathological weights

Added _sigmoid(z) helper that clamps z to [-50, 50] before computing 1/(1+exp(-z)).
Used in both the raw-logistic step and the Platt step of predict_proba.

math.exp overflows for z &lt; ~-710, so extreme negative weights (e.g. -1000)
previously raised OverflowError at runtime. The clip is harmless for all
weights of practical magnitude (|z| never exceeds ~50 during training).

Added two tests: extreme negative weights return a valid [0,1] float without
raising; extreme positive weights do the same.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`882ac72`](https://github.com/chen-star/wave-alpha/commit/882ac72752a91bccf4aff9b547f75a3be8c79689))

* fix(right-edge): walk_forward fits Platt on prior-fold data only

Previously Platt scalers were fit on data that included the current fold&#39;s
test rows before those rows were calibrated — in-sample leakage that biases
per-degree calibration error downward.

Fix: compute raw probas → collect test_raw list → fit Platt from prior
accumulated data → emit calibrated OOS predictions → then add this fold&#39;s
pairs to the accumulator for future folds.

The FINAL Platt (stored in platt_per_degree and used by the production model)
is still fit on all accumulated data — correct because it isn&#39;t self-evaluated.

Added test verifying first-fold predictions have calibrated_proba == raw_proba
(identity Platt since no prior data exists).

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`2372987`](https://github.com/chen-star/wave-alpha/commit/2372987ff650770985042ae906cf953491c9d654))

* fix(right-edge): paired bootstrap for promotion gate

Replaced independent bootstrap (different seeds per model) with paired bootstrap:
same resample indices applied to both logistic and heuristic prediction lists.
This reduces variance in the comparison and makes the verdict more powerful.

Also added explicit row-alignment assertion at the top of evaluate_promotion:
same length required, and (outcome, degree) must match at every row position.

Updated tests to cover: paired-indices property, misaligned inputs, length
mismatch, empty-list edge case.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`b4b218c`](https://github.com/chen-star/wave-alpha/commit/b4b218c6b387867bec3bee322fd902d98e2bdd16))

* fix(cli): assemble training rows across all quarters (warmup window was bypassed)

Previously as_of_pairs only covered fold test windows, starving walk_forward
of warmup-quarter training rows. Now we walk all quarter boundaries from start
to end so that warmup quarters produce TrainingRows that accumulate before the
first real fold&#39;s test window.

Updated the e2e smoke test to assert n_train_rows &gt; n_oos_rows (training data
includes warmup quarters that are not OOS).

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`d6356b7`](https://github.com/chen-star/wave-alpha/commit/d6356b72ecc6ead01591635c6ef64db09a008705))

* fix(backtest): pivot_layer uses real features (was: placeholder constants)

Replace _features_for() (which hardcoded retracement_progress=0.5,
fib_zone_match=0.5) with FeatureExtractor.extract() driven by
ATR and required_move from detect_multi_degree_with_snapshots.
Delete _features_for and _index_of_timestamp (no longer needed).

Brier numbers from pivot_layer now reflect the heuristic evaluated on
real point-in-time features rather than placeholder constants. Adds a
regression test locking in that evaluations come from real feature
computation.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`0091158`](https://github.com/chen-star/wave-alpha/commit/009115839d8cd6ea79c55294fcc932b55fd66dc8))

* fix(data): CachedProvider refetches when tail is stale

OHLCVCache exposes max_ts(ticker, interval); CachedProvider.fetch
returns cached bars only when the cache tail is within 1 calendar day
of the requested end date. Without this, nightly scans against the
default permissive cache silently served stale bars — a real bug
acknowledged by the v0.5 README workaround.

The upstream call falls back to cached data on empty response so
transient yfinance hiccups don&#39;t blow away the working set.

README updated to drop the manual cache-clear workaround.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`3400eb9`](https://github.com/chen-star/wave-alpha/commit/3400eb96e3fe9f7ceeb0f092ac8491afade13898))

* fix(scan): ruff lint fixes for scan package

- Add missing Literal import in orchestrator.py
- Replace BLE001 noqa with a plain comment (BLE001 not enabled in this project)
- Sort datetime imports in test_models.py
- Remove unused pytest + OHLCVProvider imports in test_orchestrator.py

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`2c79580`](https://github.com/chen-star/wave-alpha/commit/2c795809e1489b0da53fda43a0905391a5526f76))

* fix(web): preserve existing api_key on blank settings submission

Load existing config before saving so that an empty api_key field leaves
the stored key intact. Empty scan_model/deep_model also fall back to
existing values. Update settings.html to not render the key into the
value attribute (more secure); show a placeholder hint instead.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`156aced`](https://github.com/chen-star/wave-alpha/commit/156acedcc125a0db56c1720f8c5fec544b62653a))

* fix(web): rewrite deepdive.js for Lightweight Charts v5 API

Replace removed addCandlestickSeries/series.setMarkers calls with the v5
equivalents (chart.addSeries + LightweightCharts.createSeriesMarkers).
Add Python-side regression tests that guard against bundle-drift and
verify all vendored static assets are served by FastAPI.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`ed6d840`](https://github.com/chen-star/wave-alpha/commit/ed6d840c1cd102b370ed297de429eb3db8db5017))

* fix(llm): support cached mode without API key via CacheOnlyClient

Add LLMClient Protocol and CacheOnlyClient dataclass so --llm-mode cached
works for paper-reproducible runs even when no API key is present. Update
LLMRunner.client annotation to LLMClient (removes type: ignore). Rework
_build_runner to use explicit branching instead of catching MissingAPIKeyError,
so CACHED mode never silently degrades to engine-only.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`42b5288`](https://github.com/chen-star/wave-alpha/commit/42b52883c89092bdcd32668dce75ff898f0c6e55))

* fix(derive,features): clarify direction logic and guard short volume window

I3: simplify _implied_direction&#39;s branching. The 4-segment case (&#34;expect
W5 continuation&#34;) is the only true special case; everything else collapses
to &#34;expect reversal of the most recent leg&#34;. Eliminates the redundant
`is False` comparison. Adds a regression test for the 5-segment
impulse-complete reversal case.

I6: extract _volume_decline helper. Guards against (a) bar history shorter
than recent_window + baseline_window (previously silently overlapped the
slices), and (b) zero baseline volume. Returns 0.0 in both edge cases
instead of producing misleading values. Adds a regression test.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`2c16abc`](https://github.com/chen-star/wave-alpha/commit/2c16abc08d9b87764525e4421c0ec7eb2d35142e))

* fix(enumerator): per-template dedup and recency weighting

I1: cap top-K-per-template to 1 by tracking best_per_template instead of
collecting every valid window. Without this, one template family (e.g.
contracting_triangle) could occupy multiple top-K slots and crowd out
genuine alternates.

I7: weight scoring with a recency factor that decays linearly from 1.0
(window ends at the latest pivot) to 0.2 (&gt;= 16-pivot gap). Score weights
are now base 0.50 / confluence 0.20 / recency 0.30, summing to 1.0. A
twenty-year-old impulse no longer outranks a current setup.

New tests: dedup-to-one-per-template, prefer-recent-over-stale.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`b15d9ac`](https://github.com/chen-star/wave-alpha/commit/b15d9ac71a56ad57b9776379465ca5508d42d904))

* fix(engine): address final-review critical issues C1+C2+C3 plus zigzag bootstrap

C1: YahooProvider only set period=&#34;max&#34; when both start and end were unset.
Pipeline calls fetch(end=as_of) only, so yfinance fell back to its 1-month
default and returned ~20 daily bars — too few for ATR(14) warmup. Switch to
period whenever start is unset and filter to &lt;= end client-side downstream.

C2: tests/test_pipeline.py shifted timestamps only by year, collapsing 300
bars onto 5 unique dates; the assertion `isinstance(.., list)` then passed
on []. Replace with a hand-crafted ~200-bar Elliott-flavored impulse and
add a regression assertion that the pipeline produces &gt;=1 count.

C3: dsl.evaluate used eval with an empty __builtins__, but length.__globals__
or ().__class__ trivially escaped the sandbox. Replace with an AST-walking
evaluator that allowlists Compare/BinOp/Call/Name/Constant/BoolOp/UnaryOp;
blocks attribute access, subscripts, lambdas, comprehensions. Add 5 tests
that exercise the historical escape vectors.

Bonus zigzag fix: zigzag_atr&#39;s bootstrap branch tracked a single bidirectional
extreme, so during a smooth segment the running extreme followed the latest
bar and never accumulated a threshold-exceeding move from the swing peak.
Real SPY data and the new pipeline fixture both exposed this. Switch to
dual extreme tracking during the unset-direction phase: lock in the first
swing peak/trough as the first pivot when price retraces by `thr` from
either running max or running min.

Live SPY analysis now produces 5 counts at each interval, three-way
coherence 0.90, three structurally valid signals. Test count: 58 -&gt; 64
(5 sandbox + 1 pipeline regression).

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`45adaf0`](https://github.com/chen-star/wave-alpha/commit/45adaf045a884c55318daac6121bb41642cbe30d))

* fix(elliott): keep DSL eval Decimal-throughout to match YAML templates

The float wrappers added during Task 13 broke YAML template expressions of
the form `Decimal(&#39;0.618&#39;) * length(WA)` because length() returned float in
the eval namespace, and Decimal * float raises TypeError.

Restore Decimal-returning helpers in the eval namespace and update the test
to use Decimal(&#39;1.618&#39;) matching the YAML convention. Adds a regression test
for the YAML-template pattern.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`8214b21`](https://github.com/chen-star/wave-alpha/commit/8214b2157048808ed95b9ae12f22f22212f29c81))

* fix(domain): use StrEnum for Interval to satisfy ruff UP042

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`6b5ae0d`](https://github.com/chen-star/wave-alpha/commit/6b5ae0d29da11b3215dc25bd9dc9cf1bb5adef98))

### Performance

* perf(web): memoize deps.analyze across HTMX fragments

The deep-dive page fires 4 HTMX fragments that each previously ran the
full analyze pipeline (3-interval bar fetch + 5-degree pivot detect +
count enumerate × 3 intervals). Now memoized via lru_cache(maxsize=8)
keyed on (ticker, as_of, llm_mode), reducing per-page work ~4x.

Tests must cache_clear() between assertions; route tests that
monkeypatch `deps.analyze` continue to work because the public name
is unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`0d618a3`](https://github.com/chen-star/wave-alpha/commit/0d618a390363e4a7abf982adb8545a0ad566db64))

### Refactor

* refactor(web): trust-popup follow-ups (nav-offset token, redundancy, narrative)

- Promote magic 88px sticky-content offset to --nav-offset CSS token in :root,
  used by .about-toc top and .about-section scroll-margin-top.
- Drop the per-pair data table from the coherence popover — pairwise scores
  are already shown in the always-visible flex-row beneath the multiplier;
  popover is now narrative-only with a pointer to those scores.
- Expand the scan-table Right-edge column-header narrative with invalidation
  semantics, training window, and a pointer to the deep-dive for model details.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`4f5101f`](https://github.com/chen-star/wave-alpha/commit/4f5101f2e127530fa32f92756f1482ae34b75728))

* refactor(web/scan): tighten query-param validation (Bias literal, ge=0) ([`e83d8d3`](https://github.com/chen-star/wave-alpha/commit/e83d8d3ec2fcac0351cac5952d5794a3c3522669))

* refactor(history): require non-empty top_3 to prevent template IndexError ([`8a8bdf5`](https://github.com/chen-star/wave-alpha/commit/8a8bdf55791d8b1e732fa31acedc4c4f95030199))

* refactor(web): collapse history badge template + swallow read errors

Deduplicate the badge text branches in _history.html into a single
render with an inner conditional for the parenthetical. Wrap the
history_fragment read path in a try/except so DB corruption or missing
artifacts yield an empty-state 200 rather than a 500 spinner.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`1c31bfe`](https://github.com/chen-star/wave-alpha/commit/1c31bfe3eaa86dc191fb4c1747b0929c3753db3a))

* refactor(history): drop unused window param + fix StoredRow test fixture

Remove the silently-ignored `window` parameter from `_compute_badge` so
callers cannot pass a value that has no effect. Update all test call sites
accordingly. Replace the `date`-typed `created_at` in the `_row` factory
with `datetime.combine(...)` to match `StoredRow.created_at: datetime` and
remove the suppressed type-ignore comment.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`f805766`](https://github.com/chen-star/wave-alpha/commit/f805766e72bbfbc62b6f158df8ebe99667a6ca1b))

* refactor(history): import _PATTERN_LABELS + UTC created_at

Replace the local _PATTERN_LABELS copy in service.py with an import from
elliott/models.py to prevent silent drift when new patterns are added.
Switch created_at stamping from naive datetime.now() to datetime.now(tz=UTC)
and add a recency assertion to test_record_snapshot_writes_a_row.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`5e53ea1`](https://github.com/chen-star/wave-alpha/commit/5e53ea12b6f766038d1cdea35da4d0eb93d4649f))

* refactor(history): close SQLite connections + cache schema-init

Replace _connect helper (which leaked fds via sqlite3 transaction CM) with
_ensure_schema + explicit conn.close() pattern; schema DDL now runs once per
db_path per process. Adds cold-start test to pin the no-create contract.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`58b58bc`](https://github.com/chen-star/wave-alpha/commit/58b58bc19cf83d99db389d1231ed83669a62f449))

* refactor(history): tighten frozen tests + max_length on top_3

Replace bare try/except in test_snapshot_is_frozen with pytest.raises(ValidationError).
Add test_stability_badge_is_frozen to verify the frozen=True dataclass invariant.
Add Field(max_length=3) on Snapshot.top_3 so accidental over-population raises
ValidationError rather than silently corrupting UI assumptions.
Add test_snapshot_rejects_more_than_three_top_counts to cover the new constraint.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`9b9dd39`](https://github.com/chen-star/wave-alpha/commit/9b9dd393aa2556f89d5a220d3fef229c167000f2))

* refactor(history): justify repr() choice + inverse-identity test

Add a one-line comment in identity.py explaining why repr() is load-bearing
(all key components are stdlib built-ins with spec-defined repr). Add inverse
test confirming score and violation fields do not affect the hash, closing the
stability-badge design contract gap flagged in code review.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`ff505c4`](https://github.com/chen-star/wave-alpha/commit/ff505c4243ce9f8abe01779944ee2ac740110b76))

* refactor(right-edge): public names — HeuristicAdapter, quarter_start

Renamed _HeuristicAdapter → HeuristicAdapter in loader.py (class is imported
by pivot_layer.py and cli/app.py; leading underscore is misleading for a
cross-module symbol). Updated all callers and tests.

Renamed _quarter_start → quarter_start in training.py (imported by cli/app.py).
The module-private _next_quarter_start keeps its underscore.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`d5132b6`](https://github.com/chen-star/wave-alpha/commit/d5132b67c42e92701d33439619483ed661682f1f))

* refactor(right-edge): drop stub features (rsi_divergence, fib_zone)

Remove rsi_divergence_strength and fib_zone_match from RightEdgeFeatures,
extract_features(), and HeuristicConfirmationModel. Renormalize heuristic
weights across the five real features (sum = 1.0); bump version to
heuristic_v2. Update all tests and the placeholder initializer in
pivot_layer.py to match the new 5-field schema.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`052bdb4`](https://github.com/chen-star/wave-alpha/commit/052bdb475cc9a96a3d47b6edc8e9599fecca09dd))

* refactor(web): _data_dir() indirection for test isolation

Replace module-level _DATA_DIR constant in deps.py with a _data_dir()
function so tests can monkeypatch the cache location without touching
the real ~/.wave_alpha/. Also lift lazy imports in deepdive._load_bars
to module level and have it call deps._data_dir() so both seams share
the same indirection point.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`42938bc`](https://github.com/chen-star/wave-alpha/commit/42938bc25ff3c7a8706d8c9c942a6a525b078b35))

### Style

* style: apply ruff format to two web tests

Catches up on formatting drift introduced during the trust-popup work.
Pure formatting; no behavior change.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`74f33bc`](https://github.com/chen-star/wave-alpha/commit/74f33bcdb2ae3e78d58d29f56a4571fc31c81e9c))

* style: ruff format pass across source and tests

Mechanical formatting; no logic changes. Aligns the working tree with
ruff&#39;s current default style so the upcoming CI workflow can enforce
`ruff format --check` without immediate red.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`31bc424`](https://github.com/chen-star/wave-alpha/commit/31bc4240cafa114fac01d939bdfa0007a20e51d2))

* style: ruff format collapse short signatures

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`46ea64a`](https://github.com/chen-star/wave-alpha/commit/46ea64a585db41ffb6569a7e89973a125e3e461b))

* style(backtest): apply ruff format to pre-existing backtest modules

Whitespace and line-length fixes applied by ruff format — no logic
changes.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`86542c0`](https://github.com/chen-star/wave-alpha/commit/86542c0be6dd01631c1dfb3a8ff9c78d30c5ab1f))

* style(pivots): ruff format + pairwise() fixes

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`7d268b6`](https://github.com/chen-star/wave-alpha/commit/7d268b69bacc8680c60d25c9141ed87ff3938fa4))

* style(data): apply ruff format to point_in_time

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`7db0082`](https://github.com/chen-star/wave-alpha/commit/7db0082e644d7cd4b2c449c355b0c078f96169b0))

### Test

* test(web/home): tighten Home tests (hermetic route, accurate test name) ([`758baa7`](https://github.com/chen-star/wave-alpha/commit/758baa788eee24f769d3c7dcaa01b4209413981a))

* test(llm): add shape assertion + restore anthropic forbidden token

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`b3f6b96`](https://github.com/chen-star/wave-alpha/commit/b3f6b967d780eae32c1cd789851cbadf58d7b1c0))

* test(llm): pin privacy contract — payload contains no OHLCV or credentials

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`ca75c88`](https://github.com/chen-star/wave-alpha/commit/ca75c88f4022af15b25e3978be9dbd3ec69341b4))

* test(packaging): cross-platform paths, timeouts, better assertions

Address review feedback on Task 4: handle Windows venv layout,
bound all subprocess calls with timeouts, surface anthropic stderr
on assertion failure, and correct the module docstring.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`60e82e1`](https://github.com/chen-star/wave-alpha/commit/60e82e120db4a496f80811cbc8fbb0a5fd26794d))

* test(packaging): assert engine works without [llm] extra

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`796505f`](https://github.com/chen-star/wave-alpha/commit/796505f1d2c5753c5d55688b085959108bf541a7))

* test(backtest): tighten T5/T7 grid shape assertions

Per code-review nits flagged on commits 6a91f4a (T5 render) and
8b25adb (T7 CLI):

T5 (render_grid_markdown):
- Assert &#34;**Phase 1 trigger:**&#34; appears for phase 1 reports.
- Assert winner expectancy renders with explicit sign (+0.200 not 0.200)
  — guards against silent regressions to plain {:.3f}.
- New test test_render_grid_markdown_phase2_omits_trigger_line pins
  the negative case: phase 2 reports get the Verdict block but NOT
  the GO/NO-GO trigger line.

T7 (backtest grid CLI artifacts):
- summary.csv: parse with DictReader, assert axis-name column present
  in fieldnames + 2 rows + cell_ids match deterministic format.
- result.json: parse, assert top-level envelope keys (cell_id,
  axis_values, fixed, rules, headline) plus all 5 breakdown keys.
- trades.csv: parse, assert 300 rows + required field set including
  tf_count/degree/expected_bars_to_target (which the sanity-run
  inspection script depends on).
- Cell directory names match the deterministic minconf-&lt;level&gt; form.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`756b88a`](https://github.com/chen-star/wave-alpha/commit/756b88a32cbf61ec566f2da84dee99f5b158a45e))

* test(right-edge): point auto-mode loader tests at missing-paths for both artifact types

Tests assumed the auto-mode default paths didn&#39;t exist; now that real
artifacts ship under right_edge/models/, must explicitly point both
logistic_path and calibrated_heuristic_path at missing files in tests
that exercise fallback behavior.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`d7ade27`](https://github.com/chen-star/wave-alpha/commit/d7ade278579ed0fa1dacc8d09d37f886b40feb52))

* test(backtest): tighten pivot_layer regression to detect placeholder return

Replaced `assert max(probas) - min(probas) &gt;= 0.0` (always passes) with an
assertion that ≥2 distinct rounded probas exist across the sawtooth evaluations.

A placeholder bug that returned the same constant proba for every tentative
pivot (the old _features_for behaviour) would now fail this test. Real
FeatureExtractor output varies across pivots with different retracement_progress
and bars_since_candidate values, producing 4 distinct probas on the fixture.

Co-Authored-By: Claude Sonnet 4.6 &lt;noreply@anthropic.com&gt; ([`1cde7c9`](https://github.com/chen-star/wave-alpha/commit/1cde7c9312fe482bf47bcb8d8a0745ea1c6b866f))

* test(backtest): synthetic lookahead leak guard

End-to-end test that an unsorted-bars provider trips PointInTimeView&#39;s
assertion mid-backtest. If this test stops firing, the harness has a
real lookahead hole.

Found and fixed: _evaluate_one had a bare `except Exception: return None`
that silently swallowed UnsortedBarsError/LookaheadError. Added explicit
re-raise for those guard types before the generic except clause.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`7515c98`](https://github.com/chen-star/wave-alpha/commit/7515c989d3fcaaab0ddb3cef43652b1adde083e6))

* test(pipeline): live LLM-mode end-to-end with stub client

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`4d23932`](https://github.com/chen-star/wave-alpha/commit/4d23932ed542a5a16312ba232a191824b9984590))

* test: add sanity test for package version

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`9cab1dc`](https://github.com/chen-star/wave-alpha/commit/9cab1dc2336c5597f726c79c4b906b60de5d7cc5))

### Unknown

* Revert &#34;chore(release): 0.1.0&#34;

This reverts commit dd070bb8b04baab75b8849625f3b4e6619a06c1d. ([`bea0fd5`](https://github.com/chen-star/wave-alpha/commit/bea0fd5dba6c96c2d9e84b767418ac403446044d))

* Merge branch &#39;feat/wave-count-history&#39;

Adds wave-count history snapshots: persist per-symbol daily snapshots
of the engine&#39;s top-3 ranked counts, signal, and coherence to
~/.wave_alpha/history.sqlite, surfaced as a stability-badged timeline
strip on the /deepdive/&lt;ticker&gt; page. Auto-on-visit (deepdive page +
analyze CLI), dedup by (ticker, as_of). Pipeline untouched.

Spec: docs/superpowers/specs/2026-05-09-wave-count-history-design.md
Plan: docs/superpowers/plans/2026-05-09-wave-count-history.md

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`af685e4`](https://github.com/chen-star/wave-alpha/commit/af685e4d9265c5a049c8a9f5e6a7994f38160808))

* plan(history): wave-count history implementation plan

11 TDD tasks covering identity hash, models, SQLite store, service
(record/read), web route + template, deepdive integration with write
hook in counts_fragment, CLI analyze write hook, CSS, and final
regression sweep. Plan corrects two spec assumptions (TradeSignal
field names, CoherenceResult shape) against the real types.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`572874d`](https://github.com/chen-star/wave-alpha/commit/572874debe6c12123ac2abfde526d587692cfe86))

* spec(history): wave-count history — auto-snapshot + timeline strip

Design for persisting per-symbol daily snapshots of the engine&#39;s top-3
ranked counts, signal, and coherence to ~/.wave_alpha/history.sqlite,
surfaced as a timeline strip with a stability badge on /deepdive/&lt;ticker&gt;.
Closes the trust gap where every visit shows fresh counts with no audit
trail. Auto-on-visit (deepdive page + analyze CLI), dedup by
(ticker, as_of). Pipeline untouched.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`bef6633`](https://github.com/chen-star/wave-alpha/commit/bef66338a0b71dbff4d4ce2dcbf188ecd49aac6d))

* spike(llm): try --json-schema on Max-plan CLI path — negative result

Extended ClaudeCodeClient with optional json_schema field that maps to
`claude -p --json-schema &lt;schema&gt;`. Re-ran spike with
ScanResponse.model_json_schema() passed in.

Outcome: model emitted prose narrative, no JSON at all (parse error
went from &#39;extra data after valid JSON&#39; to &#39;expecting value at char 0&#39;).
Latency 51.7s (vs 46.6s without). The --json-schema flag does NOT
constrain output on the Max-plan `claude -p` path the way structured
output does in the API.

Conclusion reinforces the spec §9 soft-fail verdict — subprocess
transport is the wrong shape; pivot to claude-agent-sdk or accept
that this approach is dead.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`a7e169f`](https://github.com/chen-star/wave-alpha/commit/a7e169f7f7445f079ecf115867fc27766469ec7b))

* Merge feat/ebt-refit-1-and-3 — degree-1 EBT refit + v0.5.11 ([`df4a4e4`](https://github.com/chen-star/wave-alpha/commit/df4a4e4556243c688c5f3657ee65a741dc3a4fa9))

* Merge feat/multi-degree-enumeration — multi-degree enumeration + v0.5.10 ([`031c785`](https://github.com/chen-star/wave-alpha/commit/031c7856f2093236d2face02f85e26bd88046c30))

* Merge feat/target-r-sweep — target_R sweep verdict (MARGINAL) ([`2e2a7f4`](https://github.com/chen-star/wave-alpha/commit/2e2a7f4f613208e76d2eac36ddfb84880ac4170e))

* Merge feat/target-source-wiring — target_source dispatch + v0.5.9 ([`420a39a`](https://github.com/chen-star/wave-alpha/commit/420a39a26b8cbc364de6d504632f328906fbe3d7))

* Merge feat/atr-multiple-default — atr_multiple default + v0.5.8

Implements the §6.4 verdict from the ablation grid spec.
TradeRules.stop_source default flips from count_invalidation to
atr_multiple, reflecting the +0.116R Phase 2 sensitivity gap.

7 test_derive.py sites migrated to pass count_invalidation
explicitly where they assert on count-derived stop behavior.
Pipeline (analyze, scan, backtest) was already passing bars per
the prior atr_multiple wiring spec; no breakage in src/. ([`a44f646`](https://github.com/chen-star/wave-alpha/commit/a44f646d0ccda312e8ae962f1a536080ca1c10cc))

* Merge feat/ebt-empirical-fit — EBT empirical refit + v0.5.7

Replaces _DEFAULT_BARS_PER_DEGREE[2]=25 with the empirical median
(17 bars) and decouples max_holding_bars from the × 1.5 multiplier
via a separate _EBT_P75_BY_DEGREE lookup (degree 2 = 29).

Validated against the ablation grid Phase 2 winner cell. Verdict:
MARGINAL. Post-refit expectancy +0.218R vs pre-refit +0.272R,
|delta|=0.0535R (just above 0.05R PASS threshold). Refit ships
because the empirical median is the truthful default for the
user-facing signal field; absolute edge tradeoff is documented in
the spec §6.

Closes 2026-05-05 sanity run §9 S2 and 2026-05-06 ablation grid §9 G4. ([`6dd442b`](https://github.com/chen-star/wave-alpha/commit/6dd442b0ab74deb0efdb3be7165e569374e93cca))

* Merge feat/ablation-grid-spec — ablation grid sweep + v0.5.6

Tiered grid harness (Phase 1 16 cells → Phase 2 32 cells, conditional)
with cross-cell aggregation, GO/NO-GO trigger, and a backtest grid CLI
subcommand. Phase 1 verdict: GO with direction_modes=[long] dominant.
Phase 2 verdict: stop_source=atr_multiple dominant (+0.116R), best cell
expectancy +0.272R / PF 1.44 / hit rate 0.40. Three findings recorded
in §6.3, including target_source wiring gap (G7) discovered during
Phase 2. ([`0514a85`](https://github.com/chen-star/wave-alpha/commit/0514a859b75d9b0c74ce9860f5712add8c111c64))

* Merge feat/atr-multiple-wiring — atr_multiple stop wired + v0.5.5

Closes deferred decision S5 from the trade-sim sanity-run spec. The
schema-stated atr_multiple stop_source is now wired through
derive_signal and pipeline.run_analysis instead of silently falling
through to last_pivot. Multiple=2, period=14 hard-coded; configurable
knobs deferred to the ablation grid spec. Six TDD tests pin the
invariants. Behavior change for any prior caller of stop_source=
&#39;atr_multiple&#39; — this is the documented goal, not a regression. ([`cb6fb0b`](https://github.com/chen-star/wave-alpha/commit/cb6fb0bb366c6a3b235405d60b850dae2ed939b0))

* Merge feat/trade-sim-sanity-run — derive bug fixes + sanity run verdict GO

Two engine fixes affecting any short-signal path: _target_price now
honors trade direction, _stop_price count_invalidation for 5-seg
impulses uses W5 extreme. Plus pre-run instrumentation (degree + EBT
on signal/record), tooling (prewarm + B-bar inspector), and the
sanity-run spec with results and GO verdict for the ablation grid. ([`943ea9d`](https://github.com/chen-star/wave-alpha/commit/943ea9da96b417b9af639654fb3ddf9fe20ffc62))

* docs+config(sanity-run): switch centroid to count_invalidation

Phase E iter 1 discovered atr_multiple silently falls through to
last_pivot in derive_signal — the schema accepts the option but
_stop_price_atr_multiple is never called. Switch centroid to the
implemented path (count_invalidation, now direction-aware) and log
S5/S6 deferred decisions for the wiring + inspection-script gaps.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`1b99921`](https://github.com/chen-star/wave-alpha/commit/1b99921e8e98759ebaff4d36ef5c7232efdb9f1c))

* Merge feat/4h-cache-strategy — 4h cache fix + trade-layer surfacing + CLI warning

Phase A: cache mechanics fix — eliminates redundant yfinance calls when end &lt; today-730d
Phase B: trade-layer surfacing — tf_count on TradeRecord, coverage_4h/by_tf_count on result, report + sidecars
Phase C: CLI warning + README docs
Phase D: v0.5.3 bump ([`4ff0e9b`](https://github.com/chen-star/wave-alpha/commit/4ff0e9b475d3c15825a840a94e0237ac74d3a916))

* Merge feat/fib-structural-features — pattern-aware Fib confluence + right-edge plumbing

Phase 1: engine_score now rewards multi-ratio Fib confluence on impulse and
zigzag counts via canonical bands + trapezoid scorer. Independent of
right-edge models.

Phase 2: RightEdgeFeatures gains 14 default-zero slots (7 raw fib ratios + 7
presence indicators); LogisticConfirmationModel._vector reads dynamically
from feature_order so legacy 5-feature artifacts continue to predict
identically. Top wave count threads through assess_from_bars and
assemble_training_rows so train and serve see the same fib slots.

Phase 3: trainer supports --with-fib-features (CLI flag). Training run
2026-05-04 produced v2 (19-feat) logistic with verdict PROMOTE_CALIBRATION
vs heuristic, but mildly worse than v1 on aggregate Brier (+0.0070) with
weak fib coefficients across the board. Decision: keep v1 bundled,
v2 committed alongside for reproducibility but not loaded. Plumbing stays
load-bearing for future experiments. Verdict + follow-up hypotheses in
docs/superpowers/specs/2026-05-04-fib-features-training-run.md.

403 tests green, ruff clean. ([`1748c50`](https://github.com/chen-star/wave-alpha/commit/1748c50d589be7db82d48cdd4eb8fb38f0af89a5))

* Merge feat/right-edge-followups — gate extension + calibrated heuristic + shipped artifacts

Three follow-ups from the 2026-05-03 HOLD verdict:
1. Spec/docstring fix: 5 degrees (0..4) per _DEFAULT_ATR_MULTIPLES, not 3.
2. PROMOTE_CALIBRATION verdict: gate ships when calibration is strictly
   better by ≥20% per degree, even if Brier overlaps.
3. CalibratedHeuristicModel + train calibrated-heuristic CLI.

Re-running training under the new gate produced two shipped artifacts:
- right_edge_logistic_v1.json (PROMOTE_CALIBRATION) — calibration 2-3× better
- right_edge_calibrated_heuristic_v1.json (PROMOTE) — Brier strictly better than
  raw heuristic AND logistic; calibration mixed vs logistic across degrees.

Loader auto-preference: logistic &gt; calibrated &gt; raw heuristic. Whether to
flip in favor of the simpler model is logged as an open design question in
the spec.

7 commits, 358 tests passing, ruff clean.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`91b7f12`](https://github.com/chen-star/wave-alpha/commit/91b7f12c87b6ac5271ce033086b3b8ee307f6d4e))

* Merge feat/right-edge-logistic — right-edge logistic model + promotion gate

Closes spec §5.4 promotion gate. Trains a calibrated logistic regression on
backtest-derived labels via expanding-window walk-forward; per-degree Platt
calibration; paired-bootstrap CI Brier comparison + per-degree calibration
check produces a PROMOTE/HOLD/REJECT verdict that&#39;s embedded in the model
artifact JSON. Live loader refuses non-PROMOTE artifacts and falls back to the
heuristic. Bundles a fix for the placeholder-features bug in pivot_layer.

19 commits, 333 tests passing, ruff clean. Phase 7 (run training on real data
to produce the artifact) deferred to operator.

Co-Authored-By: Claude Opus 4.7 (1M context) &lt;noreply@anthropic.com&gt; ([`d2530f3`](https://github.com/chen-star/wave-alpha/commit/d2530f3ae32551a4ee69f7a11b571b299d6c6ece))

* Merge branch &#39;feat/v0.5.1-polish&#39;

v0.5.1 polish release covering four follow-ups from accumulated v0.4
+ v0.5 reviews:

- fix(data): CachedProvider auto-refetches when its cached tail is
  more than 1 calendar day older than the requested end date — fixes
  the silent-stale-data bug acknowledged in the v0.5 README workaround.
- perf(web): deps.analyze memoized via lru_cache(maxsize=8) keyed on
  (ticker, as_of, llm_mode), reducing per-deep-dive-page work ~4x
  (the page fires 4 HTMX fragments that each previously ran the full
  pipeline).
- feat(web): coherence tri-badge W ✓ D ✓ 4h ⚠ at the top of the
  coherence fragment per spec §11 #3 deferred item.
- chore: SYMBOL_RE consolidated into watchlist/symbol.py (was
  duplicated in store + parsers); scan errors preserve exception
  class name; sort_by(reverse=...) honored for ticker key.

Test count: 267 passing (was 250). All ruff clean. ([`66bd935`](https://github.com/chen-star/wave-alpha/commit/66bd9359fc1d357116d211d1017d9da41595219d))

* Merge branch &#39;feat/v0.5-watchlist-scanner&#39;

Ships v0.5 watchlist + scan dashboard per spec §12 v0.3 row.

Watchlist (manual + bulk import):
- WatchlistEntry (frozen Pydantic) at ~/.wave_alpha/watchlist.yaml
- CSV and TradingView TXT parsers (handles ###Group### markers and
  exchange prefixes)
- CLI: wave-alpha watchlist add|rm|ls|import
- Web: /watchlist page with form-driven CRUD

Scan orchestrator:
- ScanRow + ScanReport models; per-ticker error capture
- run_scan iterates symbols, calls pipeline.run_analysis, --llm-mode
  disabled by default (no API key required)
- JSON-archived reports at ~/.wave_alpha/scans/&lt;id&gt;.json
- CLI: wave-alpha scan defaults to watchlist; --symbols A,B,C override
- Web: /scan dashboard with sortable HTMX table (form-based so sort
  and signals-only filter compose, not clobber)

Review-driven fixes applied before merge:
- predicted_direction promoted to public (was leaking underscore +
  type:ignore through three call sites)
- HTMX state-loss bug fixed (single form vs four buttons)
- README cache claim corrected to match CachedProvider&#39;s actual
  permissive semantics; staleness invalidation noted as v0.5.x

No-LLM-by-default invariant verified by both grep and a sentinel test
that monkeypatches AnthropicClient.__init__. ([`8f3097b`](https://github.com/chen-star/wave-alpha/commit/8f3097be85e0fca0b8f86f745f8a4043ddd77dfe))

* Merge branch &#39;feat/v0.4-backtest-harness&#39;

Ships v0.4 backtest harness per docs/superpowers/specs/2026-05-03-wave-alpha-design.md
§8: walk-forward iterator, 50-ticker curated universe, hard lookahead
guards (synthetic-leak test fires on planted violations), pivot layer
(per-degree confirmation, Brier, calibration), count layer (top-1/top-3
forward direction, by coherence bucket, by year), --llm-mode disabled
mandatory baseline.

Notable: a real lookahead hole was found and fixed in count_layer&#39;s
broad except mask (AssertionError silently swallowed). The synthetic
leak guard test in tests/backtest/test_lookahead_guard.py is the
regression: planted violations now propagate as designed.

Right-edge features remain placeholder constants for v0.4; the v0.4.x
logistic model will replace the heuristic and produce meaningful
calibration numbers. ([`9e1a4fd`](https://github.com/chen-star/wave-alpha/commit/9e1a4fd04ca81b799f8d9e06b09de0709ef3f480))

* Merge branch &#39;feat/v0.3-web-ui-deepdive&#39;

v0.3 web UI deep-dive: local FastAPI + HTMX + vendored TradingView
Lightweight Charts surface for the v0.1 engine + v0.2 LLM contract.

Highlights:
- New [ui] extra: fastapi, uvicorn, jinja2, python-multipart.
- web/ package — app factory, formatters, chart_data builder,
  deps.analyze() seam, system + settings + deepdive routers, base/
  deepdive/settings templates plus 5 fragment partials.
- Vendored static assets under web/static/vendor/: lightweight-charts
  v5.0.3 (Apache 2.0), htmx 1.9.12 (BSD), alpine 3.13.10 (MIT).
- wave-alpha ui [--port] [--no-browser] [--reload] CLI command with
  port discovery (8765-8775) and webbrowser auto-launch.
- Hand-written app.css + deepdive.js (Lightweight Charts v5 API).
- Settings page reads/writes ~/.wave_alpha/config.yaml; blank api_key
  submission preserves the existing key (post-review fix).
- _data_dir() indirection isolates ~/.wave_alpha from test side effects
  (post-review fix).

Tests: 148/148 pass; ruff clean; bumps version to 0.3.0. ([`cd90fbe`](https://github.com/chen-star/wave-alpha/commit/cd90fbe78a00f9f061c86e2407834ababa9b79b3))

* Merge branch &#39;feat/v0.2-llm-contract&#39;

v0.2 LLM contract: Anthropic-API-backed reranking layer over the v0.1 engine.

Highlights:
- prefs/ — AppConfig + LLMConfig (Pydantic v2) with YAML round-trip and env-var
  precedence on api_key resolution.
- llm/ — modes, candidates serializer, scan + deep-dive prompts, strict response
  parsers, AnthropicClient with prompt-cache breakpoint, SQLite response cache,
  LLMRunner orchestrator, and blend_scores ranking per spec §6.
- pipeline.py — RankedCount + run_analysis(llm_mode, llm_runner, alpha).
- cli — analyze --llm-mode {live,cached,disabled} --alpha; config show/set.
- CACHED mode supports paper-reproducible runs without an API key via
  CacheOnlyClient (post-review fix).

121/121 tests pass; ruff clean. Bumps version to 0.2.0. ([`dcb301d`](https://github.com/chen-star/wave-alpha/commit/dcb301d685f88f5e5d08eb645cad1e7ce24e25cf))

* Merge branch &#39;chore/post-v0.1-cleanup&#39;

Post-v0.1 review cleanup: 4 commits addressing all 7 Important + 6 Minor
issues from the final code review.

- enumerator: per-template dedup (I1) and recency weighting (I7)
- derive_signal direction logic clarity + 5-segment test (I3)
- features.py volume_decline guards short slices (I6)
- PointInTimeView asserts sorted bars (I4)
- CachedProvider wires OHLCVCache into the CLI&#39;s default provider (I5)
- coherence_score documents v0.2 reconvergence on nesting/direction (I2)
- CLI as_of resolved at invocation, not import (M2)
- README aligned to v0.2/v0.3 versioning (M3)
- test_version_command compares against __version__ (M4)
- _serialize docstring documents the dispatch convention (M5)
- coherence package re-exports public API (M6)
- _DEFAULT_BARS_PER_DEGREE comment links to spec §9 (M7)

Test count: 64 → 72. Ruff clean. Live SPY analyze unchanged: 5 counts per
interval, three-way coherence 0.90, 3 trade signals. ([`fa359f4`](https://github.com/chen-star/wave-alpha/commit/fa359f48410a3b4df5e33f956c614c2fd1d4d195))

* Merge branch &#39;feat/v0.1-engine-core&#39;

v0.1 engine core: 30 commits, 64 tests passing.

Subsystems delivered:
- Project scaffolding (uv + hatch + ruff + pytest)
- Data layer: Bar, Interval, OHLCVProvider ABC, YahooProvider with 4h
  resampling, SQLite-backed OHLCVCache, PointInTimeView with hard
  lookahead assertions
- Pivot detection: Wilder ATR, dual-extreme bootstrap ATR-ZigZag,
  multi-degree orchestrator with hierarchical subset invariant
- Elliott engine: WaveSegment/WaveCount, AST-sandboxed constraint DSL,
  6 YAML pattern templates, rule validator, fib helpers, count enumerator
- Right-edge: features extractor, HeuristicConfirmationModel v1
- Coherence: pairwise + three-way blend with insufficient-history fallback
- Trades: TradeRules + TradeSignal models, signal derivation
- Pipeline + CLI: wave-alpha analyze TICKER --json ([`398b254`](https://github.com/chen-star/wave-alpha/commit/398b25413c3b281eaa03b343f6e0535f3a0d3e3d))
