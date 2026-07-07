import pandas as pd
import pytest
from api.services.coverage_service import (
    extract_tables,
    compute_flakiness,
    classify_level,
)


# --- extract_tables ---

def test_extract_simple_from():
    assert extract_tables("SELECT * FROM orders") == {"orders"}


def test_extract_join_and_schema_prefix():
    sql = "SELECT * FROM dbo.orders o JOIN [dbo].[customers] c ON o.cid = c.id"
    assert extract_tables(sql) == {"dbo.orders", "dbo.customers"}


def test_extract_strips_quotes():
    assert extract_tables('SELECT * FROM "orders"') == {"orders"}


def test_extract_excludes_cte_names():
    sql = "WITH recent AS (SELECT * FROM orders) SELECT * FROM recent"
    assert extract_tables(sql) == {"orders"}


def test_extract_empty_query():
    assert extract_tables("") == set()


# --- classify_level ---

def test_tested_when_dq_rule_targets_column():
    assert classify_level(
        column="amt", rule_columns={"amt"}, reconciled_columns=set(), observed_columns=set()
    ) == "tested"


def test_tested_when_reconciled():
    assert classify_level(
        column="amt", rule_columns=set(), reconciled_columns={"amt"}, observed_columns={"amt"}
    ) == "tested"


def test_observed_when_only_profiled():
    assert classify_level(
        column="amt", rule_columns=set(), reconciled_columns=set(), observed_columns={"amt"}
    ) == "observed"


def test_untested_otherwise():
    assert classify_level(
        column="amt", rule_columns=set(), reconciled_columns=set(), observed_columns=set()
    ) == "untested"


# --- compute_flakiness ---

def test_flakiness_score_counts_transitions():
    # PASSED,FAILED,PASSED,FAILED = 3 transitions over window 4 -> 3/3 = 1.0
    statuses = ["PASSED", "FAILED", "PASSED", "FAILED"]
    assert compute_flakiness(statuses) == pytest.approx(1.0)


def test_flakiness_stable_history_is_zero():
    assert compute_flakiness(["PASSED"] * 10) == 0.0


def test_flakiness_short_history_is_zero():
    assert compute_flakiness(["PASSED"]) == 0.0
    assert compute_flakiness([]) == 0.0


def test_flakiness_one_transition():
    # 1 transition / 3 = 0.333...
    assert compute_flakiness(["PASSED", "PASSED", "FAILED", "FAILED"]) == pytest.approx(1 / 3)
