# Task 3 Report: Tracked Glue Job Validation and Executor

Status: Implemented Task 3 only.

Changes:
- Added `aws_glue_catalog_compare` to `JobDefinition` job type support and Pydantic validation.
- Added job validation for config reference, source/target database/table fields, and boolean compare flags.
- Added `RunExecutor` routing and `_execute_aws_glue_catalog_compare` using `AwsGlueService.compare_tables`.
- Added executor result mapping for pass, drift failure, and Glue error outcomes.
- Added RED/GREEN validation and executor unit tests.

TDD evidence:
- RED: `pytest tests/unit/test_job_validation.py tests/unit/test_run_executor_glue.py -v` failed with missing validation and missing executor/`AwsGlueService` support: 4 failed, 19 passed.
- GREEN: `pytest tests/unit/test_job_validation.py tests/unit/test_run_executor_glue.py -v` passed: 23 passed in 2.80s.

Files touched:
- `api/schemas.py`
- `etl_framework/runner/job_validation.py`
- `api/services/run_executor.py`
- `tests/unit/test_job_validation.py`
- `tests/unit/test_run_executor_glue.py`
- `.superpowers/sdd/glue-task-3-report.md`

Constraints:
- No frontend, Playwright, Athena, Airflow, Glue Spark, or mutation behavior added.
- Generated pycache files were not staged.

## Minor Review Polish

Status: Implemented minor Task 3 review improvements only.

Changes:
- Added Glue-specific non-empty validation messages while preserving `params.<field>` issue fields.
- Stored the actual `diff["format_mismatch"]` payload in the executor format mismatch record.
- Added assertions covering Glue validation messages and format mismatch record values.

TDD evidence:
- RED: `pytest tests/unit/test_job_validation.py tests/unit/test_run_executor_glue.py -v` failed as expected: 2 failed, 21 passed.
- GREEN: `pytest tests/unit/test_job_validation.py tests/unit/test_run_executor_glue.py -v` passed: 23 passed in 2.55s.

Files touched:
- `etl_framework/runner/job_validation.py`
- `api/services/run_executor.py`
- `tests/unit/test_job_validation.py`
- `tests/unit/test_run_executor_glue.py`
- `.superpowers/sdd/glue-task-3-report.md`

Constraints:
- No frontend, routes, Athena, Airflow, or unrelated work added.
