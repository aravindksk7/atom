# tests/unit/test_file_mapping.py
from __future__ import annotations

import pytest

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


from etl_framework.reconciliation.file_mapping import pair_files, DiscoveredFile


def _df(name: str, **tokens: str) -> DiscoveredFile:
    return DiscoveredFile(path=f"/x/{name}", file_name=name, tokens=tokens)


def test_pair_files_matches_one_to_one() -> None:
    sources = [_df("sales_east_20260101.csv", region="east", date="20260101")]
    targets = [_df("fin_east_20260101.dat", region="east", date="20260101")]

    mapping = pair_files(sources, targets, ["region", "date"])

    assert len(mapping.pairs) == 1
    assert mapping.pairs[0].key == ("east", "20260101")
    assert not mapping.unmatched_sources
    assert not mapping.unmatched_targets


def test_pair_files_collapses_shards_sharing_a_key_into_one_group() -> None:
    sources = [
        _df("sales_east_p1_20260101.csv", region="east", date="20260101"),
        _df("sales_east_p2_20260101.csv", region="east", date="20260101"),
    ]
    targets = [_df("fin_east_20260101.dat", region="east", date="20260101")]

    mapping = pair_files(sources, targets, ["region", "date"])

    assert len(mapping.pairs) == 1
    assert len(mapping.pairs[0].source.files) == 2
    assert len(mapping.pairs[0].target.files) == 1


def test_pair_files_reports_unmatched_groups_on_either_side() -> None:
    sources = [
        _df("sales_east_20260101.csv", region="east", date="20260101"),
        _df("sales_north_20260101.csv", region="north", date="20260101"),
    ]
    targets = [
        _df("fin_east_20260101.dat", region="east", date="20260101"),
        _df("fin_west_20260101.dat", region="west", date="20260101"),
    ]

    mapping = pair_files(sources, targets, ["region", "date"])

    assert len(mapping.pairs) == 1
    assert [g.key for g in mapping.unmatched_sources] == [("north", "20260101")]
    assert [g.key for g in mapping.unmatched_targets] == [("west", "20260101")]


def test_pair_files_raises_when_a_file_is_missing_a_match_on_token() -> None:
    sources = [_df("sales_east.csv", region="east")]  # no "date" token captured

    with pytest.raises(ValueError, match="missing match_on token"):
        pair_files(sources, [], ["region", "date"])


def test_pair_files_with_empty_match_on_collapses_every_file_into_one_group_per_side() -> None:
    sources = [_df("sales_data_20260101.csv"), _df("sales_data_20260102.csv")]
    targets = [_df("fin_data_20260101.dat")]

    mapping = pair_files(sources, targets, [])

    assert len(mapping.pairs) == 1
    assert mapping.pairs[0].key == ()
    assert len(mapping.pairs[0].source.files) == 2
    assert len(mapping.pairs[0].target.files) == 1
