import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
import etl_framework.repository.contract_models  # noqa: F401
from etl_framework.repository.contract_repository import ContractRepository
from etl_framework.repository.repository import JobRepository, SchemaSnapshotRepository
from api.services.contract_tester import CheckResult, ContractTestReport, ContractTestingEngine


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_check_result_model():
    check = CheckResult(
        id="schema_001",
        category="schema",
        name="Column type validation",
        status="PASS",
        target="order_id",
        expected="VARCHAR",
        actual="VARCHAR",
        message="Column type matches expected"
    )
    assert check.status == "PASS"
    assert check.category == "schema"


def test_schema_conformance_all_pass(db):
    contract_repo = ContractRepository(db)
    job_repo = JobRepository(db)
    schema_repo = SchemaSnapshotRepository(db)

    contract_repo.create({
        "name": "payments_v1",
        "source_job": "payments_etl",
        "owner": "data-eng@co.com",
        "sla_hours": 4.0,
    })
    job_repo.create({
        "name": "payments_etl",
        "params": {
            "null_check_columns": ["order_id", "user_id"],
            "key_columns": ["order_id"],
        },
    })
    schema_repo.save("payments_etl", "run-1", "source", [
        {"name": "order_id", "dtype": "int64"},
        {"name": "user_id", "dtype": "int64"},
        {"name": "amount", "dtype": "float64"},
    ])
    db.commit()

    engine = ContractTestingEngine(db)
    results = engine.evaluate_schema_conformance("payments_v1")

    assert len(results) == 2
    assert all(r.status == "PASS" for r in results)
    targets = {r.target for r in results}
    assert targets == {"order_id", "user_id"}


def test_schema_conformance_missing_column(db):
    contract_repo = ContractRepository(db)
    job_repo = JobRepository(db)
    schema_repo = SchemaSnapshotRepository(db)

    contract_repo.create({
        "name": "payments_v1",
        "source_job": "payments_etl",
        "owner": "data-eng@co.com",
        "sla_hours": 4.0,
    })
    job_repo.create({
        "name": "payments_etl",
        "params": {
            "null_check_columns": ["order_id", "missing_col"],
            "key_columns": ["order_id"],
        },
    })
    schema_repo.save("payments_etl", "run-1", "source", [
        {"name": "order_id", "dtype": "int64"},
    ])
    db.commit()

    engine = ContractTestingEngine(db)
    results = engine.evaluate_schema_conformance("payments_v1")

    assert len(results) == 2
    results_by_target = {r.target: r for r in results}
    assert results_by_target["order_id"].status == "PASS"
    assert results_by_target["missing_col"].status == "FAIL"
    assert results_by_target["missing_col"].actual == "Missing"

