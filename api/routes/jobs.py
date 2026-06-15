from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.schemas import JobDefinition
from etl_framework.repository.models import SavedJob
from etl_framework.repository.repository import JobRepository
from api.services.audit_service import AuditService

router = APIRouter(tags=["jobs"])

_SEED_JOBS: list[JobDefinition] = [
    JobDefinition(
        name="orders_reconciliation",
        description="Reconcile orders table",
        tags=["orders", "daily"],
        query="SELECT * FROM orders",
        key_columns=["id"],
    ),
    JobDefinition(
        name="customers_reconciliation",
        description="Reconcile customers table",
        tags=["customers"],
        query="SELECT * FROM customers",
        key_columns=["id"],
    ),
    JobDefinition(
        name="products_reconciliation",
        description="Reconcile products table",
        tags=["products"],
        query="SELECT * FROM products",
        key_columns=["id"],
    ),
    JobDefinition(
        name="inventory_check",
        description="Check inventory counts",
        tags=["inventory", "daily"],
        query="SELECT * FROM inventory",
        key_columns=["id"],
    ),
    JobDefinition(
        name="sales_summary_validation",
        description="Validate sales summary aggregates",
        tags=["sales"],
        query="SELECT * FROM sales_summary",
        key_columns=["id"],
    ),
    JobDefinition(
        name="sap_bo_sales_report",
        description="Validate SAP BO sales report across environments",
        tags=["sap_bo", "sales"],
        job_type="bo_report",
        query="",
        key_columns=["region", "product_category"],
        params={"report_id": "RPT_SALES_SUMMARY_001", "mode": "api"},
    ),
    JobDefinition(
        name="automic_nightly_load",
        description="Monitor Automic nightly ETL execution",
        tags=["automic", "nightly"],
        job_type="automic_job",
        query="",
        key_columns=[],
        params={"job_name": "ETL_NIGHTLY_LOAD"},
    ),
]


def _job_to_schema(job: SavedJob) -> JobDefinition:
    params = dict(job.params or {})
    rules_raw = params.pop("rules", [])
    depends_on = params.pop("depends_on", [])
    from api.schemas import DQRule
    rules = [DQRule.model_validate(r) for r in (rules_raw or [])]
    return JobDefinition(
        name=job.name,
        description=job.description,
        tags=job.tags or [],
        job_type=job.job_type,
        query=job.query,
        key_columns=job.key_columns or [],
        exclude_columns=job.exclude_columns or [],
        source_env=job.source_env,
        target_env=job.target_env,
        params=params,
        enabled=job.enabled,
        rules=rules,
        depends_on=depends_on,
    )


def _job_to_data(job: JobDefinition) -> dict:
    data = job.model_dump(exclude={"rules", "depends_on"})
    params = dict(data.get("params") or {})
    if job.rules:
        params["rules"] = [r.model_dump() for r in job.rules]
    if job.depends_on:
        params["depends_on"] = list(job.depends_on)
    data["params"] = params
    return data


@router.get("", response_model=list[JobDefinition])
def list_jobs(db: Session = Depends(get_session)):
    repo = JobRepository(db)
    jobs = repo.list()
    if not jobs:
        return _SEED_JOBS
    return [_job_to_schema(job) for job in jobs]


@router.post("", response_model=JobDefinition, status_code=201)
def create_job(body: JobDefinition, request: Request, db: Session = Depends(get_session)):
    repo = JobRepository(db)
    if repo.get(body.name) is not None:
        raise HTTPException(status_code=409, detail="Job already exists")
    job = repo.create(_job_to_data(body))
    AuditService(db).log(
        request, "job.created", "job", job.name,
        {"job_type": job.job_type, "enabled": job.enabled},
    )
    return _job_to_schema(job)


@router.post("/import", response_model=list[JobDefinition], status_code=201)
def import_jobs(body: list[JobDefinition], request: Request, db: Session = Depends(get_session)):
    repo = JobRepository(db)
    imported = []
    for job_def in body:
        existed = repo.get(job_def.name) is not None
        job = repo.upsert(_job_to_data(job_def))
        AuditService(db).log(
            request,
            "job.updated" if existed else "job.created",
            "job",
            job.name,
            {"source": "import", "job_type": job.job_type},
        )
        imported.append(_job_to_schema(job))
    return imported


@router.put("/{name}", response_model=JobDefinition)
def update_job(name: str, body: JobDefinition, request: Request, db: Session = Depends(get_session)):
    repo = JobRepository(db)
    before = repo.get(name)
    data = _job_to_data(body)
    data["name"] = name
    job = repo.update(name, data)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    AuditService(db).log(
        request,
        "job.updated",
        "job",
        name,
        {
            "before": _job_to_schema(before).model_dump(mode="json") if before else None,
            "after": _job_to_schema(job).model_dump(mode="json"),
        },
    )
    return _job_to_schema(job)


@router.delete("/{name}", status_code=204)
def delete_job(name: str, request: Request, db: Session = Depends(get_session)):
    repo = JobRepository(db)
    job = repo.get(name)
    if not repo.delete(name):
        raise HTTPException(status_code=404, detail="Job not found")
    AuditService(db).log(
        request,
        "job.deleted",
        "job",
        name,
        {"job_type": job.job_type if job else None},
    )


# ---------------------------------------------------------------------------
# P2 – Query dry-run / validate (EXPLAIN)
# ---------------------------------------------------------------------------

class _ValidateRequest(BaseModel):
    source_env: str
    target_env: str
    config_data: dict = {}


@router.post("/{name}/validate")
def validate_job(name: str, body: "_ValidateRequest", db: Session = Depends(get_session)):
    from etl_framework.repository.repository import JobRepository as _JobRepo
    repo = _JobRepo(db)
    job = repo.get(name)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    definition = _job_to_schema(job)
    if not definition.query.strip():
        return {"source_ok": False, "target_ok": False, "errors": ["No query to validate"]}

    errors: list[str] = []
    plans: dict[str, str] = {}

    def _explain(engine, label: str) -> None:
        try:
            explain_sql = f"EXPLAIN {definition.query}"
            df = engine.execute_query(explain_sql)
            plans[label] = df.to_string(index=False) if not df.empty else "(no plan)"
        except Exception as exc:
            errors.append(f"{label}: {exc}")

    try:
        from api.services.run_executor import RunExecutor, DataFrameQueryEngine
        import pandas as pd
        ex = RunExecutor(
            db=db, run_id="validate", source_env=body.source_env,
            target_env=body.target_env, job_sequence=[],
            run_settings=__import__("api.schemas", fromlist=["RunSettings"]).RunSettings(
                use_live_connections=bool(body.config_data),
            ),
            config_snapshot=body.config_data,
        )
        src, tgt = ex._build_engines(definition)
        _explain(src, "source")
        _explain(tgt, "target")
    except Exception as exc:
        errors.append(str(exc))

    return {
        "source_ok": "source" not in str(errors),
        "target_ok": "target" not in str(errors),
        "source_plan": plans.get("source", ""),
        "target_plan": plans.get("target", ""),
        "errors": errors,
    }
