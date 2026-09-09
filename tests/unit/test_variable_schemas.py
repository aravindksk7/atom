import pytest
from pydantic import ValidationError

from api.schemas import CustomVariableCreate, CustomVariableUpdate


def test_create_accepts_valid_variable():
    v = CustomVariableCreate(name="run_date", var_type="date", default_value="today-1", description="Report date")
    assert v.name == "run_date"


def test_create_rejects_invalid_name():
    with pytest.raises(ValidationError):
        CustomVariableCreate(name="run-date", var_type="text", default_value=None)


def test_create_rejects_default_value_not_matching_type():
    with pytest.raises(ValidationError):
        CustomVariableCreate(name="batch_id", var_type="alphanumeric", default_value="not valid!")


def test_create_allows_blank_default_value():
    v = CustomVariableCreate(name="batch_id", var_type="alphanumeric", default_value=None)
    assert v.default_value is None


def test_update_rejects_default_value_not_matching_declared_type():
    with pytest.raises(ValidationError):
        CustomVariableUpdate(var_type="number", default_value="abc")


def test_update_allows_partial_fields():
    u = CustomVariableUpdate(description="new description")
    assert u.var_type is None
    assert u.default_value is None
