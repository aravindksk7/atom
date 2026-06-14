from __future__ import annotations
import math
import numpy as np
import pandas as pd
from etl_framework.reconciliation.models import MismatchRecord


class PandasBackend:
    def __init__(
        self,
        key_columns: list[str],
        float_tolerance: float = 1e-9,
        null_equals_null: bool = True,
        mismatch_row_limit: int = 1000,
    ) -> None:
        self._key_columns = key_columns
        self._float_tolerance = float_tolerance
        self._null_equals_null = null_equals_null
        self._mismatch_row_limit = mismatch_row_limit

    def compare(self, df_source: pd.DataFrame, df_target: pd.DataFrame) -> list[MismatchRecord]:
        mismatches: list[MismatchRecord] = []
        merged = pd.merge(
            df_source, df_target,
            on=self._key_columns, how="outer", indicator=True, suffixes=("_src", "_tgt")
        )
        for _, row in merged.iterrows():
            if len(mismatches) >= self._mismatch_row_limit:
                break
            indicator = row["_merge"]
            key_vals = {k: row[k] for k in self._key_columns}
            if indicator == "left_only":
                mismatches.append(MismatchRecord(
                    key_values=key_vals, column_name="<row>",
                    source_value="present", target_value="missing",
                    mismatch_type="missing_in_target",
                ))
            elif indicator == "right_only":
                mismatches.append(MismatchRecord(
                    key_values=key_vals, column_name="<row>",
                    source_value="missing", target_value="present",
                    mismatch_type="missing_in_source",
                ))
            else:
                value_cols = [c for c in df_source.columns if c not in self._key_columns]
                for col in value_cols:
                    src_col = f"{col}_src" if f"{col}_src" in merged.columns else col
                    tgt_col = f"{col}_tgt" if f"{col}_tgt" in merged.columns else col
                    a, b = row.get(src_col), row.get(tgt_col)
                    if not self._values_match(a, b, pd.api.types.is_float_dtype(type(a))):
                        mismatches.append(MismatchRecord(
                            key_values=key_vals, column_name=col,
                            source_value=a, target_value=b,
                            mismatch_type="value_diff",
                        ))
                        if len(mismatches) >= self._mismatch_row_limit:
                            break
        return mismatches

    def _values_match(self, a, b, is_float: bool) -> bool:
        a_na = a is None or (isinstance(a, float) and math.isnan(a))
        b_na = b is None or (isinstance(b, float) and math.isnan(b))
        try:
            a_na = a_na or pd.isna(a)
            b_na = b_na or pd.isna(b)
        except (TypeError, ValueError):
            pass
        if a_na and b_na:
            return self._null_equals_null
        if a_na or b_na:
            return False
        if is_float:
            return bool(np.isclose(float(a), float(b), rtol=0, atol=self._float_tolerance))
        return a == b
