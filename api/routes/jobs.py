from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from api.dependencies import get_session, is_admin_request
from api.schemas import JobDefinition, JobRunSummaryOut, PreviewFileMappingRequest
from etl_framework.repository.models import SavedJob
from etl_framework.repository.repository import JobRepository, RunRepository
from api.services.audit_service import AuditService
from etl_framework.runner.job_validation import validate_job_definition

router = APIRouter(tags=["jobs"])

# Every other saved job type only reads. A file_transfer job writes: to an SFTP
# or S3 destination with the profile's credentials, and to any folder inside
# SERVER_FILE_ALLOWED_DIRS. Authoring one is therefore an admin action, while
# reading, launching and deleting an existing one is not.
WRITE_CAPABLE_JOB_TYPES = frozenset({"file_transfer"})

WRITE_CAPABLE_JOB_DENIAL = (
    "file_transfer jobs write to servers and local folders; "
    "creating or editing them requires the admin role"
)


def _write_capable(*job_types: str | None) -> bool:
    return any(job_type in WRITE_CAPABLE_JOB_TYPES for job_type in job_types)


def require_admin_for_write_capable_job(request: Request, *job_types: str | None) -> None:
    """Refuse a non-admin caller who is creating, importing or editing a job of
    a write-capable type. ``job_types`` takes both the incoming type and the
    stored one, so turning an existing job into a file_transfer job -- and
    editing one that already is -- are both covered."""
    if _write_capable(*job_types) and not is_admin_request(request):
        raise HTTPException(status_code=403, detail=WRITE_CAPABLE_JOB_DENIAL)

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
    pass_condition_raw = params.pop("pass_condition", None)
    from api.schemas import DQRule, PassCondition
    rules = [DQRule.model_validate(r) for r in (rules_raw or [])]
    pass_condition = PassCondition.model_validate(pass_condition_raw) if pass_condition_raw else None
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
        pass_condition=pass_condition,
    )


def _job_to_data(job: JobDefinition) -> dict:
    data = job.model_dump(exclude={"rules", "depends_on", "pass_condition"})
    params = dict(data.get("params") or {})
    if job.rules:
        params["rules"] = [r.model_dump() for r in job.rules]
    if job.depends_on:
        params["depends_on"] = list(job.depends_on)
    if job.pass_condition:
        params["pass_condition"] = job.pass_condition.model_dump(exclude_none=True)
    data["params"] = params
    return data


@router.get("", response_model=list[JobDefinition])
def list_jobs(db: Session = Depends(get_session)):
    repo = JobRepository(db)
    jobs = repo.list()
    if not jobs:
        return _SEED_JOBS
    return [_job_to_schema(job) for job in jobs]


@router.get("/{name}/runs", response_model=list[JobRunSummaryOut])
def list_job_runs(name: str, limit: int = 20, db: Session = Depends(get_session)):
    results = RunRepository(db).list_results_for_job(name, limit=limit)
    return [
        JobRunSummaryOut(
            run_id=r.run_id,
            status=r.effective_status,
            started_at=r.run.started_at if r.run else None,
            completed_at=r.run.completed_at if r.run else None,
            has_data_artifact=bool(r.data_artifact_path),
        )
        for r in results
    ]


@router.post("", response_model=JobDefinition, status_code=201)
def create_job(body: JobDefinition, request: Request, db: Session = Depends(get_session)):
    repo = JobRepository(db)
    require_admin_for_write_capable_job(request, body.job_type)
    _raise_validation_errors(body)
    if repo.get(body.name) is not None:
        raise HTTPException(status_code=409, detail="Job already exists")
    job = repo.create(_job_to_data(body))
    AuditService(db).log(
        request, "job.created", "job", job.name,
        {"job_type": job.job_type, "enabled": job.enabled},
    )
    return _job_to_schema(job)


@router.post("/validate")
def validate_job_definition_body(body: dict):
    issues = [
        {"field": issue.field, "message": issue.message, "severity": issue.severity.value}
        for issue in validate_job_definition(body)
    ]
    try:
        JobDefinition.model_validate(body)
    except ValidationError as exc:
        for err in exc.errors():
            issues.append({
                "field": ".".join(str(part) for part in err.get("loc", [])) or None,
                "message": err.get("msg", "validation error"),
                "severity": "error",
            })
    return {"ok": not any(issue["severity"] == "error" for issue in issues), "issues": issues}


# This is an interactive preview click on a request thread, not a real job
# run -- it must not queue behind (or make a real job queue behind it for)
# the full _SMB_LOCK_TIMEOUT_SECONDS default (5 minutes). Mirrors the Test
# Connection route's own 30s bound on the same host lock, just shorter since
# a preview has even less reason to wait.
_PREVIEW_SMB_LOCK_TIMEOUT_SECONDS = 15


@router.post("/preview-file-mapping")
def preview_file_mapping(body: PreviewFileMappingRequest, db: Session = Depends(get_session)):
    from etl_framework.reconciliation.file_mapping import FileMappingSpec, pair_files, pair_files_automated
    from api.services.multi_file_remote import RemoteFileSourceSession, SmbConnectError

    try:
        spec = FileMappingSpec.from_params({"file_mapping": body.file_mapping})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        with RemoteFileSourceSession(db) as session:
            source_files = session.discover(spec.source, lock_timeout_seconds=_PREVIEW_SMB_LOCK_TIMEOUT_SECONDS)
            target_files = session.discover(spec.target, lock_timeout_seconds=_PREVIEW_SMB_LOCK_TIMEOUT_SECONDS)

            if spec.strategy == "automated":
                source_frames = {f.path: session.read_file(f, spec.source) for f in source_files}
                target_frames = {f.path: session.read_file(f, spec.target) for f in target_files}
                mapping, scores = pair_files_automated(
                    source_files, source_frames, target_files, target_frames, spec.automated,
                )
                scores_by_pair = {(s.source.path, s.target.path): s for s in scores}
            else:
                mapping = pair_files(source_files, target_files, spec.match_on)
                scores_by_pair = {}
    except HTTPException:
        raise
    except SmbConnectError as exc:
        # Mirrors the Test Connection route's wording for the same
        # contended-lock situation; other smb failures (bad credentials, a
        # missing share) also land here rather than the generic 400 below,
        # so the underlying message is included too.
        raise HTTPException(
            status_code=409,
            detail=f"Another SMB operation is in progress for this host -- try again shortly ({exc})",
        )
    except ValueError as exc:
        # Covers resolve_file_server_profile's missing/mismatched credentials_ref,
        # but also _group_by_key's match_on-token errors and pandas parsing errors
        # from session.read_file -- all are "bad input" cases, so 422 fits either
        # way, but don't assume this message is always about credentials.
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    def _group(group) -> dict:
        return {"key": dict(zip(mapping.match_on, group.key)), "files": [f.file_name for f in group.files]}

    pairs_out = []
    for pair in mapping.pairs:
        pair_key = dict(zip(mapping.match_on, pair.key)) if mapping.match_on else {
            "source_file": pair.source.files[0].file_name if pair.source.files else None,
            "target_file": pair.target.files[0].file_name if pair.target.files else None,
        }
        score = None
        if pair.source.files and pair.target.files:
            score = scores_by_pair.get((pair.source.files[0].path, pair.target.files[0].path))
        pairs_out.append({
            "key": pair_key,
            "source_files": [f.file_name for f in pair.source.files],
            "target_files": [f.file_name for f in pair.target.files],
            "similarity_score": score.score if score is not None else None,
        })

    return {
        "pairs_total": len(mapping.pairs),
        "pairs": pairs_out,
        "unmatched_sources": [_group(g) for g in mapping.unmatched_sources],
        "unmatched_targets": [_group(g) for g in mapping.unmatched_targets],
    }


@router.post("/import", response_model=list[JobDefinition], status_code=201)
def import_jobs(body: list[JobDefinition], request: Request, db: Session = Depends(get_session)):
    repo = JobRepository(db)
    imported = []
    # Checked for the whole payload first: this route already fails the entire
    # request on the first invalid item, so a partial import is not a shape
    # callers can depend on.
    for job_def in body:
        existing = repo.get(job_def.name)
        require_admin_for_write_capable_job(
            request, job_def.job_type, existing.job_type if existing else None,
        )
    for job_def in body:
        _raise_validation_errors(job_def)
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
    require_admin_for_write_capable_job(
        request, body.job_type, before.job_type if before else None,
    )
    _raise_validation_errors(body)
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


def _raise_validation_errors(job: JobDefinition) -> None:
    issues = validate_job_definition(job)
    errors = [issue for issue in issues if issue.severity.value == "error"]
    if errors:
        raise HTTPException(
            status_code=422,
            detail=[
                {"field": issue.field, "message": issue.message, "severity": issue.severity.value}
                for issue in errors
            ],
        )


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
        # Use SET FMTONLY ON to ask the MSSQL engine to parse/compile the query
        # without executing it.  The query is passed as a bound parameter so it
        # cannot break out of the text boundary — no string interpolation here.
        try:
            from sqlalchemy import text

            with engine._engine.connect() as conn:
                conn.execute(text("SET FMTONLY ON"))
                try:
                    conn.execute(text(definition.query))
                except Exception:
                    pass  # FMTONLY returns no rows; drivers may raise innocuous errors
                finally:
                    conn.execute(text("SET FMTONLY OFF"))
            plans[label] = "(syntax ok)"
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
