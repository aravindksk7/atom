# tests/property/test_file_mapping_property.py
"""Property-based tests for file-mapping pairing correctness using hypothesis."""
from __future__ import annotations

import pandas as pd
from hypothesis import given, settings
from hypothesis import strategies as st

from etl_framework.reconciliation.file_mapping import (
    AutomatedMappingSpec,
    DiscoveredFile,
    pair_files,
    pair_files_automated,
)

_KEY_ALPHABET = st.sampled_from(["alpha", "bravo", "charlie", "delta"])


def _discovered_files(prefix: str):
    return st.lists(
        st.builds(
            lambda key, idx: DiscoveredFile(
                path=f"/{prefix}/{key}_{idx}.csv",
                file_name=f"{key}_{idx}.csv",
                tokens={"region": key},
            ),
            key=_KEY_ALPHABET,
            idx=st.integers(min_value=0, max_value=1000),
        ),
        min_size=0,
        max_size=8,
        unique_by=lambda f: f.path,
    )


# ── pair_files (explicit) partition invariant ─────────────────────────────────

@given(sources=_discovered_files("src"), targets=_discovered_files("tgt"))
@settings(max_examples=60)
def test_pair_files_partitions_every_discovered_file_exactly_once(sources, targets) -> None:
    """Every discovered file ends up in exactly one place: a pair's source
    group, a pair's target group, an unmatched-source group, or an
    unmatched-target group -- on the correct side, never duplicated, never
    dropped.
    """
    mapping = pair_files(sources, targets, ["region"])

    seen_source_paths: list[str] = []
    seen_target_paths: list[str] = []
    for pair in mapping.pairs:
        seen_source_paths.extend(f.path for f in pair.source.files)
        seen_target_paths.extend(f.path for f in pair.target.files)
    for group in mapping.unmatched_sources:
        seen_source_paths.extend(f.path for f in group.files)
    for group in mapping.unmatched_targets:
        seen_target_paths.extend(f.path for f in group.files)

    assert sorted(seen_source_paths) == sorted(f.path for f in sources)
    assert sorted(seen_target_paths) == sorted(f.path for f in targets)
    # No file appears twice across all groups on its own side.
    assert len(seen_source_paths) == len(set(seen_source_paths))
    assert len(seen_target_paths) == len(set(seen_target_paths))


@given(sources=_discovered_files("src"), targets=_discovered_files("tgt"))
@settings(max_examples=60)
def test_pair_files_every_pair_key_present_on_both_sides(sources, targets) -> None:
    """A key only becomes a pair if at least one file with that key exists
    on both the source and target side."""
    mapping = pair_files(sources, targets, ["region"])

    source_keys = {f.tokens["region"] for f in sources}
    target_keys = {f.tokens["region"] for f in targets}
    for pair in mapping.pairs:
        region = pair.key[0]
        assert region in source_keys
        assert region in target_keys


# ── pair_files_automated invariants ───────────────────────────────────────────

@given(
    row_counts=st.lists(st.integers(min_value=1, max_value=5), min_size=1, max_size=4, unique=True),
    threshold=st.floats(min_value=0.0, max_value=1.0),
)
@settings(max_examples=40)
def test_pair_files_automated_never_reuses_a_file(row_counts, threshold) -> None:
    """No source or target file is ever used in more than one pair,
    regardless of how many candidates tie on score."""
    source_files = [
        DiscoveredFile(path=f"/s/f{i}.csv", file_name=f"f{i}.csv", tokens={}) for i in range(len(row_counts))
    ]
    target_files = [
        DiscoveredFile(path=f"/t/f{i}.dat", file_name=f"f{i}.dat", tokens={}) for i in range(len(row_counts))
    ]
    source_frames = {f.path: pd.DataFrame({"id": range(n)}) for f, n in zip(source_files, row_counts)}
    target_frames = {f.path: pd.DataFrame({"id": range(n)}) for f, n in zip(target_files, row_counts)}

    mapping, scores = pair_files_automated(
        source_files, source_frames, target_files, target_frames,
        AutomatedMappingSpec(similarity_threshold=threshold),
    )

    used_sources = [f.path for pair in mapping.pairs for f in pair.source.files]
    used_targets = [f.path for pair in mapping.pairs for f in pair.target.files]
    assert len(used_sources) == len(set(used_sources))
    assert len(used_targets) == len(set(used_targets))
    assert len(mapping.pairs) == len(scores)
    for score in scores:
        assert score.score >= threshold


@given(threshold=st.floats(min_value=0.0, max_value=1.0))
@settings(max_examples=20)
def test_pair_files_automated_empty_inputs_produce_no_pairs(threshold) -> None:
    mapping, scores = pair_files_automated(
        [], {}, [], {}, AutomatedMappingSpec(similarity_threshold=threshold),
    )
    assert mapping.pairs == []
    assert mapping.unmatched_sources == []
    assert mapping.unmatched_targets == []
    assert scores == []
