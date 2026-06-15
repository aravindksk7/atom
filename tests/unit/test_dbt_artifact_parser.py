from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import RunSettings
from api.services.dbt_artifact_parser import DbtArtifactParser
from api.services.run_executor import RunExecutor
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import JobRepository, RunRepository


def test_dbt_artifact_parser_joins_manifest_names(tmp_path):
    run_results = tmp_path / "run_results.json"
    manifest = tmp_path / "manifest.json"
    run_results.write_text(
        json.dumps({
            "metadata": {"generated_at": "2026-06-16T00:00:00Z"},
            "results": [
                {"unique_id": "model.pkg.orders", "status": "success", "execution_time": 1.25},
                {"unique_id": "model.pkg.customers", "status": "error", "execution_time": 0.5, "message": "failed"},
            ],
        }),
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps({
            "nodes": {
                "model.pkg.orders": {"name": "orders"},
                "model.pkg.customers": {"name": "customers"},
            }
        }),
        encoding="utf-8",
    )

    summary = DbtArtifactParser().parse(run_results, manifest)
    assert summary.generated_at == "2026-06-16T00:00:00Z"
    assert summary.total == 2
    assert summary.passed == 1
    assert summary.failed == 1
    assert [result.name for result in summary.results] == ["orders", "customers"]


def test_run_executor_persists_dbt_artifact_result(tmp_path):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)
    run_results = tmp_path / "run_results.json"
    run_results.write_text(
        json.dumps({
            "results": [
                {"unique_id": "model.pkg.orders", "status": "success", "execution_time": 1.0},
                {"unique_id": "model.pkg.customers", "status": "fail", "execution_time": 0.25},
            ]
        }),
        encoding="utf-8",
    )

    RunRepository(db).create_run("r-dbt", "dev", "prod", {})
    JobRepository(db).create({
        "name": "dbt_artifacts",
        "description": "",
        "tags": [],
        "job_type": "dbt_artifact",
        "query": "",
        "key_columns": [],
        "exclude_columns": [],
        "source_env": None,
        "target_env": None,
        "params": {"run_results_path": str(run_results)},
        "enabled": True,
    })

    RunExecutor(
        db=db,
        run_id="r-dbt",
        source_env="dev",
        target_env="prod",
        job_sequence=["dbt_artifacts"],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    run = RunRepository(db).get_run("r-dbt")
    assert run.status == "FAILED"
    assert run.results[0].query_name == "dbt_artifacts"
    assert run.results[0].value_mismatch_count == 1
