"""Schema introspection for Postgres and DuckDB, diffing and persistence with description preservation (PRD §11.2, DS-11)."""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret
from app.db.models import Dataset, DatasetColumn, DatasetRelationship, DatasetSchemaVersion, DatasetTable
from app.sql.executor import duckdb_local_path, pg_connect


def quote_ident(name: str) -> str:
    """Quote an identifier read from a customer catalog (embedded quotes are doubled)."""
    return '"' + name.replace('"', '""') + '"'


log = structlog.get_logger()
SENSITIVE_NAME = re.compile(
    r"(password|passwd|token|secret|ssn|aadhaar|aadhar|pan_no|pan_number|\bpan\b|salary|dob|date_of_birth|birth|phone|mobile|email|iban|account_no|card)",
    re.I,
)
SAMPLE_TYPES = (
    "text",
    "character varying",
    "varchar",
    "character",
    "char",
    "user-defined",
    "boolean",
    "bool",
    "name",
    "citext",
)
PG_SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")


def is_sensitive_name(name: str) -> bool:
    return bool(SENSITIVE_NAME.search(name))


# ---------------------------------------------------------------- Postgres
async def introspect_postgres(
    cfg: dict[str, Any], password: str, schemas: list[str] | None, allow_sampling: bool
) -> dict[str, Any]:
    conn = await pg_connect(cfg, password)
    try:
        schemas = schemas or [
            r["nspname"]
            for r in await conn.fetch(
                "SELECT nspname FROM pg_namespace WHERE nspname NOT IN ('pg_catalog','information_schema','pg_toast') AND nspname NOT LIKE 'pg_temp%'"
            )
        ]
        tables = await conn.fetch(
            """SELECT n.nspname AS schema, c.relname AS name,
                      CASE WHEN c.relkind IN ('v','m') THEN 'view' ELSE 'table' END AS kind,
                      GREATEST(c.reltuples::bigint, 0) AS row_estimate, obj_description(c.oid, 'pg_class') AS comment
               FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE c.relkind IN ('r','p','v','m') AND n.nspname = ANY($1::text[]) ORDER BY 1, 2""",
            schemas,
        )
        columns = await conn.fetch(
            """SELECT c.table_schema, c.table_name, c.column_name, c.data_type, c.udt_name, c.is_nullable = 'YES' AS nullable,
                      c.column_default, c.ordinal_position,
                      col_description(format('%I.%I', c.table_schema, c.table_name)::regclass::oid, c.ordinal_position) AS comment
               FROM information_schema.columns c WHERE c.table_schema = ANY($1::text[]) ORDER BY 1, 2, 8""",
            schemas,
        )
        pks = await conn.fetch(
            """SELECT n.nspname AS schema, cl.relname AS table, a.attname AS column
               FROM pg_constraint con JOIN pg_class cl ON cl.oid = con.conrelid JOIN pg_namespace n ON n.oid = cl.relnamespace
               JOIN unnest(con.conkey) WITH ORDINALITY k(attnum, ord) ON true
               JOIN pg_attribute a ON a.attrelid = cl.oid AND a.attnum = k.attnum
               WHERE con.contype = 'p' AND n.nspname = ANY($1::text[]) ORDER BY 1, 2, ord""",
            schemas,
        )
        fks = await conn.fetch(
            """SELECT n.nspname AS schema, cl.relname AS table, a.attname AS column, fn.nspname AS ref_schema,
                      fcl.relname AS ref_table, fa.attname AS ref_column
               FROM pg_constraint con JOIN pg_class cl ON cl.oid = con.conrelid JOIN pg_namespace n ON n.oid = cl.relnamespace
               JOIN pg_class fcl ON fcl.oid = con.confrelid JOIN pg_namespace fn ON fn.oid = fcl.relnamespace
               JOIN unnest(con.conkey, con.confkey) WITH ORDINALITY k(attnum, fattnum, ord) ON true
               JOIN pg_attribute a ON a.attrelid = cl.oid AND a.attnum = k.attnum
               JOIN pg_attribute fa ON fa.attrelid = fcl.oid AND fa.attnum = k.fattnum
               WHERE con.contype = 'f' AND n.nspname = ANY($1::text[])""",
            schemas,
        )
        enums = await conn.fetch(
            "SELECT t.typname, e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid ORDER BY t.typname, e.enumsortorder"
        )
        indexes = await conn.fetch(
            "SELECT schemaname, tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = ANY($1::text[])",
            schemas,
        )
        stats = await conn.fetch(
            "SELECT schemaname, tablename, attname, n_distinct FROM pg_stats WHERE schemaname = ANY($1::text[])",
            schemas,
        )
        enum_values: dict[str, list[str]] = {}
        for r in enums:
            enum_values.setdefault(r["typname"], []).append(r["enumlabel"])
        n_distinct = {
            (r["schemaname"], r["tablename"], r["attname"]): float(r["n_distinct"] or 0) for r in stats
        }
        pk_set = {(r["schema"], r["table"], r["column"]) for r in pks}
        snapshot: dict[str, Any] = {
            "dialect": "postgres",
            "schemas": schemas,
            "tables": [],
            "captured_at": datetime.now(UTC).isoformat(),
        }
        by_table: dict[tuple[str, str], dict[str, Any]] = {}
        for t in tables:
            entry = {
                "schema": t["schema"],
                "name": t["name"],
                "kind": t["kind"],
                "row_estimate": int(t["row_estimate"] or 0),
                "comment": t["comment"],
                "columns": [],
                "foreign_keys": [],
                "indexes": [],
            }
            by_table[(t["schema"], t["name"])] = entry
            snapshot["tables"].append(entry)
        for c in columns:
            entry = by_table.get((c["table_schema"], c["table_name"]))
            if entry is None:
                continue
            dtype = c["udt_name"] if c["data_type"] == "USER-DEFINED" else c["data_type"]
            col: dict[str, Any] = {
                "name": c["column_name"],
                "type": dtype,
                "nullable": bool(c["nullable"]),
                "default": c["column_default"],
                "ordinal": int(c["ordinal_position"]),
                "comment": c["comment"],
                "is_pk": (c["table_schema"], c["table_name"], c["column_name"]) in pk_set,
                "samples": None,
                "sample_note": None,
                "enum_values": enum_values.get(c["udt_name"]),
            }
            if is_sensitive_name(c["column_name"]):
                col["sample_note"] = "sampling skipped (sensitive name)"
            elif not allow_sampling:
                col["sample_note"] = "sampling disabled"
            elif col["enum_values"]:
                col["samples"] = col["enum_values"][:5]
            elif (c["data_type"] or "").lower() in SAMPLE_TYPES or (c["data_type"] == "USER-DEFINED"):
                nd = n_distinct.get((c["table_schema"], c["table_name"], c["column_name"]))
                if nd is None or 0 < nd <= 50 or (nd < 0 and nd > -0.05):
                    col["samples"] = await _pg_sample(
                        conn, c["table_schema"], c["table_name"], c["column_name"]
                    )
                else:
                    col["sample_note"] = "sampling skipped (high cardinality)"
            entry["columns"].append(col)
        for f in fks:
            entry = by_table.get((f["schema"], f["table"]))
            if entry is not None:
                entry["foreign_keys"].append(
                    {
                        "column": f["column"],
                        "ref_schema": f["ref_schema"],
                        "ref_table": f["ref_table"],
                        "ref_column": f["ref_column"],
                    }
                )
        for i in indexes:
            entry = by_table.get((i["schemaname"], i["tablename"]))
            if entry is not None:
                entry["indexes"].append({"name": i["indexname"], "definition": i["indexdef"]})
        return snapshot
    finally:
        await conn.close()


async def _pg_sample(conn: Any, schema: str, table: str, column: str) -> list[Any] | None:
    c, t = quote_ident(column), f"{quote_ident(schema)}.{quote_ident(table)}"
    q = f"SELECT DISTINCT {c} FROM (SELECT {c} FROM {t} WHERE {c} IS NOT NULL LIMIT 2000) s ORDER BY 1 LIMIT 5"  # nosec B608 (quote_ident)
    try:
        await conn.execute("SET statement_timeout = '5s'")
        rows = await conn.fetch(q)
        return [str(r[0])[:80] for r in rows]
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- DuckDB
def _duckdb_snapshot(path: str, allow_sampling: bool) -> dict[str, Any]:
    import duckdb

    con = duckdb.connect(path, read_only=True)
    try:
        tables = con.execute(
            "SELECT table_name, estimated_size FROM duckdb_tables() WHERE NOT internal ORDER BY 1"
        ).fetchall()
        snapshot: dict[str, Any] = {
            "dialect": "duckdb",
            "schemas": ["main"],
            "tables": [],
            "captured_at": datetime.now(UTC).isoformat(),
        }
        for name, size in tables:
            cols = con.execute(
                "SELECT column_name, data_type, is_nullable = 'YES', ordinal_position FROM information_schema.columns "
                "WHERE table_name = ? ORDER BY ordinal_position",
                [name],
            ).fetchall()
            entry: dict[str, Any] = {
                "schema": "main",
                "name": name,
                "kind": "table",
                "row_estimate": int(size or 0),
                "comment": None,
                "columns": [],
                "foreign_keys": [],
                "indexes": [],
            }
            for cname, ctype, nullable, ordinal in cols:
                col: dict[str, Any] = {
                    "name": cname,
                    "type": ctype,
                    "nullable": bool(nullable),
                    "default": None,
                    "ordinal": int(ordinal),
                    "comment": None,
                    "is_pk": False,
                    "samples": None,
                    "sample_note": None,
                    "enum_values": None,
                }
                if is_sensitive_name(cname):
                    col["sample_note"] = "sampling skipped (sensitive name)"
                elif not allow_sampling:
                    col["sample_note"] = "sampling disabled"
                elif ctype.upper() in ("VARCHAR", "BOOLEAN", "TEXT"):
                    try:
                        c, t = quote_ident(cname), quote_ident(name)
                        rows = con.execute(
                            f"SELECT DISTINCT {c} FROM (SELECT {c} FROM {t} WHERE {c} IS NOT NULL LIMIT 2000) "  # nosec B608 (quote_ident)
                            "ORDER BY 1 LIMIT 5"
                        ).fetchall()
                        col["samples"] = [str(r[0])[:80] for r in rows]
                    except duckdb.Error:
                        col["samples"] = None
                entry["columns"].append(col)
            snapshot["tables"].append(entry)
        return snapshot
    finally:
        con.close()


async def introspect_duckdb(ds: Dataset, allow_sampling: bool) -> dict[str, Any]:
    path = await duckdb_local_path(ds)
    return await asyncio.to_thread(_duckdb_snapshot, path, allow_sampling)


# ---------------------------------------------------------------- diff & persistence
def diff_snapshots(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    def key(t: dict[str, Any]) -> str:
        return f"{t['schema']}.{t['name']}"

    old_tables = {key(t): t for t in (old or {}).get("tables", [])}
    new_tables = {key(t): t for t in new.get("tables", [])}
    diff: dict[str, Any] = {
        "added_tables": sorted(set(new_tables) - set(old_tables)),
        "removed_tables": sorted(set(old_tables) - set(new_tables)),
        "added_columns": [],
        "removed_columns": [],
        "changed_columns": [],
    }
    for k in set(old_tables) & set(new_tables):
        oc = {c["name"]: c for c in old_tables[k]["columns"]}
        nc = {c["name"]: c for c in new_tables[k]["columns"]}
        diff["added_columns"] += [f"{k}.{c}" for c in sorted(set(nc) - set(oc))]
        diff["removed_columns"] += [f"{k}.{c}" for c in sorted(set(oc) - set(nc))]
        for c in sorted(set(oc) & set(nc)):
            if oc[c]["type"] != nc[c]["type"] or oc[c]["nullable"] != nc[c]["nullable"]:
                diff["changed_columns"].append(
                    {"column": f"{k}.{c}", "from": oc[c]["type"], "to": nc[c]["type"]}
                )
    return diff


async def apply_snapshot(
    session: AsyncSession, ds: Dataset, snapshot: dict[str, Any]
) -> DatasetSchemaVersion:
    prev = (
        (
            await session.execute(
                select(DatasetSchemaVersion)
                .where(DatasetSchemaVersion.dataset_id == ds.id)
                .order_by(DatasetSchemaVersion.version.desc())
            )
        )
        .scalars()
        .first()
    )
    version = DatasetSchemaVersion(
        dataset_id=ds.id,
        version=(prev.version + 1) if prev else 1,
        raw_snapshot=snapshot,
        diff=diff_snapshots(prev.raw_snapshot if prev else None, snapshot),
    )
    session.add(version)
    existing_tables = {
        (t.schema_name, t.table_name): t
        for t in (await session.execute(select(DatasetTable).where(DatasetTable.dataset_id == ds.id)))
        .scalars()
        .all()
    }
    seen: set[tuple[str, str]] = set()
    table_ids: dict[tuple[str, str], uuid.UUID] = {}
    for t in snapshot["tables"]:
        k = (t["schema"], t["name"])
        seen.add(k)
        row = existing_tables.get(k)
        if row is None:
            row = DatasetTable(
                dataset_id=ds.id, schema_name=t["schema"], table_name=t["name"], kind=t["kind"], exposed=True
            )
            session.add(row)
            await session.flush()
            existing_cols: dict[str, DatasetColumn] = {}
        else:
            existing_cols = {c.column_name: c for c in row.columns}
        row.kind = t["kind"]
        row.row_estimate = t.get("row_estimate")
        if not row.description and t.get("comment"):
            row.description, row.description_source = t["comment"], "db_comment"
        table_ids[k] = row.id
        seen_cols: set[str] = set()
        for c in t["columns"]:
            seen_cols.add(c["name"])
            col = existing_cols.get(c["name"])
            if col is None:
                col = DatasetColumn(table_id=row.id, column_name=c["name"], data_type=c["type"])
                session.add(col)
            col.data_type = c["type"]
            col.nullable = bool(c.get("nullable", True))
            col.is_pk = bool(c.get("is_pk"))
            col.ordinal = int(c.get("ordinal", 0))
            col.sample_values = (
                c.get("samples")
                if c.get("samples") is not None
                else ([c["sample_note"]] if c.get("sample_note") else None)
            )
            if not col.description and c.get("comment"):
                col.description, col.description_source = c["comment"], "db_comment"
        for name, col in existing_cols.items():
            if name not in seen_cols:
                await session.delete(col)
    for k, row in existing_tables.items():
        if k not in seen:
            await session.delete(row)
    await session.flush()
    await session.execute(
        delete(DatasetRelationship).where(
            DatasetRelationship.dataset_id == ds.id, DatasetRelationship.type == "fk"
        )
    )
    for t in snapshot["tables"]:
        for fk in t.get("foreign_keys", []):
            src, dst = (
                table_ids.get((t["schema"], t["name"])),
                table_ids.get((fk["ref_schema"], fk["ref_table"])),
            )
            if src and dst:
                session.add(
                    DatasetRelationship(
                        dataset_id=ds.id,
                        from_table_id=src,
                        from_column=fk["column"],
                        to_table_id=dst,
                        to_column=fk["ref_column"],
                        type="fk",
                        confidence=1.0,
                    )
                )
    ds.current_schema_version = version.version
    await session.flush()
    return version


async def run_introspection(session: AsyncSession, ds: Dataset) -> DatasetSchemaVersion:
    if ds.type == "postgres":
        password = decrypt_secret(ds.encrypted_password) if ds.encrypted_password else ""
        snapshot = await introspect_postgres(
            ds.connection_config, password, ds.connection_config.get("schemas") or None, ds.allow_sampling
        )
    else:
        snapshot = await introspect_duckdb(ds, ds.allow_sampling)
    return await apply_snapshot(session, ds, snapshot)
