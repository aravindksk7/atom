"""Session-wide test setup.

Under pytest-xdist (``-n``) each worker is its own process but they share the
filesystem, so a file-backed SQLite ETL_DATABASE_URL would be one database for
all of them. Give each worker its own file. This runs before any test module
imports etl_framework.repository.database, which reads the variable at import.
"""
import os

_worker = os.environ.get("PYTEST_XDIST_WORKER")
_url = os.environ.get("ETL_DATABASE_URL", "")
if _worker and _url.startswith("sqlite:///") and _url.endswith(".db"):
    os.environ["ETL_DATABASE_URL"] = f"{_url[:-3]}_{_worker}.db"
