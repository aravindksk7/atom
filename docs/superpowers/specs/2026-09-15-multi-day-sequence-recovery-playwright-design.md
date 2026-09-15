# Multi-Day Sequence Recovery Playwright Test Design

**Date:** 2026-09-15
**Status:** Approved

## Purpose

Add deterministic browser coverage for a three-business-day execution-sequence batch that fails during Day 2, is fixed and restarted from its failed step, and completes Day 2 and Day 3 without rerunning Day 1.

## Scope

The test will cover one saved execution sequence containing three ordered jobs for every business-day iteration:

1. Data Ingestion
2. Data Processing, dependent on Data Ingestion
3. Report Generation, dependent on Data Processing

The batch will run for three consecutive business dates using the existing sequence repeat-execution controls and the `ignore` weekend policy so the test result does not depend on the calendar date on which it executes.

## Architecture

Create a new TypeScript Playwright spec under `tests/e2e` that uses the repository's existing authenticated fixture. The spec will define a small inline `SchedulerPage` page object responsible for navigation, sequence creation, batch launch, history inspection, fix action, restart action, and status assertions.

Execution APIs will be mocked with `page.route` while sequence-editor interactions remain browser-driven. A stateful mock controller will expose successive batch and run states and record launch/restart requests. This provides exact control over the Day 2 mid-sequence failure and makes the test fast and repeatable.

Locators will prefer accessible queries such as `getByRole` and `getByText`; stable existing `data-testid` selectors will be used where the UI does not expose a unique accessible name.

## State Flow

### Creation

The page object opens the Sequences tab, creates a uniquely named sequence, adds the three jobs in dependency order, saves it, and launches repeat execution for three iterations on consecutive dates.

### Initial Execution and Failure

The mocked responses represent:

- Day 1: all three jobs passed.
- Day 2: Data Ingestion passed, Data Processing failed with a controlled configuration error, and Report Generation is blocked or not run.
- Day 3: not started.
- Parent sequence/batch: failed or stopped after the Day 2 failure.

The test will assert both the aggregate failed/paused state and the Data Processing error state and message.

### Fix and Resume

The test performs a visible fix action supported by the current UI. If the product has no run-detail edit control, the test will navigate to the job editor, update the failing job configuration, and save it. The route controller then switches to its corrected mode.

The test returns to the failed run and clicks the existing `Restart from failure` action, which is the product's equivalent of retrying from the failed step.

### Verification

The restarted Day 2 run response marks already successful work as carried over and executes only the failed and downstream steps. Day 2 then passes, Day 3 starts and passes, and the batch reaches `COMPLETED`.

The test will verify:

- No second launch request is made for Day 1.
- Restart payload references the failed Day 2 run.
- Day 2 Data Ingestion is carried over rather than rerun.
- Day 2 Data Processing and Report Generation pass after restart.
- Day 3 runs all three jobs and passes.
- Final batch status is `COMPLETED` with three completed iterations.

The stateful mock will keep request counters and run identifiers so these guarantees are asserted directly, not inferred only from rendered text.

## Reliability and Diagnostics

The test will use Playwright web-first assertions and `expect.poll` for asynchronous state transitions rather than fixed sleeps. Route handlers will validate request methods and payloads before returning fixtures. Meaningful `console.log` messages will identify the Creation, Failure, Fix/Resume, and Verification stages. Stage comments will be included because the requested deliverable explicitly requires them.

## Non-Goals

- Validating the backend execution engine or scheduler timing.
- Waiting across real calendar days.
- Exercising external data-processing systems.
- Adding a reusable project-wide POM framework for this single scenario.
