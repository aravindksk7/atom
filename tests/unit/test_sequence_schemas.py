"""Schema shape for saved execution sequences."""
from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_step_ref_defaults():
    from api.schemas import SequenceStepRef
    step = SequenceStepRef(step_id="a", job_name="orders_recon")
    assert step.depends_on == []
    assert step.trigger_rule == "all_success"
    assert step.on_failure == "skip_downstream"
    assert step.max_retries is None
    assert step.hold_after is False
    assert step.wait_seconds == 0


def test_step_ref_rejects_blank_step_id():
    from api.schemas import SequenceStepRef
    with pytest.raises(ValidationError):
        SequenceStepRef(step_id="", job_name="orders_recon")


def test_sequence_ref_version_defaults_to_latest():
    from api.schemas import SequenceRef
    assert SequenceRef(sequence_id=1).sequence_version is None


def test_defaults_rejects_unknown_keys():
    from api.schemas import SequenceDefaults
    with pytest.raises(ValidationError):
        SequenceDefaults(nonsense=1)


def test_precondition_rejects_out_of_range_weekday():
    from api.schemas import SequencePrecondition
    with pytest.raises(ValidationError):
        SequencePrecondition(weekdays=[7])


def test_stage_step_rejects_depends_on():
    from pydantic import ValidationError
    from api.schemas import SequenceStage

    with pytest.raises(ValidationError):
        SequenceStage(mode="serial", steps=[
            {"step_id": "a", "job_name": "j", "depends_on": ["x"]},
        ])


def test_stage_requires_a_step():
    from pydantic import ValidationError
    from api.schemas import SequenceStage

    with pytest.raises(ValidationError):
        SequenceStage(mode="parallel", steps=[])


def test_flow_input_rejects_stages_with_steps():
    from pydantic import ValidationError
    from api.schemas import ExecutionSequenceCreate

    with pytest.raises(ValidationError):
        ExecutionSequenceCreate(
            name="n",
            steps=[{"step_id": "a", "job_name": "j"}],
            stages=[{"mode": "serial", "steps": [{"step_id": "b", "job_name": "j"}]}],
        )


def test_flow_input_rejects_stages_with_flow_mode():
    from pydantic import ValidationError
    from api.schemas import ExecutionSequenceVersionCreate

    with pytest.raises(ValidationError):
        ExecutionSequenceVersionCreate(
            flow_mode="serial",
            stages=[{"mode": "serial", "steps": [{"step_id": "b", "job_name": "j"}]}],
        )


def test_flow_input_rejects_empty_stages():
    from pydantic import ValidationError
    from api.schemas import ExecutionSequenceCreate

    with pytest.raises(ValidationError):
        ExecutionSequenceCreate(name="n", stages=[])


def test_flow_mode_rejects_depends_on():
    from pydantic import ValidationError
    from api.schemas import SequenceValidateRequest

    with pytest.raises(ValidationError):
        SequenceValidateRequest(flow_mode="parallel", steps=[
            {"step_id": "a", "job_name": "j"},
            {"step_id": "b", "job_name": "j", "depends_on": ["a"]},
        ])


def test_steps_without_flow_mode_keep_their_edges():
    from api.schemas import ExecutionSequenceCreate

    body = ExecutionSequenceCreate(name="n", steps=[
        {"step_id": "a", "job_name": "j"},
        {"step_id": "b", "job_name": "j", "depends_on": ["a"]},
    ])
    assert body.flow_mode is None and body.stages is None
    assert body.steps[1].depends_on == ["a"]


def test_launch_max_workers_must_be_positive():
    from pydantic import ValidationError
    from api.schemas import SequenceBatchLaunchRequest, SequenceLaunchRequest

    assert SequenceLaunchRequest().max_workers is None
    assert "max_workers" in SequenceBatchLaunchRequest.model_fields
    with pytest.raises(ValidationError):
        SequenceLaunchRequest(max_workers=0)
