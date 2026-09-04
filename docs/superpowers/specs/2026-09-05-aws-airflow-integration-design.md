# AWS Airflow / MWAA Integration — Design

**Date:** 2026-09-05
**Status:** Approved (design), pending implementation plan
**Author:** Principal data engineer (via brainstorming)
**Depends on:** `docs/superpowers/specs/2026-07-26-aws-athena-query-runner-design.md`

## Context
The AWS expansion needs full Airflow integration to orchestrate and track DAG execution. ATOM must support both AWS MWAA (via AWS authentication) and standard standalone Airflow 2.x REST API environments.

## Goals
- Support Airflow 2.x REST API (standard) and AWS MWAA (via AWS creds).
- Trigger DAG runs, poll status, and track completion/failures.
- DAG integrity check functionality (parsing/validation).
- Tracked `airflow_dag_run` job type in standard ATOM run execution flow.
- Enable the Airflow sub-tab in AWS UI.

## Design Decisions
| Decision | Choice | Rationale |
|---|---|---|
| API Client | Wrapper for Airflow REST API (`httpx`) | Standard, robust, supports auth tokens (Basic, Bearer, MWAA/JWT). |
| Auth Layer | Adaptive Runtime (`AwsAirflowRuntime`) | Decouples AWS MWAA-specific token generation from standard REST client. |
| Job Type | `airflow_dag_run` | Consistent with other ATOM job types (`aws_athena_query`, `s3_row_count`). |
| Deployment | Dual support (MWAA & Airflow REST API) | Ensures broad flexibility as requested. |

## Architecture Components
1. **Client**: `etl_framework/airflow/client.py` - REST API wrapper.
2. **RuntimeResolver**: `api/services/aws_airflow_runtime.py` - Handles authentication via AWS (for MWAA) or direct (for plain Airflow).
3. **Execution Service**: `api/services/aws_airflow_service.py` - DAG run orchestration and polling logic.
4. **API Routes**: `/api/aws/airflow/*` endpoints for UI interaction.
5. **UI**: Airflow sub-tab in AWS AWS-UI.
6. **Integration**: `airflow_dag_run` handler in `RunExecutor`.
