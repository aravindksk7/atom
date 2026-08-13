# Design Document: IBM Netezza Support

## Overview

This document specifies the design for extending the ETL & SAP BO Testing Framework to support IBM Netezza databases alongside Microsoft SQL Server. The framework will allow environments to configure IBM Netezza database connections using either the pure-Python `nzpy` driver or `pyodbc` with IBM Netezza ODBC drivers.

## Scope & Requirements

1. **Configuration Extensibility**:
   - Add optional `db_type` configuration field to environment profiles (`mssql` | `netezza`, defaulting to `mssql`).
   - Default port mapping: `mssql` -> `1433`, `netezza` -> `5480`.
   - Support driver selection (`nzpy` or `pyodbc` with Netezza ODBC drivers).

2. **Database Engine Integration**:
   - Dynamic SQLAlchemy connection URL generation in `DB_Engine` based on `db_type` and `db_driver`.
   - Support `netezza+nzpy://` for pure-Python driver connections.
   - Support `netezza+pyodbc://` for ODBC-backed connections.
   - Unified error handling wrapping underlying database errors into `DatabaseConnectionError` and `QueryExecutionError` while protecting credentials.

3. **Reconciliation & Validation**:
   - Existing Pandas and Reconciliation engine operations remain database-agnostic.
   - SQL query execution returns `pandas.DataFrame` regardless of underlying database engine (`mssql` or `netezza`).

## Architecture & Data Flow

```
[Environment Config]
         │
         ▼ (db_type: netezza, db_driver: nzpy|pyodbc)
  [Config Loader]
         │
         ▼
    [DB Engine]
         │
 ┌───────┴─────────────────────────┐
 ▼                                 ▼
(netezza+nzpy://)       (netezza+pyodbc://)
 │                                 │
 ▼                                 ▼
[nzpy Driver]           [IBM Netezza ODBC]
 └───────┬─────────────────────────┘
         │
         ▼
 [pandas.DataFrame] ──► [Reconciliation Engine]
```

## Detailed Components

### 1. Config Loader Updates (`src/config/loader.py` or equivalent)
- `EnvConfig` data model updated with `db_type: str = "mssql"`.
- If `db_type == "netezza"` and `port` is not explicitly set, default `port` to `5480`.
- Validation ensures `host`, `database`, `user`, and `password` are present.

### 2. Database Engine Updates (`src/db/engine.py` or equivalent)
- `get_connection_url()` helper method:
  - If `db_type == "mssql"`: `mssql+pyodbc://{user}:{password}@{host}:{port}/{database}?driver={db_driver}`
  - If `db_type == "netezza"`:
    - If `db_driver == "nzpy"`: `netezza+nzpy://{user}:{password}@{host}:{port}/{database}`
    - Else: `netezza+pyodbc://{user}:{password}@{host}:{port}/{database}?driver={db_driver}`

### 3. Dependencies
- Add optional `nzpy` package to `pyproject.toml` dependencies.

## Verification & Testing Strategy
- Unit tests for `Config_Loader` testing `db_type: netezza` defaults and validations.
- Unit tests for `DB_Engine` verifying connection URL construction for both `nzpy` and `pyodbc` drivers.
- Integration tests using mock database connections / engines.
