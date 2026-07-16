from etl_framework.expectations.schema_compat import classify_diff, classify_type_change


def test_widening_is_non_breaking():
    assert classify_type_change("int32", "int64") == "non_breaking"
    assert classify_type_change("float32", "float64") == "non_breaking"
    assert classify_type_change("int64", "float64") == "non_breaking"


def test_narrowing_is_breaking():
    assert classify_type_change("int64", "int32") == "breaking"
    assert classify_type_change("float64", "int64") == "breaking"


def test_object_transitions_are_risky():
    assert classify_type_change("object", "int64") == "risky"
    assert classify_type_change("int64", "object") == "risky"


def test_classify_diff_overall_is_worst_change():
    diff = {
        "added": ["new_col"],
        "removed": [],
        "changed": [{"column": "amount", "from": "int32", "to": "int64"}],
    }
    result = classify_diff(diff)
    assert result["compatibility"] == "non_breaking"
    assert result["changed"][0]["compatibility"] == "non_breaking"

    diff["removed"] = ["gone_col"]
    assert classify_diff(diff)["compatibility"] == "breaking"


def test_classify_diff_no_changes_is_full():
    assert classify_diff({"added": [], "removed": [], "changed": []})["compatibility"] == "full"
