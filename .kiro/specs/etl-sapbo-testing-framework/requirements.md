# Requirements Document

## Introduction

A lightweight, production-ready ETL and SAP BusinessObjects (BO) testing framework built in Python. The framework enables data engineers and QA teams to automate reconciliation between source and target environments (e.g., Dev and QA), monitor Automic job execution status, validate SAP BO report data across environments, and generate a clean standalone HTML dashboard summarising all test results. The primary database focus is Microsoft SQL Server, with extensibility in mind.

---

## Glossary

- **Framework**: The ETL and SAP BO testing framework described in this document.
- **Environment**: A configured database or API endpoint representing a distinct deployment stage (e.g., Dev, QA, Production).
- **Source_Environment**: The reference environment used as the baseline for reconciliation (typically Dev or Production).
- **Target_Environment**: The environment under test, compared against the Source_Environment (typically QA or UAT).
- **Config_Loader**: The component responsible for reading and validating environment configuration files.
- **Automic_Client**: The component responsible for querying the Automic REST API for job execution status.
- **DB_Engine**: The SQLAlchemy-based component responsible for establishing database connections and executing SQL queries.
- **Reconciliation_Engine**: The component responsible for comparing datasets from two environments using pandas and identifying discrepancies.
- **SAP_BO_Validator**: The component responsible for validating SAP BusinessObjects report data across two environments.
- **Report_Generator**: The component responsible for producing a standalone HTML dashboard from test results.
- **Run_ID**: A unique identifier assigned to an Automic job execution instance.
- **Job_Name**: A human-readable label identifying an Automic job.
- **Mismatch**: A row or field that exists in one dataset but not the other, or whose values differ between datasets.
- **Reconciliation_Result**: A structured object containing matched row count, mismatch count, missing row counts, and detailed mismatch records.
- **Test_Suite**: A collection of test cases executed in a single framework run.
- **HTML_Report**: The standalone HTML file produced by the Report_Generator summarising the Test_Suite results.
- **Run_Script**: The `run_tests.py` entry point that orchestrates the full test execution lifecycle.
- **Ad-Hoc_Mode**: A framework execution mode where a single component or test is invoked directly without a full Test_Suite definition.
- **Isolated_Execution**: The property of a component that can be instantiated and invoked independently without requiring other framework components to be initialised.

---

## Requirements

### Requirement 1: Environment Configuration Management

**User Story:** As a data engineer, I want to define and load configurations for two environments from a file, so that all components can connect to the correct databases and APIs without hardcoding credentials.

#### Acceptance Criteria

1. THE Config_Loader SHALL read environment configuration from a YAML or TOML file located at a path specified by the caller.
2. WHEN the configuration file is not found at the specified path, THE Config_Loader SHALL raise a descriptive `ConfigurationError` identifying the missing file path.
3. WHEN a required field is absent from the configuration file, THE Config_Loader SHALL raise a `ConfigurationError` identifying the missing field name.
4. THE Config_Loader SHALL support configuration of at least two named environments (e.g., `dev` and `qa`), each containing database connection parameters and optional API endpoint parameters.
5. THE Config_Loader SHALL resolve environment variable references (e.g., `${DB_PASSWORD}`) within configuration values, substituting the actual environment variable value at load time.
6. IF an environment variable referenced in the configuration is not set, THEN THE Config_Loader SHALL raise a `ConfigurationError` identifying the unresolved variable name.
7. THE Config_Loader SHALL expose loaded environment configurations as typed objects accessible by environment name.

---

### Requirement 2: Automic Job Monitoring

**User Story:** As a QA engineer, I want to query the Automic REST API for job execution statuses by Run ID or Job Name, so that I can verify whether ETL pipeline jobs completed successfully before running reconciliation tests.

#### Acceptance Criteria

1. THE Automic_Client SHALL authenticate with the Automic REST API using credentials supplied from the active environment configuration.
2. WHEN a Run_ID is provided, THE Automic_Client SHALL return the execution status (`SUCCESS`, `FAILED`, `RUNNING`, `NOT_FOUND`) for that Run_ID.
3. WHEN a Job_Name is provided, THE Automic_Client SHALL return the most recent execution status for that Job_Name.
4. WHEN the Automic REST API returns an HTTP error response (4xx or 5xx), THE Automic_Client SHALL raise an `AutomicAPIError` containing the HTTP status code and response body.
5. WHEN the Automic REST API does not respond within the configured timeout period and all retry attempts have been exhausted, THE Automic_Client SHALL raise an `AutomicTimeoutError`.
6. THE Automic_Client SHALL support querying a list of Run_IDs or Job_Names in a single call, returning a mapping of each identifier to its status.
7. THE Automic_Client SHALL log each outbound API request and the resulting status at DEBUG level.
8. THE Automic_Client SHALL retry failed requests up to a configurable maximum number of attempts with exponential backoff before raising an error.

---

### Requirement 3: Database Connection Management

**User Story:** As a data engineer, I want the framework to manage SQL Server database connections reliably, so that queries can be executed against both environments without manual connection handling.

#### Acceptance Criteria

1. THE DB_Engine SHALL establish a SQLAlchemy connection to a SQL Server database using parameters from the environment configuration (host, port, database name, username, password, driver).
2. WHEN a database connection cannot be established within the configured timeout, THE DB_Engine SHALL raise a `DatabaseConnectionError` identifying the environment name and connection parameters (excluding passwords).
3. THE DB_Engine SHALL use connection pooling with configurable pool size and overflow limits.
4. WHEN a SQL query is executed, THE DB_Engine SHALL return results as a `pandas.DataFrame`.
5. WHEN a SQL query execution fails, THE DB_Engine SHALL raise a `QueryExecutionError` containing the environment name, the query text, and the underlying database error message.
6. THE DB_Engine SHALL log each executed query and its row count result at DEBUG level.
7. THE DB_Engine SHALL support parameterised queries to prevent SQL injection.
8. THE DB_Engine SHALL close all connections gracefully when the framework run completes or when an unhandled exception occurs.

---

### Requirement 4: DB Reconciliation Engine

**User Story:** As a data engineer, I want to compare query results from two environments row-by-row and field-by-field, so that I can identify data discrepancies introduced by ETL transformations or environment differences.

#### Acceptance Criteria

1. WHEN given a SQL query and two environment names, THE Reconciliation_Engine SHALL execute the query against both environments and return a `Reconciliation_Result`.
2. THE Reconciliation_Engine SHALL identify rows present in the Source_Environment dataset but absent from the Target_Environment dataset and record them as `missing_in_target`.
3. THE Reconciliation_Engine SHALL identify rows present in the Target_Environment dataset but absent from the Source_Environment dataset and record them as `missing_in_source`.
4. WHEN matching rows exist in both datasets, THE Reconciliation_Engine SHALL compare each column value and record any differences as `value_mismatches`, including the column name, source value, and target value.
5. THE Reconciliation_Engine SHALL accept a configurable list of key columns used to align rows between datasets before comparison.
6. THE Reconciliation_Engine SHALL accept a configurable tolerance value for floating-point column comparisons to avoid false mismatches from rounding differences.
7. THE Reconciliation_Engine SHALL accept a configurable list of columns to exclude from comparison (e.g., audit timestamps).
8. THE Reconciliation_Engine SHALL return summary counts: total rows in source, total rows in target, matched rows, `missing_in_target` count, `missing_in_source` count, and `value_mismatch` count.
9. WHEN both datasets are identical, THE Reconciliation_Engine SHALL return a `Reconciliation_Result` with zero mismatches.
10. THE Reconciliation_Engine SHALL support a configurable row limit to cap the number of mismatch records stored in the result (to prevent memory issues on large datasets).
11. FOR ALL valid pairs of datasets that are identical after applying the configured key columns and exclusions, reconciling the source against the target then reconciling the target against the source SHALL produce symmetric `missing_in_target` and `missing_in_source` counts (round-trip symmetry property).

---

### Requirement 5: SAP BO Report Validation

**User Story:** As a QA analyst, I want to validate that SAP BusinessObjects report data is consistent across environments, so that I can confirm reports return correct results after ETL runs or environment promotions.

#### Acceptance Criteria

1. THE SAP_BO_Validator SHALL accept a report identifier and two environment configurations, then retrieve the report's underlying data from both environments for comparison.
2. WHEN a SAP BO report identifier does not exist in the target environment, THE SAP_BO_Validator SHALL raise a `ReportNotFoundError` identifying the report identifier and environment.
3. THE SAP_BO_Validator SHALL delegate dataset comparison to the Reconciliation_Engine, reusing all reconciliation logic including key columns, exclusions, and tolerance settings.
4. THE SAP_BO_Validator SHALL support validation of report data sourced from direct SQL queries against the underlying database (as a primary method) or from BO REST API responses (as a configurable alternative).
5. WHEN a SAP BO REST API call fails, THE SAP_BO_Validator SHALL raise a `BOAPIError` containing the report identifier, HTTP status code, and response body.
6. THE SAP_BO_Validator SHALL return a `Reconciliation_Result` that can be consumed identically to a DB reconciliation result.

---

### Requirement 6: HTML Report Generation

**User Story:** As a team lead, I want a standalone HTML dashboard generated after each framework run, so that I can review test outcomes without needing a running server or access to the test environment.

#### Acceptance Criteria

1. WHEN a Test_Suite completes, THE Report_Generator SHALL produce a single self-contained HTML file that requires no external network requests to render correctly.
2. THE Report_Generator SHALL include a summary widget displaying: total jobs monitored, total reconciliation tests run, count of passed tests, and count of failed tests.
3. THE Report_Generator SHALL include a searchable, sortable table listing each executed job with its name, status, duration, and environment.
4. THE Report_Generator SHALL include a reconciliation results section listing each test with its query name, source row count, target row count, mismatch count, and a pass/fail status badge.
5. WHEN a reconciliation test has mismatches, THE Report_Generator SHALL include an expandable detail section showing the mismatch records (key column values, column name, source value, target value), capped at a configurable maximum number of rows for readability.
6. THE Report_Generator SHALL colour-code status indicators: green for passed, red for failed, amber for running or inconclusive.
7. THE Report_Generator SHALL embed the test run timestamp, framework version, and environment names in the report header.
8. THE Report_Generator SHALL write the HTML file to a configurable output path, defaulting to `./reports/report_<timestamp>.html`.
9. WHEN the output directory does not exist, THE Report_Generator SHALL create it before writing the file; IF directory creation fails (e.g., due to insufficient permissions or disk space), THEN THE Report_Generator SHALL halt execution and raise a `ReportOutputError` identifying the target path and the underlying OS error.
10. THE Report_Generator SHALL use Jinja2 templates for report rendering, keeping template logic separate from Python code.

---

### Requirement 7: Logging and Observability

**User Story:** As a developer, I want consistent, structured logging throughout the framework, so that I can diagnose failures quickly in CI/CD pipelines and production runs.

#### Acceptance Criteria

1. THE Framework SHALL use Python's `logging` module with a configurable log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
2. THE Framework SHALL write log output simultaneously to both the console (stdout) and a rotating file handler, with the log file path configurable; both output destinations SHALL always be active and neither may be disabled through configuration.
3. WHEN a component raises a handled exception, THE Framework SHALL log the error at ERROR level with the exception traceback.
4. THE Framework SHALL include the component name and timestamp in every log message.
5. WHEN running in DEBUG mode, THE Framework SHALL log SQL query text, API request URLs, and response payload sizes.

---

### Requirement 8: Run Script Entry Point

**User Story:** As an engineer, I want a single `run_tests.py` script that orchestrates the full test lifecycle, so that I can execute the framework with a single command and a config file path.

#### Acceptance Criteria

1. THE Run_Script SHALL accept a `--config` command-line argument specifying the path to the environment configuration file.
2. THE Run_Script SHALL accept an optional `--env-source` and `--env-target` argument to override the default source and target environment names.
3. THE Run_Script SHALL accept an optional `--output-dir` argument to override the default HTML report output directory.
4. THE Run_Script SHALL execute Automic job status checks, DB reconciliation tests, and SAP BO validations in sequence, collecting all results before generating the HTML report.
5. WHEN all tests pass, THE Run_Script SHALL exit with return code `0`.
6. WHEN one or more tests fail, THE Run_Script SHALL exit with return code `1`.
7. THE Run_Script SHALL include a sample test suite definition (as inline configuration or a separate YAML file) using dummy connection parameters, so that a new user can run the framework end-to-end without real credentials.

---

### Requirement 9: Ad-Hoc and Isolated Execution

**User Story:** As a data engineer or QA analyst, I want to run individual checks or comparisons in isolation without executing the full test suite, so that I can quickly investigate a specific job, SQL query, or SAP BO report on demand without configuring and running everything.

#### Acceptance Criteria

1. THE Run_Script SHALL accept a `--run-jobs` flag that, when provided, causes THE Run_Script to execute only Automic job status checks and skip all DB reconciliation and SAP BO validation steps.
2. THE Run_Script SHALL accept a `--run-sql` flag that, when provided, causes THE Run_Script to execute only DB reconciliation tests and skip all Automic job checks and SAP BO validation steps.
3. THE Run_Script SHALL accept a `--run-bo` flag that, when provided, causes THE Run_Script to execute only SAP BO report validations and skip all Automic job checks and DB reconciliation steps.
4. THE Run_Script SHALL accept a `--test-name` argument specifying the name of a single named test case; WHEN `--test-name` is provided, THE Run_Script SHALL perform a case-sensitive exact match against defined test case names, execute only the matching test case, and skip all other test cases; IF no test case name matches the supplied value, THEN THE Run_Script SHALL exit with return code `1` and log an ERROR identifying the unmatched name.
5. WHEN the `--run-sql` flag is combined with a `--query` argument containing an inline SQL string and `--env-source` and `--env-target` arguments identifying two environment names, THE Run_Script SHALL execute a single ad-hoc SQL reconciliation using those inputs without requiring a Test_Suite YAML configuration file.
6. WHEN the `--run-sql` flag is combined with a `--query-file` argument containing the path to a SQL file and `--env-source` and `--env-target` arguments identifying two environment names, THE Run_Script SHALL read the SQL from the specified file and execute a single ad-hoc SQL reconciliation without requiring a Test_Suite YAML configuration file; IF the path supplied to `--query-file` does not exist or is not readable, THEN THE Run_Script SHALL exit with return code `1` and log an ERROR identifying the missing file path.
7. WHEN the `--run-bo` flag is combined with a `--report-id` argument and `--env-source` and `--env-target` arguments, THE Run_Script SHALL execute a single ad-hoc SAP BO report comparison for the specified report identifier without requiring a Test_Suite YAML configuration file.
8. WHEN the `--run-jobs` flag is combined with a `--run-id` argument or a `--job-name` argument, THE Run_Script SHALL use the `--config` argument solely to supply Automic API credentials and SHALL execute a single ad-hoc Automic job status check for the specified Run_ID or Job_Name without requiring a Test_Suite YAML configuration file.
9. THE Automic_Client SHALL be importable and instantiable as a standalone Python object by passing an environment configuration dict directly to its constructor, without requiring THE Run_Script or any other framework component to be initialised.
10. THE Reconciliation_Engine SHALL be importable and instantiable as a standalone Python object by passing two DB_Engine instances directly to its constructor, without requiring THE Run_Script or any other framework component to be initialised.
11. THE SAP_BO_Validator SHALL be importable and instantiable as a standalone Python object by passing two environment configuration dicts directly to its constructor, without requiring THE Run_Script or any other framework component to be initialised.
12. THE Run_Script SHALL accept an `--output-format` argument with the permitted values `html` and `console`; WHEN `--output-format html` is specified, THE Run_Script SHALL produce an HTML_Report scoped to the executed subset; WHEN `--output-format console` is specified, THE Run_Script SHALL print a formatted summary table to stdout and SHALL NOT write an HTML file; IF a value other than `html` or `console` is supplied, THEN THE Run_Script SHALL exit with return code `1` and log an ERROR listing the permitted values.
13. THE `--output-format` argument SHALL default to `html` when not supplied, preserving the existing full-suite report behaviour.
14. WHEN operating in Ad-Hoc_Mode, THE Run_Script SHALL initialise only the component required by the selected flag and SHALL NOT instantiate, connect, or invoke any component that is not needed for the selected execution subset.
15. WHEN `--run-sql` is used without a `--config` file and with `--query` or `--query-file`, THE Run_Script SHALL accept `--env-source-url` and `--env-target-url` arguments as inline SQLAlchemy connection strings so that a user can perform a quick one-off reconciliation without creating a configuration file.
16. IF conflicting test-type flags are provided together (e.g., `--run-jobs` combined with `--run-sql`), THEN THE Run_Script SHALL execute the union of the selected test types and SHALL log a WARNING listing the combined selection.
