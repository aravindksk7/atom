# Frontend UI/UX Redesign — Design

**Date:** 2026-07-16
**Status:** Approved

## Purpose

The frontend (`frontend/index.html` + `frontend/app.js`, Alpine.js SPA over a FastAPI backend) currently uses a flat top-nav strip of 11 tabs (Config, Launch, Monitor, History, Adapters, Reports, Differences, Compare, Contracts, Logs, Help) with a single amber accent on near-black. As the tab count grew, the flat top bar stopped scaling and there's no logical grouping or landing/overview screen. This redesigns navigation, color system, and core components to standard dashboard patterns (grouped sidebar, top bar for status/profile, card-based content), adds a Home/Overview landing tab, and introduces a vibrant-but-accessible multi-accent palette with light/dark toggle — without changing the underlying stack (Alpine.js + vanilla CSS, no framework migration).

## Current state (as discovered)

- `frontend/index.html`: single large HTML file, Alpine `x-data="app()"`, `<nav class="top-nav">` with a `template x-for="tab in tabs"` tab strip, then an `<section class="auth-status-bar">` connection/token banner, then `<main class="main-content">` with one `x-show="currentView === '<id>'"` block per tab.
- `frontend/app.js`: `tabs` array (~line 112) defines id/label/icon per tab, flat, no grouping metadata. `currentView` (Alpine state) drives which tab block shows.
- `frontend/styles.css`: `:root` (~line 1028) defines dark-only CSS custom properties (`--bg`, `--panel`, `--accent` (amber `#ffb300`), `--cyan`, `--blue`, `--violet`, `--magenta`, `--emerald`, `--amber`, `--rose`) — one accent, no semantic/group mapping, no light variant.
- No existing Home/Overview tab — app lands on `config` by default.
- No theme toggle, no light palette.

## Goals

1. Group the 11 tabs into a sidebar with workflow-stage sections instead of a flat top strip.
2. Add a Home/Overview landing tab with at-a-glance status and quick actions.
3. Introduce a 60-30-10 vibrant multi-accent palette (dark + light), WCAG AA compliant, with a theme toggle.
4. Define reusable component styles (buttons, forms, cards, tables, status pills, nav items) applied consistently across all 11+1 tabs.
5. Keep the existing Alpine.js/vanilla-CSS stack and all existing tab logic/behavior intact — this is a structural + visual layer change, not a rewrite.

## Non-goals

- No React/Tailwind migration (Tailwind CSS file present in `vendor/` is unrelated/unused for this work).
- No new search feature.
- No changes to backend APIs, job/run logic, or any tab's functional behavior beyond what's needed to relocate the auth-status banner into the new Home tab and the sidebar shell.

## Navigation hierarchy

New default landing view: `home` (no group header, standalone above all groups).

```
Home                        (standalone, default landing)

SETUP
  Config
  Adapters
  Contracts

EXECUTION
  Launch
  Monitor

ANALYSIS
  History
  Reports
  Differences
  Compare

SYSTEM
  Logs
  Help
```

- Sidebar is collapsible (icon-only collapsed state) to preserve room for content on narrow viewports.
- `tabs` array in `app.js` gains a `group` field (`null` for `home`, else one of `setup`/`execution`/`analysis`/`system`); sidebar template groups by this field and renders a small gradient-underlined group header per section.
- Top bar becomes minimal: current page title/breadcrumb (left), connection status pill + theme toggle + token/profile menu (right). The tab strip and the old `auth-status-bar` section are removed from the top of the page; the auth/token banner content moves into the new Home tab as a card (still reachable/manageable from there, and the top-bar status pill remains as an always-visible glance indicator).

## Color scheme

60% neutral base, 30% primary (indigo) for interactive/active state, 10% accents split across four group colors + three semantic states. Both dark and light variants defined as CSS custom properties on `:root` / `:root[data-theme="light"]`; a `themeMode` Alpine state (persisted to `localStorage`) toggles the attribute.

**Base (60%)**

| Token | Dark | Light | Use |
|---|---|---|---|
| `--bg` | `#0b0d12` | `#f7f8fb` | app background |
| `--panel` | `#141821` | `#ffffff` | cards, sidebar |
| `--panel-raised` | `#1c212c` | `#eef0f5` | hover/active rows |
| `--border` | `#262c38` | `#e2e5eb` | dividers |
| `--text` | `#f1f4f9` | `#151922` | primary text |
| `--text-soft` | `#aab3c2` | `#525a6b` | secondary text |
| `--muted` | `#6c7686` | `#8992a3` | placeholders, disabled |

**Primary (30%)**

| Token | Hex | Use |
|---|---|---|
| `--primary` | `#6366f1` | active nav item, primary buttons, links |
| `--primary-hover` | `#818cf8` | hover state |
| `--primary-soft` | `#6366f11a` (10% alpha) | active nav background wash |

**Accents (10%)**

| Token | Hex | Maps to |
|---|---|---|
| `--accent-setup` | `#f59e0b` | Setup group |
| `--accent-exec` | `#22d3ee` | Execution group |
| `--accent-analysis` | `#a855f7` | Analysis group |
| `--accent-system` | `#64748b` | System group |
| `--success` | `#22c55e` | connected, pass, healthy |
| `--warning` | `#f59e0b` | degraded, pending |
| `--danger` | `#f43f5e` | offline, fail, destructive |

All base-on-base and text-on-accent pairings verified ≥4.5:1 contrast (body text) / ≥3:1 (large text, icons). Gradients used only as accents (primary button fill, group-header underline), never as full-page/card backgrounds.

## Component spec

- **Buttons**: `btn-primary` (gradient fill `--primary`→`--primary-hover`, white text, 8px radius, hover lift + shadow, 40%-opacity disabled state), `btn-secondary` (transparent, 1px `--border`, hover → `--panel-raised`), `btn-danger` (solid `--danger`, destructive actions only, always behind a confirm step). Icon-only buttons ≥36×36px hit target. Two sizes: `sm` (28px) and default (36px).
- **Forms**: inputs on `--panel-raised` with 1px `--border`; focus state = 2px `--primary` ring at 40% alpha plus border thickening (not color-only); labels always above field, 12px `--text-soft`; validation errors shown inline below field with `--danger` border, not toast-only. Booleans use toggle switches, not checkboxes.
- **Cards**: `--panel` bg, 1px `--border`, 12px radius, 16-20px padding; header row = title + optional right-aligned action; status/job/run cards get a 3px left border in the relevant semantic color for at-a-glance scanning; interactive card hover brightens border to `--primary` with no layout shift.
- **Sidebar nav item**: default `--text-soft`/transparent; hover → `--panel-raised`; active → `--primary-soft` wash + `--text` + 3px left border in the item's group accent + filled icon variant.
- **Tables**: sticky header on `--panel-raised`, border-bottom row separators (no zebra striping), row hover → `--panel-raised`, numeric/ID/timestamp columns right-aligned/monospace.
- **Status pills**: pill shape, 11px text, colored dot + label, background = semantic color at 12% alpha, text = full semantic color.

## Home / Overview tab (new)

- **Stat row**: 4 clickable cards — Active Runs, Last Run Status (status pill), Connected Environments, Pending Jobs — each navigates to the relevant existing tab.
- **Quick actions**: `+ New Config`, `▶ Launch Job`, `📊 View Reports` as a `btn-primary` row, wired to the same modal/navigation handlers the source tabs already use.
- **Recent activity table**: last 8 runs (name, environment, status pill, timestamp, duration) using the shared table component, linking into History.
- **Health card**: the current `auth-status-bar` content (token identity, connect/manage actions) relocated here as a card; the top-bar connection pill remains as the persistent glance indicator.

## Architecture / implementation notes

- All changes are additive/restructuring within the existing three files (`index.html`, `app.js`, `styles.css`) plus new CSS custom properties — no new build step, no new dependencies.
- `app.js`: add `group` to each `tabs` entry; add `home` tab entry; add `themeMode` state + `toggleTheme()` + `localStorage` persistence; add whatever minimal state/computed values the Home tab's stat row and recent-activity table need (likely derived from data already fetched for History/Monitor rather than new API calls).
- `index.html`: replace `<nav class="top-nav">` tab strip with a `<aside class="sidebar">` grouped nav (collapsible) + a slimmed `<nav class="top-nav">` for title/status/theme/profile; move the `auth-status-bar` section's markup into the new `home` view's health card; add the `home` view block.
- `styles.css`: restructure `:root` token block per the Color scheme section above, add `:root[data-theme="light"]` overrides, add sidebar/group-header/stat-card/status-pill styles, update existing `.btn-primary`/`.btn-secondary`/card/table selectors to the new tokens rather than introducing parallel class names (existing markup keeps its class names; only the underlying CSS variables/rules change) — minimizes churn across the ~5,000+ lines of tab markup in `index.html`.
- Existing `data-testid` attributes must be preserved as-is so existing Playwright E2E specs (`tests/e2e/*.spec.ts`, see `2026-07-14-playwright-e2e-suite-design.md`) keep passing; new elements (sidebar, home tab, theme toggle) get new `data-testid`s following the existing `kebab-case` convention.

## Testing

- Existing Playwright E2E suite (`tests/e2e/`) must continue to pass unmodified against the restructured DOM — sidebar/top-bar changes must not alter any tab's internal `data-testid`s or interaction flow.
- Add new E2E coverage for: sidebar navigation (group expand/collapse, active-state highlighting), Home tab (stat cards navigate correctly, recent activity links into History, quick actions open correct modals/tabs), theme toggle (persists across reload, applies to both sidebar and content).
- Manual contrast check (or automated axe/pa11y pass) on both theme variants before merging.

## Out of scope

- Framework migration (React/Tailwind).
- Global search.
- New backend endpoints — Home tab must derive its data from existing endpoints/state already used by History/Monitor/Adapters.
