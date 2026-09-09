from api.schemas import RunSettings
from api.services.run_executor import RunExecutor


class _FakeJobRepo:
    def list(self):
        return []


def test_build_jobs_index_substitutes_seed_job_query(db_session=None):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from etl_framework.repository.database import Base

    engine = create_engine("sqlite:///:memory:")
    from etl_framework.repository import models  # noqa: F401
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    executor = RunExecutor(
        db=db, run_id="r1", source_env="dev", target_env="prod",
        job_sequence=[], run_settings=RunSettings(),
        config_snapshot={"variables": {"table": "orders_snapshot"}},
    )
    executor._job_repo = _FakeJobRepo()
    index = executor._build_jobs_index()
    assert index["orders_reconciliation"].query == "SELECT * FROM orders"


class _FakeSavedJob:
    name = "custom_job"
    description = ""
    tags = []
    job_type = "bo_report"
    query = "SELECT * FROM t WHERE dt = '{{run_date}}'"
    key_columns = []
    exclude_columns = []
    source_env = None
    target_env = None
    params = {
        "report_id": "R1",
        "bo_parameters": [{"name": "Date", "value": "{{run_date}}"}],
    }
    enabled = True


class _FakeJobRepoWithJob:
    def list(self):
        return [_FakeSavedJob()]


def test_build_jobs_index_substitutes_saved_job_query():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from etl_framework.repository.database import Base

    engine = create_engine("sqlite:///:memory:")
    from etl_framework.repository import models  # noqa: F401
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    executor = RunExecutor(
        db=db, run_id="r1", source_env="dev", target_env="prod",
        job_sequence=[], run_settings=RunSettings(),
        config_snapshot={"variables": {"run_date": "2026-09-08"}},
    )
    executor._job_repo = _FakeJobRepoWithJob()
    index = executor._build_jobs_index()
    assert index["custom_job"].query == "SELECT * FROM t WHERE dt = '2026-09-08'"
    assert index["custom_job"].params["bo_parameters"][0]["value"] == "2026-09-08"
