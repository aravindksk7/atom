# AWS Glue Spark / ETL Job Execution — Design

**Date:** 2026-09-05
**Status:** Approved (design), pending implementation plan
**Author:** Principal data engineer (via brainstorming)
**Depends on:** `docs/superpowers/specs/2026-07-26-aws-glue-catalog-compare-design.md`

## Context
ATOM currently supports AWS Glue Data Catalog table comparison and inspection. The AWS expansion now extends Glue to include triggering, polling, and tracking native AWS Glue Spark/ETL jobs.

## Goals
- Support listing available Glue ETL/Spark jobs.
- Support triggering Glue job runs (`start_job_run`) with custom script arguments.
- Support polling Glue job run state (`get_job_run`) to completion.
- Add `aws_glue_job_run` tracked job type to `RunExecutor`.
- Enable Glue Job execution controls in the AWS UI.

## Architecture & Components
1. **Service Layer**: Extend `AwsGlueService` in `api/services/aws_glue_service.py` with `list_jobs`, `get_job`, `start_job_run`, `get_job_run_status`, `run_job_to_completion`.
2. **API Routes**: Add `/api/aws/glue/jobs/*` in `api/routes/aws_glue.py`.
3. **Job Validation & Execution**: Add `aws_glue_job_run` to `api/schemas.py`, `job_validation.py`, and `api/services/run_executor.py`.
4. **UI Panel**: Extend Glue sub-tab in `tab-aws.html` and `aws.js`.
5. **Testing**: Unit tests in `test_aws_glue_service.py`, `test_aws_glue_routes.py`, `test_job_validation.py`, `test_run_executor_glue.py`, and Playwright E2E in `19-aws-glue-tab.spec.ts`.
