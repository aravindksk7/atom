# Web UI Tab Layout Fixes — Design

**Date:** 2026-09-16
**Status:** Approved for planning

## Problem

Three user-reported issues in the web UI:

- **A. Contracts tab** — example contracts are hidden by default, and the tab content
  renders pushed to the bottom of the viewport instead of starting at the top.
- **B. File Servers page** — the server list renders pushed to the bottom of the
  viewport instead of starting at the top.
- **C. Other tabs** — verify they are arranged sensibly for end users.

An audit of all 17 tab partials found a single structural root cause behind A's
bottom-alignment and B, plus a set of unrelated defects in other tabs.

## Root Cause: partials outside `<main>`

`frontend/index.template.html` opens `<main class="main-content">` at line 89 and
closes it at line 156. Three tab INCLUDE markers sit *after* that closing tag:

| Partial | Template line | Generated `index.html` line |
|---|---|---|
| `partials/tab-contracts.html` | 371 | 8131 |
| `partials/tab-file-servers.html` | 376 | 8460 |
| `partials/tab-help.html` | 441 | 8615 |

`</main>` is at `frontend/index.html:7916`, so all three render as siblings that
follow `<main>` rather than children of it.

The layout consequence follows from two rules in `frontend/styles.css`:

- `:120` — `.app-content { flex: 1; min-width: 0; display: flex; flex-direction: column; }`
- `:194-200` — `.main-content { flex: 1; max-width: 1280px; width: 100%; margin: 0 auto; padding: 1.5rem; }`

When Contracts, File Servers, or Help is the active tab, `<main>` renders empty but
still claims `flex: 1`, absorbing all free vertical space in the column. The active
tab, being a later sibling, is laid out below it — at the bottom of the viewport.
The same three tabs also inherit none of `max-width: 1280px`, `margin: 0 auto`, or
`padding: 1.5rem`, so they span the full viewport width with no padding.

No CSS rule vertically centers or bottom-aligns `.main-content` children. There is
no `margin-top: auto`, `place-items`, `place-content`, or `align-content` anywhere
in `styles.css`, and neither `.app-shell` nor `.app-content` sets `align-items` or
`justify-content`. The bottom-alignment is purely structural.

`scripts/build-html.js:51-59` validates the *count* of INCLUDE markers against the
number of `.html` files in `frontend/partials/`, but not their position. That is why
this defect passed CI.

## Design

### 1. Move the three partials inside `<main>` and guard the invariant

Relocate the `tab-contracts.html`, `tab-file-servers.html`, and `tab-help.html`
INCLUDE markers to sit between `<main class="main-content">` and `</main>` in
`frontend/index.template.html`, alongside the other 14 tabs.

Markup that must *stay* outside `<main>` is unaffected and stays where it is:

- the contract create/edit modal (`index.template.html:379-418`),
- the generic help dialog (`index.template.html:421-436`),
- the toast stack (`index.template.html:355-365`),
- the mismatch drawer.

These are overlays positioned relative to the viewport, not tab content.

Then extend `scripts/build-html.js` with a placement check: every INCLUDE marker
whose path matches `partials/tab-*.html` must appear at a line index greater than
the line containing `<main class="main-content">` and less than the line containing
`</main>`. Violations throw, matching the existing malformed-marker and count-mismatch
error style. Because CI already fails when the regenerated `index.html` differs from
the committed copy, this makes the invariant permanently enforced.

**Rationale for this over a CSS fix:** adding `align-self: flex-start` or collapsing
`.main-content`'s `flex: 1` would paper over the misplacement while leaving the three
tabs outside the 1280px content column, so they would still be full-bleed and
unpadded. Moving the markup fixes width, padding, centering, and vertical position
in one change, and makes all 17 tabs structurally identical.

**Help tab note:** `.help-layout` (`styles.css:1922-1928`) is a `248px + 1fr` grid and
`.help-sidebar` (`styles.css:1929-1931`) is `position: sticky; top: 84px`. Both were
authored assuming the in-`main` scroll context and 1280px cap, so moving Help inside
`<main>` restores their intended behavior rather than changing it. Visually confirm
the Help sidebar still sticks correctly below the top nav and auth bar after the move.

### 2. Contracts: examples expanded when there are no contracts

`frontend/features/contracts.js:25` initializes `showContractExamples: false`, so the
example templates are always collapsed on arrival.

Change `loadContracts()` to set `showContractExamples` from the loaded contract count
— `true` when zero contracts exist, `false` otherwise — applied **once per page load**,
guarded by a new `contractExamplesAutoApplied` flag that is set the first time the
rule runs. After that, the value is owned by the user's toggle: creating the first
contract, deleting the last one, or any later `loadContracts()` refresh must not
re-open or re-close the panel behind the user's back.

Effect: a new user landing on an empty Contracts tab sees the example library
expanded and can click "Use example" immediately. A user with existing contracts sees
their contract list and detail pane without scrolling past the examples grid, and the
"Examples" button (`tab-contracts.html:8`) still toggles the panel on demand.

Two related defects in the same partial are fixed at the same time:

- `tab-contracts.html:321` — the "Select a contract to view details" placeholder
  carries `flex-1 flex items-center justify-center`, vertically centering it while the
  sibling contract-list card at `:77` is top-aligned. The two columns visibly
  disagree. Change to top-aligned with padding so both columns start at the same y.
- `tab-contracts.html:79` — "No contracts yet." is bare text inside the list card
  rather than the shared `empty-state` block used by Config (`tab-config.html:10-16`)
  and Monitor (`tab-monitor.html:13-21`). Promote it to the standard pattern.

### 3. File Servers: empty state

Once the partial is inside `<main>`, the list top-aligns with no CSS change, because
`.main-content` is a plain block container.

The remaining defect is that `tab-file-servers.html:10-45` iterates
`x-for="fs in fileServers"` inside a bare `space-y-3` div with no empty branch. An
account with zero file servers sees the section header and nothing else — no
explanation, no call to action.

Add an `empty-state` card in the established shape (icon, title, sub) pointing at the
existing "Add File Server" button (`tab-file-servers.html:7`), consistent with
Config's and Monitor's empty states.

### 4. Other tabs: defect fixes only

Scope is defect repair, not redesign or visual normalization. Each item below is a
concrete malfunction.

**4a. Broken markup — History select-all checkbox**

`frontend/partials/tab-history.html:104`:

```
:checked="selectedResultCount() === selectedRun.results.length && selectedRun.results.length  aria-label="input"> 0" />
```

An `aria-label="input">` was injected into the middle of the `:checked` expression,
terminating the `<input>` element early and leaving a stray `0" />` text node inside
the `<th>`. The Alpine expression is syntactically invalid, so the select-all
checkbox never reflects selection state. Restore the intended expression
(`… && selectedRun.results.length > 0`) with `aria-label` as a proper separate
attribute.

**4b. History has no persistent section header**

History is the only tab whose `section-header` is nested inside conditionals: it sits
at `tab-history.html:714`, inside `x-show="historySubTab==='runs'"` (`:713`), inside
`<template x-if="!selectedRun">` (`:376`). Consequently the Trends (`:390`), Lineage
(`:443`), Audit (`:466`), Profile (`:517`), Schema (`:589`), and Coverage (`:653`)
sub-tabs render with no header at all, and the run-detail view (`:4-373`) substitutes
a bare `section-title` at `:8` with no `section-sub`.

Hoist a persistent `section-header` (title + sub) to the tab root so every History
sub-tab and the detail view have a stable header, matching the other 16 tabs. The
existing per-sub-tab filter controls at `:716-736` stay where they are, attached to
the Runs sub-tab.

**4c. Lists that render nothing when empty**

Each of these renders a visible container with no rows and no message, leaving the
user unable to distinguish "empty" from "still loading" or "broken":

- `tab-scheduler-reports.html:57-75` — Live Status Grid `<tbody>`
- `tab-scheduler-reports.html:83-90` — Execution Timeline
- `tab-adapters.html:49` — Documents card, `x-show`-hidden entirely when empty
- `tab-adapters.html:264` — Recent Lookups card, `x-show`-hidden entirely when empty
- `tab-aws.html:160` — S3 partitions table
- `tab-aws.html:246` — Glue "Loaded Jobs" select
- `tab-aws.html:446` — Airflow DAGs select
- `tab-aws.html:504` — Airflow task instances table
- `tab-compare.html:344`, `:662`, `:960`, `:1836` — result tables, `x-if`'d on
  `results.length` with no alternate branch
- `tab-sequences.html:52-68` — sequence step table

Add an empty message to each. Use the `empty-state` block for card- and table-level
emptiness; for the two `<select>` cases use a disabled placeholder option conveying
that nothing has been loaded yet. For the two `tab-adapters.html` cards, keep the
card visible with an empty message instead of hiding it, so the feature remains
discoverable.

**4d. Dead conditions in Adapters**

`tab-adapters.html:279` and `:330` carry `x-show="currentView === 'adapters'"` on
cards already inside `<template x-if="currentView === 'adapters'">` (`:1`). The
condition can never be false. Remove both.

### 5. Explicitly out of scope

- `tab-compare.html:1` — the root carries `class="compare-tab-container"`, which is
  undefined in `styles.css` and `vendor/tailwind.css`. It is load-bearing as a
  selector for `tests/e2e/38-live-docker-aws-compare.spec.ts:6`, so it stays.
- `tab-launch.html:2` — gates on `currentView === 'jobs'` rather than `'launch'`.
  Documented at `:1` and matching `frontend/app.js:196`; renaming would churn the
  tab id, help content, and e2e selectors for no user-visible gain.
- `tab-launch.html:1723-1768` — the CI/CD modal lives outside the root template by
  design, documented at `:1723-1727`, so it stays mounted across tabs.
- Contract list ordering. `api/routes/contracts.py:107` returns repository order and
  the user confirmed ordering is not the complaint.
- No visual redesign, no color/spacing normalization, no new features.

## Files Changed

| File | Change |
|---|---|
| `frontend/index.template.html` | Move 3 tab INCLUDE markers inside `<main>` |
| `scripts/build-html.js` | Add INCLUDE-marker placement validation |
| `frontend/features/contracts.js` | Auto-expand examples when no contracts exist (once per load) |
| `frontend/partials/tab-contracts.html` | Top-align detail placeholder; standard empty state |
| `frontend/partials/tab-file-servers.html` | Add empty state |
| `frontend/partials/tab-history.html` | Fix broken checkbox markup; hoist section header |
| `frontend/partials/tab-scheduler-reports.html` | Add empty states (grid, timeline) |
| `frontend/partials/tab-adapters.html` | Add empty states; remove 2 dead `x-show` conditions |
| `frontend/partials/tab-aws.html` | Add empty states (2 tables, 2 selects) |
| `frontend/partials/tab-compare.html` | Add empty branches to 4 result tables |
| `frontend/partials/tab-sequences.html` | Add empty state to step table |
| `frontend/index.html` | Regenerated — never hand-edited |

## Verification

1. `npm run build:html` — regenerates `frontend/index.html` from the template and
   partials. Never hand-edit `frontend/index.html`; CI fails when it drifts from the
   sources.
2. Confirm the new placement guard fires: temporarily move one tab INCLUDE marker
   outside `<main>`, confirm the build throws, then revert.
3. `npx playwright test` — full e2e suite. Specs that touch the changed surfaces:
   `09-contracts.spec.ts`, `17-timezone-display.spec.ts`, `20-dark-foundation.spec.ts`,
   `02b-launch-jobs-remote-preview.spec.ts`, `38-live-docker-aws-compare.spec.ts`.
4. Manual check, each at a normal desktop viewport: Contracts, File Servers, and Help
   render starting at the top of the content area, constrained to the same 1280px
   column and padding as Config and Monitor.
5. Manual check: Contracts with zero contracts shows examples expanded; with at least
   one contract, examples start collapsed and the "Examples" button still toggles them.
6. Manual check: File Servers with zero servers shows the empty-state card.
7. Manual check: History select-all checkbox toggles all result rows and reflects
   state correctly, and every History sub-tab shows a section header.
