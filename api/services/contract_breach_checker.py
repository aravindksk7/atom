"""Post-run hook that opens/resolves contract breaches based on run outcome."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from etl_framework.runner.state import TestCaseState


class ContractBreachChecker:
    def check(
        self,
        states: list["TestCaseState"],
        run_id: str,
        db: "Session",
    ) -> None:
        try:
            from etl_framework.repository.contract_repository import ContractRepository
            from etl_framework.repository.repository import NotificationRepository
            from api.services.notifier import notify
            from etl_framework.runner.state import TestStatus

            repo = ContractRepository(db)

            failed_jobs: set[str] = set()
            passed_jobs: set[str] = set()
            for state in states:
                if state.status in (TestStatus.FAILED, TestStatus.ERROR):
                    failed_jobs.add(state.name)
                elif state.status in (TestStatus.PASSED, TestStatus.SLOW):
                    passed_jobs.add(state.name)

            breach_hooks = NotificationRepository(db).list_enabled_for_event("contract.breached")
            for job_name in failed_jobs:
                for contract in repo.list_by_source_job(job_name):
                    breach = repo.open_breach(contract.id, run_id, "dq_violation")
                    if breach is not None:
                        notify(
                            run_id,
                            "contract.breached",
                            extra={
                                "contract": contract.name,
                                "source_job": job_name,
                                "breach_type": "dq_violation",
                                "owner": contract.owner,
                            },
                            hooks=breach_hooks,
                            db_session=db,
                        )

            resolve_hooks = NotificationRepository(db).list_enabled_for_event("contract.resolved")
            for job_name in passed_jobs - failed_jobs:
                for breach, contract in repo.resolve_breaches_for_job(job_name, run_id):
                    notify(
                        run_id,
                        "contract.resolved",
                        extra={
                            "contract": contract.name,
                            "source_job": job_name,
                            "duration_hours": breach.duration_hours,
                            "owner": contract.owner,
                        },
                        hooks=resolve_hooks,
                        db_session=db,
                    )
        except Exception:
            pass  # never let contract checking affect the run
