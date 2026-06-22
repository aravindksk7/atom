# Auth Setup Wizard Design

**Date:** 2026-06-16
**Status:** Approved

---

## Problem

New users open the app and immediately hit silent 401 failures on every API call because no Bearer token exists in `localStorage`. The current workaround — Config → Security (collapsed section) → create token → paste token — is buried and non-obvious. Direct navigation to API URLs (e.g. `/api/audit?limit=100`) returns `401 Unauthorized` with no user-visible guidance.

---

## Solution

A two-part frontend-only addition: a persistent **status bar** and a **centered auth modal**. No backend changes are required — `POST /api/tokens` is already auth-exempt.

---

## Components

### 1. Status Bar

A thin strip rendered immediately below the `<nav>`, always visible.

**Amber state** (no token in `localStorage`):
```
🔑  No API token set — Set up access →
```
Clicking "Set up access →" opens the auth modal.

**Green state** (token present):
```
●  Connected as {activeTokenName}          Manage →
```
"Manage →" switches to the Config tab and expands the Security section.

The bar never hides itself. Users cannot dismiss it. It reflects live token state — if the user revokes their token in Config → Security, the bar returns to amber on next page load.

### 2. Auth Modal

Centered dialog with a dark semi-transparent backdrop. Opens when the user clicks "Set up access →" in the amber status bar.

**Structure (single screen, two parallel paths):**

```
┌─────────────────────────────────────┐
│  🔑 API Access Setup            [✕] │
│                                     │
│  CREATE NEW TOKEN                   │
│  [Token name input    ] [Create →]  │
│                                     │
│  ─────────── or ───────────         │
│                                     │
│  PASTE EXISTING TOKEN               │
│  [Token input (password)] [Activate]│
└─────────────────────────────────────┘
```

**✕ button:** Closes modal. Amber bar remains. App stays usable (API calls fail silently as before).

---

## Data Flow

### Create path

1. User enters a token name → clicks **Create →**
2. Validate: name must be non-empty (inline error if blank, no API call)
3. `POST /api/tokens` with `{ name }` — no `Authorization` header needed
4. On success:
   - `localStorage.setItem('etl_token', resp.raw_token)`
   - Set `activeTokenName = resp.name`
   - Close modal
   - Status bar turns green: `● Connected as {name}`
   - Call `app.loadAll()` to refresh all tab data
5. On API error (e.g. 409 name taken): show inline error below the Create row; do not close modal

### Paste path

1. User pastes a token → clicks **Activate**
2. Validate: field must be non-empty (inline error if blank)
3. `localStorage.setItem('etl_token', value.trim())`
4. Close modal
5. Call `GET /api/tokens` (now authenticated) to resolve the active token's name
   - On success: set `activeTokenName` to the first enabled token's name; bar turns green
   - On failure (bad token): bar turns green with "● Connected" (no name); user will discover the issue on next API call
6. Call `app.loadAll()` to refresh all tab data

---

## New Alpine.js State

Added to the `app()` return object:

```js
showAuthModal: false,       // controls modal visibility
authTokenName: '',          // name field in Create path
authPasteValue: '',         // paste field in Paste path
authError: '',              // inline error message in modal
activeTokenName: '',        // resolved name shown in green bar
```

On `init()`: if `localStorage.getItem('etl_token')` is set, call `GET /api/tokens` to resolve `activeTokenName`.

---

## New Methods

```js
// Open/close modal
openAuthModal()   // sets showAuthModal = true, clears authError
closeAuthModal()  // sets showAuthModal = false

// Create path
async createToken()   // POST /api/tokens, save, close modal, loadAll()

// Paste path
async activateToken() // save to localStorage, resolve name, close modal, loadAll()

// Refresh all tab data after auth
async loadAll()       // parallel: loadConfigs(), loadJobs(), loadRuns()

// Navigate to Config > Security for token management
goToTokenManagement() // sets activeTab = 'config', expands Security section
```

---

## `loadAll()` Helper

Replaces the scattered per-tab API calls triggered on tab switch. After auth succeeds, the app is fully loaded regardless of which tab the user is on:

```js
async loadAll() {
  await Promise.allSettled([
    this.loadConfigs(),
    this.loadJobs(),
    this.loadRuns(),
  ]);
}
```

Uses `Promise.allSettled` so a failure in one call does not block the others.

---

## Files Changed

| File | Change |
|---|---|
| `frontend/index.html` | Add status bar HTML below `<nav>`; add modal HTML before `</body>` |
| `frontend/app.js` | Add 5 state properties, 5 methods, `loadAll()` helper; call `loadAll()` on `init()` if token present |

No backend changes. No new dependencies.

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| Name field empty | Inline: "Enter a token name" — no API call |
| Paste field empty | Inline: "Paste your token" — no API call |
| `POST /api/tokens` → 409 | Inline: "A token with that name already exists" |
| `POST /api/tokens` → other error | Inline: server error message |
| Bad pasted token | Modal closes; bar shows green without name; API calls fail on next action |

---

## Testing

| Test | How |
|---|---|
| Amber bar renders when no token | Check `x-show` condition with `storedToken = ''` |
| Green bar renders when token set | Set `localStorage.etl_token`, reload, verify bar state |
| Create path end-to-end | Fill name, click Create, verify token in localStorage, bar green |
| Paste path end-to-end | Paste valid token, click Activate, verify localStorage, bar green |
| Modal dismisses on ✕ | Click ✕, verify modal hidden, amber bar still visible |
| Empty name shows inline error | Submit blank name, verify error text, no API call |
| `loadAll()` fires after auth | Stub `loadConfigs/loadJobs/loadRuns`, verify all called after create |
| Manage → link | Click Manage →, verify Config tab active, Security section expanded |
