from pathlib import Path

import pytest

from etl_framework.expectations.suite import ExpectationSuite, load_suite, dump_suite


def test_suite_roundtrip(tmp_path: Path) -> None:
    suite = ExpectationSuite(
        job="orders_reconciliation",
        rules=[
            {"type": "not_null", "column": "id", "severity": "error"},
            {"type": "row_count_min", "min_value": 1},
        ],
    )
    path = tmp_path / "orders_reconciliation.yml"
    dump_suite(suite, path)
    loaded = load_suite(path)
    assert loaded == suite


def test_load_suite_rejects_missing_job(tmp_path: Path) -> None:
    path = tmp_path / "bad.yml"
    path.write_text("rules: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="job"):
        load_suite(path)


def test_load_suite_rejects_non_mapping(tmp_path: Path) -> None:
    path = tmp_path / "bad.yml"
    path.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_suite(path)
