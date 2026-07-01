from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from etl_framework.reconciliation.models import MismatchRecord


def _mismatch_key(m: MismatchRecord) -> tuple:
    """Stable hashable key for a mismatch: (frozen key_values, column_name, mismatch_type)."""
    return (tuple(sorted(m.key_values.items())), m.column_name, m.mismatch_type)


@dataclass
class MismatchDiffResult:
    """Comparison between two sets of mismatches (e.g. two consecutive runs).

    Categories:
    - **new**: mismatches present in `run_b` but not `run_a` (regressions).
    - **resolved**: mismatches present in `run_a` but not `run_b` (fixes).
    - **persistent**: mismatches present in both runs (unresolved issues).
    """
    query_name: str
    run_a_label: str
    run_b_label: str
    compared_at: datetime
    new: list[MismatchRecord] = field(default_factory=list)
    resolved: list[MismatchRecord] = field(default_factory=list)
    persistent: list[MismatchRecord] = field(default_factory=list)

    @property
    def has_regressions(self) -> bool:
        return bool(self.new)

    @property
    def summary(self) -> dict[str, int]:
        return {
            "new": len(self.new),
            "resolved": len(self.resolved),
            "persistent": len(self.persistent),
        }


def diff_mismatches(
    mismatches_a: list[MismatchRecord],
    mismatches_b: list[MismatchRecord],
    query_name: str = "",
    run_a_label: str = "run_a",
    run_b_label: str = "run_b",
) -> MismatchDiffResult:
    """Diff two mismatch lists and categorise each entry.

    Args:
        mismatches_a: Mismatches from the baseline run (e.g. yesterday).
        mismatches_b: Mismatches from the current run (e.g. today).

    Returns:
        A :class:`MismatchDiffResult` with ``new``, ``resolved``, and
        ``persistent`` lists.
    """
    keys_a = {_mismatch_key(m): m for m in mismatches_a}
    keys_b = {_mismatch_key(m): m for m in mismatches_b}

    new = [keys_b[k] for k in keys_b if k not in keys_a]
    resolved = [keys_a[k] for k in keys_a if k not in keys_b]
    persistent = [keys_b[k] for k in keys_b if k in keys_a]

    return MismatchDiffResult(
        query_name=query_name,
        run_a_label=run_a_label,
        run_b_label=run_b_label,
        compared_at=datetime.now(timezone.utc),
        new=new,
        resolved=resolved,
        persistent=persistent,
    )
