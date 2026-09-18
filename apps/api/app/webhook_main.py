"""Webhook service entrypoint: only the WhatsApp ingress routes (PRD §8.2). Same codebase, separate process."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.core.redis import close_redis
from app.db.session import dispose_engine
from app.deployments.whatsapp.webhook import router as webhook_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings.log_level, service="webhook", env=settings.app_env)
    yield
    await close_redis()
    await dispose_engine()


app = FastAPI(title="AgentDesk Webhooks", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(RequestContextMiddleware, hsts=settings.is_prod_like)
install_error_handlers(app)
app.include_router(webhook_router)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
