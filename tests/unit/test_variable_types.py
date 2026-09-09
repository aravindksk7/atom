from datetime import date, timedelta

import pytest

from api.services.variable_types import (
    resolve_date_expression,
    validate_variable_name,
    validate_variable_value,
)


def test_validate_variable_name_accepts_valid_identifiers():
    validate_variable_name("run_date")
    validate_variable_name("_leading_underscore")
    validate_variable_name("batch2")


@pytest.mark.parametrize("name", ["run-date", "run date", "2run", "", "run.date"])
def test_validate_variable_name_rejects_invalid(name):
    with pytest.raises(ValueError):
        validate_variable_name(name)


def test_validate_variable_value_text_accepts_anything():
    validate_variable_value("anything goes! 123", "text")


@pytest.mark.parametrize("value", ["abc123", "ABC", "123"])
def test_validate_variable_value_alphanumeric_accepts(value):
    validate_variable_value(value, "alphanumeric")


@pytest.mark.parametrize("value", ["abc-123", "abc 123", "abc_123", ""])
def test_validate_variable_value_alphanumeric_rejects(value):
    with pytest.raises(ValueError):
        validate_variable_value(value, "alphanumeric")


@pytest.mark.parametrize("value", ["123", "-123", "1.5", "-1.5"])
def test_validate_variable_value_number_accepts(value):
    validate_variable_value(value, "number")


@pytest.mark.parametrize("value", ["abc", "1.2.3", "", "1,5"])
def test_validate_variable_value_number_rejects(value):
    with pytest.raises(ValueError):
        validate_variable_value(value, "number")


@pytest.mark.parametrize("value", ["2026-09-08", "today", "TODAY", "today-1", "today+2"])
def test_validate_variable_value_date_accepts(value):
    validate_variable_value(value, "date")


@pytest.mark.parametrize("value", ["2026-9-8", "09/08/2026", "tomorrow", "today-", "today+x"])
def test_validate_variable_value_date_rejects(value):
    with pytest.raises(ValueError):
        validate_variable_value(value, "date")


def test_resolve_date_expression_literal_passes_through():
    assert resolve_date_expression("2026-09-08") == "2026-09-08"


def test_resolve_date_expression_today():
    assert resolve_date_expression("today") == date.today().isoformat()


def test_resolve_date_expression_today_minus_n():
    assert resolve_date_expression("today-1") == (date.today() - timedelta(days=1)).isoformat()


def test_resolve_date_expression_today_plus_n():
    assert resolve_date_expression("today+2") == (date.today() + timedelta(days=2)).isoformat()


def test_resolve_date_expression_is_case_insensitive():
    assert resolve_date_expression("TODAY-1") == (date.today() - timedelta(days=1)).isoformat()
