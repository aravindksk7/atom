#!/usr/bin/env python3
"""CLI entry point for running ETL reconciliation tests."""
import argparse
import sys

from etl_framework.runner.test_runner import TestRunner
from etl_framework.runner.state import TestStatus
from etl_framework.utils.logging import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="ETL Framework Test Runner")
    parser.add_argument("--config", required=True, help="Path to environment config YAML")
    parser.add_argument("--source-env", required=True, help="Source environment name")
    parser.add_argument("--target-env", required=True, help="Target environment name")
    parser.add_argument("--max-workers", type=int, default=None,
                        help="Maximum parallel test workers (default: auto)")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-format", default="text", choices=["text", "json"])
    parser.add_argument("--health-check", action="store_true",
                        help="Run DB health checks and exit before running tests")
    args = parser.parse_args()

    configure_logging(level=args.log_level, log_format=args.log_format)

    if args.health_check:
        print("Health check mode: no DB connections configured yet.")
        print("Pass --health-check with a loaded config to check DB connectivity.")
        return 0

    runner = TestRunner(max_workers=args.max_workers)
    print(f"Runner configured: max_workers={runner.max_workers}")
    print(f"Source: {args.source_env} -> Target: {args.target_env}")
    print("No test cases registered yet - use this runner programmatically.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
