import re

import pandas as pd

# Column identifiers must consist of word characters and spaces only.
# Anything else (semicolons, dashes, quotes, etc.) is rejected to prevent
# SQL injection through user-supplied key_columns values.
_SAFE_COLUMN = re.compile(r"^[\w ]+$")


def _validate_columns(columns: list[str]) -> None:
    for col in columns:
        if not _SAFE_COLUMN.match(col):
            raise ValueError(f"Unsafe column name rejected: {col!r}")


def _quote_col(col: str) -> str:
    """Wrap a column name in MSSQL square-bracket quoting."""
    return f"[{col.strip()}]"


def build_hash_query(base_query: str, key_columns: list[str]) -> str:
    if not key_columns:
        raise ValueError("key_columns must not be empty")
    _validate_columns(key_columns)
    key_list = ", ".join(_quote_col(c) for c in key_columns)
    return (
        f"SELECT {key_list}, "
        f"CHECKSUM_AGG(CHECKSUM(*)) OVER () AS hash_value "
        f"FROM ({base_query}) AS _base"
    )


def build_chunk_query(
    base_query: str,
    key_columns: list[str],
    offset: int,
    chunk_size: int,
) -> str:
    if not key_columns:
        raise ValueError("key_columns must not be empty")
    _validate_columns(key_columns)
    order_cols = ", ".join(_quote_col(c) for c in key_columns)
    return (
        f"SELECT * FROM ({base_query}) AS _base "
        f"ORDER BY {order_cols} "
        f"OFFSET {offset} ROWS FETCH NEXT {chunk_size} ROWS ONLY"
    )


def hashes_match(df_source: pd.DataFrame, df_target: pd.DataFrame) -> bool:
    if len(df_source) != len(df_target):
        return False
    if "hash_value" not in df_source.columns or "hash_value" not in df_target.columns:
        return False
    return df_source["hash_value"].reset_index(drop=True).equals(
        df_target["hash_value"].reset_index(drop=True)
    )
