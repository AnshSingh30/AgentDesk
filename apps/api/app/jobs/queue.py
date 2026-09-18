"""ARQ enqueue helper shared by API and webhook services."""

from __future__ import annotations

from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import settings

_pool: ArqRedis | None = None


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(settings.redis_url)


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


async def enqueue(
    function: str, *args: Any, _job_id: str | None = None, _queue_name: str | None = None, **kwargs: Any
) -> str:
    pool = await get_pool()
    job = await pool.enqueue_job(
        function, *args, _job_id=_job_id, _queue_name=_queue_name or "arq:queue", **kwargs
    )
    if job is None:  # duplicate job id already queued
        return _job_id or ""
    return job.job_id


async def job_status(job_id: str) -> dict[str, Any]:
    from arq.jobs import Job

    pool = await get_pool()
    job = Job(job_id, pool)
    status = await job.status()
    info: dict[str, Any] = {"job_id": job_id, "status": status.value}
    try:
        result = await job.result_info()
        if result is not None:
            info["success"] = result.success
            info["result"] = result.result if result.success else str(result.result)[:500]
            info["finished_at"] = result.finish_time.isoformat() if result.finish_time else None
    except Exception:  # noqa: BLE001
        pass
    return info
