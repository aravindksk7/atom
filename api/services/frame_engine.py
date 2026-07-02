from __future__ import annotations
import types

import pandas as pd


class FrameEngine:
    """Wrap a pre-loaded DataFrame so ReconciliationEngine can consume it."""

    def __init__(self, df: pd.DataFrame, env_name: str) -> None:
        self._df = df
        self._env = types.SimpleNamespace(name=env_name)

    def execute_query(self, query: str, params: dict | None = None) -> pd.DataFrame:
        return self._df
