#!/usr/bin/env python3
"""CLI entry point for running ETL reconciliation tests."""
import sys

from etl_framework.runner.cli import main


if __name__ == "__main__":
    sys.exit(main())
