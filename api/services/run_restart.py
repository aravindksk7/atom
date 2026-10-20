from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from api.schemas import RunSettings, SequenceStepRef, StepCondition
from api.services.sequence_conditions import ParentOutcome
from etl_framework.repository.models import MismatchDetail, RunStep, TestResult
from etl_framework.repository.repository import RunRepository, RunStepRepository
from etl_framework.runner.state import TestCaseState, TestStatus


CARRIED_OVER_STATUSES = frozenset({"PASSED", "SLOW", "SKIPPED", "APPROVED"})
# Step statuses an operator may mark as passed after the fact.
MARKABLE_STATUSES = frozenset({"FAILED", "ERROR"})


class RestartError(ValueError):
    pass


class RestartNotFound(RestartError):
    pass


class RunInProgress(RestartError):
    pass


class StepNotFailed(RestartError):
    pass


@dataclass(frozen=True)
class RestartPlan:
    run_id: str
    status: str
    restarted_from_run_id: str
    steps: list[SequenceStepRef]
    run_settings: RunSettings
    config_snapshot: dict[str, Any]
    carried_over_states: list[TestCaseState]
    seeded_outcomes: dict[str, ParentOutcome]


def restart_run(db: Session, original_run_id: str) -> RestartPlan:
    run_repo = RunRepository(db)
    original = run_repo.get_run(original_run_id)
    if original is None:
        raise RestartNotFound("Run not found")
    if original.status in {"PENDING", "RUNNING"}:
        raise RestartError("run is still in progress.")

    original_steps = RunStepRepository(db).list_steps(original_run_id)
    if not any(step.status not in CARRIED_OVER_STATUSES for step in original_steps):
        raise RestartError("nothing to restart.")
    if original.selection_id and run_repo.has_active_run_for_selection(original.selection_id):
        raise RestartError("selection already has an active run.")

    steps = [_step_ref_from_row(step) for step in original_steps]
    config_snapshot = dict(original.config_snapshot or {})
    new_run_id = str(uuid.uuid4())
    run_repo.create_run(
        new_run_id,
        original.source_env or "",
        original.target_env or "",
        config_snapshot=config_snapshot,
        run_type=original.run_type,
        pair_id=original.pair_id,
        selection_id=original.selection_id,
        selection_version=original.selection_version,
        ci_context=original.ci_context,
        restarted_from_run_id=original_run_id,
    )

    step_repo = RunStepRepository(db)
    step_repo.materialize_steps(new_run_id, steps)

    result_for_step = _results_by_step(db, original_run_id, original_steps)
    carried_states: list[TestCaseState] = []
    seeded: dict[str, ParentOutcome] = {}
    for original_step in original_steps:
        if original_step.status not in CARRIED_OVER_STATUSES:
            continue
        original_result = result_for_step.get(original_step.step_index)
        copied_result = (
            _copy_result_for_step(db, original_result, new_run_id)
            if original_result is not None else None
        )
        status = _outcome_status(original_step.status)
        step_repo.update_status(
            new_run_id,
            original_step.step_index,
            original_step.status,
            carried_over=True,
        )
        state = TestCaseState(name=original_step.job_name, test_type="reconciliation", status=_test_status(status))
        carried_states.append(state)
        seeded[original_step.step_id] = ParentOutcome(status=status, result=copied_result)

    settings_data = config_snapshot.get("run_settings") if isinstance(config_snapshot, dict) else None
    run_settings = RunSettings(**settings_data) if isinstance(settings_data, dict) else RunSettings()
    return RestartPlan(
        run_id=new_run_id,
        status="PENDING",
        restarted_from_run_id=original_run_id,
        steps=steps,
        run_settings=run_settings,
        config_snapshot=config_snapshot,
        carried_over_states=carried_states,
        seeded_outcomes=seeded,
    )


def mark_step_passed_and_restart(
    db: Session, run_id: str, step_id: str, note: str, released_by: str,
) -> RestartPlan | None:
    """Record that a FAILED/ERROR step actually passed in the source system,
    then restart the run from there.

    Typical case: a remote job's status poll timed out, so the app marked the
    step ERROR while the job itself completed. The step becomes APPROVED --
    already a carried-over status -- so the restart does not re-trigger the
    job, seeds it as PASSED, and runs only what failed or was blocked.
    Returns None when that step was the run's last unfinished one: it is
    still marked, but there is nothing left to run.
    """
    run = RunRepository(db).get_run(run_id)
    if run is None:
        raise RestartNotFound("Run not found")
    if run.status in {"PENDING", "RUNNING"}:
        raise RunInProgress("run is still in progress.")
    step_repo = RunStepRepository(db)
    step = step_repo.get_step_by_step_id(run_id, step_id)
    if step is None:
        raise RestartNotFound("Step not found")
    if step.status not in MARKABLE_STATUSES:
        raise StepNotFailed(f"Step is not failed (current status: {step.status})")

    now = datetime.now(timezone.utc)
    result = _results_by_step(db, run_id, step_repo.list_steps(run_id)).get(step.step_index)
    if result is not None:
        result.override_status = "PASSED"
        result.override_reason = note
        result.override_by = released_by
        result.override_at = now
    step.status = "APPROVED"
    step.release_action = "mark_passed"
    step.release_note = note
    step.released_by = released_by
    step.released_at = now
    db.commit()
    if all(s.status in CARRIED_OVER_STATUSES for s in step_repo.list_steps(run_id)):
        return None
    return restart_run(db, run_id)


def _results_by_step(db: Session, run_id: str, steps: list[RunStep]) -> dict[int, TestResult]:
    """Map step_index -> that step's TestResult in ``run_id``.

    Results are fetched once and grouped by job name; each step takes the Nth
    result for its job, where N is its OCCURRENCE among same-named steps (both
    in step_index/id order). A step's global step_index doesn't correlate with
    its position among same-named results when other jobs run in between, so
    occurrence rank is the correct key for a job repeated non-contiguously.
    """
    results_by_job: dict[str, list[TestResult]] = {}
    for result in (
        db.query(TestResult).filter(TestResult.run_id == run_id).order_by(TestResult.id).all()
    ):
        results_by_job.setdefault(result.query_name, []).append(result)

    occurrence_seen: dict[str, int] = {}
    mapped: dict[int, TestResult] = {}
    for step in sorted(steps, key=lambda s: s.step_index):
        rank = occurrence_seen.get(step.job_name, 0)
        occurrence_seen[step.job_name] = rank + 1
        candidates = results_by_job.get(step.job_name, [])
        if rank < len(candidates):
            mapped[step.step_index] = candidates[rank]
    return mapped


def _step_ref_from_row(step: RunStep) -> SequenceStepRef:
    condition = StepCondition(**step.condition) if isinstance(step.condition, dict) else None
    return SequenceStepRef(
        step_id=step.step_id or f"step-{step.step_index}",
        job_name=step.job_name,
        depends_on=list(step.depends_on or []),
        trigger_rule=step.trigger_rule or "all_success",
        hold_after=bool(step.hold_after),
        condition=condition,
        wait_seconds=step.wait_seconds or 0,
        max_retries=step.max_retries,
        on_failure=step.on_failure or "skip_downstream",
    )


def _copy_result_for_step(db: Session, original: TestResult, new_run_id: str) -> TestResult:
    copied = TestResult(
        run_id=new_run_id,
        query_name=original.query_name,
        status=original.status,
        duration_seconds=original.duration_seconds,
        source_row_count=original.source_row_count,
        target_row_count=original.target_row_count,
        value_mismatch_count=original.value_mismatch_count,
        missing_in_target_count=original.missing_in_target_count,
        missing_in_source_count=original.missing_in_source_count,
        error_message=original.error_message,
        executed_at=original.executed_at,
        source_file_name=original.source_file_name,
        target_file_name=original.target_file_name,
        sample_rows=original.sample_rows,
        segment_summary=original.segment_summary,
        mismatch_summary=original.mismatch_summary,
        schema_diff=original.schema_diff,
        data_artifact_path=original.data_artifact_path,
        override_status=original.override_status,
        override_reason=original.override_reason,
        override_by=original.override_by,
        override_at=original.override_at,
    )
    db.add(copied)
    db.flush()
    for mismatch in original.mismatches:
        db.add(MismatchDetail(
            test_result_id=copied.id,
            key_values=mismatch.key_values,
            column_name=mismatch.column_name,
            source_value=mismatch.source_value,
            target_value=mismatch.target_value,
            mismatch_type=mismatch.mismatch_type,
            delta=mismatch.delta,
            relative_delta=mismatch.relative_delta,
            accepted=mismatch.accepted,
            accepted_note=mismatch.accepted_note,
            accepted_at=mismatch.accepted_at,
            accepted_by=mismatch.accepted_by,
            rejected=mismatch.rejected,
            rejected_note=mismatch.rejected_note,
            rejected_at=mismatch.rejected_at,
            rejected_by=mismatch.rejected_by,
        ))
    db.commit()
    db.refresh(copied)
    return copied


def _outcome_status(status: str) -> str:
    return "PASSED" if status == "APPROVED" else status


def _test_status(status: str) -> TestStatus:
    try:
        return TestStatus(status)
    except ValueError:
        return TestStatus.PASSED
