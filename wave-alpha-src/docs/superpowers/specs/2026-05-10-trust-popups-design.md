# Trust-Building Popovers and `/about` Page

**Status:** approved · ready for implementation plan
**Date:** 2026-05-10
**Owner:** Alex Chen
**Related:** `2026-05-09-web-ui-redesign-design.md` (visual design system this builds on), `src/wave_alpha/web/`

---

## §1. Why

The 2026-05-09 web UI redesign shipped plain-English headlines and three short hover tooltips (`Multi-timeframe agreement`, `Right-edge confirmation`, `Engine ↔ LLM blend (α)`) using a CSS-only `.gloss` component. This closed the *definition* gap — users now know what each term means. It did not close the *trust* gap:

- **Why did THIS specific signal fire?** A user looking at C1 with score 0.82 has no path to the contributing numbers — engine vs LLM, the coherence multiplier, soft violations.
- **How does the engine work overall?** The plain-English headlines describe the surface; nothing on the site narrates the pipeline end-to-end without sending the user to a markdown spec they can't read in-app.

This spec adds two layers on top of the existing redesign:

1. A new `.popover` component — click-pinned card with a headline, narrative, data block, explicit close button, and a `Learn more →` link.
2. A new `/about` page — a single scrolling overview of the pipeline with anchor sections that the popovers deep-link into.

The existing `.gloss` tooltips stay. The new layer is additive.

## §2. Scope

**In scope:**

- A new `.popover` Jinja macro + Alpine.js–driven click-pinned card component, with explicit close button (×), click-outside-to-close, Esc-to-close, and a manual focus-trap implemented in `popover.js` (Alpine core build does not include `x-trap`).
- 12 popover trigger sites across 4 existing pages (`/deepdive`, `/scan/{id}`, `/`, `/settings`).
- A new `/about` page (`GET /about`) with sticky table of contents and 11 anchor sections.
- A muted "About" link in the top nav (between Settings and the as-of picker).
- Inline SVG diagrams (one per `/about` section).
- No backend data dependencies for any popover beyond what `RankedCount`, the report payload, and existing route handlers already expose.

**Out of scope (deferred to follow-up specs):**

- Surfacing per-component scoring breakdowns (`pattern_fit`, `fib_quality`, `recency` as separate fields). The candidate popover uses only fields already on `RankedCount`. Adding a `score_breakdown: dict[str, float]` to `WaveCount` is a future engine change tracked separately.
- α-blend Sharpe/return numbers. No formal sweep exists in `reports/`; the Settings popover stays narrative until one is run.
- Backtest stats panels per metric (e.g., right-edge calibration plots, walk-forward Brier curves).
- Limitations / honest-caveats popovers per metric (the `/about#what-this-is-not` section absorbs this content centrally).
- Onboarding tour or first-visit guided overlay.

## §3. Constraints

- **No CDN runtime dependencies.** Per `CLAUDE.md`: SVG diagrams are inline in `about.html`; the Alpine factory for popover state lives in a vendored `static/popover.js` (~40 lines, no external lib).
- **Stack stays the same.** Jinja2 + HTMX + Alpine.js + Lightweight Charts. No new framework.
- **No engine changes.** `pipeline.py`, `enumerator.py`, `coherence/`, `right_edge/`, `llm/` are untouched. The popovers consume already-exposed fields.
- **No new persistence.** No DB writes, no new files under `~/.wave_alpha/`.
- **Accessibility.** Popovers are keyboard-operable: trigger is a `<button>`, focus moves to the close button on open, Esc closes and returns focus to the trigger. Tab cycles within the popover while open.
- **Reduced motion.** Popovers open and close instantly under `prefers-reduced-motion: reduce` (no slide / fade).
- **Privacy contract preserved.** Popovers render entirely from data already in the rendered page; they do not initiate new fetches that include user data.
- **Honest content.** No fabricated numbers in any popover. If a number isn't already computed by the engine or stored in a report, the popover narrates without it.

## §4. Component design

### `.popover` macro and JavaScript

A single Jinja macro `popover(headline, anchor)` placed in `_macros.html`:

```jinja
{% macro popover(headline, anchor=none) %}
  <span class="popover" x-data="popover()">
    <button type="button"
            class="popover-trigger"
            aria-label="Show details for {{ headline }}"
            x-on:click="open = !open"
            x-bind:aria-expanded="open">?</button>
    <div class="popover-card"
         role="dialog"
         x-show="open"
         x-on:keydown.escape.window="open = false"
         x-on:click.outside="open = false"
         x-cloak>
      <div class="popover-head">
        <span class="popover-headline">{{ headline }}</span>
        <button type="button" class="popover-close"
                aria-label="Close"
                x-on:click="open = false">×</button>
      </div>
      <div class="popover-body">{{ caller() }}</div>
      {% if anchor %}
        <a class="popover-more" href="/about#{{ anchor }}">Learn more →</a>
      {% endif %}
    </div>
  </span>
{% endmacro %}
```

Used as a `{% call %}` block so each consumer supplies its own narrative + data block:

```jinja
{% call popover("How we got " ~ ("%.2f" | format(multiplier)) ~ "×", anchor="coherence") %}
  <p class="popover-narrative">Coherence multiplies the engine score by how strongly weekly, daily, and 4-hour timeframes agree on the same wave count.</p>
  <table class="popover-data">
    <tr><th>W ↔ D</th><td class="num">{{ "%.2f" | format(pair_wd) }}</td></tr>
    <tr><th>W ↔ 4h</th><td class="num">{{ "%.2f" | format(pair_w4) }}</td></tr>
    <tr><th>D ↔ 4h</th><td class="num">{{ "%.2f" | format(pair_d4) }}</td></tr>
    <tr class="total"><th>multiplier</th><td class="num">{{ "%.2f" | format(multiplier) }}×</td></tr>
  </table>
{% endcall %}
```

`static/popover.js` defines a single Alpine factory:

```js
window.popover = () => ({
  open: false,
});
```

The vendored Alpine bundle (`web/static/vendor/alpine/alpine.min.js`) is core-only — `x-trap` (from `@alpinejs/focus`) is **not** available. The macro therefore drops `x-trap` and `popover.js` implements a small manual focus-trap (~20 lines): on open, query focusable descendants, route Tab / Shift+Tab between first and last, restore focus to the trigger on close. `x-on:click.outside`, `x-cloak`, `x-show`, `x-on:keydown.escape.window` are all in the core build.

### Visual treatment (tokens from `2026-05-09-web-ui-redesign-design.md` §5)

| Property | Value |
|----------|-------|
| Card background | `--bg-elevated` |
| Card border | `--border-default` (cyan hairline) |
| Card shadow | `--shadow-elev` |
| Card width | 380 px (desktop), `min(380px, calc(100vw - 24px))` on mobile |
| Card max height | 360 px, scroll on overflow |
| Card padding | 16 px |
| Headline | mono 11 / 800 / cyan / uppercase / `letter-spacing: 0.16em` |
| Narrative | sans 13 / 400 / `--fg-secondary` |
| Data block | mono 12 / 400 / tabular numerals; row: `<th>` muted left + `<td>` primary right |
| Data block "total" row | top border `--border-strong`, value in cyan with glow |
| `Learn more →` link | sans 12 / cyan / underlined on hover |
| Close button | 24 × 24, top-right of card head, cyan on hover |
| Trigger button | 16 × 16 circle, `?` glyph mono 11, muted by default, cyan glow on hover/focus |
| Open animation | 120 ms ease-out fade + 4 px translate-y; instant under `prefers-reduced-motion` |

Only one popover open at a time: opening a new trigger closes any other open popover. Implemented by emitting a global `popover:open` custom event from each trigger and listening on `window` to set sibling `open = false`.

### Where the trigger appears (12 sites)

| Page | Surface | Location |
|------|---------|----------|
| `/deepdive/{ticker}` | Coherence card title | After "Multi-timeframe agreement" `.gloss` term |
| `/deepdive/{ticker}` | Right-edge card title | After "Right-edge confirmation" `.gloss` term |
| `/deepdive/{ticker}` | Trade plan card title | Beside "Trade plan" |
| `/deepdive/{ticker}` | Wave counts list | One trigger per candidate (C1 / C2 / C3) on the right of the score-pills row |
| `/deepdive/{ticker}` | Hero KPIs | Beside "Confidence" value and beside "R:R" value |
| `/scan/{id}` | Table column headers — Score, Coherence, R:R, Right-edge | Beside each column header text (column-level, not per-row) |
| `/` | KPI strip — Signals, Top R:R, Watchlist hits, Stable picks | Beside each KPI title (4 triggers) |
| `/settings` | α slider | Beside the existing `.gloss` term |

Total trigger count: 3 (deep-dive cards) + 3 (per-candidate, max) + 2 (hero KPIs) + 4 (scan headers) + 4 (home KPIs) + 1 (settings) = **17 trigger instances** across 4 pages.

## §5. Per-popover content

All copy below is final unless marked `[fill in]`. Variables in `{}` are templated from the page's existing context.

### `/deepdive/{ticker}`

**Coherence card — `coherence` anchor**

- Headline: `How we got {multiplier}×`
- Narrative: "Coherence multiplies the engine score by how strongly the weekly, daily, and 4-hour timeframes agree on the same wave count. High agreement = high confidence the count is real, not a chart artifact."
- Data block (from `coherence_payload` already passed to `_coherence_row.html`):
  - `W ↔ D`, `W ↔ 4h`, `D ↔ 4h` pairwise scores
  - Total row: `multiplier`

**Right-edge card — `right-edge` anchor**

- Headline: `How we got probability {p}`
- Narrative: "Right-edge confirmation estimates whether the most recent pivot is real or likely to be invalidated by a deeper move against the wave. Trained per degree on real pivots from 2015–2024."
- Data block (from `right_edge_payload`):
  - `model` — the active variant (`heuristic` / `calibrated_heuristic` / `logistic`)
  - `degree` — pivot degree
  - `features` — feature count + `(incl. fib)` if fib features active
  - `calibration` — per-degree ECE if available, else `—`

**Trade plan card — `trade-plan` anchor**

- Headline: `How we picked these levels`
- Narrative: "Entry is the close at the most recent confirming pivot. Stop hugs the prior wave's structural low. Target uses {target_source} from the active count."
- Data block (from `trade_signal`):
  - `entry source` — descriptor sentence from `signal.entry_reason` if present, else `close at {pivot_label} {pivot_date}`
  - `stop source` — descriptor sentence
  - `target source` — `{target_source}` (e.g., `fib_extension (1.618 × W1)` or `fixed_R ({target_R}R)`)
  - `R:R` — numeric

**Wave counts — `enumeration` anchor (one popover per candidate row)**

- Headline: `Why C{n} {ranked-suffix}` where suffix is `won` for C1, `was second` for C2, `was third` for C3.
- Narrative: "C{n} is the {pattern} interpretation. Final score = α × engine + (1 − α) × llm, then × coherence multiplier."
- Data block (from `RankedCount`, no engine changes):
  - `engine score` — `rc.engine_score`
  - `llm prob` — `rc.llm_prob` (or "disabled" pill if `None`)
  - `coherence ×` — coherence multiplier from the report
  - `final score` — `rc.final_score`
  - `soft violations` — list as compact pills, or `none` if empty

**Hero KPIs — `confidence` and `rr` anchors**

- Confidence: headline `What confidence means`. Narrative: "Confidence is C1's final score after the coherence multiplier. >0.7 is rare; the median across signals is around 0.45." No data block.
- R:R: headline `How R:R is computed`. Narrative: "Reward:risk = (target − entry) ÷ (entry − stop). Targets use `fib_extension` by default; switch to `fixed_R` in Settings to use a multiple of the entry-to-stop distance instead." No data block.

### `/scan/{id}` — column header popovers

Each is short — one paragraph, no data block.

- **Score** — anchor `enumeration`. "Final score for the row's top candidate after coherence × (engine + llm blend). Higher = stronger fit. Pair with coherence and right-edge before acting."
- **Coherence** — anchor `coherence`. "Tri-coherence shows weekly / daily / 4-hour agreement on the top count. ✓ = strong, ⚠ = weak, dot = missing timeframe data."
- **R:R** — anchor `trade-plan`. "Reward-to-risk for the row's trade signal. Targets default to fib_extension; rows without a valid signal show '—'."
- **Right-edge** — anchor `right-edge`. "Probability the most recent pivot is real, per the trained right-edge model."

### `/` — Home KPI popovers

- **Signals** — anchor `pipeline`. "Tickers in the latest scan with a non-null trade signal (entry, stop, target, and R:R all defined)."
- **Top R:R** — anchor `trade-plan`. "Maximum signal R:R across the latest scan."
- **Watchlist hits** — anchor `pipeline`. "Watchlist symbols that produced a signal in the latest scan (case-insensitive symbol match)."
- **Stable picks** — anchor `coherence`. "Watchlist symbols whose history streak ≥ 3 — same direction in the last 3 scans."

### `/settings` — α slider popover

- Anchor: `alpha-blend`
- Headline: `Why our default α = 0.65`
- Narrative: "α blends the deterministic engine score with the LLM rerank probability: `final = α × engine + (1 − α) × llm`. 0.65 is our internal default — tune toward 1.0 to trust the engine more, toward 0.0 to trust the LLM more. We do not yet have a formal Sharpe sweep; see `/about#alpha-blend` for the methodology and what to watch for."
- No data block (no real numbers to show yet).

## §6. `/about` page

### Route and template

- `GET /about` → renders `about.html`, served from a new `web/routes/about.py` registered in `web/app.py`.
- No path or query parameters. Pure static content.
- "About" appears in the top nav between "Settings" and the as-of picker, low-emphasis (muted text, no glow when inactive, cyan when on `/about`).

### Layout

```
[Top nav]
─────────
H1 "How wave-alpha works"
sub: "A deterministic Elliott Wave engine with optional LLM rerank. This page documents what the engine does, how each metric is computed, and what it does not claim."

Two-column body (desktop ≥ 1024 px):
  Left (1fr) — sticky TOC card with anchor links
  Right (3fr) — scrolling content with the 11 sections below

Mobile (< 1024 px): TOC collapses to a top-of-page card; content stacks single-column.
```

### Section list

Each section: ~150–250 words, one inline SVG diagram, footer line `Source: src/wave_alpha/<file>:<symbol> · Spec: §<n> of 2026-05-03-wave-alpha-design.md`.

| Anchor | Title | Source citation |
|--------|-------|-----------------|
| `pipeline` | Pipeline overview | `pipeline.py:run_analysis` · §5 of engine spec |
| `pivots` | Pivots & degrees | `elliott/zigzag.py` · §6 |
| `templates` | Wave templates | `elliott/templates_data/` · §7 |
| `enumeration` | Enumeration & scoring | `elliott/enumerator.py` · §8 |
| `coherence` | Multi-timeframe coherence | `coherence/` · §9 |
| `right-edge` | Right-edge confirmation | `right_edge/` · §10 |
| `alpha-blend` | LLM rerank & α blend | `llm/` · §11 |
| `trade-plan` | Trade plan derivation | `trades/derive.py` · §12 |
| `lookahead` | Lookahead-safety guarantee | `data/point_in_time.py:PointInTimeView` · §13 |
| `privacy` | Privacy contract | `llm/prompts.py`, `llm/candidates.py` · §14 |
| `what-this-is-not` | What this is not | — |

(Engine source paths above are the expected locations; verified during implementation against the actual tree.)

### `what-this-is-not` content

Plain copy, no diagram:

> - This is not financial advice. The engine produces deterministic candidate counts and signals; whether to act on them is your judgment.
> - This is not a backtest of itself in real time. Coherence and right-edge are computed per request, not validated per request.
> - The engine can and does miss patterns. The template set in `templates_data/` is finite; price action that doesn't fit a template gets no candidates.
> - High coherence does not mean the count is correct. It means the engine reached the same count across timeframes — which is more likely to be correct, not certain.
> - LLM rerank is heuristic. With α = 0.65 the engine still dominates; setting α = 0.0 makes the LLM responsible for most of the ranking, which is not validated.

### Diagrams

Each non-final section has one inline SVG, ~480 px wide, system fonts (`-apple-system, …`), brand spectrum tokens. Diagrams are schematic, not data-driven — drawn in code, no live data dependency. Examples:

- `pipeline` — flow chart of the four-stage pipeline (bars → PointInTimeView → multi-degree pivots → enumeration → coherence → optional LLM → signal).
- `pivots` — schematic candle chart with three pivot degrees overlaid in cyan / magenta / orange.
- `coherence` — three-circle Venn-style diagram (W / D / 4h) with overlap zones colored by agreement strength.
- `right-edge` — schematic pivot near the right edge with a probability gauge.
- `alpha-blend` — slider visualization with engine vs LLM stacked bars at α = 0.0, 0.5, 0.65, 1.0.

The remaining sections (`templates`, `enumeration`, `trade-plan`, `lookahead`, `privacy`) get smaller schematic SVGs (~320 px) or, where a diagram would not add information, a single mono-font code listing instead.

## §7. Implementation surface (file list)

**Net new files:**

- `src/wave_alpha/web/templates/about.html`
- `src/wave_alpha/web/routes/about.py`
- `src/wave_alpha/web/static/popover.js` (~60 lines: Alpine factory + manual focus-trap + global single-open coordinator)

**Lightly modified:**

- `src/wave_alpha/web/templates/_macros.html` — add `popover()` macro.
- `src/wave_alpha/web/static/app.css` — add the `.popover*` block (visual tokens from §4).
- `src/wave_alpha/web/templates/base.html` — add "About" link in the nav, load `popover.js`.
- `src/wave_alpha/web/app.py` — register the about router.
- `src/wave_alpha/web/templates/deepdive.html` — popover triggers in hero KPIs and (via the partials) on the three sidebar cards.
- `src/wave_alpha/web/templates/_coherence_row.html` — popover with the per-pair data block.
- `src/wave_alpha/web/templates/_right_edge.html` — popover with model / degree / features / calibration.
- `src/wave_alpha/web/templates/_trade_plan.html` — popover with entry / stop / target / R:R sources.
- `src/wave_alpha/web/templates/_counts_list.html` — popover per ranked candidate.
- `src/wave_alpha/web/templates/_scan_table.html` — popovers on column headers.
- `src/wave_alpha/web/templates/home.html` — popovers on KPI strip.
- `src/wave_alpha/web/templates/settings.html` — popover beside the α slider.

**Not touched:**

- `pipeline.py`, all engine internals, every CLI command, every persistence path, every existing route except `app.py` (router registration).

## §8. Testing

- `test_about_route.py` — `GET /about` returns 200, contains all 11 anchor IDs, includes the disclaimer paragraph from `web/app.py`.
- `test_popover_macro.py` — render `_macros.html` with the popover macro and assert structural HTML (button, dialog, close, optional anchor link).
- Visual smoke test (manual): boot `uv run wave-alpha ui`, walk the four affected pages, click each popover trigger, verify (a) only one is open at a time, (b) Esc closes and returns focus to trigger, (c) clicking outside closes, (d) close × works, (e) `Learn more →` deep-links to the correct anchor and the target section is visible at the top of the viewport.
- `prefers-reduced-motion: reduce` toggled in DevTools — popovers open without animation.
- Keyboard-only walk-through — Tab to a trigger, Enter, Tab cycles inside, Esc closes.

## §9. Rollout

Single PR. No feature flag — popovers are additive UI; the existing redesign continues to work without them. No DB migration. No config change. CHANGELOG.md gets a "Trust popovers + /about page" line under 0.6.x with a link to this spec.

## §10. Deferred

- Per-component scoring breakdown (`pattern_fit`, `fib_quality`, `recency`) attached to `WaveCount` — needs a small `enumerator.py` change and a new field on the model. Tracked separately so this spec stays web-only.
- α-blend Sharpe sweep on the curated universe — once run, the Settings popover gets a real data block and `/about#alpha-blend` gets a chart.
- Right-edge calibration plot embedded in `/about#right-edge`.
- `/about` deep-links opening the relevant popover on the source page (currently they go to a doc section, not back to the metric).
- Onboarding tour / first-visit overlay.

---

**Status:** approved at the design-walkthrough stage 2026-05-10. Ready for `superpowers:writing-plans` to produce the step-by-step implementation plan.
