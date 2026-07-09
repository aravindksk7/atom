from __future__ import annotations

import dataclasses
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from etl_framework.exceptions import SchemaValidationError
from etl_framework.reconciliation.backends.base import ComparisonBackend
from etl_framework.reconciliation.chunker import build_chunk_query, build_hash_query, hashes_match
from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.reconciliation.normalizer import TypeNormalizer
from etl_framework.runner.state import TestStatus
from etl_framework.utils.tracing import span as _span

logger = logging.getLogger("etl_framework.reconciliation.engine")


def _column_key(column: object) -> str:
    return "".join(ch for ch in str(column).lower() if ch.isalnum())


class ReconciliationEngine:
    def __init__(
        self,
        source_engine,
        target_engine,
        key_columns: list[str],
        exclude_columns: list[str] | None = None,
        float_tolerance: float = 1e-9,
        mismatch_row_limit: int = 1000,
        schema_mismatch_policy: str = "warn",   # "warn" | "error"
        null_equals_null: bool = True,
        chunk_size: int = 0,
        use_hash_precheck: bool = True,
        backend: ComparisonBackend | None = None,
        column_tolerances: dict[str, float] | None = None,
        datetime_tolerance_seconds: float = 0.0,
        case_insensitive_columns: list[str] | None = None,
        whitespace_normalize_columns: list[str] | None = None,
        change_tracking_column: str | None = None,
        since: datetime | None = None,
        parallel_columns: bool = False,
        parallel_workers: int = 4,
        segment_columns: list[str] | None = None,
    ):
        self._source_engine = source_engine
        self._target_engine = target_engine
        self._key_columns = key_columns
        self._exclude_columns = set(exclude_columns or [])
        self._exclude_column_keys = {_column_key(col) for col in self._exclude_columns}
        self._float_tolerance = float_tolerance
        self._mismatch_row_limit = mismatch_row_limit
        self._schema_mismatch_policy = schema_mismatch_policy
        self._null_equals_null = null_equals_null
        self._chunk_size = chunk_size
        self._use_hash_precheck = use_hash_precheck
        self._backend = backend
        self._column_tolerances: dict[str, float] = column_tolerances or {}
        self._datetime_tolerance = timedelta(seconds=datetime_tolerance_seconds)
        self._normalizer = TypeNormalizer()
        self._case_insensitive_columns: frozenset[str] = frozenset(case_insensitive_columns or [])
        self._whitespace_normalize_columns: frozenset[str] = frozenset(whitespace_normalize_columns or [])
        self._change_tracking_column: str | None = change_tracking_column
        self._since: datetime | None = since
        self._parallel_columns: bool = parallel_columns
        self._parallel_workers: int = parallel_workers
        self._segment_columns: list[str] = segment_columns or []

    def reconcile(
        self,
        query: str,
        query_name: str,
        params: dict | None = None,
        max_duration_seconds: float | None = None,
    ) -> ReconciliationResult:
        with _span("reconciliation.reconcile", attributes={"query_name": query_name}):
            t0 = time.monotonic()
            executed_at = datetime.now(timezone.utc)

            if self._chunk_size > 0:
                # Hash pre-check: if enabled, run a lightweight hash query first;
                # if hashes match, return an early PASSED result.
                if self._use_hash_precheck:
                    hash_q_src = build_hash_query(query, self._key_columns)
                    hash_q_tgt = build_hash_query(query, self._key_columns)
                    hash_src = self._normalizer.normalize(
                        self._source_engine.execute_query(hash_q_src, params)
                    )
                    hash_tgt = self._normalizer.normalize(
                        self._target_engine.execute_query(hash_q_tgt, params)
                    )
                    if hashes_match(hash_src, hash_tgt):
                        row_count = len(hash_src)
                        logger.info(
                            "Reconciliation '%s': hash pre-check matched — skipping full compare.",
                            query_name,
                        )
                        # schema validation skipped in hash pre-check; full compare catches it
                        early_result = ReconciliationResult(
                            query_name=query_name,
                            source_env=self._source_engine._env.name,
                            target_env=self._target_engine._env.name,
                            source_row_count=row_count,
                            target_row_count=row_count,
                            matched_count=row_count,
                            missing_in_target_count=0,
                            missing_in_source_count=0,
                            value_mismatch_count=0,
                            mismatches=[],
                            status=TestStatus.PASSED,
                            executed_at=executed_at,
                            duration_seconds=time.monotonic() - t0,
                        schema_diff=None,
                        mismatch_summary=self._build_mismatch_summary(0, 0, 0),
                        )
                        return self._apply_slo(early_result, max_duration_seconds)

                # Chunked loading: paginate through source and target in chunks.
                chunks_src, chunks_tgt = [], []
                offset = 0
                while True:
                    q_src = build_chunk_query(query, self._key_columns, offset, self._chunk_size)
                    q_tgt = build_chunk_query(query, self._key_columns, offset, self._chunk_size)
                    chunk_src = self._normalizer.normalize(
                        self._source_engine.execute_query(q_src, params)
                    )
                    chunk_tgt = self._normalizer.normalize(
                        self._target_engine.execute_query(q_tgt, params)
                    )
                    if chunk_src.empty and chunk_tgt.empty:
                        break
                    if not chunk_src.empty:
                        chunks_src.append(chunk_src)
                    if not chunk_tgt.empty:
                        chunks_tgt.append(chunk_tgt)
                    offset += self._chunk_size
                    if len(chunk_src) < self._chunk_size or len(chunk_tgt) < self._chunk_size:
                        break
                df_source = pd.concat(chunks_src, ignore_index=True) if chunks_src else pd.DataFrame()
                df_target = pd.concat(chunks_tgt, ignore_index=True) if chunks_tgt else pd.DataFrame()
            else:
                df_source = self._normalizer.normalize(
                    self._source_engine.execute_query(query, params)
                )
                df_target = self._normalizer.normalize(
                    self._target_engine.execute_query(query, params)
                )

            df_source, df_target = self._filter_incremental(df_source, df_target)
            schema_diff = self._validate_schemas(df_source, df_target, query_name)
            df_source_norm, df_target_norm = self._align_columns(df_source, df_target, schema_diff)

            df_source_norm, df_target_norm = self._preprocess_for_compare(df_source_norm, df_target_norm)

            if self._backend is not None:
                compare_with_counts = getattr(self._backend, "compare_with_counts", None)
                if callable(compare_with_counts):
                    backend_result = compare_with_counts(df_source_norm, df_target_norm)
                    mismatch_list = backend_result.mismatches
                    matched_count = backend_result.matched_count
                    mit_count = backend_result.missing_in_target_count
                    mis_count = backend_result.missing_in_source_count
                    value_count = backend_result.value_mismatch_count
                    mismatch_summary = backend_result.mismatch_summary
                else:
                    mismatch_list = self._backend.compare(df_source_norm, df_target_norm)
                    matched_count, mit_count, mis_count, value_count = self._count_mismatches(
                        df_source_norm,
                        df_target_norm,
                    )
                    _, _, value_counts_by_column = self._find_value_mismatches(
                        pd.merge(
                            df_source_norm,
                            df_target_norm,
                            on=self._key_columns,
                            how="outer",
                            indicator=True,
                            suffixes=("_src", "_tgt"),
                        ).query("_merge == 'both'"),
                        df_source_norm,
                    )
                    compared_rows_by_column = {
                        str(col): int(matched_count)
                        for col in df_source_norm.columns
                        if col not in self._key_columns and not self._is_excluded_column(col)
                    }
                    mismatch_summary = self._build_mismatch_summary(
                        mit_count,
                        mis_count,
                        value_count,
                        value_counts_by_column,
                        compared_rows_by_column,
                    )
                total_issues = mit_count + mis_count + value_count
                result = ReconciliationResult(
                    query_name=query_name,
                    source_env=getattr(getattr(self._source_engine, "_env", None), "name", "source"),
                    target_env=getattr(getattr(self._target_engine, "_env", None), "name", "target"),
                    source_row_count=len(df_source),
                    target_row_count=len(df_target),
                    matched_count=matched_count,
                    missing_in_target_count=mit_count,
                    missing_in_source_count=mis_count,
                    value_mismatch_count=value_count,
                    mismatches=mismatch_list,
                    status=TestStatus.PASSED if total_issues == 0 else TestStatus.FAILED,
                    executed_at=executed_at,
                    duration_seconds=0.0,
                    schema_diff=schema_diff,
                    mismatch_summary=mismatch_summary or self._build_mismatch_summary(
                        mit_count,
                        mis_count,
                        value_count,
                    ),
                )
            else:
                result = self._compare(df_source_norm, df_target_norm, query_name, executed_at,
                                       schema_diff)
            if self._segment_columns and result.mismatches:
                self._attach_segment_values(result.mismatches, df_source_norm, df_target_norm)
            result = dataclasses.replace(result, duration_seconds=time.monotonic() - t0)
            return self._apply_slo(result, max_duration_seconds)

    # ------------------------------------------------------------------
    # Incremental filtering
    # ------------------------------------------------------------------

    def _filter_incremental(
        self,
        df_source: pd.DataFrame,
        df_target: pd.DataFrame,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Keep only rows modified at or after ``self._since``.

        Filters both sides by ``change_tracking_column >= since`` so the
        comparison only covers recently changed data.  Rows where the
        change-tracking column is NULL are always included (conservative).
        """
        col = self._change_tracking_column
        since = self._since
        if col is None or since is None:
            return df_source, df_target

        def _apply(df: pd.DataFrame) -> pd.DataFrame:
            if col not in df.columns:
                logger.warning(
                    "change_tracking_column '%s' not in DataFrame; incremental filter skipped.", col
                )
                return df
            dt_col = pd.to_datetime(df[col], errors="coerce", utc=True)
            since_utc = pd.Timestamp(since, tz="UTC") if since.tzinfo is None else pd.Timestamp(since).tz_convert("UTC")
            mask = dt_col.isna() | (dt_col >= since_utc)
            return df[mask].reset_index(drop=True)

        src_filtered = _apply(df_source)
        tgt_filtered = _apply(df_target)
        logger.info(
            "Incremental filter since %s: source %d→%d rows, target %d→%d rows",
            since.isoformat(), len(df_source), len(src_filtered),
            len(df_target), len(tgt_filtered),
        )
        return src_filtered, tgt_filtered

    # ------------------------------------------------------------------
    # Pre-compare normalisation (case / whitespace)
    # ------------------------------------------------------------------

    def _preprocess_for_compare(
        self,
        df_source: pd.DataFrame,
        df_target: pd.DataFrame,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        if not self._case_insensitive_columns and not self._whitespace_normalize_columns:
            return df_source, df_target
        df_source = df_source.copy()
        df_target = df_target.copy()
        for col in self._case_insensitive_columns | self._whitespace_normalize_columns:
            for df in (df_source, df_target):
                if col not in df.columns:
                    continue
                if pd.api.types.is_object_dtype(df[col]) or pd.api.types.is_string_dtype(df[col]):
                    if col in self._whitespace_normalize_columns:
                        df[col] = df[col].str.strip().str.replace(r"\s+", " ", regex=True)
                    if col in self._case_insensitive_columns:
                        df[col] = df[col].str.lower()
        return df_source, df_target

    # ------------------------------------------------------------------
    # Schema helpers
    # ------------------------------------------------------------------

    def _is_excluded_column(self, column: object) -> bool:
        return _column_key(column) in self._exclude_column_keys

    def _validate_schemas(
        self,
        df_source: pd.DataFrame,
        df_target: pd.DataFrame,
        query_name: str,
    ) -> dict[str, list[str]] | None:
        src_cols = {col for col in df_source.columns if not self._is_excluded_column(col)}
        tgt_cols = {col for col in df_target.columns if not self._is_excluded_column(col)}
        missing_in_target = sorted(src_cols - tgt_cols)
        extra_in_target = sorted(tgt_cols - src_cols)

        if not missing_in_target and not extra_in_target:
            return None

        diff = {
            "missing_in_target": missing_in_target,
            "extra_in_target": extra_in_target,
        }

        if self._schema_mismatch_policy == "error":
            raise SchemaValidationError(query_name, missing_in_target, extra_in_target)

        logger.warning(
            "Schema mismatch in '%s': missing_in_target=%s, extra_in_target=%s. "
            "Comparing on common columns only.",
            query_name, missing_in_target, extra_in_target,
        )
        return diff

    def _align_columns(
        self,
        df_source: pd.DataFrame,
        df_target: pd.DataFrame,
        schema_diff: dict | None,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        if schema_diff:
            common = [
                c for c in df_source.columns
                if c in df_target.columns and not self._is_excluded_column(c)
            ]
            df_source = df_source[common]
            df_target = df_target[common]
        else:
            drop_src = [c for c in df_source.columns if self._is_excluded_column(c)]
            drop_tgt = [c for c in df_target.columns if self._is_excluded_column(c)]
            df_source = df_source.drop(columns=drop_src)
            df_target = df_target.drop(columns=drop_tgt)
        return df_source, df_target

    # ------------------------------------------------------------------
    # Comparison core
    # ------------------------------------------------------------------

    def _compare(
        self,
        df_source: pd.DataFrame,
        df_target: pd.DataFrame,
        query_name: str,
        executed_at: datetime,
        schema_diff: dict | None,
    ) -> ReconciliationResult:
        src_dups = df_source.duplicated(subset=self._key_columns).sum()
        tgt_dups = df_target.duplicated(subset=self._key_columns).sum()
        if src_dups > 0:
            logger.warning(
                "Query '%s': source DataFrame has %d duplicate key rows — "
                "merge counts will be inflated. Deduplicate the query.",
                query_name, src_dups,
            )
        if tgt_dups > 0:
            logger.warning(
                "Query '%s': target DataFrame has %d duplicate key rows — "
                "merge counts will be inflated. Deduplicate the query.",
                query_name, tgt_dups,
            )

        src_count = len(df_source)
        tgt_count = len(df_target)

        merged = pd.merge(
            df_source,
            df_target,
            on=self._key_columns,
            how="outer",
            indicator=True,
            suffixes=("_src", "_tgt"),
        )

        missing_in_target = merged[merged["_merge"] == "left_only"]
        missing_in_source = merged[merged["_merge"] == "right_only"]
        both = merged[merged["_merge"] == "both"]

        mit_count = len(missing_in_target)
        mis_count = len(missing_in_source)

        mit_records = self._rows_to_mismatch_records(
            missing_in_target, "missing_in_target"
        )
        mis_records = self._rows_to_mismatch_records(
            missing_in_source, "missing_in_source"
        )
        value_records, value_count, value_counts_by_column = self._find_value_mismatches(
            both,
            df_source,
        )
        compared_rows_by_column = {
            str(col): int(len(both))
            for col in df_source.columns
            if col not in self._key_columns and not self._is_excluded_column(col)
        }

        all_mismatches = (mit_records + mis_records + value_records)[: self._mismatch_row_limit]
        total_issues = mit_count + mis_count + value_count

        status = TestStatus.PASSED if total_issues == 0 else TestStatus.FAILED

        logger.info(
            "Reconciliation '%s': src=%d tgt=%d matched=%d mit=%d mis=%d vmc=%d",
            query_name, src_count, tgt_count, len(both),
            mit_count, mis_count, value_count,
        )

        return ReconciliationResult(
            query_name=query_name,
            source_env=self._source_engine._env.name,
            target_env=self._target_engine._env.name,
            source_row_count=src_count,
            target_row_count=tgt_count,
            matched_count=len(both),
            missing_in_target_count=mit_count,
            missing_in_source_count=mis_count,
            value_mismatch_count=value_count,
            mismatches=all_mismatches,
            status=status,
            executed_at=executed_at,
            duration_seconds=0.0,
            schema_diff=schema_diff,
            mismatch_summary=self._build_mismatch_summary(
                mit_count,
                mis_count,
                value_count,
                value_counts_by_column,
                compared_rows_by_column,
            ),
        )

    @staticmethod
    def _build_mismatch_summary(
        missing_in_target_count: int,
        missing_in_source_count: int,
        value_mismatch_count: int,
        value_counts_by_column: dict[str, int] | None = None,
        compared_rows_by_column: dict[str, int] | None = None,
    ) -> dict[str, dict[str, int]]:
        by_column = {
            str(column): int(count)
            for column, count in (value_counts_by_column or {}).items()
            if int(count) > 0
        }
        compared = {
            str(column): int(count)
            for column, count in (compared_rows_by_column or {}).items()
            if int(count) >= 0
        }
        missing_row_count = int(missing_in_target_count or 0) + int(missing_in_source_count or 0)
        if missing_row_count > 0:
            by_column["<row>"] = by_column.get("<row>", 0) + missing_row_count
            compared["<row>"] = compared.get("<row>", 0) + missing_row_count
        return {
            "by_column": by_column,
            "compared_rows_by_column": compared,
            "by_type": {
                "value_diff": int(value_mismatch_count or 0),
                "missing_in_target": int(missing_in_target_count or 0),
                "missing_in_source": int(missing_in_source_count or 0),
            },
        }

    def _rows_to_mismatch_records(
        self,
        df: pd.DataFrame,
        mismatch_type: str,
    ) -> list[MismatchRecord]:
        records = []
        for _, row in df.head(self._mismatch_row_limit).iterrows():
            key_values = {k: row.get(k) for k in self._key_columns}
            records.append(MismatchRecord(
                key_values=key_values,
                column_name="<row>",
                source_value=None,
                target_value=None,
                mismatch_type=mismatch_type,
            ))
        return records

    def _count_mismatches(
        self,
        df_source: pd.DataFrame,
        df_target: pd.DataFrame,
    ) -> tuple[int, int, int, int]:
        merged = pd.merge(
            df_source,
            df_target,
            on=self._key_columns,
            how="outer",
            indicator=True,
            suffixes=("_src", "_tgt"),
        )
        missing_in_target = int((merged["_merge"] == "left_only").sum())
        missing_in_source = int((merged["_merge"] == "right_only").sum())
        both = merged[merged["_merge"] == "both"]
        value_count = self._count_value_mismatches(both, df_source)
        return len(both), missing_in_target, missing_in_source, value_count

    def _count_value_mismatches(
        self,
        both: pd.DataFrame,
        df_source: pd.DataFrame,
    ) -> int:
        count = 0
        compare_cols = [
            c for c in df_source.columns
            if c not in self._key_columns and not self._is_excluded_column(c)
        ]
        for col in compare_cols:
            src_col = f"{col}_src" if f"{col}_src" in both.columns else col
            tgt_col = f"{col}_tgt" if f"{col}_tgt" in both.columns else col
            if src_col not in both.columns or tgt_col not in both.columns:
                continue

            src_na = both[src_col].isna()
            tgt_na = both[tgt_col].isna()
            both_na = src_na & tgt_na
            neither_na = ~src_na & ~tgt_na
            col_tol = self._column_tolerances.get(col, self._float_tolerance)

            if pd.api.types.is_datetime64_any_dtype(df_source[col]) and self._datetime_tolerance.total_seconds() > 0:
                val_eq = pd.Series(False, index=both.index, dtype=bool)
                if neither_na.any():
                    delta_ns = (both.loc[neither_na, src_col] - both.loc[neither_na, tgt_col]).abs()
                    tol_ns = int(self._datetime_tolerance.total_seconds() * 1e9)
                    val_eq[neither_na] = delta_ns <= pd.Timedelta(nanoseconds=tol_ns)
            elif pd.api.types.is_float_dtype(df_source[col]):
                val_eq = pd.Series(False, index=both.index, dtype=bool)
                if neither_na.any():
                    val_eq[neither_na] = np.isclose(
                        both.loc[neither_na, src_col].to_numpy(dtype=float),
                        both.loc[neither_na, tgt_col].to_numpy(dtype=float),
                        rtol=0,
                        atol=col_tol,
                    )
            else:
                val_eq = both[src_col].eq(both[tgt_col]).fillna(False)

            mismatch_mask = ~((both_na & self._null_equals_null) | (neither_na & val_eq))
            count += int(mismatch_mask.sum())
        return count

    def _attach_segment_values(
        self,
        mismatches: list[MismatchRecord],
        df_source: pd.DataFrame,
        df_target: pd.DataFrame,
    ) -> None:
        """Set MismatchRecord.segment_values from source rows (target fallback).

        Never raises — drill-down enrichment must not fail a run.
        """
        try:
            seg_cols = [
                c for c in self._segment_columns
                if c in df_source.columns or c in df_target.columns
            ]
            if not seg_cols:
                return

            def build_lookup(df: pd.DataFrame) -> dict:
                cols = [c for c in seg_cols if c in df.columns]
                if not cols or df.empty or not all(k in df.columns for k in self._key_columns):
                    return {}
                lut = {}
                n_keys = len(self._key_columns)
                for row in df[self._key_columns + cols].itertuples(index=False, name=None):
                    lut[row[:n_keys]] = dict(zip(cols, row[n_keys:]))
                return lut

            src_lut = build_lookup(df_source)
            tgt_lut = build_lookup(df_target)
            for m in mismatches:
                key = tuple(m.key_values.get(k) for k in self._key_columns)
                vals = src_lut.get(key) or tgt_lut.get(key)
                if vals is not None:
                    m.segment_values = {c: vals.get(c) for c in seg_cols if c in vals}
        except Exception:  # pragma: no cover - defensive
            logger.warning("segment value enrichment failed; skipping", exc_info=True)

    def _compare_column(
        self,
        col: str,
        both: pd.DataFrame,
        df_source: pd.DataFrame,
    ) -> tuple[list[MismatchRecord], int]:
        """Return (mismatch_records, total_count) for a single value column."""
        src_col = f"{col}_src" if f"{col}_src" in both.columns else col
        tgt_col = f"{col}_tgt" if f"{col}_tgt" in both.columns else col
        if src_col not in both.columns or tgt_col not in both.columns:
            return [], 0

        src_na = both[src_col].isna()
        tgt_na = both[tgt_col].isna()
        both_na = src_na & tgt_na
        neither_na = ~src_na & ~tgt_na
        col_tol = self._column_tolerances.get(col, self._float_tolerance)

        if pd.api.types.is_datetime64_any_dtype(df_source[col]) and self._datetime_tolerance.total_seconds() > 0:
            val_eq = pd.Series(False, index=both.index, dtype=bool)
            if neither_na.any():
                delta_ns = (both.loc[neither_na, src_col] - both.loc[neither_na, tgt_col]).abs()
                tol_ns = int(self._datetime_tolerance.total_seconds() * 1e9)
                val_eq[neither_na] = delta_ns <= pd.Timedelta(nanoseconds=tol_ns)
        elif pd.api.types.is_float_dtype(df_source[col]):
            val_eq = pd.Series(False, index=both.index, dtype=bool)
            if neither_na.any():
                val_eq[neither_na] = np.isclose(
                    both.loc[neither_na, src_col].to_numpy(dtype=float),
                    both.loc[neither_na, tgt_col].to_numpy(dtype=float),
                    rtol=0,
                    atol=col_tol,
                )
        else:
            val_eq = both[src_col].eq(both[tgt_col]).fillna(False)

        mismatch_mask = ~((both_na & self._null_equals_null) | (neither_na & val_eq))
        col_count = int(mismatch_mask.sum())
        records: list[MismatchRecord] = []
        if col_count:
            for _, row in both.loc[mismatch_mask].head(self._mismatch_row_limit).iterrows():
                sv, tv = row[src_col], row[tgt_col]
                delta: float | None = None
                rel_delta: float | None = None
                try:
                    delta = float(tv) - float(sv)
                    rel_delta = delta / float(sv) if float(sv) != 0 else None
                except (TypeError, ValueError):
                    pass
                records.append(MismatchRecord(
                    key_values={k: row.get(k) for k in self._key_columns},
                    column_name=col,
                    source_value=sv,
                    target_value=tv,
                    mismatch_type="value_diff",
                    delta=delta,
                    relative_delta=rel_delta,
                ))
        return records, col_count

    def _find_value_mismatches(
        self,
        both: pd.DataFrame,
        df_source: pd.DataFrame,
    ) -> tuple[list[MismatchRecord], int, dict[str, int]]:
        compare_cols = [
            c for c in df_source.columns
            if c not in self._key_columns and not self._is_excluded_column(c)
        ]

        if self._parallel_columns and len(compare_cols) > 1:
            indexed: list[tuple[list[MismatchRecord], int] | None] = [None] * len(compare_cols)
            with ThreadPoolExecutor(max_workers=self._parallel_workers) as pool:
                futures = {
                    pool.submit(self._compare_column, col, both, df_source): i
                    for i, col in enumerate(compare_cols)
                }
                for fut in as_completed(futures):
                    indexed[futures[fut]] = fut.result()
            col_results = indexed
        else:
            col_results = [self._compare_column(col, both, df_source) for col in compare_cols]

        records: list[MismatchRecord] = []
        count = 0
        counts_by_column: dict[str, int] = {}
        for col, (col_records, col_count) in zip(compare_cols, col_results):  # type: ignore[misc]
            count += col_count
            if col_count:
                counts_by_column[str(col)] = col_count
            remaining = self._mismatch_row_limit - len(records)
            if remaining > 0:
                records.extend(col_records[:remaining])
        return records, count, counts_by_column

    def _apply_slo(
        self,
        result: ReconciliationResult,
        max_duration_seconds: float | None,
    ) -> ReconciliationResult:
        if max_duration_seconds and result.duration_seconds > max_duration_seconds:
            result = dataclasses.replace(result, status=TestStatus.SLOW)
            logger.warning(
                "Reconciliation '%s' took %.1fs, exceeding SLO of %.1fs",
                result.query_name, result.duration_seconds, max_duration_seconds,
            )
        return result


def _is_na(val: object) -> bool:
    if val is None:
        return True
    try:
        return bool(pd.isna(val))
    except (TypeError, ValueError):
        return False
