"""ARQ worker (PRD §8.2): ingestion, introspection, WA processing, scheduled cleanup."""

from __future__ import annotations

from typing import Any

import structlog
from arq import cron
from arq.connections import RedisSettings

from app.core.config import settings
from app.core.logging import configure_logging
from app.jobs import tasks

log = structlog.get_logger()


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging(settings.log_level, service="worker", env=settings.app_env)
    from app.core.s3 import ensure_bucket

    try:
        await ensure_bucket()
    except Exception as e:  # noqa: BLE001
        log.warning("s3_bucket_check_failed", error=str(e))
    log.info("worker_started", env=settings.app_env)


async def shutdown(ctx: dict[str, Any]) -> None:
    from app.core.redis import close_redis
    from app.db.session import dispose_engine

    await close_redis()
    await dispose_engine()


async def ingest_document(ctx: dict[str, Any], document_id: str, job_id: str | None = None) -> dict[str, Any]:
    return await tasks.ingest_document_job(ctx, document_id, job_id)


async def reindex_kb(ctx: dict[str, Any], kb_id: str) -> dict[str, Any]:
    return await tasks.reindex_kb_job(ctx, kb_id)


async def delete_document_vectors(ctx: dict[str, Any], document_id: str) -> None:
    await tasks.delete_document_vectors_job(ctx, document_id)


async def introspect_dataset(
    ctx: dict[str, Any], dataset_id: str, draft_after: bool = False
) -> dict[str, Any]:
    return await tasks.introspect_dataset_job(ctx, dataset_id, draft_after)


async def draft_descriptions(
    ctx: dict[str, Any], dataset_id: str, table_ids: list[str], overwrite: bool = False
) -> dict[str, Any]:
    return await tasks.draft_descriptions_job(ctx, dataset_id, table_ids, overwrite)


async def import_spreadsheet(
    ctx: dict[str, Any], dataset_id: str, key: str, plan: dict[str, Any]
) -> dict[str, Any]:
    return await tasks.import_spreadsheet_job(ctx, dataset_id, key, plan)


async def process_whatsapp_message(
    ctx: dict[str, Any], deployment_id: str, message: dict[str, Any], contact: dict[str, Any]
) -> dict[str, Any]:
    return await tasks.process_whatsapp_message_job(ctx, deployment_id, message, contact)


async def export_org(ctx: dict[str, Any], org_id: str, user_id: str) -> dict[str, Any]:
    return await tasks.export_org_job(ctx, org_id, user_id)


async def cleanup_retention(ctx: dict[str, Any]) -> dict[str, Any]:
    return await tasks.cleanup_retention_job(ctx)


async def rollup_usage_daily(ctx: dict[str, Any], day: str | None = None) -> dict[str, Any]:
    return await tasks.rollup_usage_daily_job(ctx, day)


async def usage_alerts(ctx: dict[str, Any]) -> dict[str, Any]:
    return await tasks.usage_alerts_job(ctx)


class WorkerSettings:
    functions = [
        ingest_document,
        reindex_kb,
        delete_document_vectors,
        introspect_dataset,
        draft_descriptions,
        import_spreadsheet,
        process_whatsapp_message,
        export_org,
        cleanup_retention,
        rollup_usage_daily,
        usage_alerts,
    ]
    cron_jobs = [
        cron(cleanup_retention, hour=3, minute=15, run_at_startup=False),
        cron(rollup_usage_daily, hour=0, minute=30, run_at_startup=False),
        cron(usage_alerts, minute=5, run_at_startup=False),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 8
    job_timeout = 600
    max_tries = 3
    retry_jobs = True
    health_check_interval = 30
    keep_result = 3600
