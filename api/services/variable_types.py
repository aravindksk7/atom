from __future__ import annotations

import re
from datetime import date, timedelta

VAR_TYPES = ("text", "number", "date", "alphanumeric")

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALPHANUMERIC_RE = re.compile(r"^[A-Za-z0-9]+$")
_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
_DATE_LITERAL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_EXPR_RE = re.compile(r"^today([+-]\d+)?$", re.IGNORECASE)


def validate_variable_name(name: str) -> None:
    """Raise ValueError unless `name` is a valid {{placeholder}} identifier."""
    if not _NAME_RE.match(name):
        raise ValueError(
            "name must start with a letter or underscore and contain only "
            "letters, digits, and underscores"
        )


def validate_variable_value(value: str, var_type: str) -> None:
    """Raise ValueError if `value` doesn't satisfy `var_type`'s format."""
    if var_type == "text":
        return
    if var_type == "alphanumeric":
        if not _ALPHANUMERIC_RE.match(value):
            raise ValueError(f"'{value}' is not a valid alphanumeric value (letters and digits only)")
        return
    if var_type == "number":
        if not _NUMBER_RE.match(value):
            raise ValueError(f"'{value}' is not a valid number")
        return
    if var_type == "date":
        stripped = value.strip()
        if _DATE_EXPR_RE.match(stripped):
            return
        if _DATE_LITERAL_RE.match(stripped):
            try:
                date.fromisoformat(stripped)
                return
            except ValueError:
                pass
        raise ValueError(
            f"'{value}' is not a valid date: use YYYY-MM-DD, 'today', 'today+N', or 'today-N'"
        )
    raise ValueError(f"unknown variable type '{var_type}'")


def resolve_date_expression(value: str) -> str:
    """Evaluate 'today'/'today+N'/'today-N' to a concrete ISO date. A literal
    YYYY-MM-DD (or anything else that isn't a today-expression) passes
    through unchanged."""
    match = _DATE_EXPR_RE.match(value.strip())
    if not match:
        return value
    offset = int(match.group(1)) if match.group(1) else 0
    return (date.today() + timedelta(days=offset)).isoformat()
