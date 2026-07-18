from __future__ import annotations

import argparse
import json
from datetime import datetime
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
_stats_session_factory = None  # test seam; resolved lazily in _scheduler_stats_exit_code
_report_session_factory = None  # test seam; resolved lazily in _scheduler_report_exit_code


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


def _default_stats_session_factory():
    from etl_framework.repository.database import SessionLocal, init_db
    init_db()
    return SessionLocal


def _default_report_session_factory():
    from etl_framework.repository.database import SessionLocal, init_db
    init_db()
    return SessionLocal


def _parse_report_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _print_scheduler_stats_text(stats: dict) -> None:
    summary = stats["summary"]
    scheduler = stats["scheduler"]
    gate = stats["gate"]
    state = "running" if scheduler["running"] else "stopped"
    if not scheduler["available"]:
        state = "unavailable"
    print(f"Scheduler: {state} jobs={scheduler['job_count']} timezone={scheduler['timezone']}")
    print(
        f"Window: {stats['window_days']} days schedules={summary['total_schedules']} "
        f"enabled={summary['enabled_schedules']} runs={summary['runs_triggered']}"
    )
    print(
        f"Outcomes: passed={summary['passed']} failed={summary['failed']} "
        f"error={summary['error']} cancelled={summary['cancelled']} blocked={summary['blocked']}"
    )
    print(
        f"Success rate: {summary['success_rate'] if summary['success_rate'] is not None else 'n/a'} "
        f"avg_duration_seconds={summary['average_duration_seconds'] if summary['average_duration_seconds'] is not None else 'n/a'}"
    )
    print(f"Gate: {gate['status']} exit={gate['exit_code']}")
    for reason in gate["reasons"]:
        print(f"- {reason}")


def _scheduler_stats_exit_code(args) -> int:
    from api.services.scheduler_stats import GateOptions, build_scheduler_stats

    session = None
    try:
        factory = _stats_session_factory or _default_stats_session_factory()
        session = factory()
        stats = build_scheduler_stats(
            session,
            days=args.days,
            gate_options=GateOptions(
                fail_on_stopped=args.fail_on_stopped,
                min_success_rate=args.min_success_rate,
            ),
        )
        if args.output == "json":
            print(json.dumps(stats, default=str))
        else:
            _print_scheduler_stats_text(stats)
        return int(stats["gate"]["exit_code"])
    except Exception as exc:
        if args.output == "json":
            print(json.dumps({"error": str(exc), "exit_code": 1}))
        else:
            print(f"ERROR scheduler stats: {exc}")
        return 1
    finally:
        if session is not None:
            session.close()


def _scheduler_report_filters(args):
    from api.services.scheduler_reporting import SchedulerReportFilters

    return SchedulerReportFilters(
        from_dt=_parse_report_datetime(args.from_dt),
        to_dt=_parse_report_datetime(args.to_dt),
        days=args.days,
        job=args.job,
        status=args.status,
        exit_code=args.exit_code,
    )


def _format_scheduler_report_text(summary: dict, rows: list[dict], summary_only: bool) -> str:
    counts = summary["summary"]
    lines = [
        "Scheduler report",
        f"Events: total={counts['total_events']}",
        (
            f"Outcomes: passed={counts['passed']} failed={counts['failed']} "
            f"error={counts['error']} cancelled={counts['cancelled']} blocked={counts['blocked']}"
        ),
        f"Success rate: {counts['success_rate'] if counts['success_rate'] is not None else 'n/a'}",
    ]
    if not summary_only:
        for row in rows:
            lines.append(
                f"{row['created_at']} {row['schedule_name']} {row['status']} "
                f"exit={row['exit_code']} run={row['run_id'] or 'n/a'}"
            )
    return "\n".join(lines) + "\n"


def _scheduler_report_exit_code(args) -> int:
    from api.services.scheduler_reporting import SchedulerReportingService

    session = None
    try:
        factory = _report_session_factory or _default_report_session_factory()
        session = factory()
        service = SchedulerReportingService(session)
        filters = _scheduler_report_filters(args)
        summary = service.summary(filters)
        rows = [] if args.summary else service.export_rows(filters)
        if args.format == "json":
            output = json.dumps({"summary": summary, "rows": rows}, default=str)
        elif args.format == "csv":
            output = service.export_csv(filters)
        else:
            output = _format_scheduler_report_text(summary, rows, args.summary)
        if args.report_output:
            Path(args.report_output).write_text(output, encoding="utf-8")
        else:
            print(output, end="")
        return 0
    except Exception as exc:
        if args.format == "json":
            print(json.dumps({"error": str(exc), "exit_code": 1}))
        else:
            print(f"ERROR scheduler report: {exc}")
        return 1
    finally:
        if session is not None:
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
    parser.add_argument("--scheduler-stats", action="store_true", help="Report scheduler execution and runtime statistics, then stop")
    parser.add_argument("--days", type=int, default=30, help="Scheduler stats lookback window in days, 1..365")
    parser.add_argument("--fail-on-stopped", action="store_true", help="Scheduler stats gate: fail when scheduler is unavailable or stopped")
    parser.add_argument("--min-success-rate", type=float, default=None, help="Scheduler stats gate: fail when aggregate success rate is below this percentage")
    parser.add_argument("--scheduler-report", action="store_true", help="Report scheduler telemetry, then stop")
    parser.add_argument("--summary", action="store_true", help="Only include scheduler report summary output")
    parser.add_argument("--from", dest="from_dt", default=None, help="Scheduler report start timestamp (ISO 8601)")
    parser.add_argument("--to", dest="to_dt", default=None, help="Scheduler report end timestamp (ISO 8601)")
    parser.add_argument("--job", default=None, help="Scheduler report job or schedule-name filter")
    parser.add_argument("--status", default=None, help="Scheduler report status filter")
    parser.add_argument("--exit-code", type=int, default=None, help="Scheduler report exit-code filter")
    parser.add_argument("--format", choices=["text", "json", "csv"], default="text", help="Scheduler report output format")
    parser.add_argument("--report-output", default=None, help="Write scheduler report output to this path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(level=args.log_level, log_format=args.log_format)
    if args.gate_run:
        return _gate_exit_code(args.gate_run, args.output)
    if (args.scheduler_stats or args.scheduler_report) and (args.days < 1 or args.days > 365):
        parser.error("--days must be between 1 and 365")
    if args.scheduler_stats:
        if args.min_success_rate is not None and (args.min_success_rate < 0 or args.min_success_rate > 100):
            parser.error("--min-success-rate must be between 0 and 100")
        return _scheduler_stats_exit_code(args)
    if args.scheduler_report:
        return _scheduler_report_exit_code(args)
    if not (args.config and args.source_env and args.target_env):
        parser.error("--config, --source-env and --target-env are required unless --gate-run, --scheduler-stats, or --scheduler-report is used")
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


if __name__ == "__main__":
    raise SystemExit(main())
