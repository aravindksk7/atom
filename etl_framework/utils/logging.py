import logging
import logging.handlers
import os

from etl_framework.utils.context import get_run_id

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(run_id)s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class RunContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = get_run_id()
        return True


def configure_logging(
    level: str = "INFO",
    log_file: str = "./logs/etl_framework.log",
    log_format: str = "text",
) -> None:
    root = logging.getLogger("etl_framework")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    context_filter = RunContextFilter()
    text_formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    stream_handler = logging.StreamHandler()
    stream_handler.addFilter(context_filter)
    stream_handler.setFormatter(text_formatter)
    root.addHandler(stream_handler)

    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    file_handler.addFilter(context_filter)
    file_handler.setFormatter(text_formatter)
    root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"etl_framework.{name}")
