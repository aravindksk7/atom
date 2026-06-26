"""Schema snapshot utilities: capture column metadata and diff two snapshots."""
from __future__ import annotations
import pandas as pd


def capture_schema(df: pd.DataFrame) -> list[dict]:
    """Return [{name, dtype}] preserving column order from the DataFrame."""
    return [{"name": col, "dtype": str(df[col].dtype)} for col in df.columns]


def diff_schemas(
    current: list[dict],
    previous: list[dict],
) -> dict[str, list]:
    """Return {added, removed, changed} between two schema snapshots."""
    curr_map = {col["name"]: col["dtype"] for col in current}
    prev_map = {col["name"]: col["dtype"] for col in previous}
    added = [name for name in curr_map if name not in prev_map]
    removed = [name for name in prev_map if name not in curr_map]
    changed = [
        {"column": name, "from": prev_map[name], "to": curr_map[name]}
        for name in curr_map
        if name in prev_map and curr_map[name] != prev_map[name]
    ]
    return {"added": added, "removed": removed, "changed": changed}
