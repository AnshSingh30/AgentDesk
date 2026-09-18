"""Mandatory SQL validator suite (PRD §20.2): >= 60 malicious/edge cases. 100% branch coverage target."""

from __future__ import annotations

import pytest
from app.sql.validator import ExposedTable, validate_sql

TABLES = [
    ExposedTable(
        "employees",
        {
            "employee_id": "integer",
            "full_name": "text",
            "department_id": "integer",
            "hire_date": "date",
            "exit_date": "date",
            "status": "text",
            "date_of_birth": "date",
            "gender": "text",
        },
        hidden={"date_of_birth"},
    ),
    ExposedTable("departments", {"department_id": "integer", "name": "text", "cost_center": "text"}),
    ExposedTable(
        "compensation",
        {"employee_id": "integer", "effective_date": "date", "base_salary": "numeric", "currency": "text"},
        hidden={"base_salary"},
    ),
    ExposedTable(
        "leave_requests",
        {
            "leave_id": "integer",
            "employee_id": "integer",
            "leave_type": "text",
            "days": "numeric",
            "status": "text",
        },
    ),
    ExposedTable(
        "events",
        {"id": "integer", "title": "text", "starts_at": "timestamp with time zone"},
        schema="analytics",
    ),
]

REJECTED = [
    # multi-statement & comment tricks
    ("SELECT 1; DROP TABLE employees", "postgres", "one statement"),
    ("SELECT 1;\n-- innocent\nDROP TABLE employees", "postgres", "one statement"),
    ("SELECT 1 /* */; DELETE FROM employees", "postgres", "one statement"),
    ("SELECT * FROM employees WHERE status = 'x'; DROP TABLE employees; --", "postgres", "one statement"),
    # DML/DDL incl. inside CTEs
    ("WITH d AS (DELETE FROM employees RETURNING *) SELECT * FROM d", "postgres", "not allowed"),
    ("WITH u AS (UPDATE employees SET status='x' RETURNING *) SELECT * FROM u", "postgres", "not allowed"),
    (
        "WITH i AS (INSERT INTO employees(employee_id) VALUES (1) RETURNING *) SELECT * FROM i",
        "postgres",
        "not allowed",
    ),
    ("DELETE FROM employees", "postgres", "Only SELECT"),
    ("UPDATE employees SET status='x'", "postgres", "Only SELECT"),
    ("INSERT INTO employees VALUES (1)", "postgres", "Only SELECT"),
    ("CREATE TABLE x AS SELECT 1", "postgres", "Only SELECT"),
    ("ALTER TABLE employees ADD COLUMN x int", "postgres", "Only SELECT"),
    ("DROP TABLE employees", "postgres", "Only SELECT"),
    ("TRUNCATE employees", "postgres", "Only SELECT"),
    ("GRANT SELECT ON employees TO public", "postgres", "Only SELECT"),
    (
        "MERGE INTO employees e USING departments d ON e.department_id = d.department_id WHEN MATCHED THEN DELETE",
        "postgres",
        "Only SELECT",
    ),
    # SELECT variants that write or lock
    ("SELECT * INTO new_table FROM employees", "postgres", "not allowed"),
    ("SELECT employee_id INTO TEMP t FROM employees", "postgres", "not allowed"),
    ("SELECT * FROM employees FOR UPDATE", "postgres", "not allowed"),
    ("SELECT * FROM employees FOR SHARE", "postgres", "not allowed"),
    # session / server commands
    ("SET ROLE admin", "postgres", "Only SELECT"),
    ("SET search_path = public", "postgres", "Only SELECT"),
    ("EXPLAIN ANALYZE SELECT 1", "postgres", "Only SELECT"),
    ("VACUUM employees", "postgres", "Only SELECT"),
    ("COPY employees TO PROGRAM 'ls'", "postgres", "Only SELECT"),
    ("COPY (SELECT 1) TO '/tmp/x'", "postgres", "Only SELECT"),
    # dangerous functions
    ("SELECT pg_sleep(10)", "postgres", "pg_sleep"),
    ("SELECT pg_sleep_for('1 minute')", "postgres", "pg_sleep_for"),
    ("SELECT * FROM dblink('dbname=x', 'select 1') AS t(a int)", "postgres", "not allowed"),
    ("SELECT current_setting('is_superuser')", "postgres", "current_setting"),
    ("SELECT set_config('x', 'y', false)", "postgres", "set_config"),
    ("SELECT query_to_xml('select 1', true, false, '')", "postgres", "query_to_xml"),
    ("SELECT lo_import('/etc/passwd')", "postgres", "lo_import"),
    ("SELECT pg_read_file('/etc/passwd')", "postgres", "pg_read_file"),
    ("SELECT pg_terminate_backend(1)", "postgres", "pg_terminate_backend"),
    ("SELECT xpath('/a', '<a/>'::xml)", "postgres", "xpath"),
    ("SELECT * FROM employees e, LATERAL (SELECT pg_sleep(1)) s", "postgres", "pg_sleep"),
    # catalogs
    ("SELECT * FROM pg_catalog.pg_tables", "postgres", "System catalogs"),
    ("SELECT * FROM information_schema.tables", "postgres", "System catalogs"),
    ("SELECT * FROM pg_shadow", "postgres", "System catalogs"),
    ("SELECT * FROM employees CROSS JOIN pg_stat_activity", "postgres", "System catalogs"),
    # table allowlist
    ("SELECT * FROM employees, secrets", "postgres", "not available"),
    ("SELECT s.* FROM secrets s", "postgres", "not available"),
    ("SELECT * FROM employees UNION SELECT * FROM secrets", "postgres", "not available"),
    (
        "SELECT * FROM employees WHERE employee_id IN (SELECT employee_id FROM secrets)",
        "postgres",
        "not available",
    ),
    (
        "SELECT * FROM employees e LEFT JOIN LATERAL (SELECT * FROM secrets) s ON true",
        "postgres",
        "not available",
    ),
    ("SELECT * FROM otherschema.employees", "postgres", "not available"),
    ("SELECT * FROM employees", "postgres", None),  # placeholder replaced below (unicode case)
    # hidden columns
    ("SELECT base_salary FROM compensation", "postgres", "hidden"),
    ("SELECT c.base_salary FROM compensation c", "postgres", "hidden"),
    ("SELECT e.date_of_birth FROM employees e", "postgres", "hidden"),
    (
        "SELECT * FROM employees e JOIN compensation c ON c.employee_id = e.employee_id WHERE c.base_salary > 100",
        "postgres",
        "hidden",
    ),
    ("SELECT employee_id FROM (SELECT employee_id, base_salary FROM compensation) sub", "postgres", "hidden"),
    ('SELECT "base_salary" FROM compensation', "postgres", "hidden"),
    ("SELECT (SELECT base_salary FROM compensation LIMIT 1)", "postgres", "hidden"),
    ("SELECT count(*) FROM compensation WHERE base_salary > 0", "postgres", "hidden"),
    ('SELECT * FROM compensation c WHERE 1=1 AND c."base_salary" IS NOT NULL', "postgres", "hidden"),
    # unknown columns / parse errors / params
    ("SELECT nonexistent_col FROM employees", "postgres", "unknown"),
    ("SELEC * FROM employees", "postgres", "parsed"),
    ("", "postgres", "Empty"),
    ("SELECT $1", "postgres", "not allowed"),
    # DuckDB file/URL access
    ("SELECT * FROM read_csv('/etc/passwd')", "duckdb", "not allowed"),
    ("SELECT * FROM read_parquet('s3://bucket/x.parquet')", "duckdb", "not allowed"),
    ("SELECT * FROM '/etc/passwd'", "duckdb", "not available"),
    ("SELECT * FROM glob('*')", "duckdb", "not allowed"),
    ("INSTALL httpfs", "duckdb", "Only SELECT"),
    ("ATTACH 'other.db' AS o", "duckdb", "Only SELECT"),
    ("PRAGMA database_list", "duckdb", "Only SELECT"),
    ("SELECT * FROM duckdb_settings()", "duckdb", "not allowed"),
]
REJECTED[REJECTED.index(("SELECT * FROM employees", "postgres", None))] = (
    "SELECT * FROM emplоyees",
    "postgres",
    "not available",
)

ACCEPTED = [
    ("SELECT * FROM employees", "postgres"),
    ("SELECT * FROM employees LIMIT 100000", "postgres"),
    ("SELECT count(*) FROM employees", "postgres"),
    ("SELECT department_id, count(*) FROM employees GROUP BY 1 ORDER BY 2 DESC LIMIT 5", "postgres"),
    (
        "WITH active AS (SELECT * FROM employees WHERE status = 'active') SELECT d.name, count(*) FROM active a "
        "JOIN departments d ON d.department_id = a.department_id GROUP BY d.name",
        "postgres",
    ),
    ("SELECT * FROM employees e WHERE e.hire_date > now() - interval '1 year'", "postgres"),
    ("SELECT e.full_name FROM employees AS e", "postgres"),
    ("SELECT employee_id FROM employees UNION ALL SELECT employee_id FROM compensation", "postgres"),
    ("SELECT * FROM generate_series(1, 10)", "postgres"),
    ("SELECT * FROM unnest(ARRAY[1, 2]) AS t(n)", "postgres"),
    ("select * from Employees", "postgres"),
    ("SELECT * FROM employees;", "postgres"),
    ("SELECT * FROM employees -- trailing comment", "postgres"),
    ("SELECT e.*, d.name FROM employees e JOIN departments d USING (department_id)", "postgres"),
    ("SELECT * FROM public.employees", "postgres"),
    ("SELECT * FROM compensation", "postgres"),
    ("SELECT * FROM (SELECT * FROM compensation) x", "postgres"),
    (
        "SELECT * FROM employees JOIN departments ON employees.department_id = departments.department_id",
        "postgres",
    ),
    ("SELECT DISTINCT status FROM employees ORDER BY status", "postgres"),
    ("select * from employees where full_name ilike '%a%'", "postgres"),
    ("SELECT * FROM leave_requests WHERE status = 'approved'", "postgres"),
    ("SELECT * FROM analytics.events", "postgres"),
    ("SELECT * FROM employees LIMIT 5", "duckdb"),
    ("SELECT leave_type, sum(days) AS total FROM leave_requests GROUP BY leave_type", "duckdb"),
    ("SELECT date_trunc('month', hire_date) AS m, count(*) FROM employees GROUP BY 1 ORDER BY 1", "postgres"),
]


@pytest.mark.parametrize("sql,dialect,needle", REJECTED)
def test_rejected(sql: str, dialect: str, needle: str) -> None:
    result = validate_sql(sql, TABLES, dialect=dialect, max_rows=200)
    assert not result.ok, f"should reject: {sql}"
    assert needle.lower() in (result.error or "").lower(), f"{sql}: {result.error}"


@pytest.mark.parametrize("sql,dialect", ACCEPTED)
def test_accepted(sql: str, dialect: str) -> None:
    result = validate_sql(sql, TABLES, dialect=dialect, max_rows=200)
    assert result.ok, f"{sql}: {result.error}"
    assert result.sql
    assert "LIMIT" in result.sql.upper()
    assert "base_salary" not in result.sql.lower()
    assert "date_of_birth" not in result.sql.lower()


def test_limit_is_capped() -> None:
    r = validate_sql("SELECT * FROM employees LIMIT 100000", TABLES, max_rows=200)
    assert r.ok and r.limited and "LIMIT 200" in r.sql


def test_limit_within_cap_is_kept() -> None:
    r = validate_sql("SELECT * FROM employees LIMIT 5", TABLES, max_rows=200)
    assert r.ok and not r.limited and "LIMIT 5" in r.sql


def test_union_is_wrapped_with_limit() -> None:
    r = validate_sql(
        "SELECT employee_id FROM employees UNION ALL SELECT employee_id FROM compensation",
        TABLES,
        max_rows=50,
    )
    assert r.ok and r.limited and r.sql.upper().endswith("LIMIT 50")


def test_star_expansion_excludes_hidden() -> None:
    r = validate_sql("SELECT * FROM compensation", TABLES)
    assert r.ok
    assert "currency" in r.sql and "base_salary" not in r.sql


def test_tables_reported() -> None:
    r = validate_sql(
        "WITH a AS (SELECT * FROM employees) SELECT * FROM a JOIN departments d USING (department_id)", TABLES
    )
    assert r.ok and r.tables == ["departments", "employees"]


def test_case_count() -> None:
    assert len(REJECTED) + len(ACCEPTED) >= 60
