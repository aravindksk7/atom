import logging
import os
import tempfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger("etl_framework.reporting.generator")


def to_local(value, tz_name: str | None = None):
    """Jinja filter: render an aware UTC datetime as local wall-clock time with a zone abbreviation.

    With no tz_name, converts to the server process's OS-local timezone (original behavior).
    With tz_name, converts to that IANA zone instead (the app-wide configured timezone).
    """
    if value is None:
        return ""
    if tz_name:
        from zoneinfo import ZoneInfo
        return value.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M %Z")
    return value.astimezone().strftime("%Y-%m-%d %H:%M %Z")


class ReportGenerator:
    TEMPLATE_NAME = "report.html.j2"
    DEFAULT_OUTPUT_DIR = "./reports"
    MAX_MISMATCH_DISPLAY = 100

    def __init__(
        self,
        output_dir: str = DEFAULT_OUTPUT_DIR,
        max_mismatch_display: int = MAX_MISMATCH_DISPLAY,
        timezone: str | None = None,
    ):
        self._output_dir = Path(output_dir)
        self._max_mismatch_display = max_mismatch_display
        self._timezone = timezone

        template_dir = Path(__file__).parent / "templates"
        loader = FileSystemLoader(template_dir)
        self._jinja_env = Environment(loader=loader, autoescape=True)
        self._jinja_env.filters["to_local"] = lambda v: to_local(v, self._timezone)

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
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(dir=self._output_dir, suffix=".html.tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(html_content)
            os.replace(tmp_path, str(report_path))
            logger.info(f"Generated HTML report at {report_path}")
            return str(report_path)
        except OSError as e:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
            try:
                from etl_framework.exceptions import ReportOutputError
                raise ReportOutputError(str(report_path), e) from e
            except ImportError:
                raise RuntimeError(f"Failed to write report to {report_path}: {e}") from e