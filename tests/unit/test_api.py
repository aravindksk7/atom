import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.schemas import JobDefinition, RunTrigger
from api.routes import runs as runs_module
from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401 — registers ORM models with Base
from etl_framework.repository.repository import TokenRepository
from api.main import app


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    def override_get_db():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(runs_module, "_execute_run", lambda *args, **kwargs: None)
    app.dependency_overrides[get_db] = override_get_db

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")

    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c
    app.dependency_overrides.clear()


# --- Config endpoints ---

def test_list_configs_empty(client):
    resp = client.get("/api/configs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_config(client):
    payload = {"name": "dev", "env_name": "dev", "config_data": {"db_host": "localhost", "db_port": 1433}}
    resp = client.post("/api/configs", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "dev"
    assert data["id"] is not None

    audit = client.get("/api/audit?resource_type=config&resource_id=" + str(data["id"]))
    assert audit.status_code == 200
    events = audit.json()
    assert events[0]["action"] == "config.created"
    assert events[0]["actor"] == "test"


def test_get_config(client):
    resp = client.post("/api/configs", json={"name": "qa", "env_name": "qa", "config_data": {}})
    cfg_id = resp.json()["id"]
    resp2 = client.get(f"/api/configs/{cfg_id}")
    assert resp2.status_code == 200
    assert resp2.json()["name"] == "qa"


def test_get_config_not_found(client):
    resp = client.get("/api/configs/9999")
    assert resp.status_code == 404


def test_update_config(client):
    resp = client.post("/api/configs", json={"name": "stage", "env_name": "stage", "config_data": {"timeout": 30}})
    cfg_id = resp.json()["id"]
    resp2 = client.put(f"/api/configs/{cfg_id}", json={"config_data": {"timeout": 60}})
    assert resp2.status_code == 200
    assert resp2.json()["config_data"]["timeout"] == 60


def test_delete_config(client):
    resp = client.post("/api/configs", json={"name": "tmp", "env_name": "dev", "config_data": {}})
    cfg_id = resp.json()["id"]
    resp2 = client.delete(f"/api/configs/{cfg_id}")
    assert resp2.status_code == 204
    resp3 = client.get(f"/api/configs/{cfg_id}")
    assert resp3.status_code == 404


def test_validate_config_accepts_valid_environment(client):
    resp = client.post(
        "/api/configs/validate",
        json={
            "env_name": "dev",
            "config_data": {
                "db_host": "localhost",
                "db_port": 1433,
                "db_pool_size": 5,
                "db_password": "secret",
            },
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["config_data"]["db_port"] == 1433


def test_validate_config_returns_field_errors(client):
    resp = client.post(
        "/api/configs/validate",
        json={
            "env_name": "dev",
            "config_data": {"db_host": "localhost", "db_port": 99999},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["errors"][0]["field_name"] == "db_port"


def test_import_yaml_config_saves_environments(client):
    yaml_content = """
environments:
  dev:
    db_host: localhost
    db_port: 1433
    db_password: secret
  qa:
    db_host: qa-host
    db_port: 1433
    db_password: secret
"""
    resp = client.post("/api/configs/import-yaml", json={"yaml_content": yaml_content})
    assert resp.status_code == 201
    data = resp.json()
    assert [cfg["name"] for cfg in data] == ["dev", "qa"]
    assert data[0]["config_data"]["db_host"] == "localhost"


def test_import_yaml_config_returns_clear_error(client):
    yaml_content = """
environments:
  dev:
    db_host: localhost
    db_port: 99999
"""
    resp = client.post("/api/configs/import-yaml", json={"yaml_content": yaml_content})
    assert resp.status_code == 400
    assert resp.json()["detail"]["field_name"] == "db_port"


# --- Runs endpoints ---

def test_list_runs_empty(client):
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_trigger_run(client):
    payload = {"source_env": "dev", "target_env": "prod", "job_names": ["orders_query"]}
    resp = client.post("/api/runs", json=payload)
    assert resp.status_code == 202
    data = resp.json()
    assert "run_id" in data
    assert data["status"] == "PENDING"


def test_trigger_run_stores_sequence_and_settings(client, monkeypatch):
    payload = {
        "source_env": "dev",
        "target_env": "prod",
        "job_names": ["orders_query", "customers_query"],
        "job_sequence": ["customers_query", "orders_query"],
        "config_data": {"db_host": "localhost"},
        "run_settings": {
            "execution_mode": "sequential",
            "max_workers": 1,
            "schema_mismatch_policy": "warn",
            "chunk_size": 0,
            "metrics_enabled": True,
        },
    }
    resp = client.post("/api/runs", json=payload)
    assert resp.status_code == 202

    detail = client.get(f"/api/runs/{resp.json()['run_id']}")
    assert detail.status_code == 200
    snapshot = detail.json()["config_snapshot"]
    assert snapshot["db_host"] == "localhost"
    assert [s["job_name"] for s in snapshot["job_sequence"]] == ["customers_query", "orders_query"]
    assert snapshot["run_settings"]["execution_mode"] == "sequential"
    assert snapshot["run_settings"]["schema_mismatch_policy"] == "warn"
    assert snapshot["run_settings"]["comparison_backend"] == "pandas"
    assert snapshot["source_credentials"]["name"] == "dev"
    assert snapshot["target_credentials"]["name"] == "prod"
    assert snapshot["automic_credentials"]["db_host"] == "localhost"


def test_trigger_run_injects_saved_config_credentials(client):
    cfg = client.post(
        "/api/configs",
        json={
            "name": "live",
            "env_name": "qa",
            "config_data": {
                "db_host": "sql-live",
                "db_password": "secret",
                "automic_url": "http://automic",
                "automic_user": "svc",
                "automic_password": "pw",
            },
        },
    ).json()
    run = client.post(
        "/api/runs",
        json={
            "source_env": "qa",
            "target_env": "prod",
            "job_sequence": [],
            "config_id": cfg["id"],
            "run_settings": {"use_live_connections": True},
        },
    )
    assert run.status_code == 202

    detail = client.get(f"/api/runs/{run.json()['run_id']}")
    snapshot = detail.json()["config_snapshot"]
    assert snapshot["config_id"] == cfg["id"]
    assert snapshot["source_credentials"]["db_host"] == "sql-live"
    assert snapshot["automic_credentials"]["automic_user"] == "svc"


def test_trigger_run_ignores_masked_secrets_from_stale_config_data(client):
    """Regression test: the Launch page's Saved Config dropdown is populated
    from GET /api/configs, which masks db_password/bo_password/automic_password
    as "********" for display. The frontend then echoes that same masked object
    back as config_data when triggering a live run. Previously the backend
    merged {**cfg.config_json, **body.config_data} with body.config_data
    winning, so the mask literal clobbered the real password and every live
    run triggered via a Saved Config failed authentication."""
    created = client.post(
        "/api/configs",
        json={
            "name": "live2",
            "env_name": "qa2",
            "config_data": {
                "db_host": "sql-live",
                "db_password": "super-secret",
                "bo_url": "https://bo-server",
                "bo_password": "bo-secret",
            },
        },
    ).json()

    # Simulate the frontend: fetch the (masked) list, then send that back as
    # config_data alongside config_id when launching a run.
    listed = client.get("/api/configs").json()
    masked_cfg = next(c for c in listed if c["id"] == created["id"])
    assert masked_cfg["config_data"]["db_password"] == "********"

    run = client.post(
        "/api/runs",
        json={
            "source_env": "qa2",
            "target_env": "prod",
            "job_sequence": [],
            "config_id": created["id"],
            "config_data": masked_cfg["config_data"],
            "run_settings": {"use_live_connections": True},
        },
    )
    assert run.status_code == 202

    detail = client.get(f"/api/runs/{run.json()['run_id']}")
    snapshot = detail.json()["config_snapshot"]
    assert snapshot["db_password"] == "super-secret"
    assert snapshot["bo_credentials"]["bo_password"] == "bo-secret"


def test_trigger_run_rejects_invalid_backend(client):
    resp = client.post(
        "/api/runs",
        json={
            "source_env": "dev",
            "target_env": "prod",
            "job_sequence": ["orders_query"],
            "run_settings": {"comparison_backend": "duckdb"},
        },
    )
    assert resp.status_code == 422


def test_recon_file_compare_surfaces_error_message(client):
    """An internal failure during recon-file compare must be visible on the run,
    not just swallowed into a bare ERROR status (regression: previously only
    run_bo_comparison persisted error_message; recon-file/sql compare did not)."""
    resp = client.post(
        "/api/compare/recon-file",
        json={
            "stored_run_id": "does-not-exist",
            "file_b_path": "unused.csv",
            "label_a": "Source A",
            "label_b": "Source B",
        },
    )
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "ERROR"
    assert detail["results"], "expected an error TestResult to be persisted"
    assert "not found" in detail["results"][0]["error_message"].lower()


def test_sql_compare_surfaces_error_message(client):
    resp = client.post(
        "/api/compare/sql",
        json={
            "config_id_a": 999999,
            "config_id_b": 999999,
            "query_a": "SELECT 1",
            "query_b": "SELECT 1",
            "label_a": "Source A",
            "label_b": "Source B",
        },
    )
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["status"] == "ERROR"
    assert detail["results"], "expected an error TestResult to be persisted"
    assert "not found" in detail["results"][0]["error_message"].lower()


def test_trigger_run_rejects_negative_worker_count(client):
    resp = client.post(
        "/api/runs",
        json={
            "source_env": "dev",
            "target_env": "prod",
            "job_sequence": ["orders_query"],
            "run_settings": {"max_workers": 0},
        },
    )
    assert resp.status_code == 422


def test_trigger_run_rejects_removed_metadata_only_settings(client):
    resp = client.post(
        "/api/runs",
        json={
            "source_env": "dev",
            "target_env": "prod",
            "job_sequence": ["orders_query"],
            "run_settings": {"dry_run": True},
        },
    )
    assert resp.status_code == 422


def test_run_trigger_translates_legacy_job_names_to_sequence():
    from api.schemas import SequenceStep
    trigger = RunTrigger(
        source_env="dev",
        target_env="prod",
        job_names=["first", "second"],
    )
    assert len(trigger.job_sequence) == 2
    assert all(isinstance(s, SequenceStep) for s in trigger.job_sequence)
    assert [s.job_name for s in trigger.job_sequence] == ["first", "second"]


def test_job_definition_requires_key_columns_for_reconciliation():
    with pytest.raises(ValueError, match="key_columns"):
        JobDefinition(name="orders", job_type="reconciliation", query="select * from orders")


def test_get_run_status(client):
    resp = client.post("/api/runs", json={"source_env": "dev", "target_env": "prod", "job_names": []})
    run_id = resp.json()["run_id"]
    resp2 = client.get(f"/api/runs/{run_id}/status")
    assert resp2.status_code == 200
    assert resp2.json()["run_id"] == run_id


def test_get_run_detail(client):
    resp = client.post("/api/runs", json={"source_env": "dev", "target_env": "prod", "job_names": []})
    run_id = resp.json()["run_id"]
    resp2 = client.get(f"/api/runs/{run_id}")
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["run_id"] == run_id
    assert "results" in data


def test_get_run_metrics_missing_returns_404(client):
    resp = client.post("/api/runs", json={"source_env": "dev", "target_env": "prod", "job_names": []})
    run_id = resp.json()["run_id"]
    metrics = client.get(f"/api/runs/{run_id}/metrics")
    assert metrics.status_code == 404
    assert metrics.json()["detail"] == "Metrics not found"


def test_get_run_logs_defaults_to_run_scope(client, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    run = client.post("/api/runs", json={"source_env": "dev", "target_env": "prod", "job_names": []})
    run_id = run.json()["run_id"]
    log_dir = Path("logs")
    log_dir.mkdir()
    log_dir.joinpath("etl_framework.log").write_text(
        f"2026-06-14 18:00:15 | ERROR    | {run_id} | runner | current run failed\n"
        "Traceback line for current run\n"
        "2026-06-14 18:00:16 | ERROR    | other-run | runner | other run failed\n",
        encoding="utf-8",
    )

    resp = client.get(f"/api/runs/{run_id}/logs?format=json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["scope"] == "run"
    assert data["matched_lines"] == 1
    assert "current run failed" in data["lines"][0]["text"]
    assert "other run failed" not in data["lines"][0]["text"]


def test_get_run_logs_searches_traceback_context_with_level(client, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    run = client.post("/api/runs", json={"source_env": "dev", "target_env": "prod", "job_names": []})
    run_id = run.json()["run_id"]
    log_dir = Path("logs")
    log_dir.mkdir()
    log_dir.joinpath("etl_framework.log").write_text(
        f"2026-06-14 18:00:15 | ERROR    | {run_id} | runner | Test case raised an exception\n"
        "Traceback (most recent call last):\n"
        "ImportError: missing driver\n",
        encoding="utf-8",
    )

    resp = client.get(f"/api/runs/{run_id}/logs?format=json&q=ImportError&level=ERROR")
    assert resp.status_code == 200
    data = resp.json()
    assert data["matched_lines"] == 1
    assert data["lines"][0]["level"] == "ERROR"
    assert "ImportError: missing driver" in data["lines"][0]["text"]


def test_get_run_logs_tolerates_non_utf8_bytes(client, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    run = client.post("/api/runs", json={"source_env": "dev", "target_env": "prod", "job_names": []})
    run_id = run.json()["run_id"]
    log_dir = Path("logs")
    log_dir.mkdir()
    log_dir.joinpath("etl_framework.log").write_bytes(
        f"2026-06-14 18:00:15 | ERROR    | {run_id} | runner | bad byte ".encode()
        + b"\x97\n"
    )

    resp = client.get(f"/api/runs/{run_id}/logs?format=json")
    assert resp.status_code == 200
    assert resp.json()["matched_lines"] == 1


def test_metrics_payload_can_be_built_from_run_results():
    from types import SimpleNamespace
    from api.routes.runs import _metrics_from_run

    result = SimpleNamespace(
        query_name="Source A",
        status="FAILED",
        duration_seconds=0.25,
        source_row_count=6,
        target_row_count=6,
        total_issues=1,
    )
    run = SimpleNamespace(run_id="run-db-metrics", results=[result])

    metrics = _metrics_from_run(run)
    assert metrics["source"] == "database"
    assert metrics["total_tests"] == 1
    assert metrics["failed"] == 1
    assert metrics["tests"][0]["total_issues"] == 1


def test_get_run_not_found(client):
    resp = client.get("/api/runs/nonexistent-run-id")
    assert resp.status_code == 404


# --- Jobs endpoints ---

def test_list_jobs(client):
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert data[0]["query"]
    assert data[0]["key_columns"]


def test_create_update_delete_job(client):
    payload = {
        "name": "custom_orders",
        "description": "Custom orders reconciliation",
        "tags": ["custom"],
        "job_type": "reconciliation",
        "query": "SELECT * FROM custom_orders",
        "key_columns": ["id"],
        "exclude_columns": ["updated_at"],
        "params": {},
        "enabled": True,
    }
    create = client.post("/api/jobs", json=payload)
    assert create.status_code == 201
    assert create.json()["name"] == "custom_orders"

    list_resp = client.get("/api/jobs")
    assert [job["name"] for job in list_resp.json()] == ["custom_orders"]

    payload["description"] = "Updated"
    update = client.put("/api/jobs/custom_orders", json=payload)
    assert update.status_code == 200
    assert update.json()["description"] == "Updated"

    delete = client.delete("/api/jobs/custom_orders")
    assert delete.status_code == 204


def test_import_jobs_upserts_definitions(client):
    payload = [
        {
            "name": "imported_orders",
            "description": "Imported",
            "tags": ["import"],
            "job_type": "reconciliation",
            "query": "SELECT * FROM imported_orders",
            "key_columns": ["id"],
            "enabled": True,
        }
    ]
    first = client.post("/api/jobs/import", json=payload)
    assert first.status_code == 201
    payload[0]["description"] = "Imported updated"
    second = client.post("/api/jobs/import", json=payload)
    assert second.status_code == 201
    assert second.json()[0]["description"] == "Imported updated"


def test_create_job_rejects_missing_key_columns(client):
    resp = client.post(
        "/api/jobs",
        json={
            "name": "bad_job",
            "job_type": "reconciliation",
            "query": "SELECT * FROM bad_job",
            "key_columns": [],
        },
    )
    assert resp.status_code == 422


def test_create_job_rejects_unimplemented_external_job_types(client):
    for job_type in ("bo_report", "automic_job"):
        resp = client.post(
            "/api/jobs",
            json={
                "name": f"bad_{job_type}",
                "job_type": job_type,
                "query": "",
                "key_columns": [],
            },
        )
        assert resp.status_code == 422


def test_create_dbt_artifact_job(client):
    resp = client.post(
        "/api/jobs",
        json={
            "name": "dbt_orders",
            "job_type": "dbt_artifact",
            "query": "",
            "key_columns": [],
            "params": {"run_results_path": "target/run_results.json"},
        },
    )
    assert resp.status_code == 201
    assert resp.json()["job_type"] == "dbt_artifact"


def test_health_endpoint(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_checks_accept_valid_config(client):
    resp = client.post(
        "/api/health/checks",
        json={
            "environments": {
                "dev": {
                    "db_host": "localhost",
                    "db_port": 1433,
                    "db_password": "secret",
                }
            }
        },
    )
    assert resp.status_code == 200
    assert resp.json() == [{"component": "dev", "healthy": True, "message": "OK"}]


def test_health_checks_report_invalid_config(client):
    resp = client.post(
        "/api/health/checks",
        json={
            "environments": {
                "dev": {
                    "db_host": "localhost",
                    "db_port": 99999,
                    "db_password": "secret",
                }
            }
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["component"] == "dev"
    assert data[0]["healthy"] is False
    assert "db_port" in data[0]["message"]


def test_testrun_has_run_type_and_pair_id_columns(client):
    resp = client.post("/api/configs", json={"name": "m1", "env_name": "dev", "config_data": {}})
    assert resp.status_code == 201
    # Trigger a run so TestRun row is created
    resp2 = client.post("/api/runs", json={
        "source_env": "dev", "target_env": "prod",
        "job_names": [], "config_data": {}
    })
    assert resp2.status_code == 202
    run_id = resp2.json()["run_id"]
    resp3 = client.get(f"/api/runs/{run_id}")
    assert resp3.status_code == 200
    data = resp3.json()
    assert "run_type" in data
    assert data["run_type"] == "reconciliation"
    assert "pair_id" in data


# --- pass_condition round-trip ---

def test_create_job_with_pass_condition(client):
    body = {
        "name": "pc_job",
        "job_type": "reconciliation",
        "query": "SELECT * FROM orders",
        "key_columns": ["id"],
        "pass_condition": {
            "min_row_count": 1,
            "max_value_mismatches": 0,
            "require_status": ["PASSED"],
        },
    }
    resp = client.post("/api/jobs", json=body)
    assert resp.status_code == 201
    data = resp.json()
    assert data["pass_condition"]["min_row_count"] == 1
    assert data["pass_condition"]["max_value_mismatches"] == 0
    assert data["pass_condition"]["require_status"] == ["PASSED"]


def test_update_job_pass_condition_round_trips(client):
    body = {
        "name": "pc_update_job",
        "job_type": "reconciliation",
        "query": "SELECT * FROM orders",
        "key_columns": ["id"],
    }
    client.post("/api/jobs", json=body)
    update = {**body, "pass_condition": {"min_row_count": 5, "pass_sql": "SELECT 1"}}
    resp = client.put("/api/jobs/pc_update_job", json=update)
    assert resp.status_code == 200
    pc = resp.json()["pass_condition"]
    assert pc["min_row_count"] == 5
    assert pc["pass_sql"] == "SELECT 1"
    assert pc["pass_sql_mode"] == "rows_mean_pass"


def test_update_job_pass_condition_null_clears_it(client):
    body = {
        "name": "pc_null_job",
        "job_type": "reconciliation",
        "query": "SELECT * FROM orders",
        "key_columns": ["id"],
        "pass_condition": {"min_row_count": 1},
    }
    client.post("/api/jobs", json=body)
    update = {**body, "pass_condition": None}
    resp = client.put("/api/jobs/pc_null_job", json=update)
    assert resp.status_code == 200
    assert resp.json()["pass_condition"] is None


def test_list_jobs_includes_pass_condition(client):
    body = {
        "name": "pc_list_job",
        "job_type": "reconciliation",
        "query": "SELECT * FROM orders",
        "key_columns": ["id"],
        "pass_condition": {"max_value_mismatches": 0},
    }
    client.post("/api/jobs", json=body)
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    jobs = resp.json()
    pc_job = next((j for j in jobs if j["name"] == "pc_list_job"), None)
    assert pc_job is not None
    assert pc_job["pass_condition"]["max_value_mismatches"] == 0


def test_automic_job_summary_valid():
    from api.schemas import AutomicJobSummary
    s = AutomicJobSummary(name="ETL_JOB", status="ENDED_OK")
    assert s.name == "ETL_JOB"
    assert s.status == "ENDED_OK"


def test_automic_bulk_import_request_requires_nonempty_list():
    from api.schemas import AutomicBulkImportRequest
    with pytest.raises(Exception):
        AutomicBulkImportRequest(config_id=1, job_names=[])


def test_automic_bulk_import_request_valid():
    from api.schemas import AutomicBulkImportRequest
    r = AutomicBulkImportRequest(config_id=1, job_names=["ETL_A"])
    assert r.job_names == ["ETL_A"]


def test_automic_bulk_import_response_defaults_errors_to_empty():
    from api.schemas import AutomicBulkImportResponse
    r = AutomicBulkImportResponse(imported=[])
    assert r.errors == {}


# --- SourceConfig api source_type ---

def test_source_config_api_requires_config_id_and_endpoint_name():
    from pydantic import ValidationError
    from api.schemas import SourceConfig
    with pytest.raises(ValidationError):
        SourceConfig(source_type="api", config_id=1)  # missing api_endpoint_name
    with pytest.raises(ValidationError):
        SourceConfig(source_type="api", api_endpoint_name="orders")  # missing config_id


def test_source_config_api_accepts_config_id_and_endpoint_name():
    from api.schemas import SourceConfig
    src = SourceConfig(source_type="api", config_id=1, api_endpoint_name="orders")
    assert src.source_type == "api"
    assert src.api_endpoint_name == "orders"


# --- api_reconciliation job type ---

def test_create_api_reconciliation_job_requires_endpoint_params(client):
    resp = client.post(
        "/api/jobs",
        json={
            "name": "bad_api_job",
            "job_type": "api_reconciliation",
            "query": "",
            "key_columns": ["id"],
            "params": {},
        },
    )
    assert resp.status_code == 422


def test_create_api_reconciliation_job_requires_key_columns(client):
    resp = client.post(
        "/api/jobs",
        json={
            "name": "bad_api_job2",
            "job_type": "api_reconciliation",
            "query": "",
            "key_columns": [],
            "params": {"source_api_endpoint": "orders_a", "target_api_endpoint": "orders_b"},
        },
    )
    assert resp.status_code == 422


def test_create_api_reconciliation_job_succeeds(client):
    resp = client.post(
        "/api/jobs",
        json={
            "name": "good_api_job",
            "job_type": "api_reconciliation",
            "query": "",
            "key_columns": ["id"],
            "params": {"source_api_endpoint": "orders_a", "target_api_endpoint": "orders_b"},
        },
    )
    assert resp.status_code == 201
    assert resp.json()["job_type"] == "api_reconciliation"


# --- api_endpoints secret masking ---

def test_config_masks_api_endpoint_secrets(client):
    resp = client.post(
        "/api/configs",
        json={
            "name": "api-cfg",
            "env_name": "dev",
            "config_data": {
                "api_endpoints": {
                    "orders": {
                        "base_url": "https://api.example.com/orders",
                        "auth_type": "bearer",
                        "bearer_token": "super-secret-token",
                    }
                }
            },
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["config_data"]["api_endpoints"]["orders"]["bearer_token"] == "********"
    assert data["config_data"]["api_endpoints"]["orders"]["base_url"] == "https://api.example.com/orders"


def test_update_config_preserves_masked_api_endpoint_secret(client):
    created = client.post(
        "/api/configs",
        json={
            "name": "api-cfg2",
            "env_name": "dev",
            "config_data": {
                "api_endpoints": {
                    "orders": {
                        "base_url": "https://api.example.com/orders",
                        "auth_type": "bearer",
                        "bearer_token": "super-secret-token",
                    }
                }
            },
        },
    ).json()

    # Simulate the frontend echoing back the masked value on update
    masked_data = created["config_data"]
    masked_data["api_endpoints"]["orders"]["base_url"] = "https://api.example.com/orders-v2"
    resp = client.put(f"/api/configs/{created['id']}", json={"config_data": masked_data})
    assert resp.status_code == 200

    detail = client.get(f"/api/configs/{created['id']}").json()
    # base_url change went through, but the mask did NOT clobber the real secret
    assert detail["config_data"]["api_endpoints"]["orders"]["base_url"] == "https://api.example.com/orders-v2"
    assert detail["config_data"]["api_endpoints"]["orders"]["bearer_token"] == "********"
