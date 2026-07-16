from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from etl_framework.config.loader import ConfigLoader
from etl_framework.db.engine import DBEngine
from etl_framework.runner.health import HealthChecker
from etl_framework.runner.test_runner import TestRunner
from etl_framework.utils.logging import configure_logging


def _default_gate_session_factory():
    from etl_framework.repository.database import SessionLocal, init_db
    init_db()
    return SessionLocal


_gate_session_factory = None  # test seam; resolved lazily in _gate_exit_code


def _gate_exit_code(run_id: str, output: str) -> int:
    from etl_framework.repository.models import TestRun
    factory = _gate_session_factory or _default_gate_session_factory()
    session = factory()
    try:
        run = session.query(TestRun).filter(TestRun.run_id == run_id).first()
        if run is None:
            verdict, code = "NOT_FOUND", 4
        elif run.status == "CANCELLED":
            verdict, code = "CANCELLED", 2
        elif (run.error or 0) > 0 or run.status == "ERROR":
            verdict, code = "ERROR", 3
        elif (run.failed or 0) > 0 or run.status == "FAILED":
            verdict, code = "FAILED", 1
        else:
            verdict, code = "PASSED", 0
        if output == "json":
            print(json.dumps({
                "run_id": run_id, "verdict": verdict, "exit_code": code,
                "passed": getattr(run, "passed", None),
                "failed": getattr(run, "failed", None),
                "error": getattr(run, "error", None),
            }))
        else:
            print(f"{verdict} run={run_id} exit={code}")
        return code
    finally:
        session.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ETL Framework Test Runner")
    parser.add_argument("--config", required=False, help="Path to environment config YAML")
    parser.add_argument("--source-env", required=False, help="Source environment name")
    parser.add_argument("--target-env", required=False, help="Target environment name")
    parser.add_argument("--max-workers", type=int, default=None, help="Maximum parallel test workers (default: auto)")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-format", default="text", choices=["text", "json"])
    parser.add_argument("--health-check", action="store_true", help="Run DB health checks and exit before running tests")
    parser.add_argument("--metrics-output", default=None, help="Write metrics JSON sidecar to this path after test run")
    parser.add_argument("--output", choices=["text", "json"], default="text")
    parser.add_argument(
        "--gate-run", default=None, metavar="RUN_ID",
        help="CI gate: exit 0=passed 1=failed 2=cancelled 3=error 4=not-found for the given run, then stop",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(level=args.log_level, log_format=args.log_format)
    if args.gate_run:
        return _gate_exit_code(args.gate_run, args.output)
    if not (args.config and args.source_env and args.target_env):
        parser.error("--config, --source-env and --target-env are required unless --gate-run is used")
    environments = ConfigLoader().load(args.config)

    missing = [name for name in (args.source_env, args.target_env) if name not in environments]
    if missing:
        parser.error(f"Environment not found in config: {', '.join(missing)}")

    if args.health_check:
        checker = HealthChecker()
        checks = [
            checker.check_db(args.source_env, DBEngine(environments[args.source_env])),
            checker.check_db(args.target_env, DBEngine(environments[args.target_env])),
        ]
        if args.output == "json":
            print(json.dumps([check.__dict__ for check in checks], default=str))
        else:
            for check in checks:
                status = "OK" if check.healthy else "FAIL"
                print(f"{status} {check.component}: {check.message}")
        return 0 if all(check.healthy for check in checks) else 1

    runner = TestRunner(max_workers=args.max_workers)
    if args.metrics_output:
        Path(args.metrics_output).write_text(json.dumps({"total": 0, "passed": 0, "failed": 0}), encoding="utf-8")
    if args.output == "json":
        print(json.dumps({"max_workers": runner.max_workers, "results": []}))
    else:
        print(f"Runner configured: max_workers={runner.max_workers}")
        print(f"Source: {args.source_env} -> Target: {args.target_env}")
        print("No test cases registered yet - use this runner programmatically.")
    return 0
