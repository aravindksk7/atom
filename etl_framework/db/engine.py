from __future__ import annotations

import logging
import sys
import urllib.parse
from contextlib import contextmanager
from types import SimpleNamespace

import pandas as pd
from sqlalchemy import create_engine, text

from etl_framework.config.models import EnvironmentConfig


@contextmanager
def _preserve_root_logging():
    """Undo any root-logger reconfiguration done inside the block.

    ``nzalchemy/base.py`` runs ``logging.basicConfig(level=DEBUG,
    filename='nzalchemy.log')`` at import time. That is not scoped to
    nzalchemy: it attaches a DEBUG ``FileHandler`` to the *root* logger and
    creates the file in the process's current working directory, so merely
    importing the dialect would redirect this application's logging into an
    unmanaged file (and log every SQL statement nzalchemy emits at DEBUG,
    outside our own log configuration and retention).

    Snapshotting and restoring the root logger's handlers and level is enough
    to contain it -- and the handler we drop is closed, so it does not keep
    the file open for writing.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    # basicConfig() is a no-op when the root logger already has a handler, so
    # parking a NullHandler here stops nzalchemy's FileHandler from ever being
    # constructed -- which is what creates the file. Restoring handlers
    # afterwards would otherwise still leave an empty nzalchemy.log behind in
    # the working directory. Only needed when nothing has configured logging
    # yet (a CLI/worker entrypoint); under the API server root already has
    # handlers and basicConfig no-ops on its own.
    placeholder = logging.NullHandler() if not saved_handlers else None
    if placeholder is not None:
        root.addHandler(placeholder)
    try:
        yield
    finally:
        if placeholder is not None:
            root.removeHandler(placeholder)
        for handler in root.handlers[:]:
            if handler not in saved_handlers:
                root.removeHandler(handler)
                try:
                    handler.close()
                except Exception:  # pragma: no cover — best-effort cleanup
                    pass
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def _ensure_netezza_dialect() -> None:
    """Make ``netezza+nzpy://`` / ``netezza+pyodbc://`` resolvable.

    SQLAlchemy has no built-in Netezza dialect, and ``nzpy`` is a bare
    PEP-249 driver that registers no ``sqlalchemy.dialects`` entry point —
    so the URLs below fail with "Can't load plugin:
    sqlalchemy.dialects:netezza.nzpy" unless IBM's ``nzalchemy`` package is
    installed. Importing it here (rather than relying on the entry point
    alone) lets us raise an actionable message instead of SQLAlchemy's.

    ``nzalchemy.pyodbc`` additionally does ``from sqlalchemy import
    processors``, a module SQLAlchemy 2.0 moved to
    ``sqlalchemy.engine.processors``. Aliasing it back is enough: the two
    names nzalchemy uses (``to_decimal_processor_factory``, ``to_float``)
    both still live there.
    """
    import sqlalchemy

    if not hasattr(sqlalchemy, "processors"):
        import sqlalchemy.engine.processors as _sa_processors

        sqlalchemy.processors = _sa_processors
        sys.modules.setdefault("sqlalchemy.processors", _sa_processors)

    try:
        with _preserve_root_logging():
            import nzalchemy  # noqa: F401 — imported for its dialect entry points
    except ImportError as exc:
        raise ImportError(
            "Netezza support requires the 'nzalchemy' SQLAlchemy dialect. "
            "Install it with: pip install 'etl-framework[netezza]'. "
            "The 'nzpy' driver alone is not enough — it registers no "
            "SQLAlchemy dialect."
        ) from exc


class DBEngine:
    """SQLAlchemy-backed query engine compatible with ReconciliationEngine."""

    def __init__(self, env_config: EnvironmentConfig, _engine=None) -> None:
        self._env = SimpleNamespace(name=env_config.name)
        if _engine is not None:
            self._engine = _engine
        else:
            db_type = getattr(env_config, "db_type", "mssql")
            if db_type == "oracle":
                user = urllib.parse.quote_plus(env_config.db_user)
                pwd = urllib.parse.quote_plus(env_config.db_password)
                connection_url = (
                    f"oracle+oracledb://{user}:{pwd}"
                    f"@{env_config.db_host}:{env_config.db_port}/?service_name={env_config.db_name}"
                )
            elif db_type == "netezza":
                _ensure_netezza_dialect()
                if env_config.db_driver.lower() == "nzpy":
                    connection_url = (
                        f"netezza+nzpy://{env_config.db_user}:{env_config.db_password}"
                        f"@{env_config.db_host}:{env_config.db_port}/{env_config.db_name}"
                    )
                else:
                    params = urllib.parse.quote_plus(
                        f"DRIVER={{{env_config.db_driver}}};"
                        f"SERVER={env_config.db_host};"
                        f"PORT={env_config.db_port};"
                        f"DATABASE={env_config.db_name};"
                        f"UID={env_config.db_user};"
                        f"PWD={env_config.db_password};"
                    )
                    connection_url = f"netezza+pyodbc:///?odbc_connect={params}"
            else:
                # ODBC Driver 18 defaults to Encrypt=yes with strict certificate validation
                # (a behavior change from Driver 17, which defaulted to no encryption), so
                # it rejects self-signed/internal CA certs unless TrustServerCertificate is
                # set. Driver 17 doesn't need this — leave its connection string unchanged.
                trust_cert = "TrustServerCertificate=yes;" if "18" in env_config.db_driver else ""
                params = urllib.parse.quote_plus(
                    f"DRIVER={{{env_config.db_driver}}};"
                    f"SERVER={env_config.db_host},{env_config.db_port};"
                    f"DATABASE={env_config.db_name};"
                    f"UID={env_config.db_user};"
                    f"PWD={env_config.db_password};"
                    f"Connect Timeout={env_config.db_connect_timeout};"
                    f"{trust_cert}"
                )
                connection_url = f"mssql+pyodbc:///?odbc_connect={params}"

            self._engine = create_engine(
                connection_url,
                pool_size=env_config.db_pool_size,
                max_overflow=env_config.db_pool_overflow,
                pool_timeout=env_config.db_pool_timeout,
                pool_recycle=env_config.db_pool_recycle,
                echo=False,
            )

    def execute_query(self, query: str, params: dict | None = None) -> pd.DataFrame:
        with self._engine.connect() as conn:
            return pd.read_sql(text(query), conn, params=params or {})

    def dispose(self) -> None:
        self._engine.dispose()

    def connect(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False
