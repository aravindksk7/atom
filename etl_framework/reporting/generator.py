import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger("etl_framework.reporting.generator")

class ReportGenerator:
    TEMPLATE_NAME = "report.html.j2"
    DEFAULT_OUTPUT_DIR = "./reports"
    MAX_MISMATCH_DISPLAY = 100

    def __init__(
        self,
        output_dir: str = DEFAULT_OUTPUT_DIR,
        max_mismatch_display: int = MAX_MISMATCH_DISPLAY,
    ):
        self._output_dir = Path(output_dir)
        self._max_mismatch_display = max_mismatch_display
        
        template_dir = Path(__file__).parent / "templates"
        loader = FileSystemLoader(template_dir)
        self._jinja_env = Environment(loader=loader, autoescape=True)

    def generate(self, suite_result) -> str:
        """
        Renders template with suite_result context.
        Creates output_dir if missing.
        Writes file to {output_dir}/report_{run_id}.html.
        Returns the file path written.
        """
        try:
            self._output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            try:
                from etl_framework.exceptions import ReportOutputError
                raise ReportOutputError(str(self._output_dir), e) from e
            except ImportError:
                raise RuntimeError(f"Failed to create output directory {self._output_dir}: {e}") from e

        template = self._jinja_env.get_template(self.TEMPLATE_NAME)
        html_content = template.render(suite=suite_result)
        
        run_id = getattr(suite_result, "run_id", "unknown_run")
        report_path = self._output_dir / f"report_{run_id}.html"
        
        try:
            report_path.write_text(html_content, encoding="utf-8")
            logger.info(f"Generated HTML report at {report_path}")
            return str(report_path)
        except OSError as e:
            try:
                from etl_framework.exceptions import ReportOutputError
                raise ReportOutputError(str(report_path), e) from e
            except ImportError:
                raise RuntimeError(f"Failed to write report to {report_path}: {e}") from e