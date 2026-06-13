from __future__ import annotations
from dataclasses import dataclass

from etl_framework.utils.logging import get_logger

logger = get_logger("runner.health")


@dataclass
class HealthCheckResult:
    component: str
    healthy: bool
    message: str


class HealthChecker:
    def check_db(self, name: str, engine) -> HealthCheckResult:
        try:
            with engine.connect():
                pass
            return HealthCheckResult(component=name, healthy=True, message="OK")
        except Exception as exc:
            logger.warning("Health check failed for %r: %s", name, exc)
            return HealthCheckResult(component=name, healthy=False, message=str(exc))

    def all_healthy(self, results: list[HealthCheckResult]) -> bool:
        return all(r.healthy for r in results)
