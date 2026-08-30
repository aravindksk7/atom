"""Guards that runtime backend imports are actually declared as project dependencies.

PolarsBackend is reachable from AdvancedCompareOptions (comparison_backend) and
the BO/File/SQL compare UI. DuckDBBackend is no longer user-selectable, but it is
imported unconditionally by ``etl_framework.reconciliation.backends.__init__`` and
is the comparison engine behind ``TransformCase``
(``etl_framework/transform_testing/harness.py``), so duckdb must still be installed
by ``pip install -r requirements.txt`` / ``pip install .`` alone — not merely
present by accident in a developer's environment.
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_duckdb_declared_in_requirements_txt():
    text = (_ROOT / "requirements.txt").read_text()
    assert "duckdb" in text, (
        "duckdb is imported by DuckDBBackend, which backs TransformCase, "
        "but is missing from requirements.txt"
    )


def test_duckdb_declared_in_pyproject_toml():
    text = (_ROOT / "pyproject.toml").read_text()
    assert "duckdb" in text, (
        "duckdb is imported by DuckDBBackend, which backs TransformCase, "
        "but is missing from pyproject.toml dependencies"
    )


def test_polars_declared_in_requirements_txt():
    text = (_ROOT / "requirements.txt").read_text()
    assert "polars" in text, (
        "polars is imported by PolarsBackend and selectable via "
        "comparison_backend, but is missing from requirements.txt"
    )


def test_polars_declared_in_pyproject_toml():
    text = (_ROOT / "pyproject.toml").read_text()
    assert "polars" in text, (
        "polars is imported by PolarsBackend and selectable via "
        "comparison_backend, but is missing from pyproject.toml dependencies"
    )
