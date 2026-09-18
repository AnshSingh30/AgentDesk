"""Datasets: Postgres connections, spreadsheet import, introspection, schema review, glossary, examples, SQL runner (PRD §6.6, §16.6)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import encrypt_secret
from app.core.deps import OrgContext, get_db, require_membership
from app.core.errors import Problem
from app.core.ids import uuid7
from app.core.rate_limit import client_ip
from app.core.s3 import get_bytes, head_object, presign_put, sanitize_filename
from app.core.ssrf import resolve_public_host
from app.db.models import (
    Dataset,
    DatasetColumn,
    DatasetExampleQuery,
    DatasetGlossary,
    DatasetRelationship,
    DatasetSchemaVersion,
    DatasetTable,
)
from app.db.repo import audit, get_scoped, scoped
from app.jobs.queue import enqueue
from app.sql.context import load_dataset_context
from app.sql.executor import SqlExecutionError, close_pool, execute, pg_test_connection
from app.sql.spreadsheet import preview as spreadsheet_preview
from app.sql.validator import validate_sql

log = structlog.get_logger()
router = APIRouter(tags=["datasets"])
READ_ONLY_ROLE_SQL = """CREATE ROLE agentdesk_ro LOGIN PASSWORD '<strong-password>';
GRANT CONNECT ON DATABASE your_db TO agentdesk_ro;
GRANT USAGE ON SCHEMA public TO agentdesk_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO agentdesk_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO agentdesk_ro;
ALTER ROLE agentdesk_ro SET default_transaction_read_only = on;
ALTER ROLE agentdesk_ro SET statement_timeout = '15s';"""


class PgConnection(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=120)
    username: str = Field(min_length=1, max_length=120)
    password: str = Field("", max_length=500)
    sslmode: str = Field("require", pattern="^(disable|allow|prefer|require|verify-ca|verify-full)$")
    schemas: list[str] = Field(default_factory=list, max_length=20)


class DatasetCreate(BaseModel):
    type: str = Field(pattern="^(postgres|spreadsheet)$")
    name: str = Field(min_length=1, max_length=120)
    connection: PgConnection | None = None
    allow_sampling: bool = True


class DatasetPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    allow_sampling: bool | None = None
    connection: PgConnection | None = None


class TablePatch(BaseModel):
    description: str | None = Field(default=None, max_length=2000)
    exposed: bool | None = None
    reviewed: bool | None = None


class ColumnPatch(BaseModel):
    description: str | None = Field(default=None, max_length=2000)
    hidden: bool | None = None
    reviewed: bool | None = None
    semantic_type: str | None = Field(default=None, max_length=40)


class RelationshipIn(BaseModel):
    from_table_id: uuid.UUID
    from_column: str = Field(max_length=200)
    to_table_id: uuid.UUID
    to_column: str = Field(max_length=200)


class GlossaryIn(BaseModel):
    term: str = Field(min_length=1, max_length=120)
    definition: str = Field(min_length=1, max_length=2000)


class ExampleIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    sql: str = Field(min_length=1, max_length=20000)


class QueryIn(BaseModel):
    sql: str = Field(min_length=1, max_length=20000)
    max_rows: int = Field(200, ge=1, le=5000)


def ds_out(ds: Dataset, table_stats: dict[str, int] | None = None) -> dict[str, Any]:
    cfg = dict(ds.connection_config or {})
    cfg.pop("password", None)
    return {
        "id": ds.id,
        "name": ds.name,
        "type": ds.type,
        "status": ds.status,
        "connection_config": cfg,
        "password_configured": bool(ds.encrypted_password),
        "allow_sampling": ds.allow_sampling,
        "current_schema_version": ds.current_schema_version,
        "error": ds.error,
        "storage_key": ds.storage_key,
        "created_at": ds.created_at,
        "updated_at": ds.updated_at,
        "table_stats": table_stats or {},
    }


async def _table_stats(session: AsyncSession, ds_id: uuid.UUID) -> dict[str, int]:
    total, reviewed, exposed = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(
                    func.sum(func.cast(DatasetTable.reviewed, __import__("sqlalchemy").Integer)), 0
                ),
                func.coalesce(func.sum(func.cast(DatasetTable.exposed, __import__("sqlalchemy").Integer)), 0),
            ).where(DatasetTable.dataset_id == ds_id)
        )
    ).one()
    return {"tables": total, "reviewed": int(reviewed), "exposed": int(exposed)}


@router.get("/orgs/{org_id}/datasets/read-only-role-sql")
async def read_only_sql(ctx: OrgContext = Depends(require_membership("viewer"))) -> dict[str, str]:
    return {"sql": READ_ONLY_ROLE_SQL, "egress_ip": "[OPEN] depends on hosting - see docs/runbooks"}


@router.post("/orgs/{org_id}/datasets/postgres/test")
async def test_postgres(
    body: PgConnection, ctx: OrgContext = Depends(require_membership("editor"))
) -> dict[str, Any]:
    resolve_public_host(body.host)
    try:
        return await pg_test_connection(body.model_dump(exclude={"password", "schemas"}), body.password)
    except SqlExecutionError as e:
        return {"ok": False, "error": str(e)}


@router.post("/orgs/{org_id}/datasets", status_code=status.HTTP_201_CREATED)
async def create_dataset(
    body: DatasetCreate,
    request: Request,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ds = Dataset(
        org_id=ctx.org_id,
        name=body.name.strip(),
        type=body.type,
        allow_sampling=body.allow_sampling,
        status="connecting",
    )
    if body.type == "postgres":
        if body.connection is None:
            raise Problem(422, "Postgres connection details are required")
        resolve_public_host(body.connection.host)
        ds.connection_config = body.connection.model_dump(exclude={"password"})
        ds.encrypted_password = encrypt_secret(body.connection.password) if body.connection.password else None
    session.add(ds)
    await session.flush()
    await audit(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action="dataset.created",
        target_type="dataset",
        target_id=ds.id,
        metadata={"type": ds.type},
        ip=client_ip(request),
    )
    if body.type == "postgres":
        ds.status = "introspecting"
        await enqueue("introspect_dataset", str(ds.id), True)
    return ds_out(ds)


@router.get("/orgs/{org_id}/datasets")
async def list_datasets(
    ctx: OrgContext = Depends(require_membership("viewer")), session: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    rows = (
        (await session.execute(scoped(Dataset, ctx.org_id).order_by(Dataset.created_at.desc())))
        .scalars()
        .all()
    )
    return [ds_out(d, await _table_stats(session, d.id)) for d in rows]


@router.get("/orgs/{org_id}/datasets/{ds_id}")
async def get_dataset(
    ds_id: str,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    return ds_out(ds, await _table_stats(session, ds.id))


@router.patch("/orgs/{org_id}/datasets/{ds_id}")
async def patch_dataset(
    ds_id: str,
    body: DatasetPatch,
    request: Request,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    if body.name:
        ds.name = body.name.strip()
    if body.allow_sampling is not None:
        ds.allow_sampling = body.allow_sampling
    if body.connection is not None and ds.type == "postgres":
        resolve_public_host(body.connection.host)
        ds.connection_config = body.connection.model_dump(exclude={"password"})
        if body.connection.password:
            ds.encrypted_password = encrypt_secret(body.connection.password)
        await close_pool(str(ds.id))
        await audit(
            session,
            org_id=ctx.org_id,
            actor_user_id=ctx.user.id,
            action="dataset.connection_updated",
            target_type="dataset",
            target_id=ds.id,
            ip=client_ip(request),
        )
    return ds_out(ds, await _table_stats(session, ds.id))


@router.delete("/orgs/{org_id}/datasets/{ds_id}", status_code=204)
async def delete_dataset(
    ds_id: str,
    request: Request,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> None:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    ds.deleted_at = datetime.now(UTC)
    await close_pool(str(ds.id))
    await audit(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action="dataset.deleted",
        target_type="dataset",
        target_id=ds.id,
        ip=client_ip(request),
    )


# ---------------------------------------------------------------- spreadsheets
@router.post("/orgs/{org_id}/datasets/{ds_id}/spreadsheet/uploads")
async def spreadsheet_upload(
    ds_id: str,
    body: dict[str, Any],
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    if ds.type != "spreadsheet":
        raise Problem(409, "Not a spreadsheet dataset")
    name = sanitize_filename(str(body.get("name", "")))
    size = int(body.get("size", 0))
    if not name.lower().endswith((".xlsx", ".xlsm", ".csv")):
        raise Problem(415, "Only XLSX and CSV files are supported")
    if size <= 0 or size > settings.max_spreadsheet_mb * 1024 * 1024:
        raise Problem(413, f"File exceeds the {settings.max_spreadsheet_mb} MB limit")
    mime = (
        "text/csv"
        if name.lower().endswith(".csv")
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    key = f"orgs/{ctx.org_id}/datasets/{ds.id}/uploads/{uuid7()}/{name}"
    url = await presign_put(key, mime, size)
    return {
        "upload_url": url,
        "key": key,
        "headers": {"Content-Type": mime, "Content-Length": str(size)},
        "expires_in": 900,
    }


@router.post("/orgs/{org_id}/datasets/{ds_id}/spreadsheet/preview")
async def spreadsheet_preview_endpoint(
    ds_id: str,
    body: dict[str, Any],
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    key = str(body.get("key", ""))
    if not key.startswith(f"orgs/{ctx.org_id}/datasets/{ds.id}/uploads/"):
        raise Problem(400, "Invalid upload key")
    if await head_object(key) is None:
        raise Problem(400, "Upload not found")
    data = await get_bytes(key)
    import asyncio

    try:
        return await asyncio.to_thread(spreadsheet_preview, data, key.rsplit("/", 1)[-1])
    except Exception as e:  # noqa: BLE001
        raise Problem(422, f"Could not read spreadsheet: {str(e)[:200]}") from e


@router.post("/orgs/{org_id}/datasets/{ds_id}/spreadsheet/import", status_code=202)
async def spreadsheet_import(
    ds_id: str,
    body: dict[str, Any],
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    key = str(body.get("key", ""))
    plan = body.get("plan") or {}
    if not key.startswith(f"orgs/{ctx.org_id}/datasets/{ds.id}/uploads/") or not plan.get("sheets"):
        raise Problem(400, "Upload key and import plan are required")
    ds.status = "importing"
    ds.error = None
    job_id = await enqueue("import_spreadsheet", str(ds.id), key, plan)
    return {"job_id": job_id, "status": "queued"}


# ---------------------------------------------------------------- introspection & drafting
@router.post("/orgs/{org_id}/datasets/{ds_id}/introspect", status_code=202)
async def introspect(
    ds_id: str,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    ds.status = "introspecting"
    ds.error = None
    job_id = await enqueue("introspect_dataset", str(ds.id), False)
    return {"job_id": job_id, "status": "queued"}


@router.post("/orgs/{org_id}/datasets/{ds_id}/draft-descriptions", status_code=202)
async def draft(
    ds_id: str,
    body: dict[str, Any] | None = None,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    table_ids = [str(t) for t in (body or {}).get("table_ids", [])]
    ds.status = "drafting"
    job_id = await enqueue(
        "draft_descriptions", str(ds.id), table_ids, bool((body or {}).get("overwrite", False))
    )
    return {"job_id": job_id, "status": "queued"}


@router.get("/orgs/{org_id}/datasets/{ds_id}/schema")
async def get_schema(
    ds_id: str,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    tables = (
        (
            await session.execute(
                select(DatasetTable).where(DatasetTable.dataset_id == ds.id).order_by(DatasetTable.table_name)
            )
        )
        .scalars()
        .all()
    )
    rels = (
        (await session.execute(select(DatasetRelationship).where(DatasetRelationship.dataset_id == ds.id)))
        .scalars()
        .all()
    )
    glossary = (
        (
            await session.execute(
                select(DatasetGlossary)
                .where(DatasetGlossary.dataset_id == ds.id)
                .order_by(DatasetGlossary.term)
            )
        )
        .scalars()
        .all()
    )
    examples = (
        (
            await session.execute(
                select(DatasetExampleQuery)
                .where(DatasetExampleQuery.dataset_id == ds.id)
                .order_by(DatasetExampleQuery.created_at)
            )
        )
        .scalars()
        .all()
    )
    return {
        "dataset": ds_out(ds, await _table_stats(session, ds.id)),
        "tables": [
            {
                "id": t.id,
                "schema_name": t.schema_name,
                "table_name": t.table_name,
                "kind": t.kind,
                "row_estimate": t.row_estimate,
                "description": t.description,
                "description_source": t.description_source,
                "reviewed": t.reviewed,
                "exposed": t.exposed,
                "hidden_columns": sum(1 for c in t.columns if c.hidden),
                "columns": [
                    {
                        "id": c.id,
                        "column_name": c.column_name,
                        "data_type": c.data_type,
                        "nullable": c.nullable,
                        "is_pk": c.is_pk,
                        "ordinal": c.ordinal,
                        "description": c.description,
                        "description_source": c.description_source,
                        "semantic_type": c.semantic_type,
                        "sample_values": c.sample_values,
                        "hidden": c.hidden,
                        "reviewed": c.reviewed,
                    }
                    for c in t.columns
                ],
            }
            for t in tables
        ],
        "relationships": [
            {
                "id": r.id,
                "from_table_id": r.from_table_id,
                "from_column": r.from_column,
                "to_table_id": r.to_table_id,
                "to_column": r.to_column,
                "type": r.type,
                "confidence": r.confidence,
            }
            for r in rels
        ],
        "glossary": [{"id": g.id, "term": g.term, "definition": g.definition} for g in glossary],
        "example_queries": [
            {
                "id": e.id,
                "question": e.question,
                "sql": e.sql,
                "validated_at": e.validated_at,
                "last_error": e.last_error,
            }
            for e in examples
        ],
    }


async def _table(session: AsyncSession, ds: Dataset, table_id: uuid.UUID) -> DatasetTable:
    t = (
        await session.execute(
            select(DatasetTable).where(DatasetTable.id == table_id, DatasetTable.dataset_id == ds.id)
        )
    ).scalar_one_or_none()
    if t is None:
        raise Problem(404, "Table not found")
    return t


async def _refresh_status(session: AsyncSession, ds: Dataset) -> None:
    if ds.status in ("needs_review", "ready"):
        stats = await _table_stats(session, ds.id)
        unreviewed = (
            await session.execute(
                select(func.count())
                .select_from(DatasetTable)
                .where(
                    DatasetTable.dataset_id == ds.id,
                    DatasetTable.exposed.is_(True),
                    DatasetTable.reviewed.is_(False),
                )
            )
        ).scalar_one()
        ds.status = "ready" if stats["tables"] and unreviewed == 0 else "needs_review"


@router.patch("/orgs/{org_id}/datasets/{ds_id}/tables/{table_id}")
async def patch_table(
    ds_id: str,
    table_id: uuid.UUID,
    body: TablePatch,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    t = await _table(session, ds, table_id)
    if body.description is not None:
        t.description, t.description_source, t.reviewed = body.description.strip() or None, "user", True
    if body.exposed is not None:
        t.exposed = body.exposed
    if body.reviewed is not None:
        t.reviewed = body.reviewed
    await _refresh_status(session, ds)
    return {
        "id": t.id,
        "description": t.description,
        "description_source": t.description_source,
        "reviewed": t.reviewed,
        "exposed": t.exposed,
    }


@router.patch("/orgs/{org_id}/datasets/{ds_id}/columns/{column_id}")
async def patch_column(
    ds_id: str,
    column_id: uuid.UUID,
    body: ColumnPatch,
    request: Request,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    c = (
        await session.execute(
            select(DatasetColumn)
            .join(DatasetTable)
            .where(DatasetColumn.id == column_id, DatasetTable.dataset_id == ds.id)
        )
    ).scalar_one_or_none()
    if c is None:
        raise Problem(404, "Column not found")
    if body.description is not None:
        c.description, c.description_source, c.reviewed = body.description.strip() or None, "user", True
    if body.hidden is not None and body.hidden != c.hidden:
        c.hidden = body.hidden
        await audit(
            session,
            org_id=ctx.org_id,
            actor_user_id=ctx.user.id,
            action="dataset.hidden_columns_changed",
            target_type="column",
            target_id=c.id,
            metadata={"column": c.column_name, "hidden": c.hidden},
            ip=client_ip(request),
        )
    if body.reviewed is not None:
        c.reviewed = body.reviewed
    if body.semantic_type is not None:
        c.semantic_type = body.semantic_type
    return {
        "id": c.id,
        "description": c.description,
        "description_source": c.description_source,
        "hidden": c.hidden,
        "reviewed": c.reviewed,
        "semantic_type": c.semantic_type,
    }


@router.post("/orgs/{org_id}/datasets/{ds_id}/tables/bulk-review")
async def bulk_review(
    ds_id: str,
    body: dict[str, Any] | None = None,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    ids = [uuid.UUID(str(i)) for i in (body or {}).get("table_ids", [])]
    stmt = select(DatasetTable).where(DatasetTable.dataset_id == ds.id)
    if ids:
        stmt = stmt.where(DatasetTable.id.in_(ids))
    tables = (await session.execute(stmt)).scalars().all()
    for t in tables:
        t.reviewed = True
        for c in t.columns:
            c.reviewed = True
    await _refresh_status(session, ds)
    return {"reviewed": len(tables), "status": ds.status}


# ---------------------------------------------------------------- relationships / glossary / examples
@router.post("/orgs/{org_id}/datasets/{ds_id}/relationships", status_code=201)
async def add_relationship(
    ds_id: str,
    body: RelationshipIn,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    await _table(session, ds, body.from_table_id)
    await _table(session, ds, body.to_table_id)
    r = DatasetRelationship(
        dataset_id=ds.id,
        from_table_id=body.from_table_id,
        from_column=body.from_column,
        to_table_id=body.to_table_id,
        to_column=body.to_column,
        type="user",
        confidence=1.0,
    )
    session.add(r)
    await session.flush()
    return {"id": r.id, "type": r.type}


@router.delete("/orgs/{org_id}/datasets/{ds_id}/relationships/{rel_id}", status_code=204)
async def delete_relationship(
    ds_id: str,
    rel_id: uuid.UUID,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> None:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    r = (
        await session.execute(
            select(DatasetRelationship).where(
                DatasetRelationship.id == rel_id, DatasetRelationship.dataset_id == ds.id
            )
        )
    ).scalar_one_or_none()
    if r is None:
        raise Problem(404, "Relationship not found")
    await session.delete(r)


@router.post("/orgs/{org_id}/datasets/{ds_id}/glossary", status_code=201)
async def add_glossary(
    ds_id: str,
    body: GlossaryIn,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    g = DatasetGlossary(dataset_id=ds.id, term=body.term.strip(), definition=body.definition.strip())
    session.add(g)
    await session.flush()
    return {"id": g.id, "term": g.term, "definition": g.definition}


@router.patch("/orgs/{org_id}/datasets/{ds_id}/glossary/{item_id}")
async def patch_glossary(
    ds_id: str,
    item_id: uuid.UUID,
    body: GlossaryIn,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    g = (
        await session.execute(
            select(DatasetGlossary).where(DatasetGlossary.id == item_id, DatasetGlossary.dataset_id == ds.id)
        )
    ).scalar_one_or_none()
    if g is None:
        raise Problem(404, "Glossary term not found")
    g.term, g.definition = body.term.strip(), body.definition.strip()
    return {"id": g.id, "term": g.term, "definition": g.definition}


@router.delete("/orgs/{org_id}/datasets/{ds_id}/glossary/{item_id}", status_code=204)
async def delete_glossary(
    ds_id: str,
    item_id: uuid.UUID,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> None:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    g = (
        await session.execute(
            select(DatasetGlossary).where(DatasetGlossary.id == item_id, DatasetGlossary.dataset_id == ds.id)
        )
    ).scalar_one_or_none()
    if g is None:
        raise Problem(404, "Glossary term not found")
    await session.delete(g)


async def _validate_example(session: AsyncSession, ds: Dataset, ex: DatasetExampleQuery) -> None:
    ctx = await load_dataset_context(session, ds)
    result = validate_sql(ex.sql, ctx.exposed, dialect=ctx.dialect, max_rows=50)
    if not result.ok:
        ex.validated_at, ex.last_error = None, result.error
        return
    try:
        await execute(ds, result.sql or ex.sql, 50)
        ex.validated_at, ex.last_error = datetime.now(UTC), None
    except SqlExecutionError as e:
        ex.validated_at, ex.last_error = None, str(e)


@router.post("/orgs/{org_id}/datasets/{ds_id}/example-queries", status_code=201)
async def add_example(
    ds_id: str,
    body: ExampleIn,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    ex = DatasetExampleQuery(dataset_id=ds.id, question=body.question.strip(), sql=body.sql.strip())
    session.add(ex)
    await session.flush()
    await _validate_example(session, ds, ex)
    return {
        "id": ex.id,
        "question": ex.question,
        "sql": ex.sql,
        "validated_at": ex.validated_at,
        "last_error": ex.last_error,
    }


@router.post("/orgs/{org_id}/datasets/{ds_id}/example-queries/{ex_id}/validate")
async def validate_example(
    ds_id: str,
    ex_id: uuid.UUID,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    ex = (
        await session.execute(
            select(DatasetExampleQuery).where(
                DatasetExampleQuery.id == ex_id, DatasetExampleQuery.dataset_id == ds.id
            )
        )
    ).scalar_one_or_none()
    if ex is None:
        raise Problem(404, "Example not found")
    await _validate_example(session, ds, ex)
    return {"id": ex.id, "validated_at": ex.validated_at, "last_error": ex.last_error}


@router.delete("/orgs/{org_id}/datasets/{ds_id}/example-queries/{ex_id}", status_code=204)
async def delete_example(
    ds_id: str,
    ex_id: uuid.UUID,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> None:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    ex = (
        await session.execute(
            select(DatasetExampleQuery).where(
                DatasetExampleQuery.id == ex_id, DatasetExampleQuery.dataset_id == ds.id
            )
        )
    ).scalar_one_or_none()
    if ex is None:
        raise Problem(404, "Example not found")
    await session.delete(ex)


@router.post("/orgs/{org_id}/datasets/{ds_id}/query")
async def run_query(
    ds_id: str,
    body: QueryIn,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    dctx = await load_dataset_context(session, ds)
    result = validate_sql(body.sql, dctx.exposed, dialect=dctx.dialect, max_rows=body.max_rows)
    if not result.ok:
        raise Problem(422, f"Query rejected: {result.error}", type_="urn:agentdesk:sql-rejected")
    try:
        res = await execute(ds, result.sql or body.sql, body.max_rows)
    except SqlExecutionError as e:
        raise Problem(400, f"Query failed: {e}", type_="urn:agentdesk:sql-error") from e
    return {"validated_sql": result.sql, **res.as_dict()}


@router.get("/orgs/{org_id}/datasets/{ds_id}/schema-diff/{version}")
async def schema_diff(
    ds_id: str,
    version: int,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    v = (
        await session.execute(
            select(DatasetSchemaVersion).where(
                DatasetSchemaVersion.dataset_id == ds.id, DatasetSchemaVersion.version == version
            )
        )
    ).scalar_one_or_none()
    if v is None:
        raise Problem(404, "Schema version not found")
    return {
        "version": v.version,
        "diff": v.diff,
        "created_at": v.created_at,
        "table_count": len(v.raw_snapshot.get("tables", [])),
    }


@router.get("/orgs/{org_id}/datasets/{ds_id}/schema-versions")
async def schema_versions(
    ds_id: str,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    ds = await get_scoped(session, Dataset, ctx.org_id, ds_id, what="Dataset")
    rows = (
        (
            await session.execute(
                select(DatasetSchemaVersion)
                .where(DatasetSchemaVersion.dataset_id == ds.id)
                .order_by(DatasetSchemaVersion.version.desc())
            )
        )
        .scalars()
        .all()
    )
    return [{"version": v.version, "diff": v.diff, "created_at": v.created_at} for v in rows]
