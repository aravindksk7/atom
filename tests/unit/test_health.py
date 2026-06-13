import pytest
from unittest.mock import MagicMock, patch
from etl_framework.runner.health import HealthChecker, HealthCheckResult


def _make_engine(connected: bool = True):
    engine = MagicMock()
    conn = MagicMock()
    if connected:
        engine.connect.return_value.__enter__ = lambda s: conn
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    else:
        engine.connect.side_effect = Exception("connection refused")
    return engine


def test_health_check_result_fields():
    result = HealthCheckResult(component="db_source", healthy=True, message="OK")
    assert result.component == "db_source"
    assert result.healthy is True
    assert result.message == "OK"


def test_health_check_result_unhealthy():
    result = HealthCheckResult(component="db_target", healthy=False, message="timeout")
    assert result.healthy is False


def test_checker_db_healthy():
    engine = _make_engine(connected=True)
    checker = HealthChecker()
    result = checker.check_db(name="source", engine=engine)
    assert result.healthy is True
    assert result.component == "source"


def test_checker_db_unhealthy():
    engine = _make_engine(connected=False)
    checker = HealthChecker()
    result = checker.check_db(name="target", engine=engine)
    assert result.healthy is False
    assert "connection refused" in result.message


def test_checker_all_healthy_returns_true():
    checker = HealthChecker()
    results = [
        HealthCheckResult("db_source", True, "OK"),
        HealthCheckResult("db_target", True, "OK"),
    ]
    assert checker.all_healthy(results) is True


def test_checker_any_unhealthy_returns_false():
    checker = HealthChecker()
    results = [
        HealthCheckResult("db_source", True, "OK"),
        HealthCheckResult("db_target", False, "timeout"),
    ]
    assert checker.all_healthy(results) is False


def test_checker_empty_results_returns_true():
    checker = HealthChecker()
    assert checker.all_healthy([]) is True
