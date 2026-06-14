import logging
from etl_framework.sap_bo.validator import SAPBOValidator
from etl_framework.config.models import EnvironmentConfig
from etl_framework.reconciliation.models import ReconciliationResult

logger = logging.getLogger("etl_framework.sap_bo.reports")

class SAPBOReportRunner:
    def __init__(self, source_config: EnvironmentConfig, target_config: EnvironmentConfig, mode: str = "api") -> None:
        self._source_config = source_config
        self._target_config = target_config
        self._mode = mode

    def run(self, report_id: str, sql_query: str | None = None, key_columns: list[str] | None = None, exclude_columns: list[str] | None = None, float_tolerance: float = 1e-9) -> ReconciliationResult:
        logger.info(f"Running SAP BO report validation for {report_id} in {self._mode} mode")
        validator = SAPBOValidator(self._source_config, self._target_config, mode=self._mode)
        return validator.validate_report(
            report_id=report_id,
            sql_query=sql_query,
            key_columns=key_columns,
            exclude_columns=exclude_columns,
            float_tolerance=float_tolerance
        )
