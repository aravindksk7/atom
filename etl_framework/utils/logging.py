import logging
import logging.handlers
import os

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: str = "INFO",
                      log_file: str = "./logs/etl_framework.log",
                      log_format: str = "text") -> None:
    root = logging.getLogger("etl_framework")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    text_formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(text_formatter)
    root.addHandler(stream_handler)

    os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5
    )
    file_handler.setFormatter(text_formatter)
    root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"etl_framework.{name}")
