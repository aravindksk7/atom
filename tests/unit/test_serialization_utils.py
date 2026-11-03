from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from etl_framework.utils.serialization import (
    csv_safe,
    dataclass_to_dict,
    datetime_to_iso,
    json_safe,
    safe_json_dumps,
)


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


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        np.float64("nan"),
        np.float32("nan"),
        Decimal("NaN"),
        Decimal("Infinity"),
        pd.NaT,
        pd.NA,
        np.datetime64("NaT"),
    ],
    ids=str,
)
def test_json_safe_maps_missing_and_non_finite_values_to_none(value):
    assert json_safe(value) is None


def test_json_safe_renders_numpy_datetimes_as_iso_not_epoch():
    assert json_safe(np.datetime64("2026-10-06T10:15:00.000000000")) == "2026-10-06T10:15:00"


def test_json_safe_renders_numpy_timedeltas_as_text_not_integers():
    assert json_safe(np.timedelta64(5, "ns")) == str(pd.Timedelta(5, "ns"))


@pytest.mark.parametrize("value", [b"\x01\xff", bytearray(b"\x01\xff"), memoryview(b"\x01\xff")], ids=type)
def test_json_safe_renders_binary_as_stable_hex(value):
    assert json_safe(value) == "01ff"


def test_safe_json_dumps_always_emits_strict_json():
    payload = {"when": pd.Timestamp("2026-10-06", tz="UTC"), "day": date(2026, 10, 6), "gap": float("nan")}
    assert json.loads(safe_json_dumps(payload), parse_constant=pytest.fail) == {
        "when": "2026-10-06T00:00:00+00:00",
        "day": "2026-10-06",
        "gap": None,
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
