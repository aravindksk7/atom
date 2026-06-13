import re
import logging
from decimal import Decimal

import numpy as np
import pandas as pd

logger = logging.getLogger("etl_framework.reconciliation.normalizer")

_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class TypeNormalizer:
    """Normalises SQL Server type edge cases before DataFrame comparison."""

    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        for col in df.columns:
            df[col] = self._normalize_column(df[col], col)
        return df

    def _normalize_column(self, series: pd.Series, col_name: str) -> pd.Series:
        # datetime: unify all to UTC
        if pd.api.types.is_datetime64_any_dtype(series):
            dtype = series.dtype
            if hasattr(dtype, "tz") and dtype.tz is not None:
                series = series.dt.tz_convert("UTC")
            else:
                series = series.dt.tz_localize("UTC")
            logger.debug("Normalized datetime column '%s' to UTC", col_name)
            return series

        # Decimal objects → float64
        non_null = series.dropna()
        if len(non_null) > 0 and isinstance(non_null.iloc[0], Decimal):
            series = series.apply(
                lambda x: float(x) if x is not None and not _is_nan(x) else np.nan
            ).astype(np.float64)
            logger.debug("Normalized Decimal column '%s' to float64", col_name)
            return series

        # String/object dtype: check for UUIDs and normalise to uppercase
        # pandas 3.x uses StringDtype ('str') instead of object for string columns;
        # is_object_dtype returns False there, so we check is_string_dtype as well.
        if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
            sample = series.dropna()
            if len(sample) > 0 and isinstance(sample.iloc[0], str) and _looks_like_uuid(sample.iloc[0]):
                series = series.str.upper()
                logger.debug("Normalized UUID column '%s' to uppercase", col_name)
            return series

        return series


def _is_nan(val: object) -> bool:
    try:
        return isinstance(val, float) and np.isnan(val)
    except (TypeError, ValueError):
        return False


def _looks_like_uuid(s: str) -> bool:
    return bool(_UUID_PATTERN.match(s))
