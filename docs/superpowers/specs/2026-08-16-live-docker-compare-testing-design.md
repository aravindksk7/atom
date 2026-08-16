# Live Docker Stack & Playwright Compare Test Suite Design Specification

**Date:** 2026-08-16  
**Status:** Approved  
**Topic:** Live Docker Services, Compare Capabilities (AWS, Files, SAP BO), and GitLab CI Integration Testing  

---

## 1. Overview

This design specification establishes a live Docker-backed integration test environment and Playwright E2E test suite for validating all compare functionalities across AWS (S3, Glue Catalog, Athena), file reconciliation (local, SFTP, S3), SAP BusinessObjects (BO/DS), and GitLab CI pipeline trigger/retry mechanisms.

---

## 2. Docker Integration Architecture

### 2.1 Services (`docker-compose.integration.yml`)

1. **`sapbo`** (`atom-sapbo-integration`): SAP BusinessObjects REST API mock container exposing port `18443`.
2. **`sapds`** (`atom-sapds-integration`): SAP Data Services API mock container exposing port `18444`.
3. **`sqlserver`** (`atom-sqlserver-integration`): Microsoft SQL Server 2022 container exposing port `14333`.
4. **`minio`** (`atom-minio-integration`): MinIO object storage container exposing S3 API port `29000`.
5. **`sftp`** (`atom-sftp-integration`): SFTP server container exposing port `12222` with pre-seeded test fixtures.
6. **`localstack`** (`atom-localstack-integration`): LocalStack container exposing port `4566` emulating AWS S3, AWS Glue Catalog, and AWS Athena query engine.
7. **`gitlab-ci-runner`** (`atom-gitlab-integration`): Container exposing port `18080` mocking GitLab SaaS API endpoints (`/api/v4/projects/:id/pipelines`, `/api/v4/projects/:id/jobs/:job_id/retry`), enabling pipeline triggers, status polling, and job retries.

### 2.2 Global Test Environment Lifecycle (`tests/e2e/global-setup.ts`)

- **Health Checks**: Wait for container endpoints (`localhost:18443`, `localhost:4566`, `localhost:29000`, `localhost:18080`) to pass HTTP/TCP health probes.
- **Fixture Seeding**:
  - Seed S3 buckets (`atom-source-bucket`, `atom-target-bucket`) in MinIO and LocalStack.
  - Create AWS Glue catalog databases and tables in LocalStack.
  - Seed SQL Server test databases.
  - Upload target CSV/XLSX files to SFTP container directory (`/home/e2euser/upload`).

---

## 3. Test Scenarios & Playwright E2E Coverage

### 3.1 AWS Compare Specs (`tests/e2e/38-live-docker-aws-compare.spec.ts`)

- **AWS Glue Catalog Compare**:
  - Connect to live LocalStack Glue catalog.
  - Run catalog comparison between source and target databases.
  - Assert column schema differences, type mismatches, and structural stats in the UI.
- **AWS Athena Query Runner Compare**:
  - Execute Athena SQL queries against LocalStack query engine.
  - Compare Athena result set with baseline S3 CSV datasets.
  - Validate mismatch counts and diff table rendering.
- **AWS S3 File Compare**:
  - Compare S3 object pairs across MinIO and LocalStack S3 buckets using both key-based and positional row diffing.

### 3.2 Multi-File & SAP BO Compare Specs (`tests/e2e/39-live-docker-files-sapbo-compare.spec.ts`)

- **Tabular & Multi-File Reconciliation**:
  - Test multi-file comparisons joining multiple source files against multiple target files across local paths, SFTP endpoints, and S3.
  - Verify key matching, missing row identification, and summary statistics.
- **SAP BO Live Report Compare**:
  - Trigger live report execution against `atom-sapbo-integration`.
  - Pass dynamic date prompts and parameters.
  - Compare downloaded report data against target baseline files (`.xlsx`, `.csv`).

### 3.3 GitLab CI Integration & Retry Specs (`tests/e2e/40-live-docker-gitlab-retry.spec.ts`)

- **CI Launch Script**: Run `scripts/ci/run-atom-selection.sh` targeting the `atom-gitlab-integration` service.
- **Pipeline Status & Retry (`gitlabretry`)**:
  - Test pipeline status polling, failed job retry execution via `/retry` API calls, markdown summary generation, and README splicing.

---

## 4. Verification Criteria

- All Playwright E2E specs in `tests/e2e/` pass against the live Docker compose stack.
- `docker-compose.integration.yml` successfully starts and health-checks all 7 service containers.
- All unit, integration, and transform pytest suites remain green.
