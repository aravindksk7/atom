"""Tests for PassCondition schema and StepCondition extension."""
from __future__ import annotations
import pytest
from pydantic import ValidationError


def test_pass_condition_all_defaults_are_none_or_empty():
    from api.schemas import PassCondition
    pc = PassCondition()
    assert pc.min_row_count is None
    assert pc.max_row_count is None
    assert pc.max_value_mismatches is None
    assert pc.max_missing_in_target is None
    assert pc.max_missing_in_source is None
    assert pc.require_status == []
    assert pc.pass_sql is None
    assert pc.pass_sql_mode == "rows_mean_pass"


def test_pass_condition_rejects_extra_fields():
    from api.schemas import PassCondition
    with pytest.raises(ValidationError):
        PassCondition(unknown_field=1)


def test_pass_condition_pass_sql_mode_must_be_valid():
    from api.schemas import PassCondition
    with pytest.raises(ValidationError):
        PassCondition(pass_sql="SELECT 1", pass_sql_mode="bad_mode")


def test_pass_condition_accepts_valid_fields():
    from api.schemas import PassCondition
    pc = PassCondition(
        min_row_count=1,
        max_row_count=1000,
        max_value_mismatches=0,
        max_missing_in_target=5,
        max_missing_in_source=5,
        require_status=["PASSED", "SLOW"],
        pass_sql="SELECT 1",
        pass_sql_mode="rows_mean_fail",
    )
    assert pc.min_row_count == 1
    assert pc.require_status == ["PASSED", "SLOW"]
    assert pc.pass_sql_mode == "rows_mean_fail"


def test_job_definition_accepts_pass_condition():
    from api.schemas import JobDefinition, PassCondition
    job = JobDefinition(
        name="test",
        query="SELECT * FROM t",
        key_columns=["id"],
        pass_condition=PassCondition(min_row_count=1, require_status=["PASSED"]),
    )
    assert job.pass_condition is not None
    assert job.pass_condition.min_row_count == 1
    assert job.pass_condition.require_status == ["PASSED"]


def test_job_definition_pass_condition_defaults_to_none():
    from api.schemas import JobDefinition
    job = JobDefinition(name="test", query="SELECT * FROM t", key_columns=["id"])
    assert job.pass_condition is None


def test_step_condition_new_fields_default_none():
    from api.schemas import StepCondition
    sc = StepCondition()
    assert sc.min_row_count is None
    assert sc.max_row_count is None
    assert sc.max_value_mismatches is None
    assert sc.max_missing_in_target is None
    assert sc.max_missing_in_source is None


def test_step_condition_existing_fields_unchanged():
    from api.schemas import StepCondition
    sc = StepCondition(require_status=["PASSED"], max_mismatch_count=5)
    assert sc.require_status == ["PASSED"]
    assert sc.max_mismatch_count == 5


def test_step_condition_accepts_new_fields():
    from api.schemas import StepCondition
    sc = StepCondition(min_row_count=1, max_row_count=1000, max_value_mismatches=0,
                       max_missing_in_target=2, max_missing_in_source=2)
    assert sc.min_row_count == 1
    assert sc.max_value_mismatches == 0
