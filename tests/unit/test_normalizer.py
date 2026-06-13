import pandas as pd
import numpy as np
from decimal import Decimal
import pytest
from etl_framework.reconciliation.normalizer import TypeNormalizer


def test_timezone_aware_datetime_converted_to_utc():
    df = pd.DataFrame({
        "ts": pd.to_datetime(["2024-01-01 10:00:00"]).tz_localize("US/Eastern"),
    })
    result = TypeNormalizer().normalize(df)
    # Accept both ns and us resolution — pandas 2.x uses ns, pandas 3.x uses us
    dtype_str = str(result["ts"].dtype)
    assert dtype_str in ("datetime64[ns, UTC]", "datetime64[us, UTC]"), (
        f"Expected UTC datetime dtype, got: {dtype_str}"
    )


def test_timezone_naive_datetime_localized_to_utc():
    df = pd.DataFrame({"ts": pd.to_datetime(["2024-01-01 10:00:00"])})
    result = TypeNormalizer().normalize(df)
    assert hasattr(result["ts"].dtype, "tz") and result["ts"].dtype.tz is not None


def test_decimal_column_converted_to_float64():
    df = pd.DataFrame({"price": pd.array([Decimal("9.99"), Decimal("1.50"), None], dtype=object)})
    result = TypeNormalizer().normalize(df)
    assert result["price"].dtype == np.float64
    assert abs(result["price"].iloc[0] - 9.99) < 1e-9
    assert pd.isna(result["price"].iloc[2])


def test_uuid_string_normalised_to_uppercase():
    df = pd.DataFrame({
        "id": [
            "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "A1B2C3D4-E5F6-7890-ABCD-EF1234567890",
        ]
    })
    result = TypeNormalizer().normalize(df)
    assert result["id"].iloc[0] == result["id"].iloc[1]
    assert result["id"].iloc[0] == result["id"].iloc[0].upper()


def test_non_uuid_strings_not_uppercased():
    df = pd.DataFrame({"name": ["alice", "BOB"]})
    result = TypeNormalizer().normalize(df)
    assert result["name"].iloc[0] == "alice"
    assert result["name"].iloc[1] == "BOB"


def test_float_columns_unchanged():
    df = pd.DataFrame({"price": [1.5, 2.5, 3.5]})
    result = TypeNormalizer().normalize(df)
    assert list(result["price"]) == [1.5, 2.5, 3.5]


def test_integer_columns_unchanged():
    df = pd.DataFrame({"qty": [1, 2, 3]})
    result = TypeNormalizer().normalize(df)
    assert list(result["qty"]) == [1, 2, 3]


def test_normalize_returns_copy_not_mutating_original():
    df = pd.DataFrame({"ts": pd.to_datetime(["2024-01-01"])})
    original_dtype = df["ts"].dtype
    TypeNormalizer().normalize(df)
    assert df["ts"].dtype == original_dtype  # original unchanged


def test_empty_dataframe_handled_gracefully():
    df = pd.DataFrame({"id": pd.Series([], dtype="object"), "val": pd.Series([], dtype="float64")})
    result = TypeNormalizer().normalize(df)
    assert list(result.columns) == ["id", "val"]
    assert len(result) == 0
