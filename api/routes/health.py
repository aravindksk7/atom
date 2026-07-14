from fastapi import APIRouter
from pydantic import ValidationError

from api.schemas import HealthCheckOut, HealthCheckRequest
from api.services.diagnostics_service import DiagnosticsService
from etl_framework.config.models import EnvironmentConfig
from etl_framework.runner.health import HealthChecker

router = APIRouter(tags=["health"])


class _HealthEngine:
    def connect(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


@router.post("/checks", response_model=list[HealthCheckOut])
def run_health_checks(body: HealthCheckRequest):
    checker = HealthChecker()
    results = []
    for env_name, config_data in body.environments.items():
        try:
            EnvironmentConfig.model_validate({"name": env_name, **config_data})
        except ValidationError as exc:
            first = exc.errors()[0]
            results.append(
                HealthCheckOut(
                    component=env_name,
                    healthy=False,
                    message=f"{'.'.join(str(part) for part in first['loc'])}: {first['msg']}",
                )
            )
            continue
        result = checker.check_db(env_name, _HealthEngine())
        results.append(
            HealthCheckOut(
                component=result.component,
                healthy=result.healthy,
                message=result.message,
            )
        )
    return results


@router.get("/diagnostics")
def diagnostics(include_logs: bool = True):
    return DiagnosticsService().build_bundle(include_logs=include_logs)
