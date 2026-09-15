# Multi-Day Sequence Recovery Screenshots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture reviewable screenshots at every key stage of the multi-day sequence recovery Playwright scenario.

**Architecture:** Extend the existing page object with one screenshot helper that writes deterministic filenames into Playwright's run output directory. Invoke it only after the corresponding web-first assertions establish that each stage is fully rendered.

**Tech Stack:** TypeScript, Playwright Test 1.48.

## Global Constraints

- Capture Creation, Day 2 Failure, Fix, Resume, and Completed states.
- Store screenshots beneath the Playwright test output so artifacts remain isolated and ignored by Git.
- Use full-page screenshots with stable, ordered filenames.
- Preserve all existing workflow assertions and avoid fixed sleeps.

---

### Task 1: Capture and verify key workflow screenshots

**Files:**
- Modify: `tests/e2e/51-multi-day-sequence-recovery.spec.ts`
- Test: `tests/e2e/51-multi-day-sequence-recovery.spec.ts`

**Interfaces:**
- Consumes: Playwright `testInfo.outputPath(filename)` and `page.screenshot()`.
- Produces: five PNG artifacts in the scenario's test-results output directory.

- [ ] **Step 1: Add screenshot helper**

Pass `TestInfo` to the page object and add:

```ts
async capture(name: string): Promise<void> {
  await this.page.screenshot({ path: this.testInfo.outputPath(name), fullPage: true });
}
```

- [ ] **Step 2: Capture asserted stages**

After each stage's final assertion, invoke:

```ts
await recovery.capture('01-sequence-created.png');
await recovery.capture('02-day-2-processing-failed.png');
await recovery.capture('03-processing-fix-saved.png');
await recovery.capture('04-day-2-resumed.png');
await recovery.capture('05-batch-completed.png');
```

- [ ] **Step 3: Execute focused scenario**

Run:

```powershell
npx playwright test tests/e2e/51-multi-day-sequence-recovery.spec.ts --project=chromium --output=test-results/recovery-screenshots
```

Expected: 7 tests pass and five PNG files are present under `test-results/recovery-screenshots`.

- [ ] **Step 4: Verify artifacts and regression test**

Run:

```powershell
npx playwright test tests/e2e/17-sequences.spec.ts tests/e2e/51-multi-day-sequence-recovery.spec.ts --project=chromium
```

Expected: 13 tests pass. Confirm all five PNGs are non-empty and report their paths.

- [ ] **Step 5: Review diff**

Run:

```powershell
git diff --check
git status --short
```

Expected: the test modification plus existing untracked design/plan documents; generated screenshots remain ignored and untracked.
