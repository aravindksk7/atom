# Expectation Suites (Rules-as-Code)

One YAML file per job. The file's `rules` list **replaces** the job's DQ
rules on sync — this directory is the source of truth once you adopt it.

    job: orders_reconciliation
    rules:
      - type: not_null
        column: id
        severity: error

Sync:   `POST /api/expectations/sync   {"directory": "expectations"}`
Export: `POST /api/expectations/export {"directory": "expectations"}`

Rule types and fields: see `DQRule` in `api/schemas.py` or the Jobs UI.
