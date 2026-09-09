import pytest
from etl_framework.repository.database import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from etl_framework.repository.repository import CustomVariableRepository


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    from etl_framework.repository import models  # noqa: F401 -- registers tables
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_create_and_get(db):
    repo = CustomVariableRepository(db)
    created = repo.create(name="run_date", var_type="date", default_value="today", description="Report date")
    assert created.id is not None
    fetched = repo.get(created.id)
    assert fetched.name == "run_date"
    assert fetched.var_type == "date"
    assert fetched.default_value == "today"


def test_get_by_name(db):
    repo = CustomVariableRepository(db)
    repo.create(name="batch_id", var_type="text", default_value=None, description="")
    found = repo.get_by_name("batch_id")
    assert found is not None
    assert repo.get_by_name("does_not_exist") is None


def test_list_orders_by_name(db):
    repo = CustomVariableRepository(db)
    repo.create(name="zeta", var_type="text", default_value=None, description="")
    repo.create(name="alpha", var_type="text", default_value=None, description="")
    names = [v.name for v in repo.list()]
    assert names == ["alpha", "zeta"]


def test_update(db):
    repo = CustomVariableRepository(db)
    created = repo.create(name="run_date", var_type="date", default_value="today", description="")
    updated = repo.update(created.id, default_value="today-1", description="Yesterday")
    assert updated.default_value == "today-1"
    assert updated.description == "Yesterday"
    assert updated.var_type == "date"  # untouched field stays as-is


def test_update_missing_returns_none(db):
    repo = CustomVariableRepository(db)
    assert repo.update(999, default_value="x") is None


def test_delete(db):
    repo = CustomVariableRepository(db)
    created = repo.create(name="run_date", var_type="date", default_value="today", description="")
    assert repo.delete(created.id) is True
    assert repo.get(created.id) is None
    assert repo.delete(created.id) is False
