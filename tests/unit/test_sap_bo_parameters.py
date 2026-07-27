"""Tests for the SAP BO prompt answer-builder."""
from __future__ import annotations

from etl_framework.sap_bo.parameters import build_parameter_answers


def test_datetime_date_converts_local_midnight_to_utc_on_fixed_plus1_zone():
    # Etc/GMT-1 is a fixed UTC+1 zone (POSIX sign inversion). Local midnight of
    # 2026-06-02 is 2026-06-01T23:00:00Z -- exactly what the real BO UI sent.
    built = build_parameter_answers(
        [{"id": 0, "type": "DateTime", "value": "2026-06-02"}], "Etc/GMT-1"
    )
    assert built == [
        {"id": 0, "type": "DateTime", "value": "2026-06-01T23:00:00.000Z"}
    ]


def test_datetime_date_is_dst_aware_on_a_dst_zone():
    # Europe/Paris is +2 in June (CEST), so local midnight -> 22:00Z, not 23:00Z.
    built = build_parameter_answers(
        [{"id": 0, "type": "DateTime", "value": "2026-06-02"}], "Europe/Paris"
    )
    assert built[0]["value"] == "2026-06-01T22:00:00.000Z"


def test_non_date_prompt_passes_value_through_verbatim():
    built = build_parameter_answers(
        [{"id": 3, "type": "String", "value": "EMEA"}], "Etc/GMT-1"
    )
    assert built == [{"id": 3, "type": "String", "value": "EMEA"}]


def test_full_datetime_value_is_not_reconverted():
    built = build_parameter_answers(
        [{"id": 1, "type": "DateTime", "value": "2026-06-01T23:00:00.000Z"}],
        "Etc/GMT-1",
    )
    assert built[0]["value"] == "2026-06-01T23:00:00.000Z"
