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
