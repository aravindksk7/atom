from __future__ import annotations

from sqlalchemy import inspect, text


def table_exists(bind, table_name: str) -> bool:
    return table_name in set(inspect(bind).get_table_names())


def column_exists(bind, table_name: str, column_name: str) -> bool:
    if not table_exists(bind, table_name):
        return False
    return column_name in {col["name"] for col in inspect(bind).get_columns(table_name)}


def index_exists(bind, index_name: str) -> bool:
    inspector = inspect(bind)
    for table_name in inspector.get_table_names():
        if index_name in {idx["name"] for idx in inspector.get_indexes(table_name)}:
            return True
    return False


def execute_ddl(bind, ddl: str) -> None:
    if hasattr(bind, "execute"):
        bind.execute(text(ddl))
        return
    with bind.begin() as conn:
        conn.execute(text(ddl))


def ensure_column(bind, table_name: str, column_name: str, ddl: str) -> bool:
    if column_exists(bind, table_name, column_name):
        return False
    execute_ddl(bind, ddl)
    return True


def ensure_table(bind, table_name: str, ddl: str) -> bool:
    if table_exists(bind, table_name):
        return False
    execute_ddl(bind, ddl)
    return True


def ensure_index(bind, index_name: str, ddl: str) -> bool:
    if index_exists(bind, index_name):
        return False
    execute_ddl(bind, ddl)
    return True


def execute_once(bind, ddl: str) -> None:
    execute_ddl(bind, ddl)
