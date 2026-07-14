from __future__ import annotations

import re
from typing import Iterable


MUTATING_SQL_KEYWORDS = frozenset({
    "insert", "update", "delete", "drop", "alter", "truncate", "merge",
    "create", "replace", "grant", "revoke", "exec", "execute", "call",
})

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#@]*$")
_BRACKET_SAFE_RE = re.compile(r"^[\w ]+$", re.UNICODE)


def strip_trailing_semicolon(sql: str) -> str:
    return sql.strip().rstrip(";").strip()


def _without_comments(sql: str) -> str:
    no_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--.*?$", " ", no_block, flags=re.MULTILINE)


def is_read_only_sql(sql: str) -> bool:
    cleaned = strip_trailing_semicolon(_without_comments(sql)).strip()
    if not cleaned:
        return False
    if ";" in cleaned:
        return False
    tokens = [token.lower() for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", cleaned)]
    if not tokens:
        return False
    if tokens[0] not in {"select", "with"}:
        return False
    return not any(token in MUTATING_SQL_KEYWORDS for token in tokens)


def reject_mutating_sql(sql: str) -> str:
    if not is_read_only_sql(sql):
        raise ValueError("SQL must be a single read-only SELECT or WITH query")
    return strip_trailing_semicolon(sql)


def validate_identifier(identifier: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(identifier):
        raise ValueError(f"Invalid SQL identifier: {identifier!r}")
    return identifier


def validate_identifiers(identifiers: Iterable[str]) -> list[str]:
    return [validate_identifier(identifier) for identifier in identifiers]


def quote_identifier(identifier: str, dialect: str = "sqlserver") -> str:
    if dialect.lower() in {"sqlserver", "mssql"}:
        cleaned = identifier.strip()
        if not _BRACKET_SAFE_RE.fullmatch(cleaned):
            raise ValueError(f"Invalid SQL identifier: {identifier!r}")
        return f"[{cleaned}]"
    validate_identifier(identifier)
    if dialect.lower() == "mysql":
        return f"`{identifier}`"
    return '"' + identifier.replace('"', '""') + '"'


def wrap_query(sql: str, alias: str = "q") -> str:
    validate_identifier(alias)
    return f"({reject_mutating_sql(sql)}) AS {quote_identifier(alias, 'ansi')}"
