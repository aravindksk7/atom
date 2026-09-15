"""One-time audit: list every distinct file_watcher/file_mapping
credentials_ref currently referenced by a saved job, sequence, or schedule,
so an operator can create a matching FileServerProfile for each one before
this deploy removes the old inline-credentials fallback. Run with:
    python scripts/list_file_server_credential_refs.py
"""
from __future__ import annotations

from etl_framework.repository.database import SessionLocal
from etl_framework.repository.models import ExecutionSequenceVersion, SavedJob, ScheduledRun


def _find_refs(value, found: set[str]) -> None:
    if isinstance(value, dict):
        ref = value.get("credentials_ref")
        if isinstance(ref, str) and ref:
            found.add(ref)
        for v in value.values():
            _find_refs(v, found)
    elif isinstance(value, list):
        for v in value:
            _find_refs(v, found)


def main() -> None:
    found: set[str] = set()
    with SessionLocal() as db:
        for job in db.query(SavedJob).all():
            _find_refs(job.params, found)
        for version in db.query(ExecutionSequenceVersion).all():
            _find_refs(version.steps_json, found)
        for schedule in db.query(ScheduledRun).all():
            _find_refs(schedule.job_sequence, found)

    if not found:
        print("No credentials_ref values found — nothing to migrate.")
        return
    print("Create a File Server profile for each of these names before deploying:")
    for ref in sorted(found):
        print(f"  - {ref}")


if __name__ == "__main__":
    main()
