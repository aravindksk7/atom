from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.schemas import JobDefinition
from api.services.variable_resolution import resolve_variables, substitute_in_job
from etl_framework.repository.database import Base
from etl_framework.repository.repository import ConfigRepository, CustomVariableRepository


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    from etl_framework.repository import models  # noqa: F401
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_resolve_uses_global_default_when_no_override(db):
    CustomVariableRepository(db).create(name="batch_id", var_type="text", default_value="B1", description="")
    assert resolve_variables(db, config_id=None) == {"batch_id": "B1"}


def test_resolve_config_override_wins_over_global_default(db):
    CustomVariableRepository(db).create(name="batch_id", var_type="text", default_value="B1", description="")
    cfg = ConfigRepository(db).create(name="dev", env_name="dev", config_data={"variables": {"batch_id": "B2"}})
    assert resolve_variables(db, config_id=cfg.id) == {"batch_id": "B2"}


def test_resolve_launch_override_wins_over_config_override(db):
    CustomVariableRepository(db).create(name="batch_id", var_type="text", default_value="B1", description="")
    cfg = ConfigRepository(db).create(name="dev", env_name="dev", config_data={"variables": {"batch_id": "B2"}})
    result = resolve_variables(db, config_id=cfg.id, overrides={"batch_id": "B3"})
    assert result == {"batch_id": "B3"}


def test_resolve_ignores_override_for_undefined_variable(db):
    cfg = ConfigRepository(db).create(name="dev", env_name="dev", config_data={"variables": {"ghost": "x"}})
    assert resolve_variables(db, config_id=cfg.id) == {}


def test_resolve_evaluates_today_expression(db):
    CustomVariableRepository(db).create(name="run_date", var_type="date", default_value="today-1", description="")
    result = resolve_variables(db, config_id=None)
    assert result["run_date"] == (date.today() - timedelta(days=1)).isoformat()


def test_resolve_leaves_literal_date_untouched(db):
    CustomVariableRepository(db).create(name="run_date", var_type="date", default_value="2026-01-01", description="")
    assert resolve_variables(db, config_id=None) == {"run_date": "2026-01-01"}


def test_substitute_in_job_replaces_query_placeholder():
    job = JobDefinition(
        name="j1", query="SELECT * FROM sales WHERE dt = '{{run_date}}'", key_columns=["id"],
    )
    result = substitute_in_job(job, {"run_date": "2026-09-08"})
    assert result.query == "SELECT * FROM sales WHERE dt = '2026-09-08'"


def test_substitute_in_job_replaces_nested_params():
    job = JobDefinition(
        name="j1", job_type="bo_report",
        params={
            "report_id": "R1",
            "bo_parameters": [{"name": "Date", "value": "{{run_date}}"}],
        },
    )
    result = substitute_in_job(job, {"run_date": "2026-09-08"})
    assert result.params["bo_parameters"][0]["value"] == "2026-09-08"


def test_substitute_in_job_leaves_unknown_placeholder_verbatim():
    job = JobDefinition(name="j1", query="SELECT '{{typo_var}}'", key_columns=["id"])
    result = substitute_in_job(job, {"run_date": "2026-09-08"})
    assert result.query == "SELECT '{{typo_var}}'"


def test_substitute_in_job_no_variables_returns_same_job():
    job = JobDefinition(name="j1", query="SELECT 1", key_columns=["id"])
    assert substitute_in_job(job, {}) is job
