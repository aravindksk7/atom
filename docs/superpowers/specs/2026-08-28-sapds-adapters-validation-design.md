# SAPDS Validation in Adapters Tab — Design Specification

## Summary
Add functionality to validate SAP Data Services (SAPDS) connections and lookup/validate SAPDS job status in the Adapters tab of the Web UI, consistent with existing SAP BusinessObjects and Automic Workload adapter panels.

## Backend Architecture

### 1. Schemas (`api/schemas.py`)
- `SAPDSTestRequest`: Config ID container for SAPDS REST API connection testing (`config_id: int`).
- `SAPDSLookupRequest`: Request model for job lookup (`config_id: int`, `identifier: str`, `id_type: Literal["job_name", "run_id"]`, `repository: str | None = None`).
- `SAPDSJobStatusOut`: Response model for job validation (`identifier: str`, `identifier_type: str`, `repository: str`, `status: str`, `environment: str`, `checked_at: datetime`).
- `SAPDSJobCreateRequest`: Request model to import a validated SAPDS job into the Job Catalog (`name: str`, `job_name: str`, `repository: str | None = None`, `poll_interval_s: int = 5`, `timeout_s: int = 600`).

### 2. Service Layer (`api/services/adapter_service.py`)
- `test_ds_connection(config_id: int) -> AdapterTestOut`:
  - Retrieves `EnvironmentConfig` for `config_id`.
  - Instantiates `DSRestClient(env)`.
  - Calls `login()` and `logout()` measuring execution latency.
  - Returns `AdapterTestOut(ok=True/False, message=..., latency_ms=...)` handling any `DSAPIError` or network exceptions with `_friendly_error`.
- `lookup_ds_job(config_id: int, identifier: str, id_type: str, repository: str | None = None) -> SAPDSJobStatusOut`:
  - Instantiates `DSRestClient(env)`.
  - Checks job status using `get_job_status(run_id=identifier, repository=repository)` or job lookup.
  - Returns `SAPDSJobStatusOut` containing status, repository, environment, and timestamp.

### 3. API Routes (`api/routes/adapters.py`)
- `POST /api/adapters/sap-ds/test`: Test connection credentials and API reachability for SAPDS.
- `POST /api/adapters/sap-ds/lookup`: Lookup and validate status of a SAPDS batch job or run.
- `POST /api/adapters/jobs/from-sap-ds`: Save a validated SAPDS job as a `ds_job` in the Job Catalog.

## Frontend Architecture

### 1. Adapters Tab UI (`frontend/partials/tab-adapters.html`)
- Add a new card for **SAP Data Services** in the grid layout:
  - Header with title "SAP Data Services" and badge `badge-purple` "Management Console".
  - Config dropdown select (`dsConfigId`).
  - **Test Connection** button (`testDSConnection()`) with latency indicator and test status message box (`dsTestResult`).
  - **Job Lookup & Validation** section:
    - Config select, Lookup Type (`job_name` / `run_id`), Identifier text input, and optional Repository text input (placeholder showing default repository from config).
    - "Lookup" button (`lookupSAPDS()`).
    - Result panel (`dsResult`) with status badge (`PASSED`, `FAILED`, `RUNNING`), repository name, environment name, checked timestamp, and `+ Add to Job Catalog` button (`addSAPDSJob()`).

### 2. Alpine.js Component (`frontend/app.js` or `frontend/app-adapters.js`)
- State variables: `dsConfigId`, `dsTesting`, `dsTestResult`, `dsIdType`, `dsIdentifier`, `dsRepository`, `dsLoading`, `dsResult`, `dsHistory`.
- Integration functions to perform backend API calls and handle UI reactions.

## Verification & Testing Strategy
1. **Unit Tests**:
   - `tests/unit/test_adapter_service.py`: Add unit tests for `test_ds_connection` and `lookup_ds_job`.
   - `tests/unit/test_adapters_routes.py`: Add endpoint tests for `/api/adapters/sap-ds/test`, `/api/adapters/sap-ds/lookup`, and `/api/adapters/jobs/from-sap-ds`.
2. **Integration / E2E Validation**:
   - Verify compatibility against `docker/sapds-mock/server.py` mock server.
