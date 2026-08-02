# Frontend WCAG 2.1 AA — Design

**Date:** 2026-08-03
**Status:** Approved (design), pending implementation plan
**Author:** Principal data engineer (via brainstorming)
**Depends on:** `2026-08-02-frontend-dark-foundation-design.md` (implemented — the dark-only color system and lazy tab mounting this spec builds on)
**Spec 2 of 3** in the delivery split established by the foundation spec.

## Context

The UI audit that produced Spec 1 also found the frontend to be near-zero on
accessibility. Spec 1 deliberately excluded all of it. This spec closes it.

Baseline was **re-measured against the current app**, not carried over from the
audit — Spec 1's lazy mounting changed what is in the DOM at any moment, so the
original figures no longer describe reality.

| Measure | Count | Notes |
|---|---|---|
| Inputs in source | 464 | across 8 partials |
| `<label class="field-label">` directly followed by an input | **266** | mechanically pairable; only 3 have dynamic (`x-text`) label text |
| Inputs inside `<template x-for>` | **63** | 13.6% — the id-collision case |
| Unnamed inputs in rendered DOM | 277 of 310 | 89% |
| `<th>` without `scope` | 18 visible | more render once tables have rows |
| `<table>` with a caption or `aria-label` | 0 of 4 | |
| Click handlers on non-focusable elements | 8 | config 5, adapters 2, launch 1 |
| `aria-live` / `role="status"` / `role="alert"` | **0** | toasts are silent to screen readers |
| `:focus-visible` rules in 56 KB of CSS | 4 | |
| `<h1>` | **0** | headings start at h2, with an h2→h4 jump |

The concentration matters for sequencing: **Launch (123 inputs), Compare (89),
and Config (41) hold 81% of the unnamed inputs.**

### Scope decisions

| Decision | Choice |
|---|---|
| Depth | **All nine items**, not just what axe detects |
| Gate | axe-core across 14 tabs, **plus four targeted assertions** for what axe structurally cannot see |
| Naming mechanism | **`id`/`for` codemod**, `aria-label` only where there is no visible label to pair with |
| Conformance claim | "No automated WCAG AA violations" — **not** "WCAG AA conformant" |

On the last row: axe detects roughly a third of WCAG criteria. Screen-reader
comprehensibility and alt-text meaningfulness were explicitly excluded because
they need a human on a real screen reader. The claim this spec earns is the
narrower, defensible one.

## Architecture

### 1. Accessible names

A committed codemod at `scripts/a11y-label-ids.js` walks the 8 partials that
contain fields. For each `<label class="field-label">Text</label>` immediately
followed by an input, it slugifies the label text into a file-scoped id and
writes `id=` on the input and `for=` on the label:

```html
<!-- before -->
<label class="field-label">Max Workers</label>
<input class="field-input" x-model="launchSettings.max_workers" />

<!-- after -->
<label class="field-label" for="launch-max-workers">Max Workers</label>
<input id="launch-max-workers" class="field-input" x-model="launchSettings.max_workers" />
```

Ids are `<file-stem>-<slugified-label>`, deduplicated with a numeric suffix.
The file prefix is what keeps ids unique once all 14 partials are concatenated
into `index.html`.

**The codemod skips anything inside a `<template x-for>`** and prints those
sites for hand-editing. Those 63 inputs take Alpine bindings using the loop
index already in scope:

```html
<template x-for="(hook, idx) in webhooks" :key="hook.id">
  <div>
    <label class="field-label" :for="`webhook-url-${idx}`">URL</label>
    <input :id="`webhook-url-${idx}`" class="field-input" x-model="hook.url" />
  </div>
</template>
```

The remaining inputs — filter boxes, search fields, checkbox rows with trailing
text — have no visible label to pair with and get hand-written `aria-label`.

**Why `id`/`for` and not wrapping or `aria-label` everywhere:** adding two
attributes cannot change layout. Wrapping the input inside its label would
restructure 266 sites *and* need a CSS change, because `.field-label`
(`frontend/styles.css:295`) is `display:block` with `margin-bottom:0.3rem` —
that margin is what creates the label/input gap, and once the input moves
inside, the gap collapses. Spec 1 just stabilised the visuals at the cost of a
28-screenshot sweep; a layout-inert option is worth choosing deliberately.
`id`/`for` also gives click-label-to-focus, which is a usability win rather
than a compliance checkbox.

**The codemod is committed, not run-and-deleted.** New partials arrive in this
app regularly (the AWS tab landed three weeks before this spec), and the script
doubles as executable documentation of the id convention.

### 2. Tables

`scope="col"` on every `<th>`, and an `aria-label` on each `<table>` naming
what it lists. Applied by source rather than by DOM, since most rows only
render once data loads.

### 3. Document structure

The app currently has **no `<h1>` at all**. `frontend/index.template.html:59`
is a `<span class="page-title" x-text="currentTabLabel">` in the top nav — it
already holds the right text and is already per-tab. It becomes the `<h1>`,
keeping its class so nothing shifts visually.

Existing heading levels are corrected to sequential order (there is at least
one h2→h4 jump). `<main>`, `<nav>`, and `<aside>` landmarks already exist.

### 4. Dialogs

Six modals plus the mismatch drawer. Only the auth modal
(`index.template.html:162`) currently carries `role="dialog"`; the drawer
(`index.template.html:219`) carries nothing.

Each gets `role="dialog"`, `aria-modal="true"`, and `aria-labelledby` pointing
at its own title element.

Focus management goes in **one shared helper in `app.js`**, not per-modal:

- On open: remember `document.activeElement`, then focus the first focusable
  element inside the dialog
- While open: `Tab` and `Shift+Tab` cycle within the dialog
- `Escape` closes
- On close: focus returns to the remembered trigger

A single helper is the right boundary here — seven call sites with identical
requirements, and per-modal copies would drift.

### 5. Live regions

The toast stack (`index.template.html:354`) gets `role="status"` and
`aria-live="polite"`; error toasts get `role="alert"` so they interrupt. Today
both success and failure are announced to nobody.

### 6. Focus visibility

Spec 1's accent rule already assigns amber (`--accent`) to focus rings. This
extends `:focus-visible` coverage from the current four rules to sidebar nav
items, sub-tabs, links, sortable table headers, checkboxes, and dialog close
buttons — reusing the established token rather than introducing a new one.

### 7. Keyboard reachability

The 8 click handlers on non-focusable elements become real `<button>`s where
the element is genuinely a control. Where the markup cannot change (a clickable
table row, for instance), they get `tabindex="0"`, `role="button"`, and a
`@keydown.enter`/`@keydown.space` handler alongside the existing `@click`.

### 8. The `sr-only` shim and the test propping it up

`frontend/index.template.html:14` is a hardcoded span reading *"Validate
Configuration Run Health Check Add Job Execution Sequence"*. A screen reader
announces that string on every page load.

It exists to satisfy `tests/integration/test_api_frontend_smoke.py:74-78`,
which does raw substring assertions against the served HTML. **Three of those
four strings exist nowhere else in the app** — `"Validate Configuration"`,
`"Run Health Check"`, and `"Add Job"` appear only inside the shim itself. The
real buttons are labelled `Validate`, `Validate Definition`, and `+ New Job`.
The buttons were renamed at some point and the shim was added to keep the
assertions green rather than updating them.

So the test is asserting against text no user can see. The fix updates those
assertions to the strings that actually exist, then deletes the shim. The test
becomes *more* meaningful, not less.

No Playwright spec references any of the four strings — only this one
integration test does, so the blast radius is a single file.

## Error Handling

This spec adds semantics and focus behavior; it introduces no new data flow and
no new failure modes. Two behaviors must be preserved and are called out as
verification points:

- **Focus trap must not strand the user.** If a dialog contains no focusable
  element, the helper must fall back to focusing the dialog container itself
  rather than looping over an empty list.
- **Focus restore must tolerate a vanished trigger.** If the element that
  opened a dialog has since been unmounted (likely now that tabs are `x-if`),
  restore falls back to `document.body` instead of throwing.

## Testing

- **`@axe-core/playwright`** as a devDependency — dev-time only, so the
  README's "no Node.js required on the deployment server" guarantee is
  untouched, same standing as `@playwright/test`.
- **New spec `tests/e2e/21-accessibility.spec.ts`:** axe with tags
  `wcag2a, wcag2aa, wcag21a, wcag21aa` across all 14 tabs, failing on any
  violation.
- **Four targeted assertions** for what axe cannot detect:
  1. Focus ring — computed `outline`/`box-shadow` is non-empty on a focused
     sidebar nav item, sub-tab, and link
  2. `aria-live` — the toast stack carries a live-region role, asserted after
     triggering a real toast
  3. Focus trap — Tab from the last focusable in each dialog returns to the
     first; Escape closes; focus lands back on the trigger
  4. Keyboard reachability — each former click-handler is reachable by Tab and
     activates on Enter
- **Existing gates unchanged:** the full Playwright suite and
  `test_api_frontend_smoke.py` must stay green.

**Expect axe to surface contrast violations Spec 1 did not.** Spec 1 verified
the token *definitions* against their intended backgrounds; axe measures
*rendered* text against actual computed backgrounds, including combinations no
one anticipated. Remediating those is planned work, not a surprise.

## Non-Goals

- No screen-reader verification. Announcement comprehensibility needs a human
  on NVDA/VoiceOver and is explicitly out of scope.
- No formal conformance claim or VPAT. The earned claim is "no automated WCAG
  AA violations".
- No semantic class migration. Approach C in Spec 1 anticipated it riding along
  with this markup pass; it is dropped, because the codemod only adds
  attributes and touching class names as well would forfeit the layout-inert
  property that made this approach worth choosing.
- No visual redesign, no new components — those are Spec 3.
- No changes to Alpine state shape, method signatures, or API calls.
