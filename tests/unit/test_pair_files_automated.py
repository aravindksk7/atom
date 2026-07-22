# tests/unit/test_pair_files_automated.py
from __future__ import annotations

import pandas as pd

from etl_framework.reconciliation.file_mapping import (
    AutomatedMappingSpec,
    DiscoveredFile,
    pair_files_automated,
)


def _df_file(name: str) -> DiscoveredFile:
    return DiscoveredFile(path=f"/x/{name}", file_name=name, tokens={})


def test_pair_files_automated_matches_best_scoring_candidates() -> None:
    source_files = [_df_file("sales_east.csv"), _df_file("sales_west.csv")]
    target_files = [_df_file("financials_east.dat"), _df_file("financials_west.dat")]
    source_frames = {
        "/x/sales_east.csv": pd.DataFrame({"id": [1, 2], "value": ["a", "b"]}),
        "/x/sales_west.csv": pd.DataFrame({"id": [1], "value": ["c"]}),
    }
    target_frames = {
        "/x/financials_east.dat": pd.DataFrame({"id": [1, 2], "value": ["a", "b"]}),
        "/x/financials_west.dat": pd.DataFrame({"id": [1], "value": ["z"]}),
    }

    mapping, scores = pair_files_automated(
        source_files, source_frames, target_files, target_frames,
        AutomatedMappingSpec(similarity_threshold=0.5),
    )

    assert len(mapping.pairs) == 2
    assert not mapping.unmatched_sources
    assert not mapping.unmatched_targets
    assert len(scores) == 2
    for score in scores:
        assert score.score >= 0.5

    paired_names = {
        (pair.source.files[0].file_name, pair.target.files[0].file_name)
        for pair in mapping.pairs
    }
    assert ("sales_east.csv", "financials_east.dat") in paired_names
    assert ("sales_west.csv", "financials_west.dat") in paired_names


def test_pair_files_automated_leaves_low_scoring_files_unmatched() -> None:
    source_files = [_df_file("sales_east.csv")]
    target_files = [_df_file("zzz_totally_unrelated.dat")]
    source_frames = {"/x/sales_east.csv": pd.DataFrame({"id": [1]})}
    target_frames = {"/x/zzz_totally_unrelated.dat": pd.DataFrame({"other": [1]})}

    mapping, scores = pair_files_automated(
        source_files, source_frames, target_files, target_frames,
        AutomatedMappingSpec(similarity_threshold=0.9),
    )

    assert not mapping.pairs
    assert len(mapping.unmatched_sources) == 1
    assert len(mapping.unmatched_targets) == 1
    assert scores == []


def test_pair_files_automated_never_uses_a_file_twice() -> None:
    # Two source files that would both score well against the same single
    # target file if matching weren't exclusive.
    source_files = [_df_file("sales_east_1.csv"), _df_file("sales_east_2.csv")]
    target_files = [_df_file("financials_east.dat")]
    frame = pd.DataFrame({"id": [1], "value": ["a"]})
    source_frames = {"/x/sales_east_1.csv": frame, "/x/sales_east_2.csv": frame}
    target_frames = {"/x/financials_east.dat": frame}

    mapping, scores = pair_files_automated(
        source_files, source_frames, target_files, target_frames,
        AutomatedMappingSpec(similarity_threshold=0.3),
    )

    assert len(mapping.pairs) == 1
    assert len(mapping.unmatched_sources) == 1
    assert len(scores) == 1


def test_pair_files_automated_respects_selected_signals_only() -> None:
    # Identical filenames but completely different columns and row counts.
    # With only "filename_tokens" selected, they should still match at 1.0;
    # with all signals (default), the score should be lower.
    source_files = [_df_file("data.csv")]
    target_files = [_df_file("data.dat")]
    source_frames = {"/x/data.csv": pd.DataFrame({"a": [1, 2, 3]})}
    target_frames = {"/x/data.dat": pd.DataFrame({"z": [1]})}

    mapping_filename_only, scores_filename_only = pair_files_automated(
        source_files, source_frames, target_files, target_frames,
        AutomatedMappingSpec(similarity_threshold=0.9, signals=("filename_tokens",)),
    )
    assert len(mapping_filename_only.pairs) == 1
    assert scores_filename_only[0].score == 1.0

    mapping_all_signals, _ = pair_files_automated(
        source_files, source_frames, target_files, target_frames,
        AutomatedMappingSpec(similarity_threshold=0.9),
    )
    assert not mapping_all_signals.pairs
