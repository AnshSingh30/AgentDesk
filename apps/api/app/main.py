"""AgentDesk API service (PRD §8.2)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.core.config import settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.core.redis import close_redis, get_redis
from app.db.session import dispose_engine, get_sessionmaker

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings.log_level, service="api", env=settings.app_env)
    from app.core.s3 import ensure_bucket
    from app.templates.loader import sync_templates

    try:
        await ensure_bucket()
    except Exception as e:  # noqa: BLE001
        log.warning("s3_bucket_check_failed", error=str(e))
    try:
        async with get_sessionmaker()() as session:
            await sync_templates(session)
            await session.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("template_sync_failed", error=str(e))
    log.info("api_started", env=settings.app_env, version=settings.git_sha)
    yield
    await close_redis()
    await dispose_engine()


def create_app(*, include_webhooks: bool = True) -> FastAPI:
    docs_enabled = settings.expose_docs or settings.app_env in ("local", "test")
    app = FastAPI(
        title="AgentDesk API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.app_url],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["ETag", "X-Request-ID", "RateLimit-Limit", "RateLimit-Remaining", "RateLimit-Reset"],
    )
    app.add_middleware(RequestContextMiddleware, hsts=settings.is_prod_like)
    install_error_handlers(app)

    from app.admin.router import router as admin_router
    from app.agents.router import router as agents_router
    from app.analytics.router import router as analytics_router
    from app.annotations.router import router as annotations_router
    from app.auth.router import router as auth_router
    from app.conversations.router import router as conversations_router
    from app.datasets.router import router as datasets_router
    from app.deployments.router import router as deployments_router
    from app.jobs.router import router as jobs_router
    from app.knowledge.router import router as knowledge_router
    from app.orgs.router import router as orgs_router
    from app.playground.router import router as playground_router
    from app.service_api.router import router as service_api_router
    from app.templates.router import router as templates_router

    for r in (
        auth_router,
        orgs_router,
        templates_router,
        agents_router,
        playground_router,
        knowledge_router,
        datasets_router,
        deployments_router,
        conversations_router,
        analytics_router,
        admin_router,
        jobs_router,
        service_api_router,
        annotations_router,
    ):
        app.include_router(r, prefix="/v1")
    if include_webhooks:
        from app.deployments.whatsapp.webhook import router as webhook_router

        app.include_router(webhook_router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz(response: Response) -> dict[str, Any]:
        checks: dict[str, str] = {}

        async def check(name: str, coro: Any) -> None:
            try:
                await asyncio.wait_for(coro, timeout=5)
                checks[name] = "ok"
            except Exception as e:  # noqa: BLE001
                checks[name] = f"error: {type(e).__name__}"

        async def db() -> None:
            async with get_sessionmaker()() as s:
                await s.execute(text("SELECT 1"))

        async def qdrant() -> None:
            from app.rag.qdrant import get_qdrant

            await get_qdrant().get_collections()

        async def s3() -> None:
            from app.core.s3 import head_object

            await head_object("__readyz__")

        await asyncio.gather(
            check("database", db()),
            check("redis", get_redis().ping()),
            check("qdrant", qdrant()),
            check("s3", s3()),
        )
        ok = all(v == "ok" for v in checks.values())
        response.status_code = 200 if ok else 503
        return {"status": "ok" if ok else "degraded", "checks": checks}

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/version", include_in_schema=False)
    async def version() -> dict[str, str]:
        return {"version": "0.1.0", "git_sha": settings.git_sha, "env": settings.app_env}

    return app


app = create_app()
