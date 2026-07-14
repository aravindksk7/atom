from __future__ import annotations

from typing import Any

from etl_framework.repository.database import engine
from etl_framework.utils.diagnostics import build_support_bundle


class DiagnosticsService:
    def build_bundle(self, include_logs: bool = True) -> dict[str, Any]:
        bundle = build_support_bundle(engine=engine)
        if not include_logs:
            bundle["recent_logs"] = []
        return bundle
