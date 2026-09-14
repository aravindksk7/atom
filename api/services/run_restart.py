from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from api.schemas import RunSettings, SequenceStepRef, StepCondition
from api.services.sequence_conditions import ParentOutcome
from etl_framework.repository.models import MismatchDetail, RunStep, TestResult
from etl_framework.repository.repository import RunRepository, RunStepRepository
from etl_framework.runner.state import TestCaseState, TestStatus


CARRIED_OVER_STATUSES = frozenset({"PASSED", "SLOW", "SKIPPED", "APPROVED"})


class RestartError(ValueError):
    pass


class RestartNotFound(RestartError):
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
    carried_states: list[TestCaseState] = []
    seeded: dict[str, ParentOutcome] = {}
    for original_step in original_steps:
        if original_step.status not in CARRIED_OVER_STATUSES:
            continue
        copied_result = _copy_result_for_step(db, original_step, new_run_id)
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


def _copy_result_for_step(db: Session, step: RunStep, new_run_id: str) -> TestResult | None:
    originals = (
        db.query(TestResult)
        .filter(TestResult.run_id == step.run_id, TestResult.query_name == step.job_name)
        .order_by(TestResult.id)
        .all()
    )
    if not originals:
        return None
    original = originals[min(step.step_index, len(originals) - 1)]
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
