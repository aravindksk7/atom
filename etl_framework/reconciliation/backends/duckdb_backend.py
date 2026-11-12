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

    ``compare_relations`` runs the same comparison on relations that already
    live in a DuckDB connection, which is how the streaming and pushdown
    reconciliation strategies compare data that never fits in memory.

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
            return self.compare_relations(
                con,
                "_src",
                "_tgt",
                source_columns=list(df_source.columns),
                target_columns=list(df_target.columns),
                src_marker=src_marker,
                tgt_marker=tgt_marker,
                kinds={str(col): series_kind(df_source[col]) for col in df_source.columns},
            )
        finally:
            con.close()

    def compare_relations(
        self,
        con,
        src_rel: str,
        tgt_rel: str,
        *,
        source_columns: list,
        target_columns: list,
        src_marker: str,
        tgt_marker: str,
        kinds: dict[str, str],
    ) -> BackendCompareResult:
        """Compare two relations that already exist in ``con``.

        Each relation must carry its marker column (always TRUE). ``kinds``
        maps a column name to ``"float"``, ``"datetime"`` or ``"other"`` and
        decides how its values are compared.
        """
        join_sql, key_aliases, value_specs = self._build_join_query(
            source_columns,
            target_columns,
            src_marker,
            tgt_marker,
            src_rel,
            tgt_rel,
        )
        matched_count, mit_count, mis_count, value_count, value_counts_by_column = self._fetch_counts(
            con,
            join_sql,
            value_specs,
            kinds,
        )
        mismatches = self._fetch_mismatch_records(
            con,
            join_sql,
            key_aliases,
            value_specs,
            kinds,
            value_count,
        )
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
        source_columns: list,
        target_columns: list,
        src_marker: str,
        tgt_marker: str,
        src_rel: str = "_src",
        tgt_rel: str = "_tgt",
    ) -> tuple[str, list[str], list[tuple[str, str, str]]]:
        s = self._q(src_rel)
        t = self._q(tgt_rel)
        key_join = " AND ".join(
            f"{s}.{self._q(k)} = {t}.{self._q(k)}" for k in self._key_columns
        )
        key_aliases = [f"__key_{idx}" for idx, _ in enumerate(self._key_columns)]
        key_cols = [
            f"COALESCE({s}.{self._q(k)}, {t}.{self._q(k)}) AS {self._q(alias)}"
            for k, alias in zip(self._key_columns, key_aliases)
        ]
        value_specs: list[tuple[str, str, str]] = []
        target_set = set(target_columns)
        markers = {src_marker, tgt_marker}
        value_cols = [
            c for c in source_columns
            if c not in self._key_columns and c in target_set and c not in markers
        ]
        value_selects: list[str] = []
        for idx, col in enumerate(value_cols):
            src_alias = f"__src_{idx}"
            tgt_alias = f"__tgt_{idx}"
            value_specs.append((col, src_alias, tgt_alias))
            value_selects.append(f"{s}.{self._q(col)} AS {self._q(src_alias)}")
            value_selects.append(f"{t}.{self._q(col)} AS {self._q(tgt_alias)}")

        select_parts = (
            key_cols
            + [
                f"COALESCE({s}.{self._q(src_marker)}, FALSE) AS __in_src__",
                f"COALESCE({t}.{self._q(tgt_marker)}, FALSE) AS __in_tgt__",
            ]
            + value_selects
        )
        return f"SELECT {', '.join(select_parts)} FROM {s} FULL OUTER JOIN {t} ON {key_join}", key_aliases, value_specs

    def _fetch_counts(
        self,
        con,
        join_sql: str,
        value_specs: list[tuple[str, str, str]],
        kinds: dict[str, str],
    ) -> tuple[int, int, int, int, dict[str, int]]:
        value_terms = [
            (col, self._count_term(col, src_alias, tgt_alias, kinds.get(str(col), "other")))
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
        kinds: dict[str, str],
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
            condition = self._mismatch_condition(src_alias, tgt_alias, kinds.get(str(col), "other"), col)
            sql = f"""
                WITH joined AS ({join_sql})
                SELECT {key_select}, {self._q(src_alias)}, {self._q(tgt_alias)}
                FROM joined
                WHERE __in_src__ AND __in_tgt__ AND {condition}
                LIMIT {remaining}
            """
            for row in self._rows(con, sql):
                source_value, target_value = row[key_count], row[key_count + 1]
                delta, relative_delta = _deltas(source_value, target_value)
                mismatches.append(MismatchRecord(
                    key_values=dict(zip(self._key_columns, row[:key_count])),
                    column_name=col,
                    source_value=source_value,
                    target_value=target_value,
                    mismatch_type="value_diff",
                    delta=delta,
                    relative_delta=relative_delta,
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
        for row in self._rows(con, sql):
            mismatches.append(MismatchRecord(
                key_values=dict(zip(self._key_columns, row)),
                column_name="<row>",
                source_value=source_value,
                target_value=target_value,
                mismatch_type=mismatch_type,
            ))

    def _count_term(self, col: str, src_alias: str, tgt_alias: str, kind: str) -> str:
        condition = self._mismatch_condition(src_alias, tgt_alias, kind, col)
        return (
            "COALESCE(SUM(CASE WHEN __in_src__ AND __in_tgt__ "
            f"AND {condition} THEN 1 ELSE 0 END), 0)"
        )

    def _mismatch_condition(self, src_alias: str, tgt_alias: str, kind: str, col: object) -> str:
        src = self._q(src_alias)
        tgt = self._q(tgt_alias)
        both_null = f"({src} IS NULL AND {tgt} IS NULL)"
        neither_null = f"({src} IS NOT NULL AND {tgt} IS NOT NULL)"

        if kind == "datetime" and self._datetime_tolerance.total_seconds() > 0:
            tol_us = int(self._datetime_tolerance.total_seconds() * 1_000_000)
            equal = f"ABS(date_diff('microsecond', {src}, {tgt})) <= {tol_us}"
        elif kind == "float":
            tol = self._column_tolerances.get(str(col), self._float_tolerance)
            equal = f"ABS(CAST({src} AS DOUBLE) - CAST({tgt} AS DOUBLE)) <= {tol}"
        else:
            equal = f"{src} = {tgt}"

        null_match = both_null if self._null_equals_null else "FALSE"
        return f"NOT ({null_match} OR ({neither_null} AND ({equal})))"

    @staticmethod
    def _rows(con, sql: str) -> list[tuple]:
        """Fetch through pandas: DuckDB's ``fetchall`` needs pytz for
        TIMESTAMP WITH TIME ZONE values, which this project does not install."""
        frame = con.execute(sql).df()
        if frame.empty:
            return []
        frame = frame.astype(object).where(frame.notna(), None)
        return list(frame.itertuples(index=False, name=None))

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


def series_kind(series: pd.Series) -> str:
    """How a pandas column is compared: ``float``, ``datetime`` or ``other``."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_float_dtype(series):
        return "float"
    return "other"


def duckdb_type_kind(type_name: str) -> str:
    """How a DuckDB column type is compared; the counterpart of ``series_kind``."""
    upper = type_name.upper()
    if upper.startswith(("TIMESTAMP", "DATE")):
        return "datetime"
    if upper.startswith(("DOUBLE", "FLOAT", "REAL", "DECIMAL")):
        return "float"
    return "other"


def _deltas(source_value: object, target_value: object) -> tuple[float | None, float | None]:
    """target - source and its ratio to source, for numeric values only."""
    if isinstance(source_value, (bool, str)) or isinstance(target_value, (bool, str)):
        return None, None
    try:
        source = float(source_value)  # type: ignore[arg-type]
        delta = float(target_value) - source  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None, None
    return delta, (delta / source if source != 0 else None)
