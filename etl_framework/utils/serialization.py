from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any


def json_safe(value: Any) -> Any:
    """Return a JSON-serializable representation of common framework values."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: json_safe(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [json_safe(v) for v in value]

    try:
        import numpy as np

        if isinstance(value, np.generic):
            return json_safe(value.item())
        if isinstance(value, np.ndarray):
            return [json_safe(v) for v in value.tolist()]
    except Exception:
        pass

    try:
        import pandas as pd

        if value is pd.NaT:
            return None
        if pd.isna(value) and not isinstance(value, list | tuple | dict | set):
            return None
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
    except Exception:
        pass

    return str(value)


def csv_safe(value: Any) -> str:
    """Return a stable string for CSV cells."""
    safe = json_safe(value)
    if safe is None:
        return ""
    if isinstance(safe, dict | list):
        return json.dumps(safe, ensure_ascii=False, default=str)
    return str(safe)


def dataclass_to_dict(value: Any) -> dict[str, Any]:
    if not dataclasses.is_dataclass(value) or isinstance(value, type):
        raise TypeError("value must be a dataclass instance")
    return json_safe(value)


def datetime_to_iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None
