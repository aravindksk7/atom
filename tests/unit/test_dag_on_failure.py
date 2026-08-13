"""on_failure policy: stop, continue, and the default."""
from __future__ import annotations

from api.schemas import SequenceStepRef
from tests.unit.test_dag_retry import FakeStepRepo, _Result, _executor


def _status_map(mapping):
    def run_step(step):
        from api.services.dag_executor import StepOutcome
        status = mapping.get(step.step_id, "PASSED")
        return StepOutcome(status=status, result=_Result(status), state=f"state-{step.step_id}")
    return run_step


# --- stop -------------------------------------------------------------------

def test_stop_aborts_the_run():
    steps = [
        SequenceStepRef(step_id="a", job_name="ja", on_failure="stop"),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"]),
    ]
    ex, repo, _ = _executor(steps, _status_map({"a": "FAILED"}))
    outcome = ex.run()

    assert outcome.cancelled is True
    assert repo.status["b"] == "CANCELLED"


def test_stop_also_cancels_an_unrelated_branch():
    steps = [
        SequenceStepRef(step_id="a", job_name="ja", on_failure="stop"),
        SequenceStepRef(step_id="other", job_name="jo", depends_on=["a"]),
    ]
    ex, repo, _ = _executor(steps, _status_map({"a": "ERROR"}))
    outcome = ex.run()

    assert outcome.cancelled is True
    assert repo.status["other"] == "CANCELLED"


def test_stop_does_nothing_when_the_step_succeeds():
    steps = [
        SequenceStepRef(step_id="a", job_name="ja", on_failure="stop"),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"]),
    ]
    ex, repo, _ = _executor(steps, _status_map({}))
    outcome = ex.run()

    assert outcome.cancelled is False
    assert repo.status["b"] == "PASSED"


# --- continue ---------------------------------------------------------------

def test_continue_excludes_the_failure_from_aggregation():
    steps = [
        SequenceStepRef(step_id="a", job_name="ja", on_failure="continue"),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"], trigger_rule="all_done"),
    ]
    ex, repo, _ = _executor(steps, _status_map({"a": "FAILED"}))
    outcome = ex.run()

    assert repo.status["a"] == "FAILED"           # the step still reads as failed
    assert outcome.tolerated_states == ["state-a"]
    assert outcome.states == ["state-b"]          # only 'b' counts toward run status


def test_continue_does_not_change_scheduling():
    steps = [
        SequenceStepRef(step_id="a", job_name="ja", on_failure="continue"),
        SequenceStepRef(step_id="b", job_name="jb", depends_on=["a"]),
    ]
    ex, repo, _ = _executor(steps, _status_map({"a": "FAILED"}))
    ex.run()

    # all_success still refuses a failed parent -- continue is about the score,
    # not about scheduling.
    assert repo.status["b"] == "BLOCKED"


def test_continue_leaves_a_successful_step_counted():
    steps = [SequenceStepRef(step_id="a", job_name="ja", on_failure="continue")]
    ex, _, _ = _executor(steps, _status_map({}))
    outcome = ex.run()

    assert outcome.states == ["state-a"]
    assert outcome.tolerated_states == []


# --- default ----------------------------------------------------------------

def test_default_counts_the_failure_and_keeps_going():
    steps = [
        SequenceStepRef(step_id="a", job_name="ja"),
        SequenceStepRef(step_id="cleanup", job_name="jc", depends_on=["a"], trigger_rule="all_done"),
    ]
    ex, repo, _ = _executor(steps, _status_map({"a": "FAILED"}))
    outcome = ex.run()

    assert repo.status["cleanup"] == "PASSED"
    assert outcome.tolerated_states == []
    assert set(outcome.states) == {"state-a", "state-cleanup"}
