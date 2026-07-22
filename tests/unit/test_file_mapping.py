# tests/unit/test_file_mapping.py
from __future__ import annotations

from etl_framework.reconciliation.file_mapping import compile_token_pattern


def test_compile_token_pattern_matches_named_tokens() -> None:
    regex = compile_token_pattern("sales_data_{region}_{date:%Y%m%d}.csv")
    match = regex.match("sales_data_east_20260101.csv")
    assert match is not None
    assert match.group("region") == "east"
    assert match.group("date") == "20260101"


def test_compile_token_pattern_rejects_non_matching_names() -> None:
    regex = compile_token_pattern("sales_data_{region}_{date:%Y%m%d}.csv")
    assert regex.match("sales_data_east_2026-01-01.csv") is None
    assert regex.match("financials_east_20260101.dat") is None


def test_compile_token_pattern_supports_bare_glob_wildcards() -> None:
    regex = compile_token_pattern("sales_data_*.csv")
    assert regex.match("sales_data_20260101.csv") is not None
    assert regex.match("sales_data_20260101.csv").groupdict() == {}
    assert regex.match("sales_data_20260101.dat") is None


from etl_framework.reconciliation.file_mapping import discover_local_files


def test_discover_local_files_matches_pattern_and_extracts_tokens(tmp_path) -> None:
    (tmp_path / "sales_data_east_20260101.csv").write_text("id,value\n1,a\n", encoding="utf-8")
    (tmp_path / "sales_data_west_20260102.csv").write_text("id,value\n1,a\n", encoding="utf-8")
    (tmp_path / "readme.txt").write_text("not a match", encoding="utf-8")

    discovered = discover_local_files(tmp_path, "sales_data_{region}_{date:%Y%m%d}.csv")

    assert [f.file_name for f in discovered] == [
        "sales_data_east_20260101.csv",
        "sales_data_west_20260102.csv",
    ]
    assert discovered[0].tokens == {"region": "east", "date": "20260101"}


def test_discover_local_files_returns_empty_list_when_nothing_matches(tmp_path) -> None:
    (tmp_path / "unrelated.csv").write_text("id,value\n1,a\n", encoding="utf-8")
    assert discover_local_files(tmp_path, "sales_data_{region}.csv") == []
