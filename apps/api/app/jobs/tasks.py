"""ARQ job functions (PRD §8.2 worker): ingestion, introspection, descriptions, spreadsheet import,
WhatsApp processing, org export, retention, usage rollups."""

from __future__ import annotations

import io
import json
import os
import tempfile
import uuid
import zipfile
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import delete, func, select, text

from app.core.config import settings
from app.core.s3 import get_bytes, presign_get, put_bytes, upload_from_path
from app.db.models import (
    Agent,
    Conversation,
    Dataset,
    DatasetGlossary,
    DatasetTable,
    Deployment,
    KnowledgeBase,
    Message,
    Notification,
    Organization,
    RunTrace,
    UsageDaily,
    UsageEvent,
)
from app.db.session import session_scope
from app.llm.gateway import resolve_model
from app.rag.ingestion import delete_document_fully, ingest_document, reindex_kb
from app.sql.describer import draft_table_descriptions
from app.sql.introspect import run_introspection
from app.sql.spreadsheet import build_duckdb
from app.usage.service import record_usage

log = structlog.get_logger()


async def ingest_document_job(
    ctx: dict[str, Any], document_id: str, job_id: str | None = None
) -> dict[str, Any]:
    return await ingest_document(document_id, job_id)


async def reindex_kb_job(ctx: dict[str, Any], kb_id: str) -> dict[str, Any]:
    return await reindex_kb(kb_id)


async def delete_document_vectors_job(ctx: dict[str, Any], document_id: str) -> None:
    await delete_document_fully(document_id)


async def introspect_dataset_job(
    ctx: dict[str, Any], dataset_id: str, draft_after: bool = False
) -> dict[str, Any]:
    ds_uuid = uuid.UUID(dataset_id)
    try:
        async with session_scope() as session:
            ds = await session.get(Dataset, ds_uuid)
            if ds is None:
                return {"skipped": "missing"}
            ds.status = "introspecting"
            version = await run_introspection(session, ds)
            ds.status = "drafting" if draft_after else "needs_review"
            ds.error = None
            result = {"version": version.version, "diff": version.diff}
    except Exception as e:  # noqa: BLE001
        async with session_scope() as session:
            ds = await session.get(Dataset, ds_uuid)
            if ds is not None:
                ds.status = "error"
                ds.error = f"{type(e).__name__}: {str(e)[:400]}"
        log.warning("introspection_failed", dataset_id=dataset_id, error=str(e))
        raise
    if draft_after:
        await draft_descriptions_job(ctx, dataset_id, [], False)
    return result


async def draft_descriptions_job(
    ctx: dict[str, Any], dataset_id: str, table_ids: list[str], overwrite: bool = False
) -> dict[str, Any]:
    ds_uuid = uuid.UUID(dataset_id)
    async with session_scope() as session:
        ds = await session.get(Dataset, ds_uuid)
        if ds is None:
            return {"skipped": "missing"}
        ds.status = "drafting"
        try:
            rm = await resolve_model(session, ds.org_id, {"mode": "platform"}, utility=True)
            done, usage = await draft_table_descriptions(
                session, ds, rm, [uuid.UUID(t) for t in table_ids] or None, overwrite_user=overwrite
            )
            await record_usage(session, ds.org_id, usage)
            ds.status = "needs_review"
            ds.error = None
            return {"tables_described": done}
        except Exception as e:  # noqa: BLE001
            ds.status = "needs_review"
            ds.error = f"Description drafting failed: {str(e)[:300]}"
            log.warning("draft_descriptions_failed", dataset_id=dataset_id, error=str(e))
            return {"error": str(e)[:300]}


async def import_spreadsheet_job(
    ctx: dict[str, Any], dataset_id: str, key: str, plan: dict[str, Any]
) -> dict[str, Any]:
    ds_uuid = uuid.UUID(dataset_id)
    async with session_scope() as session:
        ds = await session.get(Dataset, ds_uuid)
        if ds is None:
            return {"skipped": "missing"}
        org_id, version = ds.org_id, ds.current_schema_version + 1
    try:
        data = await get_bytes(key)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "data.duckdb")
            import asyncio

            report = await asyncio.to_thread(build_duckdb, data, key.rsplit("/", 1)[-1], plan, path)
            storage_key = f"orgs/{org_id}/datasets/{dataset_id}/v{version}.duckdb"
            await upload_from_path(path, storage_key)
        async with session_scope() as session:
            ds = await session.get(Dataset, ds_uuid)
            assert ds is not None
            ds.storage_key = storage_key
            ds.status = "introspecting"
            ds.connection_config = {
                **(ds.connection_config or {}),
                "source_key": key,
                "import_report": report,
            }
            schema_version = await run_introspection(session, ds)
            ds.status = "needs_review"
            ds.error = None
        return {"report": report, "schema_version": schema_version.version}
    except Exception as e:  # noqa: BLE001
        async with session_scope() as session:
            ds = await session.get(Dataset, ds_uuid)
            if ds is not None:
                ds.status = "error"
                ds.error = f"Import failed: {str(e)[:400]}"
        log.warning("spreadsheet_import_failed", dataset_id=dataset_id, error=str(e))
        raise


async def process_whatsapp_message_job(
    ctx: dict[str, Any], deployment_id: str, message: dict[str, Any], contact: dict[str, Any]
) -> dict[str, Any]:
    from app.deployments.whatsapp.processing import process_whatsapp_message

    return await process_whatsapp_message(deployment_id, message, contact)


async def export_org_job(ctx: dict[str, Any], org_id: str, user_id: str) -> dict[str, Any]:
    """Org data export as a ZIP of JSON files in S3 (ORG-6); a notification carries the download link."""
    oid = uuid.UUID(org_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        async with session_scope() as session:
            org = await session.get(Organization, oid)
            zf.writestr(
                "organization.json",
                json.dumps(
                    {
                        "id": str(oid),
                        "name": org.name if org else None,
                        "settings": org.settings if org else {},
                    },
                    default=str,
                ),
            )
            for name, model in (
                ("agents", Agent),
                ("knowledge_bases", KnowledgeBase),
                ("datasets", Dataset),
                ("deployments", Deployment),
                ("conversations", Conversation),
                ("messages", Message),
            ):
                rows = (await session.execute(select(model).where(model.org_id == oid))).scalars().all()  # type: ignore[attr-defined]
                payload = [
                    {
                        c.name: getattr(r, c.name)
                        for c in model.__table__.columns
                        if not c.name.startswith("encrypted")
                    }
                    for r in rows
                ]  # type: ignore[attr-defined]
                zf.writestr(f"{name}.json", json.dumps(payload, default=str))
    key = f"orgs/{org_id}/exports/{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.zip"
    await put_bytes(key, buf.getvalue(), "application/zip")
    url = await presign_get(key, "agentdesk-export.zip", expires=24 * 3600)
    async with session_scope() as session:
        session.add(
            Notification(
                org_id=oid,
                user_id=uuid.UUID(user_id),
                kind="export_ready",
                title="Your data export is ready",
                body=f"Download (valid 24h): {url}",
            )
        )
    return {"key": key}


async def cleanup_retention_job(ctx: dict[str, Any]) -> dict[str, Any]:
    """Daily: traces > 30 d, messages > org retention, usage_events raw > 13 months, hard-delete orgs after 14 d (PRD §15.7, ORG-6)."""
    now = datetime.now(UTC)
    out: dict[str, Any] = {}
    async with session_scope() as session:
        out["traces"] = (
            await session.execute(
                delete(RunTrace).where(
                    RunTrace.created_at < now - timedelta(days=settings.retention_traces_days)
                )
            )
        ).rowcount
        orgs = (await session.execute(select(Organization))).scalars().all()
        deleted_msgs = 0
        for org in orgs:
            days = int((org.settings or {}).get("retention_days", settings.retention_messages_days_default))
            deleted_msgs += (
                await session.execute(
                    delete(Message).where(
                        Message.org_id == org.id, Message.created_at < now - timedelta(days=days)
                    )
                )
            ).rowcount
        out["messages"] = deleted_msgs
        out["usage_events"] = (
            await session.execute(delete(UsageEvent).where(UsageEvent.created_at < now - timedelta(days=395)))
        ).rowcount
        doomed = (
            (
                await session.execute(
                    select(Organization).where(
                        Organization.deleted_at.is_not(None),
                        Organization.deleted_at < now - timedelta(days=14),
                    )
                )
            )
            .scalars()
            .all()
        )
        for org in doomed:
            await session.delete(org)
        out["orgs_hard_deleted"] = len(doomed)
    return out


async def rollup_usage_daily_job(ctx: dict[str, Any], day: str | None = None) -> dict[str, Any]:
    target = date.fromisoformat(day) if day else (datetime.now(UTC) - timedelta(days=1)).date()
    start = datetime(target.year, target.month, target.day, tzinfo=UTC)
    end = start + timedelta(days=1)
    async with session_scope() as session:
        await session.execute(delete(UsageDaily).where(UsageDaily.date == target))
        rows = (
            await session.execute(
                select(
                    UsageEvent.org_id,
                    UsageEvent.agent_id,
                    UsageEvent.kind,
                    UsageEvent.model,
                    func.sum(UsageEvent.input_tokens),
                    func.sum(UsageEvent.output_tokens),
                    func.sum(UsageEvent.units),
                    func.sum(UsageEvent.est_cost_usd),
                )
                .where(UsageEvent.created_at >= start, UsageEvent.created_at < end)
                .group_by(UsageEvent.org_id, UsageEvent.agent_id, UsageEvent.kind, UsageEvent.model)
            )
        ).all()
        for org_id, agent_id, kind, model, tin, tout, units, cost in rows:
            session.add(
                UsageDaily(
                    org_id=org_id,
                    date=target,
                    agent_id=agent_id or uuid.UUID(int=0),
                    kind=kind,
                    model=model or "",
                    input_tokens=int(tin or 0),
                    output_tokens=int(tout or 0),
                    units=int(units or 0),
                    est_cost_usd=float(cost or 0),
                )
            )
        return {"date": target.isoformat(), "rows": len(rows)}


async def usage_alerts_job(ctx: dict[str, Any]) -> dict[str, Any]:
    """Hourly: notify org admins at 80% / 100% of the monthly message limit (NT-2)."""
    month = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    notified = 0
    async with session_scope() as session:
        orgs = (
            (await session.execute(select(Organization).where(Organization.deleted_at.is_(None))))
            .scalars()
            .all()
        )
        for org in orgs:
            limit = int((org.settings or {}).get("limits", {}).get("messages_per_month", 10000))
            used = (
                await session.execute(
                    select(func.count())
                    .select_from(UsageEvent)
                    .where(
                        UsageEvent.org_id == org.id,
                        UsageEvent.kind == "message_in",
                        UsageEvent.created_at >= month,
                    )
                )
            ).scalar_one()
            for threshold in (80, 100):
                if used >= limit * threshold / 100:
                    key = f"usage_{threshold}_{month.strftime('%Y%m')}"
                    if not (org.settings or {}).get("alerts", {}).get(key):
                        session.add(
                            Notification(
                                org_id=org.id,
                                user_id=None,
                                kind="usage_alert",
                                title=f"Usage at {threshold}% of monthly limit",
                                body=f"{used} of {limit} messages used this month.",
                            )
                        )
                        org.settings = {
                            **(org.settings or {}),
                            "alerts": {**(org.settings or {}).get("alerts", {}), key: True},
                        }
                        notified += 1
    return {"notified": notified}


__all__ = [
    "DatasetGlossary",
    "DatasetTable",
    "cleanup_retention_job",
    "delete_document_vectors_job",
    "draft_descriptions_job",
    "export_org_job",
    "import_spreadsheet_job",
    "ingest_document_job",
    "introspect_dataset_job",
    "process_whatsapp_message_job",
    "reindex_kb_job",
    "rollup_usage_daily_job",
    "text",
    "usage_alerts_job",
]
