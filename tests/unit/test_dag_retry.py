"""Step-level retry inside DagExecutor."""
from __future__ import annotations

from api.schemas import SequenceStepRef


class FakeStepRepo:
    def __init__(self, releases=None):
        self.status = {}
        self.calls = []
        self.attempts = {}
        self.releases = releases or {}

    def set_status(self, step_id, status, **kw):
        self.status[step_id] = status
        self.calls.append((step_id, status))
        if "attempt" in kw:
            self.attempts[step_id] = kw["attempt"]

    def get_release(self, step_id):
        return self.releases.get(step_id)


class _Result:
    def __init__(self, status="PASSED"):
        self.status = status
        self.value_mismatch_count = 0
        self.missing_in_target_count = 0
        self.missing_in_source_count = 0
        self.source_row_count = 10


def _scripted(statuses_by_step):
    """A run_step that returns a scripted status sequence per step_id."""
    calls = {}

    def run_step(step):
        from api.services.dag_executor import StepOutcome
        n = calls.get(step.step_id, 0)
        calls[step.step_id] = n + 1
        script = statuses_by_step[step.step_id]
        status = script[min(n, len(script) - 1)]
        return StepOutcome(status=status, result=_Result(status), state=f"{step.step_id}-{n}")

    run_step.calls = calls
    return run_step


def _executor(steps, run_step, **kw):
    from api.services.dag_executor import DagExecutor
    repo = kw.pop("step_repo", None) or FakeStepRepo()
    slept = []
    ex = DagExecutor(
        steps=steps,
        step_repo=repo,
        run_step=run_step,
        is_cancel_requested=kw.pop("is_cancel_requested", lambda: False),
        max_workers=kw.pop("max_workers", 4),
        sleep=kw.pop("sleep", slept.append),
        clock=kw.pop("clock", lambda: 0.0),
        **kw,
    )
    return ex, repo, slept


def test_error_is_retried_up_to_max_retries():
    run_step = _scripted({"a": ["ERROR", "ERROR", "PASSED"]})
    steps = [SequenceStepRef(step_id="a", job_name="ja", max_retries=2)]
    ex, repo, _ = _executor(steps, run_step)
    ex.run()

    assert run_step.calls["a"] == 3
    assert repo.status["a"] == "PASSED"


def test_retry_stops_at_the_limit_and_keeps_the_last_status():
    run_step = _scripted({"a": ["ERROR"]})
    steps = [SequenceStepRef(step_id="a", job_name="ja", max_retries=2)]
    ex, repo, _ = _executor(steps, run_step)
    ex.run()

    assert run_step.calls["a"] == 3        # initial + 2 retries
    assert repo.status["a"] == "ERROR"


def test_failed_is_never_retried():
    run_step = _scripted({"a": ["FAILED"]})
    steps = [SequenceStepRef(step_id="a", job_name="ja", max_retries=5)]
    ex, repo, _ = _executor(steps, run_step)
    ex.run()

    assert run_step.calls["a"] == 1
    assert repo.status["a"] == "FAILED"


def test_slow_is_never_retried():
    run_step = _scripted({"a": ["SLOW"]})
    steps = [SequenceStepRef(step_id="a", job_name="ja", max_retries=3)]
    ex, repo, _ = _executor(steps, run_step)
    ex.run()

    assert run_step.calls["a"] == 1


def test_no_retry_when_max_retries_is_zero():
    run_step = _scripted({"a": ["ERROR"]})
    steps = [SequenceStepRef(step_id="a", job_name="ja", max_retries=0)]
    ex, _, _ = _executor(steps, run_step)
    ex.run()

    assert run_step.calls["a"] == 1


def test_null_max_retries_inherits_the_run_default():
    run_step = _scripted({"a": ["ERROR"]})
    steps = [SequenceStepRef(step_id="a", job_name="ja")]     # max_retries is None
    ex, _, _ = _executor(steps, run_step, default_max_retries=2)
    ex.run()

    assert run_step.calls["a"] == 3


def test_step_max_retries_overrides_the_run_default():
    run_step = _scripted({"a": ["ERROR"]})
    steps = [SequenceStepRef(step_id="a", job_name="ja", max_retries=0)]
    ex, _, _ = _executor(steps, run_step, default_max_retries=5)
    ex.run()

    assert run_step.calls["a"] == 1


def test_retry_delay_is_slept_between_attempts():
    run_step = _scripted({"a": ["ERROR", "PASSED"]})
    steps = [SequenceStepRef(step_id="a", job_name="ja", max_retries=1, retry_delay_seconds=7)]
    ex, _, slept = _executor(steps, run_step)
    ex.run()

    assert 7 in slept


def test_retry_delay_inherits_the_run_default():
    run_step = _scripted({"a": ["ERROR", "PASSED"]})
    steps = [SequenceStepRef(step_id="a", job_name="ja", max_retries=1)]
    ex, _, slept = _executor(steps, run_step, default_retry_delay_seconds=4)
    ex.run()

    assert 4 in slept


def test_attempt_number_is_persisted():
    run_step = _scripted({"a": ["ERROR", "ERROR", "PASSED"]})
    steps = [SequenceStepRef(step_id="a", job_name="ja", max_retries=2)]
    ex, repo, _ = _executor(steps, run_step)
    ex.run()

    # Three executions -> the final row records attempt 3.
    assert repo.attempts["a"] == 3


def test_retry_on_without_error_disables_retry():
    run_step = _scripted({"a": ["ERROR"]})
    steps = [SequenceStepRef(step_id="a", job_name="ja", max_retries=3)]
    ex, _, _ = _executor(steps, run_step, retry_on=["timeout"])
    ex.run()

    # 'timeout' matches no status today, so ERROR is not retryable.
    assert run_step.calls["a"] == 1


def test_cancel_during_retry_delay_stops_retrying():
    cancelled = {"value": False}
    run_step = _scripted({"a": ["ERROR"]})

    def sleeper(_seconds):
        cancelled["value"] = True      # cancel arrives while we wait

    steps = [SequenceStepRef(step_id="a", job_name="ja", max_retries=5)]
    ex, _, _ = _executor(
        steps, run_step,
        sleep=sleeper,
        is_cancel_requested=lambda: cancelled["value"],
    )
    ex.run()

    assert run_step.calls["a"] == 1


def test_retry_does_not_stall_an_independent_branch():
    run_step = _scripted({"slow": ["ERROR", "PASSED"], "fast": ["PASSED"]})
    steps = [
        SequenceStepRef(step_id="slow", job_name="j1", max_retries=1, retry_delay_seconds=5),
        SequenceStepRef(step_id="fast", job_name="j2"),
    ]
    ex, repo, _ = _executor(steps, run_step)
    ex.run()

    # Both are roots, so they run concurrently -- the retry sleeps on its own
    # worker thread and never blocks the coordinator.
    assert repo.status["slow"] == "PASSED"
    assert repo.status["fast"] == "PASSED"
