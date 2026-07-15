"""Guards that runtime backend imports are actually declared as project dependencies.

DuckDBBackend and PolarsBackend are reachable from AdvancedCompareOptions
(comparison_backend) and the BO/File/SQL compare UI, so both packages must be
installed by ``pip install -r requirements.txt`` / ``pip install .`` alone —
not merely present by accident in a developer's environment.
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_duckdb_declared_in_requirements_txt():
    text = (_ROOT / "requirements.txt").read_text()
    assert "duckdb" in text, (
        "duckdb is imported by DuckDBBackend and selectable via "
        "comparison_backend, but is missing from requirements.txt"
    )


def test_duckdb_declared_in_pyproject_toml():
    text = (_ROOT / "pyproject.toml").read_text()
    assert "duckdb" in text, (
        "duckdb is imported by DuckDBBackend and selectable via "
        "comparison_backend, but is missing from pyproject.toml dependencies"
    )
