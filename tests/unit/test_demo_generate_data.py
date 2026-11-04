"""The demo's seeded data must hold exactly the differences the video narrates."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

GENERATOR = Path(__file__).resolve().parents[2] / "demo" / "seed" / "generate_data.py"
DATE = "2026-06-03"


@pytest.fixture(scope="module")
def generator():
    spec = importlib.util.spec_from_file_location("demo_generate_data", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolves annotations through sys.modules
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def dataset(generator):
    return generator.build_dataset(DATE)


def by_key(rows, key="trade_id"):
    return {row[key]: row for row in rows}


def feed(dataset, name):
    return next(f for f in dataset.feeds if f.name == name)


def changed_fields(prod_rows, test_rows, key="trade_id"):
    prod, test = by_key(prod_rows, key), by_key(test_rows, key)
    return {
        column
        for trade_id in prod.keys() & test.keys()
        for column in prod[trade_id]
        if prod[trade_id][column] != test[trade_id][column]
    }


def test_is_deterministic(generator, dataset):
    assert generator.build_dataset(DATE) == dataset


def test_has_100_etls_in_4_sets_of_25(dataset):
    assert len(dataset.etls) == 100
    for number in range(1, 5):
        assert sum(name.startswith(f"SET{number}_") for name in dataset.etls) == 25


def test_has_64_csv_and_40_xml_feeds(dataset):
    assert sum(f.fmt == "csv" for f in dataset.feeds) == 64
    assert sum(f.fmt == "xml" for f in dataset.feeds) == 40


def test_the_failing_etl_is_the_last_of_set_3(dataset):
    assert dataset.failing_etl == "SET3_ETL_075"
    set_3 = [name for name in dataset.etls if name.startswith("SET3_")]
    assert set_3[-1] == dataset.failing_etl


def test_missing_record_is_only_in_its_feed(dataset):
    missing = feed(dataset, "gl_005")
    assert len(missing.test_rows) == len(missing.prod_rows) - 1


def test_additional_record_is_only_in_its_feed(dataset):
    extra = feed(dataset, "gl_020")
    assert len(extra.test_rows) == len(extra.prod_rows) + 1


def test_wrong_notional_is_in_one_xml_row(dataset):
    wrong = feed(dataset, "gl_003")
    assert wrong.fmt == "xml"
    assert changed_fields(wrong.prod_rows, wrong.test_rows) == {"notional"}
    prod, test = by_key(wrong.prod_rows), by_key(wrong.test_rows)
    assert sum(prod[k]["notional"] != test[k]["notional"] for k in prod) == 1


def test_only_fee_amount_changes_in_every_other_feed(dataset):
    exceptions = {"gl_003", "gl_005", "gl_020"}
    impacted = 0
    for f in dataset.feeds:
        if f.name in exceptions:
            continue
        assert len(f.prod_rows) == len(f.test_rows), f.name
        changed = changed_fields(f.prod_rows, f.test_rows)
        assert changed <= {"fee_amount"}, f.name
        impacted += bool(changed)
    assert impacted >= 30


def test_trades_differ_by_fee_amount_and_one_notional(dataset):
    prod, test = by_key(dataset.trades_prod), by_key(dataset.trades_test)
    assert prod.keys() == test.keys()
    assert changed_fields(dataset.trades_prod, dataset.trades_test) == {"fee_amount", "notional"}
    assert sum(prod[k]["notional"] != test[k]["notional"] for k in prod) == 1


def test_positions_are_missing_one_row_in_postgres(dataset):
    assert len(dataset.positions_test) == len(dataset.positions_prod) - 1
    assert changed_fields(dataset.positions_prod, dataset.positions_test, "position_id") == set()


def test_bo_summary_baseline_differs_only_on_the_amount_total(dataset):
    summary = {row["metric"]: row["value"] for row in dataset.bo_baselines["sales_summary"]}
    # The BO mock's live value for this total is 150.50.
    assert summary == {"orders": 3, "amount": 151.50}


def test_files_are_written_where_the_jobs_look(generator, dataset, tmp_path):
    generator.write_local_files(dataset, tmp_path)

    # Test outputs are templates the SAP DS mock publishes when an ETL succeeds.
    etl_of_gl_003 = feed(dataset, "gl_003").etl
    assert (tmp_path / "templates" / etl_of_gl_003 / "gl_003.xml").exists()
    assert (tmp_path / "crushftp" / "prod" / DATE / "positions.csv").exists()
    assert (tmp_path / "bo" / DATE / "sales_summary.csv").exists()
    jobs = (tmp_path / "sapds-jobs.json").read_text()
    assert '"SET3_ETL_075": "Error"' in jobs


def test_generated_files_are_readable_by_atom(generator, dataset, tmp_path):
    from api.services.file_source import read_tabular

    generator.write_local_files(dataset, tmp_path)
    f = feed(dataset, "gl_003")
    frame = read_tabular(
        content_b64=__import__("base64").b64encode(
            (tmp_path / "templates" / f.etl / "gl_003.xml").read_bytes()
        ).decode("ascii"),
        file_name="gl_003.xml",
    )
    assert len(frame) == len(f.test_rows)
    assert {"trade_id", "notional", "fee_amount"} <= set(frame.columns)
