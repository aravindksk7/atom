"""Sync versioned expectation-suite YAML files with job DQ rules.

Direction of truth: YAML → DB on sync (declarative replace); DB → YAML on
export. Validation runs through ``api.schemas.DQRule`` so a suite can never
install a rule the engine doesn't understand.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from api.schemas import DQRule
from etl_framework.expectations.suite import ExpectationSuite, dump_suite, load_suites
from etl_framework.repository.repository import JobRepository


class SyncReport(BaseModel):
    synced: list[str] = Field(default_factory=list)
    missing_jobs: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def sync_suites(directory: str | Path, db: Session) -> SyncReport:
    report = SyncReport()
    repo = JobRepository(db)
    for suite in load_suites(directory):
        job = repo.get(suite.job)
        if job is None:
            report.missing_jobs.append(suite.job)
            continue
        try:
            for rule in suite.rules:
                DQRule.model_validate(rule)
        except ValidationError as exc:
            report.errors.append(f"{suite.job}: {exc.errors()[0]['msg']}")
            continue
        params = dict(job.params or {})
        # Validation is the gate; storage stays byte-identical to the YAML.
        params["rules"] = suite.rules
        repo.update(suite.job, {"params": params})
        report.synced.append(suite.job)
    return report


def export_suites(directory: str | Path, db: Session) -> list[str]:
    """Write one suite YAML per job that has rules. Returns job names written."""
    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for job in JobRepository(db).list():
        rules = (job.params or {}).get("rules") or []
        if not rules:
            continue
        dump_suite(ExpectationSuite(job=job.name, rules=rules), out_dir / f"{job.name}.yml")
        written.append(job.name)
    return written
