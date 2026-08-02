import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger("etl_framework.reporting.generator")


def to_local(value, tz_name: str | None = None):
    """Jinja filter: render an aware UTC datetime as local wall-clock time with a zone abbreviation.

    With no tz_name, converts to the server process's OS-local timezone (original behavior).
    With tz_name, converts to that IANA zone instead (the app-wide configured timezone).
    """
    if value is None:
        return ""
    # The template is rendered from several suite shapes; a field that is already
    # a formatted string must pass through rather than take down the whole report.
    if not hasattr(value, "astimezone"):
        return value
    if tz_name:
        from zoneinfo import ZoneInfo
        return value.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M %Z")
    return value.astimezone().strftime("%Y-%m-%d %H:%M %Z")


# Multi-file compares tag every difference row with the file pair it came from,
# nested under this reserved key inside key_values. Mirrors PAIR_KEY in
# templates/_diff_search.js and _PairKeyDifferenceWriter in
# api/services/difference_export.py.
PAIR_KEY = "__pair__"


def pair_key_label(pair: Any) -> str:
    """Jinja filter: render a pair key the way the file-pair rollup does
    ("region=west"), so a row and its pair read alike."""
    if pair is None:
        return ""
    if not isinstance(pair, dict):
        return str(pair)
    return ", ".join(f"{name}={value}" for name, value in pair.items())


def reject_pair_key(key_values: Any) -> Any:
    """Jinja filter: the row's own key with the pairing metadata removed, so the
    key cell shows row identity rather than identity plus bookkeeping."""
    if not isinstance(key_values, dict):
        return key_values
    return {name: value for name, value in key_values.items() if name != PAIR_KEY}


class ReportGenerator:
    TEMPLATE_NAME = "report.html.j2"
    # Canonical mismatch search engine, shared with the live Compare tab (see the
    # header of the file itself). Inlined rather than {% include %}d so a stray
    # "{{" in the JS can never be parsed as template syntax.
    SEARCH_SCRIPT_NAME = "_diff_search.js"
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
        self._jinja_env.filters["pair_key_label"] = pair_key_label
        self._jinja_env.filters["reject_pair_key"] = reject_pair_key
        # Read eagerly: a missing search engine is a packaging fault, and failing
        # here beats shipping a report whose filter box silently does nothing.
        self._search_script = (template_dir / self.SEARCH_SCRIPT_NAME).read_text(encoding="utf-8")

    def generate(self, suite_result, filename: str | None = None) -> str:
        """
        Renders template with suite_result context.
        Creates output_dir if missing.
        Writes file to {output_dir}/{filename or report_{run_id}.html}.
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
        html_content = template.render(suite=suite_result, diff_search_js=self._search_script)
        
        run_id = getattr(suite_result, "run_id", "unknown_run")
        report_path = self._output_dir / (filename or f"report_{run_id}.html")
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