from __future__ import annotations

import urllib.parse
from types import SimpleNamespace

import pandas as pd
from sqlalchemy import create_engine, text

from etl_framework.config.models import EnvironmentConfig


class DBEngine:
    """SQLAlchemy-backed query engine compatible with ReconciliationEngine."""

    def __init__(self, env_config: EnvironmentConfig, _engine=None) -> None:
        self._env = SimpleNamespace(name=env_config.name)
        if _engine is not None:
            self._engine = _engine
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
            self._engine = create_engine(
                f"mssql+pyodbc:///?odbc_connect={params}",
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
