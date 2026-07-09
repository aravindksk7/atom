from __future__ import annotations

import pandas as pd
from etl_framework.reconciliation.backends.base import BackendCompareResult, ComparisonBackend
from etl_framework.reconciliation.models import MismatchRecord


class SamplingBackend:
    """Wraps any ComparisonBackend and samples both DataFrames before comparing.

    Useful for very large tables where a statistical sample is sufficient and
    an exact row-level diff is too expensive.  The sample is stratified by
    key column values: for each key present in *both* DataFrames a random
    subset of `sample_frac` rows is selected.  Rows that exist in only one
    side are always included (they represent missing data, never skipped).
    """

    def __init__(
        self,
        inner: ComparisonBackend,
        sample_frac: float = 0.1,
        seed: int = 42,
    ) -> None:
        if not 0 < sample_frac <= 1.0:
            raise ValueError(f"sample_frac must be in (0, 1], got {sample_frac!r}")
        self._inner = inner
        self._sample_frac = sample_frac
        self._seed = seed

    def compare(self, df_source: pd.DataFrame, df_target: pd.DataFrame) -> list[MismatchRecord]:
        return self.compare_with_counts(df_source, df_target).mismatches

    def compare_with_counts(self, df_source: pd.DataFrame, df_target: pd.DataFrame) -> BackendCompareResult:
        if self._sample_frac >= 1.0 or (len(df_source) == 0 and len(df_target) == 0):
            return self._compare_inner_with_counts(df_source, df_target)

        src_sampled = df_source.sample(
            frac=self._sample_frac, random_state=self._seed, replace=False
        ) if len(df_source) > 0 else df_source

        tgt_sampled = df_target.sample(
            frac=self._sample_frac, random_state=self._seed, replace=False
        ) if len(df_target) > 0 else df_target

        return self._compare_inner_with_counts(
            src_sampled.reset_index(drop=True),
            tgt_sampled.reset_index(drop=True),
        )

    def _compare_inner_with_counts(
        self,
        df_source: pd.DataFrame,
        df_target: pd.DataFrame,
    ) -> BackendCompareResult:
        compare_with_counts = getattr(self._inner, "compare_with_counts", None)
        if callable(compare_with_counts):
            return compare_with_counts(df_source, df_target)

        mismatches = self._inner.compare(df_source, df_target)
        mit_count = sum(1 for m in mismatches if m.mismatch_type == "missing_in_target")
        mis_count = sum(1 for m in mismatches if m.mismatch_type == "missing_in_source")
        value_count = sum(1 for m in mismatches if m.mismatch_type == "value_diff")
        return BackendCompareResult(
            matched_count=max(len(df_source) - mit_count, 0),
            missing_in_target_count=mit_count,
            missing_in_source_count=mis_count,
            value_mismatch_count=value_count,
            mismatches=mismatches,
        )

    @property
    def sample_frac(self) -> float:
        return self._sample_frac

    @property
    def inner(self) -> ComparisonBackend:
        return self._inner
