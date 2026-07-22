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


from etl_framework.reconciliation.file_mapping import FileMappingSpec, FileSourceSpec


def test_file_mapping_spec_parses_valid_explicit_config() -> None:
    params = {
        "file_mapping": {
            "strategy": "explicit",
            "match_on": ["region", "date"],
            "source": {"kind": "local", "root": "/spool", "pattern": "sales_{region}_{date}.csv"},
            "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}_{date}.dat"},
        }
    }

    spec = FileMappingSpec.from_params(params)

    assert spec.match_on == ("region", "date")
    assert spec.source == FileSourceSpec(kind="local", root="/spool", pattern="sales_{region}_{date}.csv")
    assert spec.target == FileSourceSpec(kind="local", root="/baseline", pattern="fin_{region}_{date}.dat")
    assert spec.unmatched_policy == "fail"


def test_file_mapping_spec_defaults_match_on_to_empty_tuple() -> None:
    spec = FileMappingSpec.from_params({
        "file_mapping": {
            "source": {"kind": "local", "root": "/spool", "pattern": "sales_data_*.csv"},
            "target": {"kind": "local", "root": "/baseline", "pattern": "fin_data_*.dat"},
        }
    })

    assert spec.match_on == ()


def test_file_mapping_spec_requires_file_mapping_object() -> None:
    with pytest.raises(ValueError, match="require a 'file_mapping' object"):
        FileMappingSpec.from_params({})


def test_file_mapping_spec_requires_source_and_target() -> None:
    with pytest.raises(ValueError, match="file_mapping.source requires"):
        FileMappingSpec.from_params({
            "file_mapping": {"target": {"kind": "local", "root": "/baseline", "pattern": "fin.csv"}}
        })


def test_file_mapping_spec_rejects_unknown_unmatched_policy() -> None:
    with pytest.raises(ValueError, match="unmatched_policy must be"):
        FileMappingSpec.from_params({
            "file_mapping": {
                "match_on": ["region"],
                "source": {"kind": "local", "root": "/spool", "pattern": "sales_{region}.csv"},
                "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
                "unmatched_policy": "retry",
            }
        })


def test_file_mapping_spec_rejects_unsupported_kind() -> None:
    with pytest.raises(ValueError, match="is not supported yet"):
        FileMappingSpec.from_params({
            "file_mapping": {
                "match_on": ["region"],
                "source": {"kind": "s3", "root": "s3://bucket/prefix", "pattern": "sales_{region}.csv"},
                "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
            }
        })


from datetime import datetime, timezone

from etl_framework.reconciliation.file_mapping import (
    FileGroup,
    FileMappingResult,
    FilePair,
    aggregate_reconciliation_results,
)
from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.runner.state import TestStatus


def _pair_result(status: TestStatus, mismatches: list[MismatchRecord] | None = None) -> ReconciliationResult:
    return ReconciliationResult(
        query_name="pair",
        source_env="source",
        target_env="target",
        source_row_count=2,
        target_row_count=2,
        matched_count=2 if status == TestStatus.PASSED else 1,
        missing_in_target_count=0,
        missing_in_source_count=0,
        value_mismatch_count=0 if status == TestStatus.PASSED else 1,
        mismatches=mismatches or [],
        status=status,
        executed_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
        duration_seconds=0.1,
    )


def test_aggregate_reconciliation_results_rolls_up_pairs() -> None:
    east_source = FileGroup(key=("east",), files=[_df("e.csv", region="east")])
    east_target = FileGroup(key=("east",), files=[_df("e.dat", region="east")])
    west_source = FileGroup(key=("west",), files=[_df("w.csv", region="west")])
    west_target = FileGroup(key=("west",), files=[_df("w.dat", region="west")])
    mapping = FileMappingResult(
        match_on=("region",),
        pairs=[
            FilePair(key=("east",), source=east_source, target=east_target),
            FilePair(key=("west",), source=west_source, target=west_target),
        ],
        unmatched_sources=[],
        unmatched_targets=[],
    )
    mismatch = MismatchRecord(
        key_values={"id": 1}, column_name="value", source_value="charlie",
        target_value="zulu", mismatch_type="value_diff",
    )
    pair_results = [_pair_result(TestStatus.PASSED), _pair_result(TestStatus.FAILED, [mismatch])]

    aggregate = aggregate_reconciliation_results("regional_sales_recon", mapping, pair_results)

    assert aggregate.status == TestStatus.FAILED
    assert aggregate.source_row_count == 4
    assert aggregate.mismatch_summary["pairs_total"] == 2
    assert aggregate.mismatch_summary["pairs_passed"] == 1
    assert aggregate.mismatch_summary["pairs_failed"] == 1
    assert aggregate.mismatches[0].key_values["__pair__"] == {"region": "west"}
    assert aggregate.source_file_name == "2 file(s) across 2 pair(s)"

    pair_summary_by_region = {p["key"]["region"]: p for p in aggregate.mismatch_summary["file_pairs"]}
    assert pair_summary_by_region["east"]["status"] == "PASSED"
    assert pair_summary_by_region["west"]["status"] == "FAILED"


def test_aggregate_reconciliation_results_rejects_length_mismatch() -> None:
    mapping = FileMappingResult(match_on=("region",), pairs=[], unmatched_sources=[], unmatched_targets=[])

    with pytest.raises(ValueError, match="one result per mapped pair"):
        aggregate_reconciliation_results("job", mapping, [_pair_result(TestStatus.PASSED)])


def test_pair_files_true_m_to_n_multiple_files_both_sides() -> None:
    sources = [
        _df("sales_east_p1.csv", region="east"),
        _df("sales_east_p2.csv", region="east"),
    ]
    targets = [
        _df("fin_east_p1.dat", region="east"),
        _df("fin_east_p2.dat", region="east"),
    ]

    mapping = pair_files(sources, targets, ["region"])

    assert len(mapping.pairs) == 1
    assert len(mapping.pairs[0].source.files) == 2
    assert len(mapping.pairs[0].target.files) == 2
    assert not mapping.unmatched_sources
    assert not mapping.unmatched_targets


from etl_framework.reconciliation.file_mapping import AutomatedMappingSpec


def test_file_mapping_spec_parses_automated_strategy_with_defaults() -> None:
    spec = FileMappingSpec.from_params({
        "file_mapping": {
            "strategy": "automated",
            "source": {"kind": "local", "root": "/spool", "pattern": "*.csv"},
            "target": {"kind": "local", "root": "/baseline", "pattern": "*.dat"},
        }
    })

    assert spec.strategy == "automated"
    assert spec.automated == AutomatedMappingSpec(similarity_threshold=0.7, signals=("filename_tokens", "column_signature", "row_count_ratio"))


def test_file_mapping_spec_parses_automated_strategy_with_overrides() -> None:
    spec = FileMappingSpec.from_params({
        "file_mapping": {
            "strategy": "automated",
            "source": {"kind": "local", "root": "/spool", "pattern": "*.csv"},
            "target": {"kind": "local", "root": "/baseline", "pattern": "*.dat"},
            "automated_mapping": {
                "similarity_threshold": 0.9,
                "signals": ["filename_tokens", "row_count_ratio"],
            },
        }
    })

    assert spec.automated == AutomatedMappingSpec(similarity_threshold=0.9, signals=("filename_tokens", "row_count_ratio"))


def test_file_mapping_spec_explicit_strategy_has_no_automated_config() -> None:
    spec = FileMappingSpec.from_params({
        "file_mapping": {
            "match_on": ["region"],
            "source": {"kind": "local", "root": "/spool", "pattern": "sales_{region}.csv"},
            "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
        }
    })

    assert spec.automated is None


def test_file_mapping_spec_rejects_unknown_strategy() -> None:
    with pytest.raises(ValueError, match="is not supported yet"):
        FileMappingSpec.from_params({
            "file_mapping": {
                "strategy": "ml_based",
                "source": {"kind": "local", "root": "/spool", "pattern": "*.csv"},
                "target": {"kind": "local", "root": "/baseline", "pattern": "*.dat"},
            }
        })


def test_file_mapping_spec_rejects_bad_similarity_threshold() -> None:
    with pytest.raises(ValueError, match="similarity_threshold must be"):
        FileMappingSpec.from_params({
            "file_mapping": {
                "strategy": "automated",
                "source": {"kind": "local", "root": "/spool", "pattern": "*.csv"},
                "target": {"kind": "local", "root": "/baseline", "pattern": "*.dat"},
                "automated_mapping": {"similarity_threshold": 1.5},
            }
        })


def test_file_mapping_spec_rejects_unknown_signal() -> None:
    with pytest.raises(ValueError, match="unknown signal"):
        FileMappingSpec.from_params({
            "file_mapping": {
                "strategy": "automated",
                "source": {"kind": "local", "root": "/spool", "pattern": "*.csv"},
                "target": {"kind": "local", "root": "/baseline", "pattern": "*.dat"},
                "automated_mapping": {"signals": ["made_up_signal"]},
            }
        })


def test_file_mapping_spec_rejects_boolean_similarity_threshold() -> None:
    with pytest.raises(ValueError, match="similarity_threshold must be"):
        FileMappingSpec.from_params({
            "file_mapping": {
                "strategy": "automated",
                "source": {"kind": "local", "root": "/spool", "pattern": "*.csv"},
                "target": {"kind": "local", "root": "/baseline", "pattern": "*.dat"},
                "automated_mapping": {"similarity_threshold": True},
            }
        })


def test_aggregate_reconciliation_results_gives_automated_pairs_distinct_keys() -> None:
    from datetime import datetime, timezone
    from etl_framework.reconciliation.file_mapping import (
        DiscoveredFile as _DF,
        FileGroup as _FG,
        FilePair as _FP,
        FileMappingResult as _FMR,
        aggregate_reconciliation_results,
    )
    from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
    from etl_framework.runner.state import TestStatus

    def _pair_result(mismatches):
        return ReconciliationResult(
            query_name="pair", source_env="s", target_env="t",
            source_row_count=1, target_row_count=1, matched_count=0,
            missing_in_target_count=0, missing_in_source_count=0,
            value_mismatch_count=len(mismatches), mismatches=mismatches,
            status=TestStatus.FAILED if mismatches else TestStatus.PASSED,
            executed_at=datetime(2026, 7, 23, tzinfo=timezone.utc), duration_seconds=0.1,
        )

    east_source = _FG(key=("sales_east.csv",), files=[_DF("/s/sales_east.csv", "sales_east.csv", {})])
    east_target = _FG(key=("financials_east.dat",), files=[_DF("/t/financials_east.dat", "financials_east.dat", {})])
    west_source = _FG(key=("sales_west.csv",), files=[_DF("/s/sales_west.csv", "sales_west.csv", {})])
    west_target = _FG(key=("financials_west.dat",), files=[_DF("/t/financials_west.dat", "financials_west.dat", {})])

    mapping = _FMR(
        match_on=(),  # automated strategy always produces an empty match_on
        pairs=[
            _FP(key=("sales_east.csv", "financials_east.dat"), source=east_source, target=east_target),
            _FP(key=("sales_west.csv", "financials_west.dat"), source=west_source, target=west_target),
        ],
        unmatched_sources=[],
        unmatched_targets=[],
    )
    east_mismatch = MismatchRecord(
        key_values={"id": 1}, column_name="value", source_value="alpha",
        target_value="zulu", mismatch_type="value_diff",
    )
    west_mismatch = MismatchRecord(
        key_values={"id": 2}, column_name="value", source_value="bravo",
        target_value="yankee", mismatch_type="value_diff",
    )
    pair_results = [_pair_result([east_mismatch]), _pair_result([west_mismatch])]

    aggregate = aggregate_reconciliation_results("auto_job", mapping, pair_results)

    # Every automated pair must get a distinct, non-empty key -- not the
    # same {} for both pairs.
    pair_keys = [fp["key"] for fp in aggregate.mismatch_summary["file_pairs"]]
    assert pair_keys[0] != pair_keys[1]
    assert all(key for key in pair_keys)  # none are empty {}

    mismatch_pair_tags = [m.key_values["__pair__"] for m in aggregate.mismatches]
    assert mismatch_pair_tags[0] != mismatch_pair_tags[1]
    assert all(tag for tag in mismatch_pair_tags)


def test_aggregate_reconciliation_results_escalates_to_error_when_a_pair_errors() -> None:
    from datetime import datetime, timezone
    from etl_framework.reconciliation.file_mapping import (
        DiscoveredFile as _DF,
        FileGroup as _FG,
        FilePair as _FP,
        FileMappingResult as _FMR,
        aggregate_reconciliation_results,
    )
    from etl_framework.reconciliation.models import ReconciliationResult
    from etl_framework.runner.state import TestStatus

    def _result(status, mismatch_summary=None):
        return ReconciliationResult(
            query_name="pair", source_env="s", target_env="t",
            source_row_count=1, target_row_count=1, matched_count=1 if status == TestStatus.PASSED else 0,
            missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=0,
            mismatches=[], status=status,
            executed_at=datetime(2026, 7, 23, tzinfo=timezone.utc), duration_seconds=0.1,
            mismatch_summary=mismatch_summary,
        )

    east_source = _FG(key=("east",), files=[_DF("/s/e.csv", "e.csv", {"region": "east"})])
    east_target = _FG(key=("east",), files=[_DF("/t/e.dat", "e.dat", {"region": "east"})])
    west_source = _FG(key=("west",), files=[_DF("/s/w.csv", "w.csv", {"region": "west"})])
    west_target = _FG(key=("west",), files=[_DF("/t/w.dat", "w.dat", {"region": "west"})])
    mapping = _FMR(
        match_on=("region",),
        pairs=[
            _FP(key=("east",), source=east_source, target=east_target),
            _FP(key=("west",), source=west_source, target=west_target),
        ],
        unmatched_sources=[], unmatched_targets=[],
    )
    pair_results = [
        _result(TestStatus.PASSED),
        _result(TestStatus.ERROR, mismatch_summary={"error": "target file was truncated mid-write"}),
    ]

    aggregate = aggregate_reconciliation_results("regional_sales_recon", mapping, pair_results)

    assert aggregate.status == TestStatus.ERROR
    assert aggregate.mismatch_summary["pairs_total"] == 2
    assert aggregate.mismatch_summary["pairs_passed"] == 1
    assert aggregate.mismatch_summary["pairs_errored"] == 1
    by_region = {p["key"]["region"]: p for p in aggregate.mismatch_summary["file_pairs"]}
    assert by_region["east"]["error"] is None
    assert by_region["west"]["error"] == "target file was truncated mid-write"


def test_aggregate_reconciliation_results_all_passed_still_reports_zero_errored() -> None:
    from datetime import datetime, timezone
    from etl_framework.reconciliation.file_mapping import (
        DiscoveredFile as _DF,
        FileGroup as _FG,
        FilePair as _FP,
        FileMappingResult as _FMR,
        aggregate_reconciliation_results,
    )
    from etl_framework.reconciliation.models import ReconciliationResult
    from etl_framework.runner.state import TestStatus

    def _result(status):
        return ReconciliationResult(
            query_name="pair", source_env="s", target_env="t",
            source_row_count=1, target_row_count=1, matched_count=1,
            missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=0,
            mismatches=[], status=status,
            executed_at=datetime(2026, 7, 23, tzinfo=timezone.utc), duration_seconds=0.1,
        )

    group = _FG(key=("east",), files=[_DF("/s/e.csv", "e.csv", {"region": "east"})])
    mapping = _FMR(match_on=("region",), pairs=[_FP(key=("east",), source=group, target=group)], unmatched_sources=[], unmatched_targets=[])

    aggregate = aggregate_reconciliation_results("job", mapping, [_result(TestStatus.PASSED)])

    assert aggregate.status == TestStatus.PASSED
    assert aggregate.mismatch_summary["pairs_errored"] == 0
