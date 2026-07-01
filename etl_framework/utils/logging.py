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
    if log_format == "json":
        try:
            from pythonjsonlogger import jsonlogger
            json_formatter = jsonlogger.JsonFormatter(
                fmt="%(asctime)s %(levelname)s %(name)s %(run_id)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%SZ",
                rename_fields={"levelname": "level", "asctime": "timestamp"},
            )
            file_handler.setFormatter(json_formatter)
        except ImportError:
            file_handler.setFormatter(text_formatter)
    else:
        file_handler.setFormatter(text_formatter)
    root.addHandler(file_handler)

    # Route uvicorn's own logger (unhandled exceptions, request errors,
    # startup/shutdown/reload messages) through the same handlers, so
    # everything lands in one file instead of being silently lost to
    # whatever console the process happens to be attached to.
    for uvicorn_logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(uvicorn_logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.addHandler(stream_handler)
        uvicorn_logger.addHandler(file_handler)
        uvicorn_logger.setLevel(root.level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"etl_framework.{name}")
