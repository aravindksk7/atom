"""Tests for ColumnProfile and SchemaSnapshot ORM models."""
from etl_framework.repository.models import ColumnProfile, SchemaSnapshot


def test_column_profile_tablename():
    assert ColumnProfile.__tablename__ == "column_profiles"


def test_schema_snapshot_tablename():
    assert SchemaSnapshot.__tablename__ == "schema_snapshots"


def test_column_profile_has_expected_columns():
    cols = {c.key for c in ColumnProfile.__table__.columns}
    assert "job_name" in cols
    assert "column_name" in cols
    assert "null_rate" in cols
    assert "p95" in cols


def test_schema_snapshot_has_columns_field():
    cols = {c.key for c in SchemaSnapshot.__table__.columns}
    assert "job_name" in cols
    assert "environment" in cols
    assert "columns" in cols
