"""Read-only execution against customer Postgres (asyncpg) and per-dataset DuckDB files (PRD §11.1, §11.4)."""

from __future__ import annotations

import asyncio
import datetime as dt
import decimal
import json
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import asyncpg
import structlog

from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.core.s3 import download_to_path, head_object
from app.core.ssrf import resolve_public_host
from app.db.models import Dataset

log = structlog.get_logger()
MAX_COLUMNS = 50
MAX_CELL = 200
MAX_BYTES = 64 * 1024
STATEMENT_TIMEOUT_S = 15
_pools: dict[str, asyncpg.Pool] = {}
_pool_lock = asyncio.Lock()
DUCK_CACHE_DIR = os.path.join(tempfile.gettempdir(), "agentdesk-duckdb")


@dataclass
class SqlResult:
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    duration_ms: int
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "duration_ms": self.duration_ms,
            "warnings": self.warnings,
        }


class SqlExecutionError(Exception):
    pass


def _cell(v: Any) -> Any:
    if v is None or isinstance(v, bool | int | float):
        return v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, dt.datetime | dt.date | dt.time):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return str(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, bytes | bytearray | memoryview):
        return "<binary>"
    if isinstance(v, dict | list):
        s = json.dumps(v, default=str)
        return s if len(s) <= MAX_CELL else s[:MAX_CELL] + "…"
    s = str(v)
    return s if len(s) <= MAX_CELL else s[:MAX_CELL] + "…"


def _cap(columns: list[str], rows: list[Any], max_rows: int) -> SqlResult:
    warnings: list[str] = []
    if len(columns) > MAX_COLUMNS:
        warnings.append(f"Only the first {MAX_COLUMNS} of {len(columns)} columns are shown")
        columns = columns[:MAX_COLUMNS]
    out: list[list[Any]] = []
    size = 0
    truncated = False
    for r in rows:
        if len(out) >= max_rows:
            truncated = True
            break
        vals = [_cell(v) for v in list(r)[:MAX_COLUMNS]]
        size += len(json.dumps(vals, default=str))
        if size > MAX_BYTES:
            truncated = True
            warnings.append("Result was cut at 64 KB")
            break
        out.append(vals)
    return SqlResult(
        columns=columns,
        rows=out,
        row_count=len(out),
        truncated=truncated or len(rows) > len(out),
        duration_ms=0,
        warnings=warnings,
    )


# ---------------------------------------------------------------- Postgres
def _pg_kwargs(cfg: dict[str, Any], password: str, *, allow_private: bool | None = None) -> dict[str, Any]:
    host = str(cfg.get("host", ""))
    ips = resolve_public_host(host, allow_private)
    sslmode = cfg.get("sslmode") or "require"
    return {
        "host": ips[0] if sslmode in ("disable", "allow", "prefer", "require") else host,
        "port": int(cfg.get("port", 5432)),
        "database": str(cfg.get("database", "")),
        "user": str(cfg.get("username", "")),
        "password": password,
        "ssl": sslmode if sslmode != "disable" else False,
        "timeout": 10,
        "command_timeout": STATEMENT_TIMEOUT_S + 5,
        "server_settings": {
            "application_name": "agentdesk",
            "statement_timeout": f"{STATEMENT_TIMEOUT_S * 1000}",
        },
    }


async def pg_connect(cfg: dict[str, Any], password: str) -> asyncpg.Connection:
    try:
        return await asyncpg.connect(**_pg_kwargs(cfg, password))
    except (TimeoutError, OSError, asyncpg.PostgresError) as e:
        raise SqlExecutionError(f"Could not connect: {type(e).__name__}: {str(e)[:200]}") from e


async def get_pool(ds: Dataset) -> asyncpg.Pool:
    key = str(ds.id)
    async with _pool_lock:
        pool = _pools.get(key)
        if pool is None:
            password = decrypt_secret(ds.encrypted_password) if ds.encrypted_password else ""
            pool = await asyncpg.create_pool(
                min_size=0,
                max_size=3,
                max_inactive_connection_lifetime=60,
                **_pg_kwargs(ds.connection_config, password),
            )
            _pools[key] = pool
        return pool


async def close_pool(dataset_id: str) -> None:
    async with _pool_lock:
        pool = _pools.pop(dataset_id, None)
    if pool is not None:
        await pool.close()


async def execute_postgres(ds: Dataset, sql: str, max_rows: int = 200) -> SqlResult:
    pool = await get_pool(ds)
    started = time.perf_counter()
    try:
        async with pool.acquire() as conn:
            await conn.execute("BEGIN READ ONLY")
            try:
                await conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_S}s'")
                await conn.execute("SET LOCAL idle_in_transaction_session_timeout = '20s'")
                rows = await conn.fetch(sql)
            finally:
                await conn.execute("ROLLBACK")
    except asyncpg.PostgresError as e:
        raise SqlExecutionError(f"{type(e).__name__}: {str(e)[:400]}") from e
    except (TimeoutError, OSError) as e:
        raise SqlExecutionError(f"Connection error: {str(e)[:200]}") from e
    columns = list(rows[0].keys()) if rows else []
    result = _cap(columns, rows, max_rows)
    result.duration_ms = int((time.perf_counter() - started) * 1000)
    return result


async def pg_test_connection(cfg: dict[str, Any], password: str) -> dict[str, Any]:
    conn = await pg_connect(cfg, password)
    try:
        await conn.fetchval("SELECT 1")
        version = await conn.fetchval("SHOW server_version")
        superuser = await conn.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        privs = await conn.fetch(
            "SELECT DISTINCT privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = current_user AND privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE')"
        )
        can_create = await conn.fetchval(
            "SELECT has_database_privilege(current_user, current_database(), 'CREATE')"
        )
        schemas = await conn.fetch(
            "SELECT nspname FROM pg_namespace WHERE nspname NOT IN ('pg_catalog','information_schema','pg_toast') "
            "AND nspname NOT LIKE 'pg_temp%' ORDER BY 1"
        )
        write_privs = sorted({r["privilege_type"] for r in privs}) + (["CREATE"] if can_create else [])
        warnings = []
        if superuser or write_privs:
            warnings.append(
                "This user can modify data. We will only run read-only queries, but we strongly recommend a read-only user."
            )
        return {
            "ok": True,
            "server_version": version,
            "superuser": bool(superuser),
            "write_privileges": write_privs,
            "schemas": [r["nspname"] for r in schemas],
            "warnings": warnings,
        }
    finally:
        await conn.close()


# ---------------------------------------------------------------- DuckDB
async def duckdb_local_path(ds: Dataset) -> str:
    """Workers cache DuckDB files locally by etag (PRD §11.6)."""
    if not ds.storage_key:
        raise SqlExecutionError("Spreadsheet dataset has no data file yet")
    os.makedirs(DUCK_CACHE_DIR, exist_ok=True)
    head = await head_object(ds.storage_key)
    if head is None:
        raise SqlExecutionError("Spreadsheet data file is missing from storage")
    etag = (head.get("ETag") or "").strip('"') or ds.storage_etag or "0"
    path = os.path.join(DUCK_CACHE_DIR, f"{ds.id}-{etag}.duckdb")
    if not os.path.exists(path):
        tmp = path + ".part"
        await download_to_path(ds.storage_key, tmp)
        os.replace(tmp, path)
    return path


def _duckdb_run(path: str, sql: str, max_rows: int, timeout: float) -> tuple[list[str], list[Any]]:
    import duckdb

    con = duckdb.connect(
        path,
        read_only=True,
        config={"enable_external_access": "false", "threads": 2, "memory_limit": "512MB"},
    )
    try:
        con.execute("SET lock_configuration = true")
    except duckdb.Error:
        pass
    timer = threading.Timer(timeout, con.interrupt)
    timer.start()
    try:
        cur = con.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(max_rows + 1)
        return columns, rows
    finally:
        timer.cancel()
        con.close()


async def execute_duckdb(ds: Dataset, sql: str, max_rows: int = 200) -> SqlResult:
    import duckdb

    path = await duckdb_local_path(ds)
    started = time.perf_counter()
    try:
        columns, rows = await asyncio.to_thread(_duckdb_run, path, sql, max_rows, STATEMENT_TIMEOUT_S)
    except duckdb.InterruptException as e:
        raise SqlExecutionError("Query timed out after 15 s") from e
    except duckdb.Error as e:
        raise SqlExecutionError(f"{type(e).__name__}: {str(e)[:400]}") from e
    result = _cap(columns, rows, max_rows)
    result.duration_ms = int((time.perf_counter() - started) * 1000)
    return result


async def execute(ds: Dataset, sql: str, max_rows: int = 200) -> SqlResult:
    if ds.type == "postgres":
        return await execute_postgres(ds, sql, max_rows)
    return await execute_duckdb(ds, sql, max_rows)


def dialect_for(ds: Dataset) -> str:
    return "postgres" if ds.type == "postgres" else "duckdb"


__all__ = [
    "SqlExecutionError",
    "SqlResult",
    "close_pool",
    "dialect_for",
    "duckdb_local_path",
    "execute",
    "execute_duckdb",
    "execute_postgres",
    "pg_connect",
    "pg_test_connection",
    "settings",
]
