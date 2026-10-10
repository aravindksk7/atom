"""``crushftp`` as a location kind: discovery, and every place that enumerates
the allowed kinds."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from etl_framework.reconciliation.file_mapping import (
    _MAX_RECURSION_DEPTH,
    FileMappingSpec,
    discover_crushftp_files,
)
from etl_framework.reconciliation.file_transfer_spec import file_transfer_param_errors, parse_file_transfer_params
from etl_framework.runner.job_validation import _validate_file_watcher


def _entry(path: str, *, is_dir: bool = False) -> SimpleNamespace:
    return SimpleNamespace(name=path.rsplit("/", 1)[-1], path=path, size=1, modified=0.0, is_dir=is_dir)


class _TreeClient:
    def __init__(self, tree: dict[str, list[SimpleNamespace]]) -> None:
        self.tree = tree

    def list(self, path: str):
        return self.tree[path]


_TREE = {
    "/data": [_entry("/data/sales_eu.csv"), _entry("/data/notes.txt"), _entry("/data/sub", is_dir=True)],
    "/data/sub": [_entry("/data/sub/sales_us.csv")],
}


def test_discover_crushftp_files_flat_ignores_subdirectories_and_extracts_tokens():
    found = discover_crushftp_files(_TreeClient(_TREE), "/data/", "sales_{region}.csv")

    assert [(f.path, f.file_name, f.tokens) for f in found] == [
        ("/data/sales_eu.csv", "sales_eu.csv", {"region": "eu"}),
    ]


def test_discover_crushftp_files_recursive_descends_and_sorts():
    found = discover_crushftp_files(_TreeClient(_TREE), "/data", "*.csv", recursive=True)

    assert [f.path for f in found] == ["/data/sales_eu.csv", "/data/sub/sales_us.csv"]


def test_discover_crushftp_files_recursive_stops_at_depth_cap():
    class Endless:
        calls = 0

        def list(self, path: str):
            Endless.calls += 1
            return [_entry(path.rstrip("/") + "/d", is_dir=True)]

    assert discover_crushftp_files(Endless(), "/x", "*.csv", recursive=True) == []
    assert Endless.calls == _MAX_RECURSION_DEPTH + 1


def test_file_transfer_accepts_crushftp_and_requires_credentials_ref():
    params = {
        "source": {"kind": "crushftp", "root": "/data", "pattern": "*.csv", "credentials_ref": "crush"},
        "destination": {"kind": "crushftp", "root": "/out", "credentials_ref": "crush"},
    }
    assert file_transfer_param_errors(params) == []
    assert parse_file_transfer_params(params).source.kind == "crushftp"

    del params["source"]["credentials_ref"]
    assert [field for field, _ in file_transfer_param_errors(params)] == ["params.source.credentials_ref"]

    bad = file_transfer_param_errors({**params, "source": {"kind": "ftp", "root": "/", "pattern": "*"}})
    assert any("'crushftp'" in message for _, message in bad)


def test_multi_file_mapping_accepts_crushftp():
    spec = FileMappingSpec.from_params({"file_mapping": {
        "match_on": [],
        "source": {"kind": "crushftp", "root": "/a", "pattern": "*.csv", "credentials_ref": "crush"},
        "target": {"kind": "local", "root": "/b", "pattern": "*.csv"},
    }})

    assert (spec.source.kind, spec.source.credentials_ref) == ("crushftp", "crush")
    with pytest.raises(ValueError, match="'crushftp'"):
        FileMappingSpec.from_params({"file_mapping": {
            "source": {"kind": "ftp", "root": "/a", "pattern": "*"},
            "target": {"kind": "local", "root": "/b", "pattern": "*"},
        }})


def test_file_watcher_validation_accepts_crushftp_and_requires_credentials_ref():
    issues: list = []
    _validate_file_watcher(
        {"location": {"kind": "crushftp", "root": "/data", "pattern": "*.csv", "credentials_ref": "crush"},
         "max_tries": 1},
        issues,
    )
    assert issues == []

    _validate_file_watcher({"location": {"kind": "crushftp", "root": "/data", "pattern": "*.csv"}, "max_tries": 1}, issues)
    assert [issue.field for issue in issues] == ["params.location.credentials_ref"]
