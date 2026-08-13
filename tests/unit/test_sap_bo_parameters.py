"""Tests for the SAP BO prompt answer-builder."""
from __future__ import annotations

from etl_framework.sap_bo.parameters import build_parameter_answers


def test_datetime_date_preserves_calendar_date_regardless_of_timezone():
    # Bare date values for DateTime prompts preserve the picked calendar date
    # as UTC midnight (e.g. 2026-06-02 -> 2026-06-02T00:00:00.000Z) regardless of timezone.
    built = build_parameter_answers(
        [{"id": 0, "type": "DateTime", "value": "2026-06-02"}], "Australia/Sydney"
    )
    assert built == [
        {"id": 0, "type": "DateTime", "value": "2026-06-02T00:00:00.000Z"}
    ]


def test_lowercase_datetime_still_gets_its_date_converted():
    # The date conversion keys off the type, so a lowercase 'datetime' from the
    # listing would otherwise ship a raw YYYY-MM-DD to BO.
    built = build_parameter_answers(
        [{"id": 0, "type": "datetime", "value": "2026-06-02"}], "Etc/GMT-1"
    )
    assert built == [
        {"id": 0, "type": "DateTime", "value": "2026-06-02T00:00:00.000Z"}
    ]


def test_unknown_prompt_type_passes_through_unmapped():
    built = build_parameter_answers(
        [{"id": 2, "type": "Numeric", "value": "42"}], "Etc/GMT-1"
    )
    assert built[0]["type"] == "Numeric"


def test_full_datetime_value_is_not_reconverted():
    built = build_parameter_answers(
        [{"id": 1, "type": "DateTime", "value": "2026-06-01T23:00:00.000Z"}],
        "Etc/GMT-1",
    )
    assert built[0]["value"] == "2026-06-01T23:00:00.000Z"
