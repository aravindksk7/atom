"""scripts/check_dependency_age.py: pins too new for the on-prem mirror."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_dependency_age", Path(__file__).resolve().parents[2] / "scripts" / "check_dependency_age.py",
)
age = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(age)  # type: ignore[union-attr]

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


def test_parse_pins_reads_markers_extras_and_comments():
    text = """
    # comment
    -c other.txt
    annotated-doc==0.0.5
    Uvicorn[standard]==0.54.0
    numpy==2.4.6 ; python_full_version < '3.12'
    numpy==2.5.4 ; python_full_version >= '3.12'
    numpy==2.5.4 ; sys_platform == 'win32'
    pandas>=2.0
    """
    assert age.parse_pins(text) == [
        ("annotated-doc", "0.0.5"), ("uvicorn", "0.54.0"), ("numpy", "2.4.6"), ("numpy", "2.5.4"),
    ]


def test_first_upload_takes_earliest_file():
    payload = {"urls": [
        {"upload_time_iso_8601": "2026-11-10T18:17:10.5Z"},
        {"upload_time_iso_8601": "2026-11-10T18:15:00Z"},
        {"upload_time": "2026-11-11T00:00:00"},
    ]}
    assert age.first_upload(payload) == datetime(2026, 11, 10, 18, 15, tzinfo=timezone.utc)
    assert age.first_upload({"urls": []}) is None


def test_too_new_uses_the_quarantine_window():
    pins = [("annotated-doc", "0.0.6"), ("annotated-doc", "0.0.5"), ("edge", "1"), ("unknown", "1")]
    uploaded = {
        ("annotated-doc", "0.0.6"): NOW - timedelta(days=1.5),
        ("annotated-doc", "0.0.5"): NOW - timedelta(days=107),
        ("edge", "1"): NOW - timedelta(days=7),          # exactly 7 days: allowed
        ("unknown", "1"): None,
    }
    flagged = age.too_new(pins, uploaded, NOW, timedelta(days=7))
    assert [(n, v) for n, v, _ in flagged] == [("annotated-doc", "0.0.6")]


def test_main_reports_young_pins(tmp_path, monkeypatch, capsys):
    constraints = tmp_path / "constraints.txt"
    constraints.write_text("annotated-doc==0.0.6\nfastapi==0.162.0\n", encoding="utf-8")
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    monkeypatch.setattr(age, "_fetch", lambda url: {
        "urls": [{"upload_time_iso_8601": recent if "annotated-doc" in url else old}],
    })
    assert age.main([str(constraints)]) == 1
    assert "annotated-doc==0.0.6" in capsys.readouterr().out
    assert age.main([str(constraints), "--min-age-days", "0.5"]) == 0


def test_main_exit_2_when_index_unreachable(tmp_path, monkeypatch):
    import urllib.error

    constraints = tmp_path / "constraints.txt"
    constraints.write_text("fastapi==0.162.0\n", encoding="utf-8")

    def boom(url):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(age, "_fetch", boom)
    assert age.main([str(constraints)]) == 2


def test_repository_constraints_cover_every_declared_dependency():
    """Every package named in requirements.txt or pyproject.toml (all extras)
    is pinned, so no install path can pull an unvetted release."""
    import re
    import tomllib

    root = Path(__file__).resolve().parents[2]
    pinned = {name for name, _ in age.parse_pins((root / "constraints.txt").read_text(encoding="utf-8"))}

    def names(specs):
        for spec in specs:
            match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", spec)
            if match and not spec.strip().startswith(("#", "-")):
                yield match.group(1).lower().replace("_", "-")

    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared = set(names(project["dependencies"]))
    for extra in project["optional-dependencies"].values():
        declared |= set(names(extra))
    declared |= set(names((root / "requirements.txt").read_text(encoding="utf-8").splitlines()))
    normalised = {p.replace("_", "-") for p in pinned}
    assert sorted(declared - normalised) == []


@pytest.mark.parametrize("path", ["requirements.txt"])
def test_requirements_apply_constraints(path):
    root = Path(__file__).resolve().parents[2]
    assert "-c constraints.txt" in (root / path).read_text(encoding="utf-8").splitlines()
