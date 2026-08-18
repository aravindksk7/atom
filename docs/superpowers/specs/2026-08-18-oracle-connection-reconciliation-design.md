# Oracle Database Connection, Reconciliation, and Web UI Integration — Design Specification

**Date:** 2026-08-18  
**Status:** Approved  

## Overview
This specification details the addition of Oracle Database support across the ETL framework. Oracle will be supported as a first-class database connection alongside SQL Server (`mssql`) and Netezza (`netezza`), enabling connection testing, environment configuration, database query execution, chunked reconciliation/comparison, and Web UI selection.

## 1. Dependencies & Driver Setup
- Optional dependency in `pyproject.toml`:
  - `oracle = ["oracledb>=2.0.0"]`
- Connection mode: `python-oracledb` in Thin mode (pure Python, requiring no Oracle Client C libraries).
- Dialect: `oracle+oracledb`.

## 2. Configuration Model (`etl_framework/config/models.py`)
- Update `db_type` schema type annotation:
  - `db_type: Literal["mssql", "netezza", "oracle"] = "mssql"`
- Update `set_db_type_defaults`:
  - When `db_type == "oracle"`: default `db_port = 1521` and `db_driver = "oracledb"`.

## 3. DBEngine Architecture (`etl_framework/db/engine.py`)
- Add handling for `db_type == "oracle"` in `DBEngine.__init__`:
  - URL format: `oracle+oracledb://{user}:{password}@{host}:{port}/?service_name={db_name}`
  - Password and username parameters URL-encoded via `urllib.parse.quote_plus`.

## 4. Reconciliation & SQL Utilities
- **SQL Utilities (`etl_framework/db/sql_utils.py`)**:
  - Update `quote_identifier`: support `"oracle"` dialect using ANSI double quotes (`"COLUMN_NAME"`).
- **Chunker & Pagination (`etl_framework/reconciliation/chunker.py`)**:
  - Update `_quote_col` and identifier validation to support Oracle ANSI syntax.
  - Oracle 12c+ pagination structure: `OFFSET {offset} ROWS FETCH NEXT {chunk_size} ROWS ONLY`.

## 5. Web UI & REST API Integration
- **Frontend Dropdowns & Controls (`frontend/index.html`, `frontend/partials/tab-config.html`, `frontend/partials/tab-compare.html`)**:
  - Add `<option value="oracle">Oracle Database</option>` to DB Type selects.
- **Frontend Logic (`frontend/features/config.js`, `frontend/features/compare.js`)**:
  - Auto-set `db_port = 1521` and `db_driver = 'oracledb'` when `oracle` is selected.
  - Field labels note `DB Name` corresponds to Oracle `Service Name`.
- **API & Adapters (`api/routes/configs.py`, `api/routes/compare.py`, `api/routes/adapters.py`)**:
  - Allow `oracle` as valid `db_type` in validation and adapter connection testing.

## 6. Testing & Validation
- Unit tests in `tests/test_oracle_integration.py` for:
  - `EnvironmentConfig` validation & defaults for Oracle.
  - `DBEngine` URL construction for Oracle.
  - Identifier quoting and chunk query generation for Oracle.
  - API validation and frontend modal default behavior.
