# Web UI Redesign — Cyber Spectrum

**Status:** approved · ready for implementation plan
**Date:** 2026-05-09
**Owner:** Alex Chen
**Related:** `src/wave_alpha/web/`, `2026-05-03-wave-alpha-design.md` (engine spec)

---

## §1. Why

The current web UI (`/scan`, `/watchlist`, `/settings`, `/deepdive/<ticker>`) reads as an unstyled prototype. User feedback verbatim:

> "Currently, the web UI looks cheap and hard to use. I have no idea how to use it. And I feel this website is not professional."

Concrete symptoms:

- `/` redirects to `/settings` — hostile first impression.
- Empty Scan page just says "No scans yet" — no path forward.
- No icons, no visual hierarchy, single hardcoded font, basic borders.
- Dense engine jargon (coherence, right-edge, soft violations, RR) shown raw with no glossary or plain-English framing.
- Watchlist is a list of strings — no signal/score/coherence shown per row.
- Settings has no help text; α blend has no explanation.
- Chart palette is lightly themed but the rest of the app does not match.

This redesign addresses all of the above with a coherent design system, a new Home dashboard, progressive-disclosure microcopy, and pattern/wave color coding so the engine output is readable at a glance.

## §2. Scope

**In scope:**

- A new `/` Home dashboard.
- Full visual redesign of every existing page (`/scan`, `/scan/{id}`, `/watchlist`, `/settings`, `/deepdive/{ticker}`).
- A `static/app.css` rewrite anchored on a documented design-token system.
- A `static/deepdive.js` recolor for the candlestick chart that locks classic green/red and applies brand-spectrum overlays for pivots/levels.
- Empty-state replacements for Scan and Watchlist with copy-paste CLI commands.
- Progressive disclosure: every expert metric (coherence, right-edge, α blend) gets a plain-English headline + a glossary tooltip.
- Pattern color coding: each Elliott pattern type and each wave label has a fixed neon hue used everywhere it appears.
- Global ⌘K ticker search and an as-of date pinned in the top nav.
- `/` route change from "redirect to /settings" to "render Home".

**Out of scope (deferred to follow-up specs):**

- Light mode (committed to dark-only; revisit if anyone requests).
- New backend routes other than `GET /` and small data needed for Home/Watchlist enrichment.
- Bulk-edit/multi-select on Watchlist.
- Mobile burger nav (desktop ≥ 1024 px is the priority; collapse gracefully but no hamburger).
- Replacing Alpine.js + HTMX (kept).
- Vendoring webfonts.
- Real-time streaming (no SSE/WebSocket added).

## §3. Constraints

- **No CDN runtime dependencies.** Per `CLAUDE.md`: "do not introduce CDN runtime dependencies." Fonts therefore use system stacks; icons are inline SVG (Heroicons-shaped), no icon font.
- **Stack stays the same.** Jinja2 templates served by FastAPI, HTMX for fragments, Alpine.js for tiny client state, Lightweight Charts for candles.
- **Route compatibility.** No existing route signature changes except `GET /` (302 → 200 HTML). All `hx-get` endpoints keep their current paths and template-fragment names.
- **Classic candle colors locked.** Per memory `feedback_chart_classic_colors.md` and user's explicit request 2026-05-09: candlestick bodies/wicks use `#26A69A` up / `#EF5350` down. Overlays (pivots, fibs, entry/stop/target lines, axis labels) MAY use the brand spectrum.
- **Privacy contract preserved.** No new data flows to Anthropic; the LLM payload contract in `llm/prompts.py` and `llm/candidates.py` is untouched.
- **Lookahead-safe.** Anything Home displays from past scans uses already-persisted reports; no new fetch path bypasses `PointInTimeView`.
- **Disclaimer stays.** The DISCLAIMER text from `web/app.py` continues to render in a footer (no longer with the leading emoji).

## §4. Information architecture

### Top nav (sticky, on every page)

```
[◆ wave-alpha]   Home · Scan · Watchlist · Settings   [⌘K Search ticker]   [📅 2026-05-09]
```

- Brand mark + holographic wordmark on the left.
- Section links centred, active item glowing cyan with a soft cyan background tint.
- Global ticker search box (placeholder + `⌘K` kbd hint). Submitting jumps to `/deepdive/<TICKER>?as_of=<global as-of>`.
- Global as-of date picker. Default = today. Stored in URL query string when present, otherwise client-side memory (Alpine `x-data`); jumping to a deepdive carries it through.
- A faint cyan→indigo→magenta→orange gradient line runs along the bottom edge of the nav.

### Sections

| Path | Page | Purpose |
|------|------|---------|
| `/` | Home (new) | Workspace start: greeting, KPI strip, big deep-dive search, today's top signals, watchlist top picks, recently analyzed |
| `/scan` | Scan list (empty + populated) | Pick from scan archives, KPI strip, filter tabs, sortable table |
| `/scan/{id}` | Scan detail | Same template as `/scan`, but with `current_id` set; existing route signature |
| `/watchlist` | Watchlist | Add form, table enriched with last-known signal/bias/coherence/RR per symbol |
| `/settings` | Settings | Sectioned form with help text and a slider for the blend α |
| `/deepdive/{ticker}` | Deep-dive | Hero ticker block, classic-candle chart with overlays, two-column body, wave counts, stability timeline |

## §5. Design tokens

System fonts only. Pure black surfaces. 12-hue neon spectrum used semantically.

### Surfaces

| Token | Hex |
|-------|-----|
| `--bg-base` | `#000000` |
| `--bg-surface` | `#07090F` |
| `--bg-elevated` | `#0E121C` |
| `--bg-input` | `#050608` |

A subtle radial-gradient + 40 px grid backdrop is painted on `body` for the cyber feel — total opacity ≤ 6 %, never interferes with text contrast.

### Text

| Token | Hex | Use |
|-------|-----|-----|
| `--fg-primary` | `#F8FAFC` | Headings, KPI values |
| `--fg-secondary` | `#C7D2DE` | Body copy |
| `--fg-muted` | `#7C8BA3` | Labels, captions |
| `--fg-subtle` | `#475569` | Decorative, dividers |

### Borders

Hairlines on slate so cards feel cut from the same cloth.

| Token | Value |
|-------|-------|
| `--border-subtle` | `rgba(34, 211, 238, 0.06)` |
| `--border-default` | `rgba(34, 211, 238, 0.14)` |
| `--border-strong` | `rgba(34, 211, 238, 0.30)` |

### Spectrum (each meaning gets one hue)

| Hue | Hex | Reserved meaning |
|-----|-----|------------------|
| cyan | `#22D3EE` | primary chrome, scan, coherence, active nav, focus |
| teal | `#2DD4BF` | flat pattern |
| lime | `#A3E635` | trend up, wave 4 label |
| green | `#4ADE80` | bull / long, target line |
| yellow | `#FACC15` | stability, wave B label |
| orange | `#FB923C` | heat / volatility / volume, wave 2 label, contracting triangle pattern |
| red | `#FB7185` | bear / short, stop line |
| rose | `#F43F5E` | invalidation, wave C label |
| magenta | `#F472B6` | wave counts, zigzag pattern, wave 3 label |
| purple | `#A855F7` | ML / right-edge, ending diagonal pattern, wave 5 label |
| indigo | `#818CF8` | fib levels, expanded flat pattern |
| blue | `#60A5FA` | neutral / informational |

Every hue ships with a `--*-soft` (12 % alpha) variant for chip backgrounds and a `--*-glow` (55 % alpha) variant for `text-shadow` / `box-shadow`.

### Locked candle colors (do not retint)

| Token | Hex |
|-------|-----|
| `--candle-up` | `#26A69A` |
| `--candle-down` | `#EF5350` |

### Pattern color map

| Pattern | Token |
|---------|-------|
| impulse | cyan |
| zigzag | magenta |
| flat | teal |
| expanded flat | indigo |
| ending diagonal | purple |
| contracting triangle | orange |

### Wave-label color map

| Label | Hue |
|-------|-----|
| W1 | cyan |
| W2 | orange |
| W3 | magenta |
| W4 | lime |
| W5 | purple |
| A | teal |
| B | yellow |
| C | rose |

These hues are used consistently in: chart pivot dots, scan-table Wave column, count cards, history-timeline rows, watchlist top-picks rows.

### Brand gradient

`linear-gradient(120deg, #22D3EE 0%, #818CF8 30%, #F472B6 60%, #FB923C 90%)` — used on: brand wordmark, brand mark fill, primary button background, page-title accent text, the gradient line under the top nav.

### Typography

```
--font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI Variable",
             "Segoe UI", system-ui, sans-serif
--font-mono: "SF Mono", "JetBrains Mono", ui-monospace,
             "Cascadia Mono", Menlo, Consolas, monospace
```

All numeric cells (`.num`) get `font-variant-numeric: tabular-nums` so columns line up.

| Role | Spec |
|------|------|
| Page title | sans 22 / 800 / `letter-spacing: -0.01em` |
| Card title | mono 11 / 800 / `letter-spacing: 0.16em` / uppercase / hue from card category |
| Field label | mono 11 / 700 / `letter-spacing: 0.10em` / uppercase / cyan |
| Body | sans 14 / 400 / `--fg-secondary` |
| KPI value | mono 24 / 600 / `--fg-primary` (with optional category glow) |
| Cell | mono 13 / 400 / tabular |
| Caption / help | sans 12 / 400 / `--fg-muted` |

### Spacing & radii

4 pt grid — `4 · 8 · 12 · 16 · 20 · 24 · 32`. Radii: `--radius-sm 6px`, `--radius 10px`. Cards default to `--radius`.

### Shadows

| Token | Value |
|-------|-------|
| `--shadow-card` | `0 0 0 1px rgba(34,211,238,0.04) inset, 0 4px 24px rgba(0,0,0,0.65)` |
| `--shadow-elev` | `0 0 0 1px rgba(34,211,238,0.08) inset, 0 18px 50px rgba(0,0,0,0.75)` |

### Icons

Inline SVG, Heroicons outline shape, `stroke="currentColor" stroke-width="1.8"`, viewBox 24×24. No icon font, no emoji as icons. The DISCLAIMER text drops the leading `⚠` and renders as plain copy in the footer.

## §6. Components

### Button

- `.btn` (default), `.btn-primary` (brand gradient + neon glow), `.btn-ghost`, `.btn-danger` (rose).
- Hover: cyan border + cyan text + cyan box-shadow glow (default); brightness +10 % (primary); rose glow (danger).
- Focus: visible 3 px cyan ring + 18 px cyan glow.

### Pill / chip

`.pill.bull · .pill.bear · .pill.warn · .pill.cyan · .pill.mag · .pill.purple · .pill.indigo · .pill.teal · .pill.lime · .pill.orange · .pill.rose · .pill.muted` — each pairs the hue with its 12 % soft background and 30 % border + 18 % shadow. Used for: bias (LONG/SHORT), candidate IDs (C1/C2/C3), final/engine/llm scores, soft violations, cached/live/disabled LLM mode, invalidation level.

### Pattern badge

`.pat.impulse · .pat.zigzag · .pat.flat · .pat.expanded-flat · .pat.end-diagonal · .pat.contracting` — pill-shaped, mono font, hue from §5 pattern map. Appears in Scan table Pattern column, Home top-signals table, count cards, watchlist rows, history timeline rows.

### Wave-label badge

`.wlabel.w1..w5 · .wlabel.wa .wlabel.wb .wlabel.wc` — small circular chip, 22 × 22 px. Hue from §5 wave map. Appears in Scan table Wave column, count cards, watchlist top picks, history rows, deep-dive ticker hero.

### Tri-coherence badge

`.tri-cell.ok · .warn · .miss` (W / D / 4h). Existing logic in `web/coherence_badge.py:build_tri_badge` is unchanged; only the styling moves to the new tokens (green ok, yellow warn, subtle muted miss).

### Score bar

`.scorebar` — `<span>` numeric value + 60 × 5 px bar with a 4-stop cyan→indigo→magenta→orange gradient fill at the score percentage.

### KPI card

`.kpi` — uniform 14 × 16 padding, 10 px radius, neon strip on the left edge (2 px wide, hue from category, with `box-shadow` glow). Variants: `.kpi.mag .bull .purple .yellow .orange .indigo .teal .lime`.

### Empty state

`.empty` — centred icon chip in cyan-soft with 22 px glow + title + sub copy + `.codeblock` snippets with syntax highlighting (comments grey, command magenta with glow, args green).

### Code block

`.codeblock` — mono 12 px on `--bg-input`, hairline border, inset cyan glow. Used on the empty Scan, in Watchlist for the bulk-import hint, in Settings for the env-var hint.

### Glossary tooltip

`.gloss` — dotted cyan underline; on hover/focus, a 240 px tooltip card appears above with plain-English definition. Implemented via CSS `::after`. Used for: "Multi-timeframe agreement", "Right-edge confirmation", "Engine ↔ LLM blend (α)".

### Tabs

`.tabs > .tab.active` — segmented control. Active tab gets cyan-soft background and neon text-shadow.

## §7. Page-by-page layout

### `/` — Home (new)

```
[Top nav]
─────────────
H1: "Good morning, <name accent>" + sub: "12 signals fresh from this morning's scan · 8 of your watched tickers triggered."
Top-right: [Add to watchlist] [Open scan →]

KPI strip (4 cards):
  Signals (cyan) · Top R:R (mag) · Watchlist hits (bull) · Stable picks (yellow)
  (The 5th "Heat" KPI shown in some mockups is dropped — no concrete metric earned the slot.)

Card "Deep-dive any ticker":
  [ AAPL  ____________________ ] [ 2026-05-09 ] [ LLM: cached ▼ ] [ Analyze → ]
  Tip: press ⌘K from anywhere.

Two-column row:
  Left card "Today's top signals":
    Table — ticker · pattern badge · bias pill · sparkline · R:R (glow)
    Sparkline color: bull-green when row is LONG, bear-red when SHORT.
  Right card "Your watchlist · top picks":
    Magenta-strip rows: ticker · pattern badge + wave label · tri-coherence · ±RR

Card "Recently analyzed":
  Cyan pills with "TICKER · YYYY-MM-DD" — clickable to /deepdive/{ticker}?as_of={date}
```

**Backend additions:**

- `GET /` returns `home.html` instead of redirecting.
- `home.html` reads:
  - the most-recent scan report (top 5 signals, total counts).
  - watchlist entries (with last-known signal looked up by symbol in the most-recent scan report).
  - "recently analyzed" — last N entries from `~/.wave_alpha/history.sqlite` (already exists per `wave_alpha/history/service.py`). N = 6.
- KPI definitions:
  - "Signals" = `report.n_with_signal` from the most-recent scan.
  - "Top R:R" = max `signal_rr` across the scan rows; sub-line names the ticker and bias.
  - "Watchlist hits" = count of watchlist symbols present in the most-recent scan with a non-null signal (case-insensitive symbol match).
  - "Stable picks" = count of unique watchlist symbols whose history-badge `streak >= 3` (looked up via `history_service.get_recent_with_badge` per symbol; cap loop at watchlist size).
- All four KPIs degrade gracefully to "—" with a muted sub-line if the underlying source is empty (no scans, no watchlist, no history).

### `/scan` and `/scan/{id}`

```
[Top nav]
─────────
H1: "Scan · 2026-05-09 [glow-cyan]"  sub: "14:30 UTC · 50 tickers · 3.2s · 0 errors"
Top-right: [Export CSV]

Pill-row of recent scan archives (cyan pill = current, muted pills = others, "… N more" overflow). Replaces the current <select>.

KPI strip (4 cards): Signals · Top R:R · Avg score · Coverage (with hue strips)

Filter bar (in a thin card): Tabs (All / Signals / Long / Short) + sort select + min-RR select. Bound via hx-get to /scan/{id}/table.

Table (in a card, padding 0): Ticker · Pattern (colored badge) · Wave (chip) · Bias (pill) · Score ↓ (with mini bar) · Coherence (tri) · R:R (glow) · Right-edge (purple). Row click → /deepdive/{ticker}?as_of={scan.as_of}.
```

**Empty state** (`/scan` with no archives):

```
Centred card:
  Cyan icon chip
  "No scans yet"
  "A scan runs the wave engine across a list of tickers and saves the report."
  Codeblock #1: uv run wave-alpha scan --symbols AAPL,MSFT,NVDA,SPY
  Codeblock #2: uv run wave-alpha scan --universe data/universe_curated.txt
  Field-help: "Once a scan finishes, refresh this page — it will appear here automatically."
```

**Filter additions:** the existing `/scan/{id}/table` HTMX endpoint accepts new query params:
- `bias=any|long|short` (new, in addition to existing `signals_only`); the tab control sends `signals_only=true&bias=any` for "Signals", `bias=long|short` for the bias tabs, and nothing for "All".
- `min_rr=<float>` (new, optional); `min_rr=0` is treated the same as missing.

`scan/ranking.py` already exposes `filter_signals` and `sort_by`; extend with two new pure functions (matching the existing `ScanRow` type) — `filter_by_bias(rows, bias: Literal["any","long","short"]) -> list[ScanRow]` matching on `row.direction.lower()`, and `filter_by_min_rr(rows, min_rr: float) -> list[ScanRow]` keeping rows where `row.signal_rr is not None and float(row.signal_rr) >= min_rr`.

### `/watchlist`

```
[Top nav]
H1 "Watchlist"  sub: "14 symbols · last refreshed from scan 2026-05-09 14:30"

Card "Add symbol":
  [ AAPL ] [ Optional note… ] [ Add ]
  Field-help with the bulk-import command.

Card (padding 0) — table:
  Symbol · Last signal (e.g. "impulse · score 0.82") · Bias pill · Tri-coherence · R:R · Notes · Remove (danger ghost)
```

The "last signal" / "bias" / "coherence" / "RR" columns are looked up from the most-recent scan report by case-insensitive symbol match (`row.ticker.upper() == entry.symbol.upper()`). Symbols not in the latest scan show "—" / muted "no signal" pill / miss tri-cells. The page header sub-line shows the source scan id, or "no scans yet" if none exist (in which case all rows show the unsignalled state, and the page renders without erroring).

### `/settings`

```
[Top nav]
H1 "Settings"  sub: "Stored in ~/.wave_alpha/config.yaml — ENV vars override file values at runtime."

Card "LLM reranker (optional)" (purple title, muted "disabled by default" pill):
  Plain-English copy: "The engine is fully deterministic on its own. The LLM only re-ranks the engine's top candidates — it never invents counts."
  Field: Anthropic API key (password) + help "Required only for live rerank. The CLI also reads $ANTHROPIC_API_KEY."
  Row: Scan model (default = claude-haiku-…) | Deep-dive model (default = claude-opus-…)
  Field: Engine ↔ LLM blend (α) — slider 0..1, value beside it (mono, glow), help: "final = α × engine + (1 − α) × llm. α = 1.0 trusts only the engine; α = 0.0 trusts only the LLM. We recommend 0.6 – 0.7."

Card "Storage paths" (yellow title):
  Read-only inputs showing the four cache/store paths.

Buttons (right): [Cancel] [Save changes] (primary)
```

The existing `POST /settings` endpoint signature is unchanged; only the form is restyled and re-organised.

### `/deepdive/{ticker}`

Layout:

```
[Top nav]
─────────
Hero card (single row):
  Left: H1 ticker (mono 30, cyan glow) + company name + bias pill + pattern badge + wave-label chip
  Right: three KPI cards — Last close · Confidence (mag) · R:R (bull glow)

Two-column body:
  Left (1.6fr) — Card "Price & pivots":
    Tabs 1W / 1D (active) / 4H
    Lightweight Charts canvas (480 px tall on desktop, 320 px mobile)
      - Candles: classic green/red, no glow
      - Pivot markers: colored circles matching wave-label hues, with white digit overlay
      - Entry / Stop / Target horizontal dashed lines (muted / red / green) with right-anchored labels
      - Optional fib level dashed line (indigo) — only shown if the active count exposes one
    Below-chart legend: candle up/down swatches, wave-label chip row, fib swatch, target/stop swatch.

  Right (1fr) — Stack of cards:
    Card "Trade plan" (bull title):
      Plain-English summary line ("Enter near 182.40, stop below the wave-2 low at 178.20…")
      3 × 2 grid: Entry · Stop · Target · R:R · Bars to target · Max hold (each glow-coloured)
    Card "Multi-timeframe agreement" (cyan title with glossary tooltip):
      Coherence ring (84 px, cyan→lime gradient stroke, percentage in centre) + tri-badge + plain-English line + per-pair pairwise scores in mono
    Card "Right-edge confirmation" (purple title with glossary tooltip):
      2 × 2 grid: Probability (purple glow) · Pivot · Degree · Model

Two-column row below:
  Card "Wave counts (top 3)" (mag title):
    C1 highlighted with magenta strip + soft mag bg, includes pattern badge, wave chip, score pills (final/engine/llm), narrative, soft-violations as warn pills, invalidation as rose pill.
    C2, C3 in plain rows.
  Card "Stability over time" (yellow title):
    Stability badge + plain summary + condensed history rows (date · pattern badge · wave chip · score · RR), most-recent row highlighted yellow.
```

All cards still load via the existing HTMX fragments (`_chart.html`, `_coherence_row.html`, `_right_edge.html`, `_trade_plan.html`, `_scenarios.html` (renamed conceptually to "wave counts top-3" but template name kept), `_history.html`). Templates are restyled, not re-routed.

The toolbar (`_toolbar.html`) is **removed** — its three controls (Ticker, As-of, LLM mode) are absorbed into the global ⌘K search + global as-of date picker + a small mode chip on the deep-dive hero. Backend route still accepts `t=` and `llm=` query params.

## §8. Interaction patterns

- **Loading.** Every HTMX target shows a `.htmx-indicator` skeleton (animated `linear-gradient` shimmer on the card area, not a small text "loading…"). Skeletons last ≥ 200 ms even if the response is faster, to avoid jank.
- **Hover.** Cards: faint cyan border tint. Table rows: cyan-soft row background. Buttons: cyan border + glow. Pattern badges: brightness +5 %.
- **Focus.** Inputs and selects: 3 px cyan ring + 18 px cyan glow. Buttons: same ring. Dotted-underline glossary terms: ring + tooltip stays open while focused.
- **Active nav.** Cyan-soft bg + cyan text + 6 px text-shadow.
- **Reduced motion.** All transitions, glow pulses, and skeleton shimmers respect `@media (prefers-reduced-motion: reduce)`. Shimmers degrade to a solid muted block; transitions become 0 ms.
- **Cursor.** All clickable elements get `cursor: pointer`. Glossary terms get `cursor: help`.
- **⌘K search.** Captured globally with Alpine (also `Ctrl+K` on non-mac). Focuses the persistent search input in the top nav (no overlay) and selects its existing text. Enter navigates to `/deepdive/<UPPER(value)>?as_of=<global as-of>`. Escape blurs the input. Ignored when the user is already typing in another input/textarea.
- **As-of date.** Stored as URL `?as_of=` when present; otherwise persisted in `localStorage["wave_alpha_as_of"]`; otherwise today. Picker writes both URL and storage on change and reloads the current view via HTMX.

## §9. Accessibility

- Body text contrast ≥ 4.5:1 against `--bg-base` (verified for all `--fg-*`).
- Glow effects are decorative only; the underlying color is sufficient on its own.
- All interactive elements reachable via Tab; visible focus styles never removed.
- All form inputs have `<label>` (no placeholder-as-label).
- Tables have `<thead>` and proper `<th>` semantics for screen readers.
- Color is not the only signal: bias pills include their text ("LONG" / "SHORT"); pattern badges include their name; tri-coherence cells include "✓ ⚠" glyphs alongside color.
- Glossary tooltip content is in `data-tip` and exposed via `aria-describedby` (Alpine helper) so it's not hover-only.

## §10. Implementation surface (file list)

**Net new files:**

- `src/wave_alpha/web/templates/home.html` (Home page)
- `src/wave_alpha/web/routes/home.py` (router for `GET /`)
- `src/wave_alpha/web/templates/_macros.html` (Jinja macros: `pat()`, `wlabel()`, `pill()`, `kpi()`, `tri()`, `icon()`)

**Substantially rewritten:**

- `src/wave_alpha/web/static/app.css` (full rewrite anchored on §5 tokens)
- `src/wave_alpha/web/static/deepdive.js` (chart theme: classic candles + spectrum overlays + pivot color from wave-label map)
- `src/wave_alpha/web/templates/base.html` (new top nav, footer, global search, as-of picker)
- All existing page templates (`scan.html`, `watchlist.html`, `settings.html`, `deepdive.html`) and partials (`_chart.html`, `_coherence_row.html`, `_counts_list.html`, `_history.html`, `_right_edge.html`, `_scan_table.html`, `_scenarios.html`, `_trade_plan.html`, `_watchlist_table.html`)

**Removed:**

- `src/wave_alpha/web/templates/_toolbar.html` (functions absorbed into top nav + hero)

**Lightly modified:**

- `src/wave_alpha/web/routes/system.py` — `GET /` no longer redirects to `/settings`; calls into `home.py`.
- `src/wave_alpha/web/app.py` — register the new home router.
- `src/wave_alpha/web/routes/scan.py` — `/scan/{id}/table` accepts `bias` and `min_rr` query params.
- `src/wave_alpha/web/routes/watchlist.py` — looks up last-known signal per symbol in the most-recent scan and passes it to `_watchlist_table.html`.
- `src/wave_alpha/scan/ranking.py` — add `filter_by_bias`, `filter_by_min_rr`.

**Not touched:**

- `pipeline.py`, all engine internals, all data providers, `llm/`, `right_edge/`, `history/`, `coherence/`, every CLI command. The redesign is web-only.

## §11. Testing

- Existing route tests must keep passing without changes (route signatures preserved, except `GET /` whose status code changes from 307 to 200 — update that one assertion).
- New tests:
  - `test_home_route.py` — Home renders with empty + populated state (no scans, no watchlist).
  - `test_scan_filter_by_bias.py` and `test_scan_filter_by_min_rr.py` — ranking module filter functions.
  - `test_watchlist_enrichment.py` — last-signal lookup falls back to "—" gracefully when no scan exists / symbol not in scan.
- Visual smoke test: boot `uv run wave-alpha ui` and walk Home → Scan (empty) → Scan (populated, fixture report) → Watchlist → Deep-dive → Settings. Verify every page renders without JS errors (browser console clean) and that all HTMX fragments load.
- `prefers-reduced-motion` toggled in DevTools — confirm shimmers and transitions stop.
- Color contrast check on the four `--fg-*` against `--bg-base` (≥ 4.5:1 for primary/secondary, ≥ 3:1 for muted).

## §12. Rollout

Single PR. No feature flag — the redesign replaces the existing UI entirely. No database migration. No config changes required.

The CHANGELOG.md (currently seeded for 0.6.0 public release) gets a "Web UI redesign" line with a link to this spec.

## §13. Deferred to v1.1

- **`/` quick-filter binding** — text-editor-style `/` to focus the search input. Reserved; v1 ships with `⌘K` (and `Ctrl+K` on non-mac) only.
- **Mobile burger nav** — desktop-first ≥ 1024 px is the v1 target. Below that, the nav collapses with horizontal scroll instead of a burger.

---

**Status:** approved at the design-walkthrough stage 2026-05-09. Ready for `superpowers:writing-plans` to produce the step-by-step implementation plan.
