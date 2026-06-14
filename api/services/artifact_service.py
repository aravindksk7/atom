import os
from fastapi import HTTPException
from etl_framework.repository.base import AbstractTestRunRepository
from etl_framework.reporting.generator import ReportGenerator
import logging

logger = logging.getLogger("api.services.artifact_service")

class ArtifactService:
    def __init__(self, repository: AbstractTestRunRepository, report_dir: str = "./reports"):
        self._repository = repository
        self._report_dir = report_dir

    def generate_html_report(self, run_id: str) -> str:
        run = self._repository.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found.")

        try:
            generator = ReportGenerator(output_dir=self._report_dir)
            report_path = generator.generate(run)
            return report_path
        except Exception as e:
            logger.error(f"Failed to generate HTML report for {run_id}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="HTML Report generation failed.")