from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.schemas import JobDefinition
from etl_framework.repository.models import SavedJob
from etl_framework.repository.repository import JobRepository

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
        params=job.params or {},
        enabled=job.enabled,
    )


def _job_to_data(job: JobDefinition) -> dict:
    return job.model_dump()


@router.get("", response_model=list[JobDefinition])
def list_jobs(db: Session = Depends(get_session)):
    repo = JobRepository(db)
    jobs = repo.list()
    if not jobs:
        return _SEED_JOBS
    return [_job_to_schema(job) for job in jobs]


@router.post("", response_model=JobDefinition, status_code=201)
def create_job(body: JobDefinition, db: Session = Depends(get_session)):
    repo = JobRepository(db)
    if repo.get(body.name) is not None:
        raise HTTPException(status_code=409, detail="Job already exists")
    return _job_to_schema(repo.create(_job_to_data(body)))


@router.post("/import", response_model=list[JobDefinition], status_code=201)
def import_jobs(body: list[JobDefinition], db: Session = Depends(get_session)):
    repo = JobRepository(db)
    return [_job_to_schema(repo.upsert(_job_to_data(job))) for job in body]


@router.put("/{name}", response_model=JobDefinition)
def update_job(name: str, body: JobDefinition, db: Session = Depends(get_session)):
    repo = JobRepository(db)
    data = _job_to_data(body)
    data["name"] = name
    job = repo.update(name, data)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_to_schema(job)


@router.delete("/{name}", status_code=204)
def delete_job(name: str, db: Session = Depends(get_session)):
    repo = JobRepository(db)
    if not repo.delete(name):
        raise HTTPException(status_code=404, detail="Job not found")
