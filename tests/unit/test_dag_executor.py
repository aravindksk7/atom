"""DagExecutor scheduling, with a fake step-runner and no database."""
from __future__ import annotations

import threading

import pytest

from api.schemas import SequenceStepRef, StepCondition


class FakeStepRepo:
    """Records status writes the way RunStepRepository would persist them."""

    def __init__(self, releases=None):
        self.status = {}
        self.calls = []
        # step_id -> action, returned once the coordinator polls for a release.
        self.releases = releases or {}

    def set_status(self, step_id, status, **kw):
        self.status[step_id] = status
        self.calls.append((step_id, status))

    def get_release(self, step_id):
        return self.releases.get(step_id)


class _Result:
    def __init__(self, status="PASSED", value=0, rows=100):
        self.status = status
        self.value_mismatch_count = value
        self.missing_in_target_count = 0
        self.missing_in_source_count = 0
        self.source_row_count = rows


def _executor(steps, run_step, **kw):
    from api.services.dag_executor import DagExecutor
    repo = kw.pop("step_repo", None) or FakeStepRepo()
    ex = DagExecutor(
        steps=steps,
        step_repo=repo,
        run_step=run_step,
        is_cancel_requested=kw.pop("is_cancel_requested", lambda: False),
        max_workers=kw.pop("max_workers", 4),
        sleep=kw.pop("sleep", lambda s: None),
        clock=kw.pop("clock", lambda: 0.0),
        **kw,
    )
    return ex, repo


def _ok(status="PASSED"):
    def run_step(step):
        from api.services.dag_executor import StepOutcome
        return StepOutcome(status=status, result=_Result(status), state=None)
    return run_step


def test_chain_runs_in_order():
    order = []

    def run_step(step):
        from api.services.dag_executor import StepOutcome
        order.append(step.step_id)
        return StepOutcome(status="PASSED", result=_Result(), state=None)

    steps = [
        SequenceStepRef(step_id="a", job_name="ja"),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"]),
        SequenceStepRef(step_id="c", job_name="jc", depends_on=["b"]),
    ]
    ex, repo = _executor(steps, run_step)
    outcome = ex.run()

    assert order == ["a", "b", "c"]
    assert outcome.cancelled is False and outcome.blocked is False
    assert repo.status == {"a": "PASSED", "b": "PASSED", "c": "PASSED"}


def test_chain_never_uses_the_thread_pool():
    threads = set()

    def run_step(step):
        from api.services.dag_executor import StepOutcome
        threads.add(threading.current_thread().name)
        return StepOutcome(status="PASSED", result=_Result(), state=None)

    steps = [
        SequenceStepRef(step_id="a", job_name="ja"),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"]),
    ]
    ex, _ = _executor(steps, run_step)
    ex.run()

    # A chain has a ready-set of one, so everything runs on the caller's thread.
    assert threads == {threading.current_thread().name}


def test_independent_branches_both_run():
    seen = []

    def run_step(step):
        from api.services.dag_executor import StepOutcome
        seen.append(step.step_id)
        return StepOutcome(status="PASSED", result=_Result(), state=None)

    steps = [
        SequenceStepRef(step_id="root", job_name="j0"),
        SequenceStepRef(step_id="left", job_name="j1", depends_on=["root"]),
        SequenceStepRef(step_id="right", job_name="j2", depends_on=["root"]),
        SequenceStepRef(step_id="join", job_name="j3", depends_on=["left", "right"]),
    ]
    ex, repo = _executor(steps, run_step)
    ex.run()

    assert seen[0] == "root"
    assert seen[-1] == "join"
    assert set(seen[1:3]) == {"left", "right"}
    assert repo.status["join"] == "PASSED"


def test_failed_parent_blocks_its_descendants_only():
    def run_step(step):
        from api.services.dag_executor import StepOutcome
        status = "FAILED" if step.step_id == "left" else "PASSED"
        return StepOutcome(status=status, result=_Result(status), state=None)

    steps = [
        SequenceStepRef(step_id="root", job_name="j0"),
        SequenceStepRef(step_id="left", job_name="j1", depends_on=["root"]),
        SequenceStepRef(step_id="left_child", job_name="j2", depends_on=["left"]),
        SequenceStepRef(step_id="right", job_name="j3", depends_on=["root"]),
    ]
    ex, repo = _executor(steps, run_step)
    outcome = ex.run()

    assert repo.status["left"] == "FAILED"
    assert repo.status["left_child"] == "BLOCKED"
    assert repo.status["right"] == "PASSED"       # unrelated branch is untouched
    assert outcome.blocked is True


def test_blocking_propagates_through_the_whole_subtree():
    def run_step(step):
        from api.services.dag_executor import StepOutcome
        status = "FAILED" if step.step_id == "a" else "PASSED"
        return StepOutcome(status=status, result=_Result(status), state=None)

    steps = [
        SequenceStepRef(step_id="a", job_name="ja"),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"]),
        SequenceStepRef(step_id="c", job_name="jc", depends_on=["b"]),
    ]
    ex, repo = _executor(steps, run_step)
    ex.run()

    assert repo.status == {"a": "FAILED", "b": "BLOCKED", "c": "BLOCKED"}


def test_all_done_runs_even_after_a_failed_parent():
    def run_step(step):
        from api.services.dag_executor import StepOutcome
        status = "FAILED" if step.step_id == "a" else "PASSED"
        return StepOutcome(status=status, result=_Result(status), state=None)

    steps = [
        SequenceStepRef(step_id="a", job_name="ja"),
        SequenceStepRef(step_id="cleanup", job_name="jc", depends_on=["a"], trigger_rule="all_done"),
    ]
    ex, repo = _executor(steps, run_step)
    ex.run()

    assert repo.status["cleanup"] == "PASSED"


def test_all_failed_only_fires_when_every_parent_failed():
    def run_step(step):
        from api.services.dag_executor import StepOutcome
        status = "FAILED" if step.step_id in {"a", "b"} else "PASSED"
        return StepOutcome(status=status, result=_Result(status), state=None)

    steps = [
        SequenceStepRef(step_id="a", job_name="ja"),
        SequenceStepRef(step_id="b", job_name="jb"),
        SequenceStepRef(step_id="alert", job_name="jc", depends_on=["a", "b"], trigger_rule="all_failed"),
    ]
    ex, repo = _executor(steps, run_step)
    ex.run()

    assert repo.status["alert"] == "PASSED"


def test_child_condition_is_checked_against_every_parent():
    def run_step(step):
        from api.services.dag_executor import StepOutcome
        value = 10 if step.step_id == "b" else 0
        return StepOutcome(status="PASSED", result=_Result("PASSED", value=value), state=None)

    steps = [
        SequenceStepRef(step_id="a", job_name="ja"),
        SequenceStepRef(step_id="b", job_name="jb"),
        SequenceStepRef(
            step_id="c", job_name="jc", depends_on=["a", "b"],
            condition=StepCondition(require_status=["PASSED"], max_mismatch_count=5),
        ),
    ]
    ex, repo = _executor(steps, run_step)
    ex.run()

    # 'a' satisfies the gate but 'b' has 10 mismatches, so 'c' is blocked.
    assert repo.status["c"] == "BLOCKED"


def test_wait_seconds_is_honoured_before_the_step():
    slept = []
    steps = [SequenceStepRef(step_id="a", job_name="ja", wait_seconds=3)]
    ex, _ = _executor(steps, _ok(), sleep=lambda s: slept.append(s))
    ex.run()
    assert 3 in slept


def test_cancel_requested_cancels_pending_steps():
    steps = [
        SequenceStepRef(step_id="a", job_name="ja"),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"]),
    ]
    ex, repo = _executor(steps, _ok(), is_cancel_requested=lambda: True)
    outcome = ex.run()

    assert outcome.cancelled is True
    assert repo.status == {"a": "CANCELLED", "b": "CANCELLED"}


def test_held_step_releases_and_unblocks_its_child():
    steps = [
        SequenceStepRef(step_id="a", job_name="ja", hold_after=True),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"]),
    ]
    repo = FakeStepRepo(releases={"a": "approve"})
    ex, _ = _executor(steps, _ok(), step_repo=repo)
    ex.run()

    assert repo.status["a"] == "APPROVED"
    assert repo.status["b"] == "PASSED"


def test_skip_release_makes_the_child_block_under_all_success():
    steps = [
        SequenceStepRef(step_id="a", job_name="ja", hold_after=True),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"]),
    ]
    repo = FakeStepRepo(releases={"a": "skip"})
    ex, _ = _executor(steps, _ok(), step_repo=repo)
    ex.run()

    # SKIPPED is done-but-not-success, so all_success refuses the child.
    assert repo.status["a"] == "SKIPPED"
    assert repo.status["b"] == "BLOCKED"


def test_skip_release_still_satisfies_all_done():
    steps = [
        SequenceStepRef(step_id="a", job_name="ja", hold_after=True),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"], trigger_rule="all_done"),
    ]
    repo = FakeStepRepo(releases={"a": "skip"})
    ex, _ = _executor(steps, _ok(), step_repo=repo)
    ex.run()

    assert repo.status["b"] == "PASSED"


def test_cancel_release_cancels_the_run():
    steps = [
        SequenceStepRef(step_id="a", job_name="ja", hold_after=True),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"]),
    ]
    repo = FakeStepRepo(releases={"a": "cancel"})
    ex, _ = _executor(steps, _ok(), step_repo=repo)
    outcome = ex.run()

    assert repo.status["a"] == "CANCELLED"
    assert outcome.cancelled is True


def test_hold_timeout_auto_cancels_the_step():
    # Preserved behaviour: HOLD_TIMEOUT_SECONDS auto-cancels a stuck hold.
    ticks = iter([0.0, 0.0, 0.0, 100.0, 100.0, 100.0, 100.0, 100.0])

    steps = [SequenceStepRef(step_id="a", job_name="ja", hold_after=True)]
    repo = FakeStepRepo()          # never released
    ex, _ = _executor(
        steps, _ok(), step_repo=repo,
        clock=lambda: next(ticks, 100.0), hold_timeout=10.0,
    )
    outcome = ex.run()

    assert repo.status["a"] == "CANCELLED"
    assert outcome.cancelled is True


def test_outcome_collects_states_for_aggregation():
    def run_step(step):
        from api.services.dag_executor import StepOutcome
        return StepOutcome(status="PASSED", result=_Result(), state=f"state-{step.step_id}")

    steps = [SequenceStepRef(step_id="a", job_name="ja"), SequenceStepRef(step_id="b", job_name="jb")]
    ex, _ = _executor(steps, run_step)
    outcome = ex.run()

    assert set(outcome.states) == {"state-a", "state-b"}
    assert len(outcome.results) == 2
