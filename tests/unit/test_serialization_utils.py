from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd

from etl_framework.utils.serialization import csv_safe, dataclass_to_dict, datetime_to_iso, json_safe


@dataclass
class Sample:
    when: datetime
    amount: Decimal


def test_json_safe_handles_common_non_json_values():
    when = datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert json_safe({"when": when, "amount": Decimal("1.5"), "missing": pd.NA}) == {
        "when": "2024-01-01T00:00:00+00:00",
        "amount": 1.5,
        "missing": None,
    }


def test_csv_safe_serializes_nested_values():
    assert csv_safe({"id": 1, "tags": ["a", "b"]}) == '{"id": 1, "tags": ["a", "b"]}'
    assert csv_safe(None) == ""


def test_dataclass_to_dict_and_datetime_to_iso():
    when = datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert dataclass_to_dict(Sample(when=when, amount=Decimal("2.5"))) == {
        "when": "2024-01-01T00:00:00+00:00",
        "amount": 2.5,
    }
    assert datetime_to_iso(when) == "2024-01-01T00:00:00+00:00"
    assert datetime_to_iso(None) is None
