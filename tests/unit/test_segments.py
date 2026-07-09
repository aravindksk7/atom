import pytest
from etl_framework.reconciliation.models import MismatchRecord
from etl_framework.reconciliation.segments import (
    pick_auto_segment_columns,
    build_segment_summary,
)


class FakeProfile:
    def __init__(self, column_name, distinct_count):
        self.column_name = column_name
        self.distinct_count = distinct_count


def _mm(mtype="value_diff", segment_values=None):
    return MismatchRecord(
        key_values={"id": 1}, column_name="amt",
        source_value=1, target_value=2, mismatch_type=mtype,
        segment_values=segment_values,
    )


# --- pick_auto_segment_columns ---

def test_auto_pick_respects_distinct_count_cutoff():
    profiles = [FakeProfile("region", 4), FakeProfile("customer_id", 90000)]
    assert pick_auto_segment_columns(profiles, key_columns=["id"]) == ["region"]


def test_auto_pick_excludes_key_columns():
    profiles = [FakeProfile("id", 10), FakeProfile("region", 4)]
    assert pick_auto_segment_columns(profiles, key_columns=["id"]) == ["region"]


def test_auto_pick_max_three_lowest_distinct_first():
    profiles = [
        FakeProfile("a", 40), FakeProfile("b", 2),
        FakeProfile("c", 10), FakeProfile("d", 5),
    ]
    assert pick_auto_segment_columns(profiles, key_columns=[]) == ["b", "d", "c"]


def test_auto_pick_skips_none_distinct_count():
    profiles = [FakeProfile("x", None), FakeProfile("region", 3)]
    assert pick_auto_segment_columns(profiles, key_columns=[]) == ["region"]


def test_auto_pick_empty_profiles_returns_empty():
    assert pick_auto_segment_columns([], key_columns=["id"]) == []


# --- build_segment_summary ---

def test_summary_groups_by_segment_value_with_counts_and_pct():
    mismatches = [
        _mm("value_diff", {"region": "EMEA"}),
        _mm("value_diff", {"region": "EMEA"}),
        _mm("missing_in_target", {"region": "EMEA"}),
        _mm("missing_in_source", {"region": "APAC"}),
    ]
    summary = build_segment_summary(mismatches, ["region"])
    emea = summary["region"][0]
    assert emea["value"] == "EMEA"
    assert emea["mismatch_count"] == 3
    assert emea["value_diff"] == 2
    assert emea["missing_in_target"] == 1
    assert emea["missing_in_source"] == 0
    assert emea["pct_of_total"] == 75.0
    apac = summary["region"][1]
    assert apac["missing_in_source"] == 1


def test_summary_null_segment_value_bucketed_as_null_literal():
    mismatches = [_mm(segment_values={"region": None}), _mm(segment_values=None)]
    summary = build_segment_summary(mismatches, ["region"])
    assert summary["region"][0]["value"] == "(null)"
    assert summary["region"][0]["mismatch_count"] == 2


def test_summary_truncates_to_top_20_values():
    mismatches = []
    for i in range(25):
        # value i appears (i+1) times so ordering is deterministic
        mismatches.extend(_mm(segment_values={"day": f"d{i}"}) for _ in range(i + 1))
    summary = build_segment_summary(mismatches, ["day"])
    assert len(summary["day"]) == 20
    assert summary["day"][0]["value"] == "d24"  # most frequent first


def test_summary_empty_inputs_return_none():
    assert build_segment_summary([], ["region"]) is None
    assert build_segment_summary([_mm()], []) is None
