"""The DAG scheduling coordinator.

Owns only scheduling: which step is ready, what blocks what, when a hold is
released, when to stop. It knows nothing about jobs, reconciliation, or HTTP --
running a step is an injected callable, and so are the clock and sleep, which
keeps the whole thing unit-testable without a database or a real job.

A chain is just a DAG whose ready-set never exceeds one step, so it runs inline
on the caller's thread and never touches the pool.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Callable

from api.services.sequence_conditions import ParentOutcome, parent_satisfies, trigger_fires

# Statuses a step can reach without ever having run.
NON_RUNNING_STATUSES = frozenset({"BLOCKED", "CANCELLED"})

# Statuses that count as a step having failed, for on_failure purposes.
FAILURE_STATUSES = frozenset({"FAILED", "ERROR"})

# RunSettings.retry_on tokens mapped to runner statuses. There is no TIMEOUT in
# TestStatus today -- a timeout surfaces as ERROR -- so "timeout" matches nothing
# and is accepted only so the setting stays forward-compatible.
RETRYABLE_STATUS_BY_TOKEN = {"error": frozenset({"ERROR"}), "timeout": frozenset()}


@dataclass(frozen=True)
class StepOutcome:
    """What running one step produced."""
    status: str                 # PASSED | FAILED | SLOW | ERROR
    result: Any | None = None   # ReconciliationResult, when there is one
    state: Any | None = None    # TestCaseState, for run aggregation


@dataclass
class DagOutcome:
    states: list = field(default_factory=list)
    tolerated_states: list = field(default_factory=list)
    results: list = field(default_factory=list)
    cancelled: bool = False
    blocked: bool = False


class DagExecutor:
    def __init__(
        self,
        steps: list,
        step_repo,
        run_step: Callable[[Any], StepOutcome],
        is_cancel_requested: Callable[[], bool],
        on_held: Callable[[Any], None] | None = None,
        max_workers: int = 1,
        hold_poll_interval: float = 5.0,
        hold_timeout: float = 86400.0,
        blocked_step_status: str = "BLOCKED",
        expire_all: Callable[[], None] | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        default_max_retries: int = 0,
        default_retry_delay_seconds: float = 0.0,
        retry_on: list[str] | None = None,
    ) -> None:
        import time as _time

        self._steps = list(steps)
        self._by_id = {s.step_id: s for s in self._steps}
        self._repo = step_repo
        self._run_step = run_step
        self._is_cancelled = is_cancel_requested
        self._on_held = on_held
        self._max_workers = max(1, int(max_workers))
        self._hold_poll = hold_poll_interval
        self._hold_timeout = hold_timeout
        self._blocked_step_status = blocked_step_status
        self._expire_all = expire_all or (lambda: None)
        self._clock = clock or _time.monotonic
        self._sleep = sleep or _time.sleep
        self._default_max_retries = default_max_retries
        self._default_retry_delay = default_retry_delay_seconds
        self._retryable: frozenset[str] = frozenset().union(
            *(RETRYABLE_STATUS_BY_TOKEN.get(token, frozenset())
              for token in (retry_on if retry_on is not None else ["error"]))
        )
        self._attempts: dict[str, int] = {}

        self._children: dict[str, list[str]] = {s.step_id: [] for s in self._steps}
        for step in self._steps:
            for parent in step.depends_on:
                if parent in self._children:
                    self._children[parent].append(step.step_id)

        self._pending: list[str] = [s.step_id for s in self._steps]
        self._outcomes: dict[str, ParentOutcome] = {}
        self._final: dict[str, str] = {}
        self._held: dict[str, float] = {}          # step_id -> held since (clock)
        self._outcome = DagOutcome()

    # --- public -------------------------------------------------------------

    def run(self) -> DagOutcome:
        pool: ThreadPoolExecutor | None = None
        in_flight: dict = {}
        try:
            while self._pending or in_flight or self._held:
                if self._outcome.cancelled or self._is_cancelled():
                    self._drain(in_flight)
                    self._cancel_pending()
                    self._outcome.cancelled = True
                    return self._outcome

                self._poll_holds()
                if self._outcome.cancelled:
                    continue
                ready = self._ready_steps()

                if not ready and not in_flight:
                    if self._held:
                        self._sleep(self._hold_poll)
                        continue
                    break   # nothing ready, nothing running, nothing held

                # A single ready step with nothing in flight runs inline. That is
                # every chain, and it keeps legacy sequences off the pool entirely.
                if len(ready) == 1 and not in_flight:
                    self._finish(ready[0], self._execute(ready[0]))
                    continue

                for step_id in ready:
                    if pool is None:
                        pool = ThreadPoolExecutor(max_workers=self._max_workers)
                    self._pending.remove(step_id)
                    self._mark(step_id, "RUNNING")
                    in_flight[pool.submit(self._run_one, step_id)] = step_id

                if in_flight:
                    done, _ = wait(list(in_flight), return_when=FIRST_COMPLETED)
                    for future in done:
                        step_id = in_flight.pop(future)
                        self._finish(step_id, future.result())
            return self._outcome
        finally:
            if pool is not None:
                pool.shutdown(wait=True)

    # --- scheduling ---------------------------------------------------------

    def _ready_steps(self) -> list[str]:
        """Pending steps whose parents are all resolved, in declared order."""
        ready = []
        for step_id in list(self._pending):
            step = self._by_id[step_id]
            if not all(p in self._final for p in step.depends_on):
                continue
            if any(p in self._held for p in step.depends_on):
                continue
            if self._decide(step_id):
                ready.append(step_id)
        return ready

    def _decide(self, step_id: str) -> bool:
        """True if the step should run; False if it was just marked BLOCKED.

        A parent that never ran (BLOCKED or CANCELLED) has no outcome to judge,
        so it blocks its children outright regardless of their trigger rule.
        Failed steps still provide an outcome, so they don't block children
        unless the trigger rule requires success.
        """
        step = self._by_id[step_id]
        for parent in step.depends_on:
            if self._final.get(parent) in NON_RUNNING_STATUSES:
                self._block(step_id)
                return False

        satisfied = [
            parent_satisfies(step.condition, self._outcomes[p])
            for p in step.depends_on
            if p in self._outcomes
        ]
        if not trigger_fires(step.trigger_rule, satisfied):
            self._block(step_id)
            return False
        return True

    def _retry_budget(self, step) -> tuple[int, float]:
        """Per-step retry settings, falling back to the run-level defaults."""
        limit = step.max_retries if step.max_retries is not None else self._default_max_retries
        delay = (
            step.retry_delay_seconds
            if step.retry_delay_seconds is not None
            else self._default_retry_delay
        )
        return max(0, int(limit or 0)), float(delay or 0.0)

    def _should_retry(self, status: str, attempt: int, limit: int) -> bool:
        # FAILED is a real data mismatch and SLOW already passed -- neither is
        # retryable however generous the budget.
        return status in self._retryable and attempt <= limit

    def _execute(self, step_id: str):
        self._pending.remove(step_id)
        self._mark(step_id, "RUNNING")
        return self._run_one(step_id)

    def _run_one(self, step_id: str) -> StepOutcome:
        step = self._by_id[step_id]
        if step.wait_seconds:
            self._sleep(step.wait_seconds)
            if self._is_cancelled():
                self._outcome.cancelled = True
                return StepOutcome(status="CANCELLED", result=None, state=None)

        limit, delay = self._retry_budget(step)
        attempt = 0
        while True:
            outcome = self._run_step(step)
            attempt += 1
            self._attempts[step_id] = attempt
            if not self._should_retry(outcome.status, attempt, limit):
                return outcome
            self._sleep(delay)
            if self._is_cancelled():
                self._outcome.cancelled = True
                return outcome

    def _finish(self, step_id: str, outcome: StepOutcome) -> None:
        step = self._by_id[step_id]
        self._outcomes[step_id] = ParentOutcome(status=outcome.status, result=outcome.result)

        failed = outcome.status in FAILURE_STATUSES
        if outcome.state is not None:
            # continue-on-error: the step still reports its own status, but its
            # failure is kept out of the run's pass/fail arithmetic.
            if failed and step.on_failure == "continue":
                self._outcome.tolerated_states.append(outcome.state)
            else:
                self._outcome.states.append(outcome.state)
        if outcome.result is not None:
            self._outcome.results.append(outcome.result)

        if step.hold_after:
            self._mark(step_id, "HELD")
            self._held[step_id] = self._clock()
            if self._on_held is not None:
                self._on_held(step)
            return

        self._settle(step_id, outcome.status)

        if failed and step.on_failure == "stop":
            self._outcome.cancelled = True

    def _settle(self, step_id: str, status: str) -> None:
        self._final[step_id] = status
        attempt = self._attempts.get(step_id)
        if attempt is not None:
            self._repo.set_status(step_id, status, attempt=attempt)
        else:
            self._mark(step_id, status)

    # --- holds --------------------------------------------------------------

    def _poll_holds(self) -> None:
        if not self._held:
            return
        self._expire_all()
        now = self._clock()
        for step_id, since in list(self._held.items()):
            action = self._repo.get_release(step_id)
            if action is None:
                if self._hold_timeout > 0 and (now - since) >= self._hold_timeout:
                    action = "cancel"
                    self._repo.set_status(
                        step_id,
                        "CANCELLED",
                        release_action="cancel",
                        release_note="hold timed out",
                    )
                else:
                    continue
            del self._held[step_id]
            if action == "cancel":
                self._settle(step_id, "CANCELLED")
                self._outcome.cancelled = True
            elif action == "skip":
                self._outcomes[step_id] = ParentOutcome(status="SKIPPED", result=None)
                self._settle(step_id, "SKIPPED")
            else:
                self._settle(step_id, "APPROVED")

    # --- terminal handling --------------------------------------------------

    def _block(self, step_id: str) -> None:
        """Mark a step BLOCKED and propagate to its whole subtree."""
        stack = [step_id]
        while stack:
            current = stack.pop()
            if current not in self._pending:
                continue
            self._pending.remove(current)
            self._final[current] = "BLOCKED"
            self._mark(current, self._blocked_step_status)
            self._outcome.blocked = True
            stack.extend(self._children.get(current, []))

    def _cancel_pending(self) -> None:
        for step_id in list(self._pending):
            self._pending.remove(step_id)
            self._settle(step_id, "CANCELLED")
        for step_id in list(self._held):
            del self._held[step_id]
            self._settle(step_id, "CANCELLED")

    def _drain(self, in_flight: dict) -> None:
        for future in list(in_flight):
            step_id = in_flight.pop(future)
            try:
                self._finish(step_id, future.result())
            except Exception:
                self._settle(step_id, "ERROR")

    def _mark(self, step_id: str, status: str) -> None:
        self._repo.set_status(step_id, status)
