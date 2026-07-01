from __future__ import annotations

from datetime import timedelta

import pandas as pd

try:
    import duckdb as _duckdb
    _DUCKDB_AVAILABLE = True
except (ImportError, TypeError):
    _DUCKDB_AVAILABLE = False

from etl_framework.reconciliation.models import MismatchRecord


class DuckDBBackend:
    """SQL-based comparison backend powered by DuckDB.

    Registers the source and target DataFrames as in-process DuckDB views and
    runs a single FULL OUTER JOIN query to detect mismatches.  This is
    significantly faster than ``PandasBackend`` for wide tables (100+ columns)
    or large row counts because the join and equality checks run inside DuckDB's
    vectorised C++ engine.

    Requires: ``pip install duckdb``
    """

    def __init__(
        self,
        key_columns: list[str],
        float_tolerance: float = 1e-9,
        null_equals_null: bool = True,
        mismatch_row_limit: int = 1000,
        column_tolerances: dict[str, float] | None = None,
        datetime_tolerance_seconds: float = 0.0,
    ) -> None:
        self._key_columns = key_columns
        self._float_tolerance = float_tolerance
        self._null_equals_null = null_equals_null
        self._mismatch_row_limit = mismatch_row_limit
        self._column_tolerances: dict[str, float] = column_tolerances or {}
        self._datetime_tolerance = timedelta(seconds=datetime_tolerance_seconds)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compare(self, df_source: pd.DataFrame, df_target: pd.DataFrame) -> list[MismatchRecord]:
        if not _DUCKDB_AVAILABLE:
            raise ImportError(
                "duckdb is required for DuckDBBackend. "
                "Install it with: pip install duckdb"
            )
        con = _duckdb.connect()
        try:
            con.register("_src", df_source)
            con.register("_tgt", df_target)
            merged = con.execute(self._build_join_query(df_source)).df()
        finally:
            con.close()

        return self._extract_mismatches(merged, df_source)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_join_query(self, df_source: pd.DataFrame) -> str:
        key_join = " AND ".join(
            f'COALESCE(_src."{k}" = _tgt."{k}", FALSE)' for k in self._key_columns
        )
        src_cols = ", ".join(f'_src."{c}" AS "{c}_src"' for c in df_source.columns)
        tgt_cols = ", ".join(f'_tgt."{c}" AS "{c}_tgt"' for c in df_source.columns)
        key_cols = ", ".join(
            f'COALESCE(_src."{k}", _tgt."{k}") AS "{k}"' for k in self._key_columns
        )
        return (
            f"SELECT {key_cols}, "
            f"(_src.\"{self._key_columns[0]}\" IS NOT NULL) AS __in_src__, "
            f"(_tgt.\"{self._key_columns[0]}\" IS NOT NULL) AS __in_tgt__, "
            f"{src_cols}, {tgt_cols} "
            f"FROM _src FULL OUTER JOIN _tgt ON {key_join}"
        )

    def _extract_mismatches(self, merged: pd.DataFrame, df_source: pd.DataFrame) -> list[MismatchRecord]:
        mismatches: list[MismatchRecord] = []
        value_cols = [c for c in df_source.columns if c not in self._key_columns]

        for row in merged.itertuples(index=False):
            if len(mismatches) >= self._mismatch_row_limit:
                break
            rd = row._asdict()
            in_src = rd.get("__in_src__", False)
            in_tgt = rd.get("__in_tgt__", False)
            key_vals = {k: rd[k] for k in self._key_columns}

            if in_src and not in_tgt:
                mismatches.append(MismatchRecord(
                    key_values=key_vals, column_name="<row>",
                    source_value="present", target_value="missing",
                    mismatch_type="missing_in_target",
                ))
            elif in_tgt and not in_src:
                mismatches.append(MismatchRecord(
                    key_values=key_vals, column_name="<row>",
                    source_value="missing", target_value="present",
                    mismatch_type="missing_in_source",
                ))
            else:
                for col in value_cols:
                    sv = rd.get(f"{col}_src")
                    tv = rd.get(f"{col}_tgt")
                    tol = self._column_tolerances.get(col, self._float_tolerance)
                    if not self._values_match(sv, tv, tol=tol):
                        mismatches.append(MismatchRecord(
                            key_values=key_vals, column_name=col,
                            source_value=sv, target_value=tv,
                            mismatch_type="value_diff",
                        ))
                        if len(mismatches) >= self._mismatch_row_limit:
                            break
        return mismatches

    def _values_match(self, a, b, tol: float) -> bool:
        import math
        try:
            a_na = a is None or (isinstance(a, float) and math.isnan(a)) or bool(pd.isna(a))
        except (TypeError, ValueError):
            a_na = a is None
        try:
            b_na = b is None or (isinstance(b, float) and math.isnan(b)) or bool(pd.isna(b))
        except (TypeError, ValueError):
            b_na = b is None
        if a_na and b_na:
            return self._null_equals_null
        if a_na or b_na:
            return False
        # datetime tolerance
        if self._datetime_tolerance.total_seconds() > 0:
            try:
                diff = abs((pd.Timestamp(a) - pd.Timestamp(b)).total_seconds())
                return diff <= self._datetime_tolerance.total_seconds()
            except Exception:
                pass
        # float tolerance
        try:
            import numpy as np
            return bool(np.isclose(float(a), float(b), rtol=0, atol=tol))
        except (TypeError, ValueError):
            pass
        return a == b
