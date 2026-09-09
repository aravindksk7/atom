from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from api.schemas import JobDefinition
from api.services.variable_types import resolve_date_expression
from etl_framework.repository.repository import ConfigRepository, CustomVariableRepository

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def resolve_variables(
    db: Session,
    config_id: int | None,
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """Merge global default -> per-Config override -> launch override, then
    evaluate any 'today'/'today+N'/'today-N' date value to a concrete ISO
    date. Call this once per run (not once per job) so every job in a
    sequence sees identical values, even across a midnight boundary."""
    global_vars = CustomVariableRepository(db).list()
    global_names = {v.name for v in global_vars}
    var_types = {v.name: v.var_type for v in global_vars}

    resolved: dict[str, str] = {
        v.name: v.default_value for v in global_vars if v.default_value is not None
    }

    if config_id is not None:
        cfg = ConfigRepository(db).get(config_id)
        cfg_overrides = (cfg.config_json or {}).get("variables", {}) if cfg is not None else {}
        for name, value in cfg_overrides.items():
            if name in global_names and value:
                resolved[name] = value

    for name, value in (overrides or {}).items():
        if name in global_names and value:
            resolved[name] = value

    for name in list(resolved):
        if var_types.get(name) == "date":
            resolved[name] = resolve_date_expression(str(resolved[name]))

    return resolved


def _substitute_text(text: str, variables: dict[str, str]) -> str:
    def _replace(match: re.Match) -> str:
        return variables.get(match.group(1), match.group(0))
    return _PLACEHOLDER_RE.sub(_replace, text)


def _substitute_value(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _substitute_text(value, variables)
    if isinstance(value, dict):
        return {k: _substitute_value(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_value(v, variables) for v in value]
    return value


def substitute_in_job(job: JobDefinition, variables: dict[str, str]) -> JobDefinition:
    """Return a copy of `job` with every {{name}} in `query` and every string
    leaf inside `params` (dicts/lists walked recursively) replaced by its
    resolved value. An unresolved placeholder (e.g. a typo'd name) is left
    verbatim rather than raising."""
    if not variables:
        return job
    return job.model_copy(update={
        "query": _substitute_text(job.query, variables),
        "params": _substitute_value(job.params, variables),
    })
