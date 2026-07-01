from __future__ import annotations

import dataclasses
import logging
import time
from datetime import datetime, timezone

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
        null_equals_null: bool = True,           # Task 7
        chunk_size: int = 0,                     # Task 9
        use_hash_precheck: bool = True,          # Task 9
        backend: ComparisonBackend | None = None,  # Task 14
    ):
        self._source_engine = source_engine
        self._target_engine = target_engine
        self._key_columns = key_columns
        self._exclude_columns = set(exclude_columns or [])
        self._float_tolerance = float_tolerance
        self._mismatch_row_limit = mismatch_row_limit
        self._schema_mismatch_policy = schema_mismatch_policy
        self._null_equals_null = null_equals_null
        self._chunk_size = chunk_size
        self._use_hash_precheck = use_hash_precheck
        self._backend = backend
        self._normalizer = TypeNormalizer()

    def reconcile(
        self,
        query: str,
        query_name: str,
        params: dict | None = None,
        max_duration_seconds: float | None = None,
    ) -> ReconciliationResult:
        with _span("reconciliation.reconcile", attributes={"query_name": query_name}):
            t0 = time.monotonic()
            executed_at = datetime.now()

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

            schema_diff = self._validate_schemas(df_source, df_target, query_name)
            df_source_norm, df_target_norm = self._align_columns(df_source, df_target, schema_diff)

            if self._backend is not None:
                mismatch_list = self._backend.compare(df_source_norm, df_target_norm)
                result = ReconciliationResult(
                    query_name=query_name,
                    source_env=getattr(getattr(self._source_engine, "_env", None), "name", "source"),
                    target_env=getattr(getattr(self._target_engine, "_env", None), "name", "target"),
                    source_row_count=len(df_source),
                    target_row_count=len(df_target),
                    matched_count=len(df_source) - sum(
                        1 for m in mismatch_list if m.mismatch_type == "missing_in_target"
                    ),
                    missing_in_target_count=sum(
                        1 for m in mismatch_list if m.mismatch_type == "missing_in_target"
                    ),
                    missing_in_source_count=sum(
                        1 for m in mismatch_list if m.mismatch_type == "missing_in_source"
                    ),
                    value_mismatch_count=sum(
                        1 for m in mismatch_list if m.mismatch_type == "value_diff"
                    ),
                    mismatches=mismatch_list,
                    status=TestStatus.PASSED if not mismatch_list else TestStatus.FAILED,
                    executed_at=executed_at,
                    duration_seconds=0.0,
                    schema_diff=schema_diff,
                )
            else:
                result = self._compare(df_source_norm, df_target_norm, query_name, executed_at,
                                       schema_diff)
            result = dataclasses.replace(result, duration_seconds=time.monotonic() - t0)
            return self._apply_slo(result, max_duration_seconds)

    # ------------------------------------------------------------------
    # Schema helpers
    # ------------------------------------------------------------------

    def _validate_schemas(
        self,
        df_source: pd.DataFrame,
        df_target: pd.DataFrame,
        query_name: str,
    ) -> dict[str, list[str]] | None:
        src_cols = set(df_source.columns) - self._exclude_columns
        tgt_cols = set(df_target.columns) - self._exclude_columns
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
                if c in df_target.columns and c not in self._exclude_columns
            ]
            df_source = df_source[common]
            df_target = df_target[common]
        else:
            drop_src = [c for c in self._exclude_columns if c in df_source.columns]
            drop_tgt = [c for c in self._exclude_columns if c in df_target.columns]
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
        value_records, value_count = self._find_value_mismatches(both, df_source)

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
        )

    def _rows_to_mismatch_records(
        self,
        df: pd.DataFrame,
        mismatch_type: str,
    ) -> list[MismatchRecord]:
        records = []
        for _, row in df.iterrows():
            key_values = {k: row.get(k) for k in self._key_columns}
            records.append(MismatchRecord(
                key_values=key_values,
                column_name="<row>",
                source_value=None,
                target_value=None,
                mismatch_type=mismatch_type,
            ))
        return records

    def _find_value_mismatches(
        self,
        both: pd.DataFrame,
        df_source: pd.DataFrame,
    ) -> tuple[list[MismatchRecord], int]:
        records: list[MismatchRecord] = []
        count = 0

        compare_cols = [
            c for c in df_source.columns
            if c not in self._key_columns and c not in self._exclude_columns
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

            if pd.api.types.is_float_dtype(df_source[col]):
                val_eq = pd.Series(False, index=both.index, dtype=bool)
                if neither_na.any():
                    val_eq[neither_na] = np.isclose(
                        both.loc[neither_na, src_col].to_numpy(dtype=float),
                        both.loc[neither_na, tgt_col].to_numpy(dtype=float),
                        rtol=0,
                        atol=self._float_tolerance,
                    )
            else:
                val_eq = both[src_col].eq(both[tgt_col]).fillna(False)

            match = (both_na & self._null_equals_null) | (neither_na & val_eq)
            mismatch_mask = ~match
            col_count = int(mismatch_mask.sum())
            count += col_count

            if col_count and len(records) < self._mismatch_row_limit:
                budget = self._mismatch_row_limit - len(records)
                for _, row in both.loc[mismatch_mask].iloc[:budget].iterrows():
                    records.append(MismatchRecord(
                        key_values={k: row.get(k) for k in self._key_columns},
                        column_name=col,
                        source_value=row[src_col],
                        target_value=row[tgt_col],
                        mismatch_type="value_diff",
                    ))

        return records, count

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
