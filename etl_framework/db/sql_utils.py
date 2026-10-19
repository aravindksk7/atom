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


def _without_comments(sql: str, backslash_escapes: bool = False) -> str:
    """Single left-to-right scan: literals become ``''``, comments become a space.

    Quoted identifiers ("x", [x]) are blanked the same way so a ``--`` inside
    them is not mistaken for a comment that hides a following statement.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"', "["):
            close = "]" if ch == "[" else ch
            j = i + 1
            while j < n:
                if backslash_escapes and ch == "'" and sql[j] == "\\":
                    j += 2  # Postgres E'...' style: backslash escapes the next char
                    continue
                if sql[j] == close:
                    if close != "]" and j + 1 < n and sql[j + 1] == close:
                        j += 2  # doubled quote is an escape
                        continue
                    break
                j += 1
            out.append("''" if ch == "'" else '""')
            i = j + 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j == -1 else j
            out.append(" ")
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j == -1 else j + 2
            out.append(" ")
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def is_read_only_sql(sql: str) -> bool:
    # Dialects disagree on whether a backslash escapes a quote inside a literal
    # (Postgres E'..' yes, SQL Server no), so require read-only under both readings.
    return all(
        _read_only_after_cleaning(_without_comments(sql, backslash_escapes=flag))
        for flag in (False, True)
    )


def _read_only_after_cleaning(text: str) -> bool:
    cleaned = strip_trailing_semicolon(text).strip()
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
    d = dialect.lower()
    if d in {"sqlserver", "mssql"}:
        cleaned = identifier.strip()
        if not _BRACKET_SAFE_RE.fullmatch(cleaned):
            raise ValueError(f"Invalid SQL identifier: {identifier!r}")
        return f"[{cleaned}]"
    validate_identifier(identifier)
    if d == "mysql":
        return f"`{identifier}`"
    return '"' + identifier.replace('"', '""') + '"'


def wrap_query(sql: str, alias: str = "q") -> str:
    validate_identifier(alias)
    return f"({reject_mutating_sql(sql)}) AS {quote_identifier(alias, 'ansi')}"
