import pandas as pd

from etl_framework.db.sql_utils import quote_identifier, reject_mutating_sql


def _validate_columns(columns: list[str], dialect: str = "sqlserver") -> None:
    for col in columns:
        quote_identifier(col, dialect)


def _quote_col(col: str, dialect: str = "sqlserver") -> str:
    """Wrap a column name in identifier quoting for the given dialect."""
    return quote_identifier(col.strip(), dialect)


def build_hash_query(base_query: str, key_columns: list[str], dialect: str = "sqlserver") -> str:
    if not key_columns:
        raise ValueError("key_columns must not be empty")
    _validate_columns(key_columns, dialect)
    base_query = reject_mutating_sql(base_query)
    key_list = ", ".join(_quote_col(c, dialect) for c in key_columns)
    return (
        f"SELECT {key_list}, "
        f"CHECKSUM_AGG(CHECKSUM(*)) OVER () AS hash_value "
        f"FROM ({base_query}) AS _base"
    )


def load_in_chunks(
    engine,
    query: str,
    key_columns: list[str],
    chunk_size: int,
    params: dict | None = None,
    normalize=None,
) -> pd.DataFrame:
    """Load a query fully, reading it in cursor batches of ``chunk_size`` rows.

    One query, no ORDER BY/OFFSET paging: engines with ``iter_query_batches``
    (DBEngine) stream it; anything else, or ``chunk_size`` 0, gets a single
    read. ``key_columns`` is no longer needed for loading and is kept for
    callers. ``normalize``, when given, is applied to every batch.
    """
    _n = normalize or (lambda df: df)
    iter_batches = getattr(engine, "iter_query_batches", None)
    if not chunk_size or iter_batches is None:
        if params is not None:
            return _n(engine.execute_query(query, params))
        return _n(engine.execute_query(query))
    parts = [_n(batch) for batch in iter_batches(query, params, batch_size=chunk_size)]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def hashes_match(df_source: pd.DataFrame, df_target: pd.DataFrame) -> bool:
    if len(df_source) != len(df_target):
        return False
    if "hash_value" not in df_source.columns or "hash_value" not in df_target.columns:
        return False
    return df_source["hash_value"].reset_index(drop=True).equals(
        df_target["hash_value"].reset_index(drop=True)
    )
