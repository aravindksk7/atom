import logging
import pandas as pd
from etl_framework.config.models import EnvironmentConfig
from etl_framework.reconciliation.engine import ReconciliationEngine
from etl_framework.reconciliation.models import ReconciliationResult
from etl_framework.sap_bo.client import BORestClient
from etl_framework.db.engine import DBEngine

logger = logging.getLogger("etl_framework.sap_bo.validator")

class _StubDBEngine:
    """A proxy engine to pass raw pandas DataFrames into the ReconciliationEngine."""
    def __init__(self, df: pd.DataFrame, env_name: str):
        self._df = df
        from types import SimpleNamespace
        self._env = SimpleNamespace(name=env_name)

    def execute_query(self, query: str, params: dict | None = None) -> pd.DataFrame:
        return self._df

class SAPBOValidator:
    def __init__(
        self,
        source_config: EnvironmentConfig,
        target_config: EnvironmentConfig,
        mode: str = "sql"   # "sql" | "api"
    ):
        self._source_config = source_config
        self._target_config = target_config
        self._mode = mode

    def validate_report(
        self,
        report_id: str,
        sql_query: str | None = None,
        key_columns: list[str] | None = None,
        exclude_columns: list[str] | None = None,
        float_tolerance: float = 1e-9,
    ) -> ReconciliationResult:
        key_cols = key_columns or []
        exclude_cols = exclude_columns or []
        
        if self._mode == "api":
            logger.info(f"Validating SAP BO report '{report_id}' using API mode")
            source_client = BORestClient(self._source_config)
            target_client = BORestClient(self._target_config)
            
            try:
                df_source = source_client.fetch_report_data(report_id)
                df_target = target_client.fetch_report_data(report_id)
            finally:
                source_client.logout()
                target_client.logout()
                
            stub_source = _StubDBEngine(df_source, self._source_config.name)
            stub_target = _StubDBEngine(df_target, self._target_config.name)
            
            reconciler = ReconciliationEngine(stub_source, stub_target, key_cols, exclude_cols, float_tolerance)
            return reconciler.reconcile(f"API_REPORT_{report_id}", report_id)
            
        else:
            logger.info(f"Validating SAP BO report '{report_id}' using SQL mode")
            if not sql_query:
                raise ValueError("sql_query must be provided when mode is 'sql'")
                
            source_engine = DBEngine(self._source_config)
            target_engine = DBEngine(self._target_config)
            try:
                reconciler = ReconciliationEngine(source_engine, target_engine, key_cols, exclude_cols, float_tolerance)
                return reconciler.reconcile(sql_query, report_id)
            finally:
                source_engine.dispose()
                target_engine.dispose()