"""Row-diffable run data artifacts.

Some runs keep the raw data they fetched (see
``RunExecutor._persist_run_data_artifact``). A stored run holding exactly one
readable tabular artifact can be diffed row-by-row against a file instead of
only by its per-test stats. Single source of truth for that decision so the
Compare service and the runs API can never disagree about which runs qualify.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from api.services.upload_store import resolve_run_data_artifact

logger = logging.getLogger("api.services.run_data_artifact")

TABULAR_EXTS = {".csv", ".xlsx", ".xls", ".json", ".xml", ".tsv", ".txt"}


def resolve_row_diffable_artifact(run) -> Path | None:
    """Path to the run's single tabular artifact, or None if it cannot be diffed.

    Returns None for runs with no artifact, with several (multi-job runs have no
    single frame), with a non-tabular artifact, or whose artifact has already
    been swept by upload retention.
    """
    raw_paths = [
        raw for raw in (
            getattr(result, "data_artifact_path", None)
            for result in (getattr(run, "results", None) or [])
        ) if raw
    ]
    if len(raw_paths) != 1:
        return None
    path = resolve_run_data_artifact(raw_paths[0])
    if path is None or path.suffix.lower() not in TABULAR_EXTS:
        return None
    return path


def run_has_row_diffable_artifact(run) -> bool:
    return resolve_row_diffable_artifact(run) is not None


def load_row_diffable_frame(run) -> pd.DataFrame | None:
    """Read the run's artifact as a frame, or None to fall back to run stats."""
    from api.services.file_source import _read_tabular_bytes

    path = resolve_row_diffable_artifact(run)
    if path is None:
        return None
    try:
        return _read_tabular_bytes(path.read_bytes(), path.suffix.lower())
    except Exception:
        logger.warning("Unreadable run data artifact %s — falling back to run stats", path)
        return None


def resolve_job_result_artifact(repo, run_id: str, job_name: str) -> Path | None:
    """Path to one job's tabular artifact within a run, or None.

    Unlike resolve_row_diffable_artifact, this looks at a single job's own
    TestResult inside a possibly multi-job run, rather than requiring the
    whole run to have exactly one result.
    """
    result = repo.get_result_for_job(run_id, job_name)
    if result is None or not result.data_artifact_path:
        return None
    path = resolve_run_data_artifact(result.data_artifact_path)
    if path is None or path.suffix.lower() not in TABULAR_EXTS:
        return None
    return path


def load_job_result_frame(repo, run_id: str, job_name: str) -> pd.DataFrame | None:
    """Read a job's stored run artifact as a frame, or None if unavailable."""
    from api.services.file_source import _read_tabular_bytes

    path = resolve_job_result_artifact(repo, run_id, job_name)
    if path is None:
        return None
    try:
        return _read_tabular_bytes(path.read_bytes(), path.suffix.lower())
    except Exception:
        logger.warning("Unreadable job result artifact %s — falling back", path)
        return None
