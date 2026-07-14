from __future__ import annotations

from api.services.diagnostics_service import DiagnosticsService


def test_diagnostics_service_can_omit_logs():
    bundle = DiagnosticsService().build_bundle(include_logs=False)
    assert "environment" in bundle
    assert "packages" in bundle
    assert "database" in bundle
    assert bundle["recent_logs"] == []
