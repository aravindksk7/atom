# Frontend Dark Foundation — Design

**Date:** 2026-08-02
**Status:** Implemented (see `docs/superpowers/plans/2026-08-02-frontend-dark-foundation.md`)
**Author:** Principal data engineer (via brainstorming)
**Depends on:** `frontend/features/` + `frontend/partials/` modularization (merged — `docs/superpowers/plans/2026-07-16-frontend-modularization.md`)
**Supersedes:** the deferred "add light theme via design tokens" line item in `docs/superpowers/plans/2026-07-16-etl-modernization-phase6.md` § Out of Scope

## Context

A UI audit of the running app (Playwright against `frontend/`, plus static
analysis of `styles.css`, the 14 tab partials, and the build pipeline) found
four systemic defects and a set of design-consistency problems. This spec
covers the **foundation** subset: the color system, performance, and viewport
handling. Accessibility and new interaction components are deliberately split
into two follow-on specs (see § Delivery Split).

The app is Alpine.js 3.14 + a vendored Tailwind subset + a 56 KB hand-written
`styles.css`, assembled from `index.template.html` + 14 partials by
`scripts/build-html.js`. There is no bundler and none is being added — the
README guarantees "no Node.js required on the deployment server". Both
`build-html.js` and `build:css` are dev-time only, with their outputs
(`frontend/index.html`, `frontend/vendor/tailwind.css`) committed to source
control. That constraint stands.

### Audit findings this spec addresses

| # | Finding | Evidence |
|---|---|---|
| 1 | Dark theme is half-applied | 672 hardcoded light Tailwind color classes across 14 files; every non-auth modal renders as a white card with dark `.field-input` boxes inside it |
| 2 | No responsive handling of the shell | No `@media` rule in 56 KB of CSS touches `.sidebar` / `.app-shell` / `.app-content` |
| 3 | ~1.1 MB uncompressed on every load | No `GZipMiddleware`; `chart.umd.min.js` (201 KB) is render-blocking; all 14 tabs mount eagerly (4,070 DOM nodes, 25 Alpine console errors at boot) |
| 4 | Three competing accent systems | Indigo `--primary`, amber `--accent`, and four `--accent-{setup,exec,analysis,system}` group colors all express "selected" |

### Corrected finding

An earlier draft of the audit reported `--muted: #8491a3` as failing WCAG AA at
3.2:1. That was a measurement error — the audit script read
`rgba(255,255,255,0.043)` as opaque white instead of compositing it over the
panel. Properly composited, `--muted` scores **5.50:1** on `.settings-group`,
**5.27:1** on the 0.06 overlay, and **6.00:1** on `--panel`. It passes AA and
**is not changed by this spec.** The one genuine contrast failure —
`text-slate-500` at 3.2:1 on `bg-white` — is a symptom of finding #1 and is
resolved by the token retarget.

## Decisions Taken

| Decision | Choice | Rationale |
|---|---|---|
| Theme scope | **Dark only.** Remove the light toggle. | Light was never finished; maintaining two themes doubles the verification surface for no current user |
| Viewport floor | **1024 px** | Internal ops tool; tablet/small-laptop is the realistic floor, phone is not a use case |
| Accessibility | WCAG 2.1 AA, audited, axe-core gate | → deferred to Spec 2 |
| Migration mechanism | **Retarget Tailwind's palette; migrate markup opportunistically** | ~75 lines of config vs. 672 hand edits across every partial; Spec 2 already opens every partial, so semantic migration rides along free |
| Accent system | **Split by role** | Only option that gives the existing group tokens a job rather than deleting them |

## Delivery Split

Full scope was requested. It ships as three sequenced specs so each lands as a
reviewable diff with its own regression gate.

| Spec | Contents | Layer |
|---|---|---|
| **1. Foundation** *(this document)* | dark-only color system, accent unification, native-control styling, gzip + defer + lazy tabs, responsive to 1024 px | config + CSS, ~6 file edits |
| **2. WCAG 2.1 AA** | label/`for` on 315 inputs, `th scope`, `aria-live` toasts, focus rings, `h1` + heading order, dialog semantics + focus trap, keyboard-reachable click targets, axe-core gate; deletes the `sr-only` shim | markup-wide |
| **3. Interaction polish** | confirm-dialog component replacing 10 `window.confirm()`, loading skeletons, Compare/Launch layout + progressive disclosure | new components |

Specs 2 and 3 get their own brainstorm → spec → plan cycle. Nothing in this
document blocks on them.

## Architecture

### 1. Color token layer

The 672 occurrences resolve to **56 distinct tokens across 7 hue families**,
and the `slate` ramp — 409 of them — is used with **zero collisions** between
surface-use and text-use:

```
slate-50   bg 49                slate-400  text 67
slate-100  border 33, bg 14     slate-500  text 55
slate-200  border 53            slate-600  text 54
slate-300  5 (mixed)            slate-700  text 65
                                slate-800/900  text 12    slate-950  bg 1
```

Because each step has exactly one role, the ramp can be inverted mechanically.

**Mechanism.** Introduce channel-triplet tokens and redefine the existing
tokens in terms of them, so **`styles.css` requires no edits**:

```css
:root {
  /* Surfaces and text: existing tokens are redefined in terms of triplets,
     so every current styles.css consumer keeps working unchanged. */
  --c-panel-2:   18 22 27;    --panel-2:      rgb(var(--c-panel-2));
  --c-raised:    28 33 44;    --panel-raised: rgb(var(--c-raised));
  --c-text:     244 247 251;  --text:         rgb(var(--c-text));
  --c-text-soft: 199 208 220; --text-soft:    rgb(var(--c-text-soft));
  --c-muted:    132 145 163;  --muted:        rgb(var(--c-muted));

  /* Borders: NEW tokens, added purely for the Tailwind mapping. The existing
     --line / --line-strong stay translucent rgba() and are NOT redefined —
     translucency is correct for styles.css, which applies them over several
     different surfaces. Tailwind's border-slate-* needs solid values, so
     these are the 0.10 / 0.16 white values pre-composited over --panel. */
  --c-border-1:  37  39  42;  /* #25272a */
  --c-border-2:  52  53  56;  /* #343538 */
}
```

`tailwind.config.js` maps the palette onto them using Tailwind's
`<alpha-value>` placeholder, so existing opacity utilities
(`bg-black bg-opacity-40` on modal scrims) keep working:

```js
colors: {
  white: 'rgb(var(--c-raised) / <alpha-value>)',
  slate: {
     50: 'rgb(var(--c-panel-2)  / <alpha-value>)',
    100: 'rgb(var(--c-border-1) / <alpha-value>)',
    200: 'rgb(var(--c-border-2) / <alpha-value>)',
    // …through 950
  },
}
```

**Surface & text mapping**

| Tailwind token | Use count | Maps to | Value |
|---|---|---|---|
| `white` | bg 21 | `--panel-raised` | `#1c212c` |
| `black` | bg 4 (scrims) | unchanged | `#000` |
| `slate-50` | bg 49 | `--panel-2` | `#12161b` |
| `slate-100` | border 33, bg 14 | `--c-border-1` | `#25272a` |
| `slate-200` | border 53 | `--c-border-2` | `#343538` |
| `slate-300` | border 3, text 1, bg 1 | `--c-border-2` | `#343538` |
| `slate-400`, `slate-500` | text 122 | `--muted` | `#8491a3` |
| `slate-600`, `slate-700` | text 119 | `--text-soft` | `#c7d0dc` |
| `slate-800`, `slate-900`, `slate-950` | text 12, bg 1 | `--text` | `#f4f7fb` |

Three call sites fall outside the ramp's dominant role — one `text-slate-100`,
one `text-slate-300`, and one `bg-slate-300`. These are converted by hand
rather than distorting the mapping for the other 406 slate uses.

**Tinted family mapping.** For `rose`, `emerald`, `amber`, `red`, `indigo`,
`orange`, `sky`: the `-50`/`-100` steps become the hue composited at 12 % over
`--panel`, `-200` at 30 %, and the `-400`…`-900` text steps become the hue's
light-400 variant. All resulting text-on-tint pairs were verified against
WCAG AA:

| Family | `-50` | `-200` | text | text on `-50` |
|---|---|---|---|---|
| rose | `#2a1b20` | `#542c35` | `#fb7185` | 6.13 |
| emerald | `#122722` | `#194a3b` | `#34d399` | 8.20 |
| amber | `#2a2414` | `#544417` | `#fbbf24` | 9.25 |
| red | `#29151b` | `#521d29` | `#f43f5e` | 4.71 |
| indigo | `#1b1e2e` | `#303557` | `#818cf8` | 5.54 |
| orange | `#2a1f17` | `#54361f` | `#fb923c` | 7.13 |
| sky | `#12242e` | `#1a4357` | `#38bdf8` | 7.45 |

Lowest is red at 4.71 — above the 4.5 AA threshold. `npm run build:css`
regenerates `frontend/vendor/tailwind.css`; the committed output is updated in
the same commit, matching how `build:html` output is already handled.

**Interface:** consumers keep writing ordinary Tailwind classes. The mapping is
one file. Changing a surface color means editing one token, not grepping
markup.

### 2. Accent role rule

Codified as a comment block above the tokens so it survives new components:

- **`--primary` (indigo `#6366f1`) = where you are** — sidebar active item,
  sub-tab rows, mode pills, segmented controls
- **`--accent` (amber `#ffb300`) = what you do** — primary buttons, focus
  rings, unsaved/dirty state
- **`--accent-{setup,exec,analysis,system}` = section identity** — sidebar
  group rules only, nowhere else

Call sites that currently violate the rule (Compare sub-tabs and Source A/B
mode pills use amber for "selected") flip to indigo. Amber on `--panel` is
10.69:1 and amber-on-`#1a1200` button text is 10.35:1; both pass comfortably.

### 3. Native control styling

`input[type=checkbox]`, `input[type=file]`, and `select` currently render with
OS chrome — system-blue checkboxes and a light "Choose File" button on a dark
panel, and two different select appearances on the same Compare screen. These
get token-driven styling in `styles.css` (`accent-color` for checkboxes,
`::file-selector-button` for file inputs, a single `.field-select` appearance
for all selects). This is CSS-layer work in the same files the token pass
touches.

### 4. Light theme removal

Delete `:root[data-theme="light"]`, `toggleTheme()`, `readStoredTheme()`, the
`themeMode` state, the top-nav toggle button, and its `data-testid`. One e2e
assertion references the toggle and is updated in the same task.

### 5. Performance

| Change | File | Risk |
|---|---|---|
| `GZipMiddleware(minimum_size=1024)` registered before the StaticFiles mount | `api/main.py:109` | none |
| `defer` on the Chart.js tag | `frontend/index.template.html:10` | none |
| `help-content.js` (64 KB) injected on first Help open instead of at boot | `frontend/app.js` | low |
| 14 tab roots `x-show` → `x-if` | `frontend/partials/*.html` | **medium** |

Only `GZipMiddleware` is added to `api/main.py`; the existing CORS-then-auth
middleware ordering is not touched (Starlette applies middleware LIFO and that
ordering is load-bearing — see the comment at `api/main.py:34`).

**On the `x-if` conversion.** Charts are safe: all five instances
(`differences.js:158,178`, `history.js:326,469`, `scheduler-reports.js:139`)
look the canvas up with `getElementById`, null-guard, and `destroy()` before
recreating — so unmount/remount is already handled. The real risk is e2e specs
that touch a tab's DOM before navigating to it. There is direct evidence
something already depends on cross-tab DOM presence:
`frontend/index.template.html:14` is a hardcoded `sr-only` span reading
*"Validate Configuration Run Health Check Add Job Execution Sequence"* — a
text-lookup shim sitting outside every tab. It is also a11y-hostile, since a
screen reader announces that string on page load.

Mitigation: `x-if` is its own task, converted **one tab at a time**, with that
tab's spec re-run after each conversion. The `sr-only` shim is deleted in
Spec 2 once nothing depends on it.

Expected effect: ~75 % transfer reduction from gzip; Chart.js off the critical
path; DOM at boot drops from 4,070 nodes (1,617 of which are the Help tab) to
roughly the active tab only; the 25 Alpine expression errors at boot disappear,
since they are hidden tabs evaluating bindings against uninitialized state.

### 6. Responsive to 1024 px

CSS alone cannot do this — the sidebar labels are bound with
`x-show="!sidebarCollapsed"`, so their visibility is JS state, not CSS. The
approach is a `matchMedia('(max-width: 1024px)')` listener that forces
`sidebarCollapsed = true` below the breakpoint and restores the user's
persisted preference above it. This reuses the existing collapse state and
its persisted setting rather than introducing a parallel mechanism.

Alongside it, one `@media (max-width: 1024px)` block handles the grids that
crowd: the KPI row (4-up → 2-up), Launch settings groups (3-col → 2-col), and
Compare's source grid (which today only has a 768 px breakpoint).

## Error Handling

No new failure modes are introduced — this spec changes presentation, asset
delivery, and mount timing, not data flow. Two existing behaviors must be
preserved and are called out as verification points:

- **Lazy `help-content.js`** must not leave the Help tab blank if the fetch
  fails. On failure the tab shows the existing empty-state pattern and the
  script may be retried on next open.
- **`x-if` remount** must not leave a chart canvas blank when a user returns
  to History / Differences / Scheduler Reports. The existing `renderChart()`
  null-guard makes this safe, but each converted tab's spec must exercise a
  navigate-away-and-back cycle.

## Testing

- **Regression gate:** the full 29-spec Playwright suite runs after every
  task. Non-negotiable.
- **Per-tab gate for `x-if`:** that tab's own spec, plus a
  navigate-away-and-back assertion for the three chart-bearing tabs.
- **Visual sweep:** Playwright screenshots of all 14 tabs at 1440 px and
  1024 px, before and after, reviewed by eye. The e2e suite asserts behavior,
  not color — it cannot catch a bad ramp mapping, so this step is required,
  not optional.
- **New spec:** asserts `content-encoding: gzip` on `index.html` and a
  total-transfer budget for the initial page load.

## Non-Goals

- No bundler. No change to the "no build step on the deployment server"
  guarantee.
- No light theme. No phone-width support below 1024 px.
- No accessibility work — that is Spec 2 in its entirety, including the
  `sr-only` shim deletion.
- No new UI components — confirm dialogs and skeletons are Spec 3.
- No semantic class migration sweep. Markup keeps its Tailwind class names;
  migration happens opportunistically when Spec 2 opens each partial.
- No changes to Alpine state shape, method signatures, or API calls.
