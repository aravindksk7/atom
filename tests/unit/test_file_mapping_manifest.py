# tests/unit/test_file_mapping_manifest.py
from __future__ import annotations

import json

from etl_framework.reconciliation.file_mapping import (
    DiscoveredFile,
    FileGroup,
    FileMappingResult,
    FileMappingManifestWriter,
    FileMappingSpec,
    FilePair,
    FileSourceSpec,
    SimilarityScore,
)


def _spec(strategy: str = "explicit") -> FileMappingSpec:
    return FileMappingSpec(
        strategy=strategy,
        match_on=("region",) if strategy == "explicit" else (),
        source=FileSourceSpec(kind="local", root="/spool", pattern="sales_{region}.csv"),
        target=FileSourceSpec(kind="local", root="/baseline", pattern="fin_{region}.csv"),
    )


def test_manifest_writer_records_explicit_pairs_and_unmatched(tmp_path) -> None:
    east_source = FileGroup(key=("east",), files=[DiscoveredFile("/s/e.csv", "e.csv", {"region": "east"})])
    east_target = FileGroup(key=("east",), files=[DiscoveredFile("/t/e.dat", "e.dat", {"region": "east"})])
    north_unmatched = FileGroup(key=("north",), files=[DiscoveredFile("/s/n.csv", "n.csv", {"region": "north"})])
    mapping = FileMappingResult(
        match_on=("region",),
        pairs=[FilePair(key=("east",), source=east_source, target=east_target)],
        unmatched_sources=[north_unmatched],
        unmatched_targets=[],
    )

    output_path = tmp_path / "manifest.json"
    FileMappingManifestWriter(str(output_path)).write(
        run_id="run-1", job_name="regional_sales_recon", spec=_spec(), mapping=mapping, similarity_scores=None,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-1"
    assert payload["job_name"] == "regional_sales_recon"
    assert payload["strategy"] == "explicit"
    assert len(payload["pairs"]) == 1
    assert payload["pairs"][0]["mapping_method"] == "explicit"
    assert payload["pairs"][0]["similarity_score"] is None
    assert payload["pairs"][0]["source_files"] == ["e.csv"]
    assert payload["pairs"][0]["target_files"] == ["e.dat"]
    assert len(payload["unmatched_sources"]) == 1
    assert payload["unmatched_sources"][0]["files"] == ["n.csv"]


def test_manifest_writer_records_automated_pairs_with_scores(tmp_path) -> None:
    source_file = DiscoveredFile("/s/a.csv", "a.csv", {})
    target_file = DiscoveredFile("/t/b.dat", "b.dat", {})
    source_group = FileGroup(key=("a.csv",), files=[source_file])
    target_group = FileGroup(key=("b.dat",), files=[target_file])
    mapping = FileMappingResult(
        match_on=(),
        pairs=[FilePair(key=("a.csv", "b.dat"), source=source_group, target=target_group)],
        unmatched_sources=[],
        unmatched_targets=[],
    )
    scores = [SimilarityScore(
        source=source_file, target=target_file, score=0.83,
        signal_scores={"filename_tokens": 0.9, "column_signature": 1.0, "row_count_ratio": 0.6},
    )]

    output_path = tmp_path / "manifest.json"
    FileMappingManifestWriter(str(output_path)).write(
        run_id="run-2", job_name="auto_job", spec=_spec(strategy="automated"), mapping=mapping,
        similarity_scores=scores,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["strategy"] == "automated"
    assert payload["pairs"][0]["mapping_method"] == "automated"
    assert payload["pairs"][0]["similarity_score"] == 0.83
    assert payload["pairs"][0]["signal_scores"]["column_signature"] == 1.0
