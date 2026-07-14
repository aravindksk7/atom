from __future__ import annotations

from datetime import timedelta

import pandas as pd

try:
    import duckdb as _duckdb
    _DUCKDB_AVAILABLE = True
except (ImportError, TypeError):
    _DUCKDB_AVAILABLE = False

from etl_framework.reconciliation.backends.base import BackendCompareResult
from etl_framework.reconciliation.compare_utils import build_mismatch_summary
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
        return self.compare_with_counts(df_source, df_target).mismatches

    def compare_with_counts(self, df_source: pd.DataFrame, df_target: pd.DataFrame) -> BackendCompareResult:
        if not _DUCKDB_AVAILABLE:
            raise ImportError(
                "duckdb is required for DuckDBBackend. "
                "Install it with: pip install duckdb"
            )
        src_marker = self._internal_column_name("__atom_in_src__", df_source, df_target)
        tgt_marker = self._internal_column_name("__atom_in_tgt__", df_source, df_target, extra={src_marker})
        src = df_source.assign(**{src_marker: True})
        tgt = df_target.assign(**{tgt_marker: True})

        con = _duckdb.connect()
        try:
            con.register("_src", src)
            con.register("_tgt", tgt)
            join_sql, key_aliases, value_specs = self._build_join_query(
                df_source,
                df_target,
                src_marker,
                tgt_marker,
            )
            matched_count, mit_count, mis_count, value_count, value_counts_by_column = self._fetch_counts(
                con,
                join_sql,
                value_specs,
                df_source,
            )
            mismatches = self._fetch_mismatch_records(
                con,
                join_sql,
                key_aliases,
                value_specs,
                df_source,
                value_count,
            )
        finally:
            con.close()

        return BackendCompareResult(
            matched_count=matched_count,
            missing_in_target_count=mit_count,
            missing_in_source_count=mis_count,
            value_mismatch_count=value_count,
            mismatches=mismatches,
            mismatch_summary=build_mismatch_summary(
                mit_count,
                mis_count,
                value_count,
                value_counts_by_column,
                {str(col): matched_count for col, _, _ in value_specs},
            ),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_join_query(
        self,
        df_source: pd.DataFrame,
        df_target: pd.DataFrame,
        src_marker: str,
        tgt_marker: str,
    ) -> tuple[str, list[str], list[tuple[str, str, str]]]:
        key_join = " AND ".join(
            f"_src.{self._q(k)} = _tgt.{self._q(k)}" for k in self._key_columns
        )
        key_aliases = [f"__key_{idx}" for idx, _ in enumerate(self._key_columns)]
        key_cols = [
            f"COALESCE(_src.{self._q(k)}, _tgt.{self._q(k)}) AS {self._q(alias)}"
            for k, alias in zip(self._key_columns, key_aliases)
        ]
        value_specs: list[tuple[str, str, str]] = []
        value_cols = [
            c for c in df_source.columns
            if c not in self._key_columns and c in df_target.columns
        ]
        value_selects: list[str] = []
        for idx, col in enumerate(value_cols):
            src_alias = f"__src_{idx}"
            tgt_alias = f"__tgt_{idx}"
            value_specs.append((col, src_alias, tgt_alias))
            value_selects.append(f"_src.{self._q(col)} AS {self._q(src_alias)}")
            value_selects.append(f"_tgt.{self._q(col)} AS {self._q(tgt_alias)}")

        select_parts = (
            key_cols
            + [
                f"COALESCE(_src.{self._q(src_marker)}, FALSE) AS __in_src__",
                f"COALESCE(_tgt.{self._q(tgt_marker)}, FALSE) AS __in_tgt__",
            ]
            + value_selects
        )
        return f"SELECT {', '.join(select_parts)} FROM _src FULL OUTER JOIN _tgt ON {key_join}", key_aliases, value_specs

    def _fetch_counts(
        self,
        con,
        join_sql: str,
        value_specs: list[tuple[str, str, str]],
        df_source: pd.DataFrame,
    ) -> tuple[int, int, int, int, dict[str, int]]:
        value_terms = [
            (col, self._count_term(col, src_alias, tgt_alias, df_source[col]))
            for col, src_alias, tgt_alias in value_specs
        ]
        value_count_sql = " + ".join(term for _, term in value_terms) if value_terms else "0"
        per_column_sql = "".join(
            f", {term} AS {self._q(f'__count_{idx}')}"
            for idx, (_, term) in enumerate(value_terms)
        )
        sql = f"""
            WITH joined AS ({join_sql})
            SELECT
                COALESCE(SUM(CASE WHEN __in_src__ AND __in_tgt__ THEN 1 ELSE 0 END), 0) AS matched_count,
                COALESCE(SUM(CASE WHEN __in_src__ AND NOT __in_tgt__ THEN 1 ELSE 0 END), 0) AS missing_in_target_count,
                COALESCE(SUM(CASE WHEN NOT __in_src__ AND __in_tgt__ THEN 1 ELSE 0 END), 0) AS missing_in_source_count,
                {value_count_sql} AS value_mismatch_count
                {per_column_sql}
            FROM joined
        """
        row = con.execute(sql).fetchone()
        matched_count, mit_count, mis_count, value_count = (
            int(value or 0)
            for value in row[:4]
        )
        value_counts_by_column = {
            str(col): int(row[4 + idx] or 0)
            for idx, (col, _) in enumerate(value_terms)
            if int(row[4 + idx] or 0) > 0
        }
        return matched_count, mit_count, mis_count, value_count, value_counts_by_column

    def _fetch_mismatch_records(
        self,
        con,
        join_sql: str,
        key_aliases: list[str],
        value_specs: list[tuple[str, str, str]],
        df_source: pd.DataFrame,
        value_mismatch_count: int,
    ) -> list[MismatchRecord]:
        mismatches: list[MismatchRecord] = []
        self._append_missing_records(
            con,
            join_sql,
            key_aliases,
            "__in_src__ AND NOT __in_tgt__",
            "missing_in_target",
            "present",
            "missing",
            mismatches,
        )
        self._append_missing_records(
            con,
            join_sql,
            key_aliases,
            "NOT __in_src__ AND __in_tgt__",
            "missing_in_source",
            "missing",
            "present",
            mismatches,
        )
        if len(mismatches) >= self._mismatch_row_limit or value_mismatch_count == 0:
            return mismatches

        key_select = ", ".join(self._q(alias) for alias in key_aliases)
        key_count = len(key_aliases)
        for col, src_alias, tgt_alias in value_specs:
            remaining = self._mismatch_row_limit - len(mismatches)
            if remaining <= 0:
                break
            condition = self._mismatch_condition(src_alias, tgt_alias, df_source[col])
            sql = f"""
                WITH joined AS ({join_sql})
                SELECT {key_select}, {self._q(src_alias)}, {self._q(tgt_alias)}
                FROM joined
                WHERE __in_src__ AND __in_tgt__ AND {condition}
                LIMIT {remaining}
            """
            for row in con.execute(sql).fetchall():
                mismatches.append(MismatchRecord(
                    key_values=dict(zip(self._key_columns, row[:key_count])),
                    column_name=col,
                    source_value=row[key_count],
                    target_value=row[key_count + 1],
                    mismatch_type="value_diff",
                ))
        return mismatches

    def _append_missing_records(
        self,
        con,
        join_sql: str,
        key_aliases: list[str],
        condition: str,
        mismatch_type: str,
        source_value: str,
        target_value: str,
        mismatches: list[MismatchRecord],
    ) -> None:
        remaining = self._mismatch_row_limit - len(mismatches)
        if remaining <= 0:
            return
        key_select = ", ".join(self._q(alias) for alias in key_aliases)
        sql = f"""
            WITH joined AS ({join_sql})
            SELECT {key_select}
            FROM joined
            WHERE {condition}
            LIMIT {remaining}
        """
        for row in con.execute(sql).fetchall():
            mismatches.append(MismatchRecord(
                key_values=dict(zip(self._key_columns, row)),
                column_name="<row>",
                source_value=source_value,
                target_value=target_value,
                mismatch_type=mismatch_type,
            ))

    def _count_term(self, col: str, src_alias: str, tgt_alias: str, source_series: pd.Series) -> str:
        condition = self._mismatch_condition(src_alias, tgt_alias, source_series)
        return (
            "COALESCE(SUM(CASE WHEN __in_src__ AND __in_tgt__ "
            f"AND {condition} THEN 1 ELSE 0 END), 0)"
        )

    def _mismatch_condition(self, src_alias: str, tgt_alias: str, source_series: pd.Series) -> str:
        src = self._q(src_alias)
        tgt = self._q(tgt_alias)
        both_null = f"({src} IS NULL AND {tgt} IS NULL)"
        neither_null = f"({src} IS NOT NULL AND {tgt} IS NOT NULL)"

        if pd.api.types.is_datetime64_any_dtype(source_series) and self._datetime_tolerance.total_seconds() > 0:
            tol_us = int(self._datetime_tolerance.total_seconds() * 1_000_000)
            equal = f"ABS(date_diff('microsecond', {src}, {tgt})) <= {tol_us}"
        elif pd.api.types.is_float_dtype(source_series):
            tol = self._column_tolerances.get(str(source_series.name), self._float_tolerance)
            equal = f"ABS(CAST({src} AS DOUBLE) - CAST({tgt} AS DOUBLE)) <= {tol}"
        else:
            equal = f"{src} = {tgt}"

        null_match = both_null if self._null_equals_null else "FALSE"
        return f"NOT ({null_match} OR ({neither_null} AND ({equal})))"

    @staticmethod
    def _q(identifier: object) -> str:
        return '"' + str(identifier).replace('"', '""') + '"'

    @staticmethod
    def _internal_column_name(
        base: str,
        df_source: pd.DataFrame,
        df_target: pd.DataFrame,
        extra: set[str] | None = None,
    ) -> str:
        existing = {str(col) for col in df_source.columns} | {str(col) for col in df_target.columns} | (extra or set())
        name = base
        idx = 2
        while name in existing:
            name = f"{base}_{idx}"
            idx += 1
        return name
