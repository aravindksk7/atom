"""Versioned expectation suites: DQ rules as YAML files under source control.

A suite file maps 1:1 to a job. Format:

    job: orders_reconciliation
    rules:
      - type: not_null
        column: id
        severity: error

Rule dicts are validated against ``api.schemas.DQRule`` at sync time (API
layer) — this module stays free of ``api`` imports so it can be used from
scripts and CI without the web app.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ExpectationSuite(BaseModel):
    job: str = Field(min_length=1)
    rules: list[dict[str, Any]] = Field(default_factory=list)


def load_suite(path: str | Path) -> ExpectationSuite:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: suite file must be a YAML mapping")
    if not raw.get("job"):
        raise ValueError(f"{path}: suite file must set 'job'")
    return ExpectationSuite.model_validate(raw)


def dump_suite(suite: ExpectationSuite, path: str | Path) -> None:
    Path(path).write_text(
        yaml.safe_dump(suite.model_dump(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def load_suites(directory: str | Path) -> list[ExpectationSuite]:
    """Load every ``*.yml``/``*.yaml`` file in *directory*, sorted by filename."""
    dir_path = Path(directory)
    suites = []
    for path in sorted(list(dir_path.glob("*.yml")) + list(dir_path.glob("*.yaml"))):
        suites.append(load_suite(path))
    return suites
