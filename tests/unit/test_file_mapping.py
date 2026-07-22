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
