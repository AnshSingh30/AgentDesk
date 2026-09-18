"""SQL safety layer - every generated query passes here before execution (PRD §11.4). Non-negotiable."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError, SqlglotError
from sqlglot.optimizer.qualify import qualify

DEFAULT_SCHEMAS = {"public", "main", ""}
SYSTEM_SCHEMAS = {"information_schema", "pg_catalog", "pg_toast", "duckdb_", "system"}
ALLOWED_TABLE_FUNCS = {
    "unnest",
    "generate_series",
    "generate_subscripts",
    "jsonb_array_elements",
    "json_array_elements",
    "jsonb_each",
    "json_each",
    "regexp_split_to_table",
    "string_to_table",
    "range",
    "jsonb_array_elements_text",
    "json_array_elements_text",
}
DENY_FUNC_PREFIXES = (
    "pg_sleep",
    "pg_read_",
    "pg_ls_",
    "pg_stat_file",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "pg_reload_conf",
    "pg_rotate_logfile",
    "pg_notify",
    "pg_advisory",
    "pg_try_advisory",
    "pg_execute_server_program",
    "pg_file_",
    "pg_logdir_ls",
    "pg_current_logfile",
    "pg_backend_pid",
    "lo_",
    "dblink",
    "set_config",
    "current_setting",
    "query_to_xml",
    "database_to_xml",
    "schema_to_xml",
    "table_to_xml",
    "cursor_to_xml",
    "xpath",
    "txid_",
    "read_csv",
    "read_parquet",
    "read_json",
    "read_ndjson",
    "read_text",
    "read_blob",
    "read_xlsx",
    "read_",
    "glob",
    "sniff_csv",
    "parquet_scan",
    "csv_scan",
    "json_scan",
    "http_get",
    "http_post",
    "st_read",
    "iceberg_",
    "delta_scan",
    "sqlite_scan",
    "postgres_scan",
    "mysql_scan",
    "getenv",
    "system",
    "shellfs",
    "current_query",
    "pg_conf_load_time",
    "brin_summarize",
    "gin_clean",
    "pg_import_system_collations",
    "pg_ls_dir",
    "pg_stat_reset",
    "copy_",
)
BLOCKED_STATEMENT_NAMES = (
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Create",
    "Alter",
    "AlterTable",
    "Drop",
    "TruncateTable",
    "Truncate",
    "Grant",
    "Revoke",
    "Command",
    "Set",
    "Copy",
    "Describe",
    "Transaction",
    "Commit",
    "Rollback",
    "Lock",
    "Into",
    "Use",
    "Cache",
    "Uncache",
    "Comment",
    "Kill",
    "Execute",
    "Prepare",
    "Attach",
    "Detach",
    "Pragma",
    "Show",
    "Analyze",
    "Refresh",
    "Call",
    "Load",
    "Install",
    "Export",
    "Vacuum",
    "Placeholder",
    "Parameter",
    "Declare",
    "Fetch",
)
BLOCKED_TYPES: tuple[type[exp.Expression], ...] = tuple(
    t for n in BLOCKED_STATEMENT_NAMES if (t := getattr(exp, n, None)) is not None and isinstance(t, type)
)


@dataclass
class ExposedTable:
    name: str
    columns: dict[str, str]  # column -> type
    hidden: set[str] = field(default_factory=set)
    schema: str = "public"

    @property
    def key(self) -> str:
        return (
            self.name.lower()
            if self.schema.lower() in DEFAULT_SCHEMAS
            else f"{self.schema.lower()}.{self.name.lower()}"
        )

    @property
    def visible_columns(self) -> dict[str, str]:
        return {c: t for c, t in self.columns.items() if c not in self.hidden}


@dataclass
class ValidationResult:
    ok: bool
    sql: str | None = None
    error: str | None = None
    tables: list[str] = field(default_factory=list)
    limited: bool = False


class SqlRejected(ValueError):
    pass


def _func_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        name = str(node.name).lower()
    else:
        try:
            name = node.sql_name().lower()
        except Exception:  # noqa: BLE001
            name = type(node).__name__.lower()
    return name.removeprefix("exploding_")  # postgres/duckdb generate_series variant


def _safe_type(dtype: str, dialect: str) -> str:
    try:
        exp.DataType.build(dtype, dialect=dialect)
        return dtype
    except SqlglotError:
        return "TEXT"


def validate_sql(
    sql: str, tables: list[ExposedTable], *, dialect: str = "postgres", max_rows: int = 200
) -> ValidationResult:
    """Parse, enforce read-only SELECT, denylists, allowlists, hidden columns, star expansion and LIMIT."""
    try:
        return ValidationResult(ok=True, **_validate(sql, tables, dialect, max_rows))
    except SqlRejected as e:
        return ValidationResult(ok=False, error=str(e))


def _validate(sql: str, tables: list[ExposedTable], dialect: str, max_rows: int) -> dict:
    if not sql or not sql.strip():
        raise SqlRejected("Empty query")
    if len(sql) > 20_000:
        raise SqlRejected("Query is too long")
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except ParseError as e:
        raise SqlRejected(f"SQL could not be parsed: {str(e)[:200]}") from e
    except SqlglotError as e:
        raise SqlRejected(f"SQL could not be parsed: {str(e)[:200]}") from e
    if len(statements) != 1:
        raise SqlRejected("Exactly one statement is allowed")
    expr = statements[0]
    if not isinstance(expr, exp.Select | exp.SetOperation | exp.Subquery):
        raise SqlRejected(f"Only SELECT queries are allowed (got {type(expr).__name__})")
    for node in expr.walk():
        if isinstance(node, BLOCKED_TYPES):
            raise SqlRejected(
                f"Statement type {type(node).__name__.upper()} is not allowed; only read-only SELECT queries can run"
            )
        if isinstance(node, exp.Func):
            name = _func_name(node)
            if name.startswith(DENY_FUNC_PREFIXES) or name.endswith("_scan") or name in ("system", "glob"):
                raise SqlRejected(f"Function {name}() is not allowed")
        if isinstance(node, exp.Table):
            _check_table(node, expr, tables)
    hidden_names = {c.lower() for t in tables for c in t.hidden}
    for col in expr.find_all(exp.Column):
        if col.name.lower() in hidden_names:
            raise SqlRejected(f"Column '{col.name}' is hidden and cannot be used in queries")
    for table in expr.find_all(exp.Table):  # strip default schema so the mapping schema resolves
        if table.db and table.db.lower() in DEFAULT_SCHEMAS:
            table.set("db", None)
    schema: dict[str, Any] = {
        t.key: {c: _safe_type(ty, dialect) for c, ty in t.visible_columns.items()} for t in tables
    }
    try:
        expr = qualify(expr, schema=schema, dialect=dialect, validate_qualify_columns=True)
    except OptimizeError as e:
        raise SqlRejected(f"Query references unknown or unavailable columns: {str(e)[:200]}") from e
    except SqlglotError as e:
        raise SqlRejected(f"Query could not be resolved: {str(e)[:200]}") from e
    for col in expr.find_all(exp.Column):  # after star expansion, re-check hidden references
        if col.name.lower() in hidden_names:
            raise SqlRejected(f"Column '{col.name}' is hidden and cannot be used in queries")
    expr, limited = _inject_limit(expr, max_rows)
    used = sorted({t.name.lower() for t in expr.find_all(exp.Table) if isinstance(t.this, exp.Identifier)})
    cte_names = {c.alias_or_name.lower() for c in expr.find_all(exp.CTE)}
    return {
        "sql": expr.sql(dialect=dialect),
        "tables": [t for t in used if t not in cte_names],
        "limited": limited,
    }


def _check_table(node: exp.Table, root: exp.Expression, tables: list[ExposedTable]) -> None:
    allowed = {t.key for t in tables}
    if not isinstance(node.this, exp.Identifier):
        fname = _func_name(node.this) if isinstance(node.this, exp.Func) else type(node.this).__name__.lower()
        if isinstance(node.this, exp.Func) and fname in ALLOWED_TABLE_FUNCS:
            return
        raise SqlRejected("Table functions, file paths and URLs are not allowed in FROM")
    name = node.name.lower()
    db = (node.db or "").lower()
    if node.catalog:
        raise SqlRejected("Cross-database references are not allowed")
    cte_names = {c.alias_or_name.lower() for c in root.find_all(exp.CTE)}
    if name in cte_names and not db:
        return
    if db in SYSTEM_SCHEMAS or db.startswith("pg_") or name.startswith(("pg_", "duckdb_", "sqlite_")):
        raise SqlRejected("System catalogs are not accessible")
    key = name if db in DEFAULT_SCHEMAS else f"{db}.{name}"
    if key not in allowed:
        raise SqlRejected(f"Table '{node.name}' is not available to this agent")


def _inject_limit(expr: exp.Expression, max_rows: int) -> tuple[exp.Expression, bool]:
    if isinstance(expr, exp.Select):
        limit = expr.args.get("limit")
        if limit is not None:
            try:
                current = int(limit.expression.this)
                if current <= max_rows:
                    return expr, False
            except (AttributeError, TypeError, ValueError):
                pass
        return expr.limit(max_rows), True
    wrapped = exp.select("*").from_(cast(exp.Query, expr).subquery("q")).limit(max_rows)
    return wrapped, True
