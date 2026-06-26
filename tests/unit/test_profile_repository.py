"""Tests for ColumnProfileRepository and SchemaSnapshotRepository."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from etl_framework.repository.database import Base
from etl_framework.repository.repository import ColumnProfileRepository, SchemaSnapshotRepository


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    from etl_framework.repository import models  # noqa: registers all ORM
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_save_and_get_latest_profile(db):
    repo = ColumnProfileRepository(db)
    repo.save("orders", "run-1", "amount", null_rate=0.1, distinct_count=50,
               min_val="1.0", max_val="999.0", mean_val=100.0, std_val=20.0,
               p25=50.0, p50=100.0, p75=150.0, p95=200.0)
    db.commit()
    profiles = repo.get_latest("orders")
    assert len(profiles) == 1
    assert profiles[0].column_name == "amount"


def test_get_history(db):
    repo = ColumnProfileRepository(db)
    repo.save("orders", "run-1", "amount", null_rate=0.1, distinct_count=10,
               min_val=None, max_val=None, mean_val=10.0, std_val=1.0,
               p25=None, p50=None, p75=None, p95=None)
    db.commit()
    repo.save("orders", "run-2", "amount", null_rate=0.2, distinct_count=12,
               min_val=None, max_val=None, mean_val=12.0, std_val=1.5,
               p25=None, p50=None, p75=None, p95=None)
    db.commit()
    history = repo.get_history("orders", "amount")
    assert len(history) == 2


def test_save_and_get_latest_snapshot(db):
    repo = SchemaSnapshotRepository(db)
    cols = [{"name": "id", "dtype": "int64"}, {"name": "name", "dtype": "object"}]
    repo.save("orders", "run-1", "source", cols)
    db.commit()
    snapshot = repo.get_latest("orders", "source")
    assert snapshot is not None
    assert len(snapshot.columns) == 2


def test_get_snapshot_history(db):
    repo = SchemaSnapshotRepository(db)
    repo.save("orders", "run-1", "source", [{"name": "id", "dtype": "int64"}])
    db.commit()
    repo.save("orders", "run-2", "source", [{"name": "id", "dtype": "int64"}, {"name": "email", "dtype": "object"}])
    db.commit()
    history = repo.get_history("orders", "source")
    assert len(history) == 2
