# ADR 0003: DuckDB files for spreadsheet datasets

Status: accepted · Date: 2026-09-16

## Context
Excel/CSV uploads must be queryable with SQL, isolated per dataset, cheap to host and safe.

## Decision
Import each sheet into a typed table inside one DuckDB file per dataset version, stored in S3 (`orgs/{org}/datasets/{ds}/v{n}.duckdb`) and cached by workers locally by ETag. Queries open the file `read_only=True` with `enable_external_access=false`, 2 threads, 512 MB memory and a 15 s interrupt.

## Consequences
- Same validator/executor/introspection flow as Postgres (dialect `duckdb`).
- No shared database server for customer files; isolation is at file level.
- Large sheets (1M rows) are capped; bigger data belongs in a real database.
