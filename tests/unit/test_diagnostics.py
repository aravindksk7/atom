from __future__ import annotations

from sqlalchemy import create_engine

from etl_framework.utils.diagnostics import build_support_bundle, collect_database_info, collect_environment_info


def test_collect_environment_info_contains_python():
    assert collect_environment_info()["python"]


def test_collect_database_info_for_sqlite():
    engine = create_engine("sqlite:///:memory:")
    assert collect_database_info(engine)["dialect"] == "sqlite"


def test_build_support_bundle_without_engine(tmp_path):
    bundle = build_support_bundle(log_dir=tmp_path)
    assert "environment" in bundle
    assert "packages" in bundle
    assert bundle["recent_logs"] == []
