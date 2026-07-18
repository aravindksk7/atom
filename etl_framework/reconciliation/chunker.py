import pandas as pd

from etl_framework.db.sql_utils import quote_identifier, reject_mutating_sql


def _validate_columns(columns: list[str]) -> None:
    for col in columns:
        quote_identifier(col, "sqlserver")


def _quote_col(col: str) -> str:
    """Wrap a column name in MSSQL square-bracket quoting."""
    return quote_identifier(col.strip(), "sqlserver")


def build_hash_query(base_query: str, key_columns: list[str]) -> str:
    if not key_columns:
        raise ValueError("key_columns must not be empty")
    _validate_columns(key_columns)
    base_query = reject_mutating_sql(base_query)
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
    base_query = reject_mutating_sql(base_query)
    order_cols = ", ".join(_quote_col(c) for c in key_columns)
    return (
        f"SELECT * FROM ({base_query}) AS _base "
        f"ORDER BY {order_cols} "
        f"OFFSET {offset} ROWS FETCH NEXT {chunk_size} ROWS ONLY"
    )


def load_in_chunks(
    engine,
    query: str,
    key_columns: list[str],
    chunk_size: int,
    params: dict | None = None,
    normalize=None,
) -> pd.DataFrame:
    """Load a query fully via OFFSET/FETCH pagination.

    Falls back to a single read when chunk_size is 0 or key_columns are empty
    because ORDER BY keys are required for deterministic pagination. normalize,
    when given, is applied to every chunk and to single-read frames.
    """
    _n = normalize or (lambda df: df)
    _execute = (
        (lambda q: engine.execute_query(q, params))
        if params is not None
        else engine.execute_query
    )
    if not chunk_size or not key_columns:
        return _n(_execute(query))
    parts: list[pd.DataFrame] = []
    offset = 0
    while True:
        q = build_chunk_query(query, key_columns, offset, chunk_size)
        chunk = _n(_execute(q))
        if chunk.empty:
            break
        parts.append(chunk)
        if len(chunk) < chunk_size:
            break
        offset += chunk_size
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def hashes_match(df_source: pd.DataFrame, df_target: pd.DataFrame) -> bool:
    if len(df_source) != len(df_target):
        return False
    if "hash_value" not in df_source.columns or "hash_value" not in df_target.columns:
        return False
    return df_source["hash_value"].reset_index(drop=True).equals(
        df_target["hash_value"].reset_index(drop=True)
    )
