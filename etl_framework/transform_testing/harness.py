"""Isolated transform testing: run a transform SQL against in-memory DuckDB
fixture tables and reconcile output with the production comparison backend."""
from __future__ import annotations

from dataclasses import dataclass, field

import duckdb
import pandas as pd

from etl_framework.reconciliation.backends.duckdb_backend import DuckDBBackend
from etl_framework.reconciliation.models import MismatchRecord


@dataclass
class TransformCase:
    transform_sql: str
    inputs: dict[str, pd.DataFrame]
    expected: pd.DataFrame
    key_columns: list[str] = field(default_factory=list)
    float_tolerance: float = 1e-9

    def execute(self) -> pd.DataFrame:
        """Run the transform against fixture tables; return the output frame."""
        con = duckdb.connect(":memory:")
        try:
            for table_name, frame in self.inputs.items():
                con.register(f"_src_{table_name}", frame)
                con.execute(f'CREATE TABLE "{table_name}" AS SELECT * FROM _src_{table_name}')
            return con.execute(self.transform_sql).df()
        finally:
            con.close()

    def run(self) -> list[MismatchRecord]:
        """Execute the transform and compare its output against ``expected``."""
        actual = self.execute()
        backend = DuckDBBackend(
            key_columns=self.key_columns,
            float_tolerance=self.float_tolerance,
        )
        return backend.compare(actual, self.expected)
