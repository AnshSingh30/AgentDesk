# ADR 0004: AST-level SQL validation with sqlglot before every execution

Status: accepted · Date: 2026-09-16

## Context
The model writes SQL against customer databases. Read-only DB users are recommended but cannot be assumed; prompt injection through retrieved data is possible.

## Decision
`validate_sql()` is mandatory and runs on our side: parse with sqlglot in the dataset's dialect, require exactly one statement whose root is a SELECT/set operation, reject every DML/DDL/command node type anywhere in the tree (including CTEs), deny dangerous functions and file/URL table functions, block system catalogs, allow only exposed tables, reject hidden columns (also after `SELECT *` expansion via the optimizer's `qualify`), and inject/cap `LIMIT`. Execution additionally runs in `BEGIN READ ONLY` with statement timeouts. A 100-case suite covers malicious and edge inputs.

## Consequences
- Some legitimate but exotic SQL (table functions, cross-database refs) is rejected; the agent retries with feedback.
- Validation is dialect-aware and the same for Postgres and DuckDB.
