"""Test-coverage matrix, gap report, and flaky-test detection.

Compute-on-read from existing tables (saved_jobs, schema_snapshots,
column_profiles, test_results). Cached in-process with a short TTL,
same pattern as the trend cache in api/routes/runs.py.
"""
from __future__ import annotations

import re
import time

from sqlalchemy import func
from sqlalchemy.orm import Session

from etl_framework.repository.models import (
    ColumnProfile,
    SavedJob,
    SchemaSnapshot,
    TestResult,
    TestRun,
)

_CACHE_TTL_SECONDS = 30
_CACHE: dict[tuple, tuple[float, dict]] = {}

FLAKY_THRESHOLD = 0.3
DEFAULT_FLAKY_WINDOW = 20

# FROM/JOIN followed by an identifier that may be schema-prefixed,
# double-quoted, or [bracketed].  Stops before aliases and parens.
_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+((?:\[[^\]]+\]|\"[^\"]+\"|[A-Za-z_][\w$]*)"
    r"(?:\.(?:\[[^\]]+\]|\"[^\"]+\"|[A-Za-z_][\w$]*))*)",
    re.IGNORECASE,
)
_CTE_RE = re.compile(r"(?:\bWITH\b|,)\s*([A-Za-z_][\w]*)\s+AS\s*\(", re.IGNORECASE)


def _clean_ident(raw: str) -> str:
    parts = [p.strip('[]"') for p in re.split(r"\.", raw)]
    return ".".join(p for p in parts if p).lower()


def extract_tables(sql: str) -> set[str]:
    """Extract table names referenced in FROM/JOIN clauses.

    Handles schema prefixes, double quotes, and [brackets]; excludes CTE names.
    Intentionally regex-based (no full AST) per the design spec.
    """
    if not sql or not sql.strip():
        return set()
    ctes = {m.group(1).lower() for m in _CTE_RE.finditer(sql)}
    tables = set()
    for m in _TABLE_RE.finditer(sql):
        name = _clean_ident(m.group(1))
        if name and name not in ctes and name != "(":
            tables.add(name)
    return tables


def classify_level(
    column: str,
    rule_columns: set[str],
    reconciled_columns: set[str],
    observed_columns: set[str],
) -> str:
    if column in rule_columns or column in reconciled_columns:
        return "tested"
    if column in observed_columns:
        return "observed"
    return "untested"


def compute_flakiness(statuses: list[str]) -> float:
    """Transitions / (window - 1). Statuses ordered oldest -> newest."""
    if len(statuses) < 2:
        return 0.0
    transitions = sum(1 for a, b in zip(statuses, statuses[1:]) if a != b)
    return transitions / (len(statuses) - 1)


# ---------------------------------------------------------------------------
# DB-backed builders
# ---------------------------------------------------------------------------

_SQL_JOB_TYPES = {"reconciliation", "freshness", "profile", "schema_snapshot"}


def build_coverage(db: Session) -> dict:
    """Build the full coverage matrix response."""
    sig = db.query(func.count(SavedJob.id), func.max(SavedJob.updated_at)).first()
    cache_key = ("coverage", id(db.get_bind()), sig[0], str(sig[1]))
    now = time.monotonic()
    cached = _CACHE.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    jobs = db.query(SavedJob).filter(SavedJob.enabled.is_(True)).all()

    # Per-job column knowledge
    snapshot_cols: dict[str, set[str]] = {}
    for snap in _latest_snapshots(db):
        cols = {c.get("name", "").lower() for c in (snap.columns or []) if c.get("name")}
        snapshot_cols.setdefault(snap.job_name, set()).update(cols)

    profile_cols: dict[str, set[str]] = {}
    for job_name, column_name in db.query(
        ColumnProfile.job_name, ColumnProfile.column_name
    ).distinct():
        profile_cols.setdefault(job_name, set()).add(column_name.lower())

    tables: dict[str, dict] = {}
    for job in jobs:
        if job.job_type not in _SQL_JOB_TYPES:
            continue
        job_tables = extract_tables(job.query or "")
        rules = (job.params or {}).get("rules") or []
        rule_columns = {str(r.get("column", "")).lower() for r in rules if r.get("column")}
        keys = {k.lower() for k in (job.key_columns or [])}
        excludes = {c.lower() for c in (job.exclude_columns or [])}
        observed = snapshot_cols.get(job.name, set()) | profile_cols.get(job.name, set())
        if job.job_type == "reconciliation":
            reconciled = (observed - excludes) | keys
        else:
            reconciled = set()
        all_columns = observed | rule_columns | keys

        for table in job_tables:
            entry = tables.setdefault(table, {"table": table, "columns": {}, "jobs": set()})
            entry["jobs"].add(job.name)
            for col in all_columns:
                level = classify_level(col, rule_columns, reconciled, observed)
                cur = entry["columns"].get(col)
                rank = {"tested": 2, "observed": 1, "untested": 0}
                if cur is None or rank[level] > rank[cur["level"]]:
                    entry["columns"][col] = {
                        "column": col, "level": level,
                        "jobs": sorted({job.name} | set(cur["jobs"] if cur else [])),
                        "rules": sorted(
                            {r.get("type") for r in rules
                             if str(r.get("column", "")).lower() == col}
                            | set(cur["rules"] if cur else [])
                        ),
                    }
                elif cur is not None and job.name not in cur["jobs"]:
                    cur["jobs"] = sorted(set(cur["jobs"]) | {job.name})

    out_tables = []
    total_cols = tested_cols = observed_only = 0
    for table in sorted(tables):
        entry = tables[table]
        cols = sorted(entry["columns"].values(), key=lambda c: c["column"])
        n = len(cols)
        t = sum(1 for c in cols if c["level"] == "tested")
        o = sum(1 for c in cols if c["level"] == "observed")
        total_cols += n
        tested_cols += t
        observed_only += o
        out_tables.append({
            "table": table,
            "columns": cols,
            "job_count": len(entry["jobs"]),
            "tested_pct": round(100.0 * t / n, 1) if n else 0.0,
        })

    result = {
        "tables": out_tables,
        "summary": {
            "tables": len(out_tables),
            "columns": total_cols,
            "tested_pct": round(100.0 * tested_cols / total_cols, 1) if total_cols else 0.0,
            "observed_pct": round(100.0 * observed_only / total_cols, 1) if total_cols else 0.0,
        },
    }
    _CACHE[cache_key] = (now, result)
    if len(_CACHE) > 100:
        cutoff = now - _CACHE_TTL_SECONDS
        for k in [k for k, (ts, _) in _CACHE.items() if ts < cutoff]:
            _CACHE.pop(k, None)
    return result


def _latest_snapshots(db: Session) -> list[SchemaSnapshot]:
    sub = (
        db.query(
            SchemaSnapshot.job_name,
            func.max(SchemaSnapshot.captured_at).label("max_captured"),
        )
        .group_by(SchemaSnapshot.job_name)
        .subquery()
    )
    return (
        db.query(SchemaSnapshot)
        .join(sub, (SchemaSnapshot.job_name == sub.c.job_name)
              & (SchemaSnapshot.captured_at == sub.c.max_captured))
        .all()
    )


def build_flaky_report(db: Session, window: int = DEFAULT_FLAKY_WINDOW) -> list[dict]:
    """Flakiness per query_name over the last `window` completed runs."""
    rows = (
        db.query(TestResult.query_name, TestResult.status,
                 TestResult.override_status, TestRun.completed_at)
        .join(TestRun, TestRun.run_id == TestResult.run_id)
        .filter(TestRun.completed_at.isnot(None))
        .filter(~TestResult.status.in_(["SKIPPED", "CANCELLED"]))
        .order_by(TestResult.query_name, TestRun.completed_at.desc())
        .all()
    )
    by_job: dict[str, list[str]] = {}
    for query_name, status, override_status, _completed in rows:
        history = by_job.setdefault(query_name, [])
        if len(history) < window:
            history.append(override_status or status)

    report = []
    for query_name, newest_first in by_job.items():
        statuses = list(reversed(newest_first))  # oldest -> newest
        score = compute_flakiness(statuses)
        if score <= 0:
            continue
        report.append({
            "job": query_name,
            "query_name": query_name,
            "score": round(score, 3),
            "transitions": sum(1 for a, b in zip(statuses, statuses[1:]) if a != b),
            "window": len(statuses),
            "flaky": score >= FLAKY_THRESHOLD,
            "recent_statuses": newest_first[:10],
        })
    report.sort(key=lambda r: -r["score"])
    return report
