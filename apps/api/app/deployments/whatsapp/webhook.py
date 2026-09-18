"""WhatsApp webhook ingress: verify handshake, signature validation, dedupe, enqueue (PRD §14.3)."""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from app.core.crypto import decrypt_secret
from app.core.errors import Problem
from app.core.metrics import wa_webhook_total
from app.core.rate_limit import rate_limited
from app.core.redis import get_redis
from app.core.security import hash_token
from app.db.models import Deployment
from app.db.session import get_sessionmaker
from app.jobs.queue import enqueue

log = structlog.get_logger()
router = APIRouter(prefix="/webhooks/whatsapp", tags=["webhooks"])
MAX_BODY = 1024 * 1024
MAX_AGE_SECONDS = 10 * 60
webhook_limit = rate_limited("webhook", 600, 60)


async def _deployment(deployment_id: str) -> Deployment | None:
    try:
        did = uuid.UUID(deployment_id)
    except ValueError:
        return None
    async with get_sessionmaker()() as session:
        dep = (await session.execute(select(Deployment).where(Deployment.id == did))).scalar_one_or_none()
        if dep is not None:
            session.expunge(dep)
        return dep


@router.get("/{deployment_id}")
async def verify(
    deployment_id: str,
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
) -> PlainTextResponse:
    dep = await _deployment(deployment_id)
    if dep is None or dep.deleted_at is not None or hub_mode != "subscribe" or not dep.verify_token_hash:
        wa_webhook_total.labels("verify_failed").inc()
        raise Problem(403, "Verification failed")
    if not hmac.compare_digest(hash_token(hub_verify_token), dep.verify_token_hash):
        wa_webhook_total.labels("verify_failed").inc()
        raise Problem(403, "Verification failed")
    from datetime import UTC, datetime

    async with get_sessionmaker()() as session:
        row = await session.get(Deployment, dep.id)
        if row is not None:
            row.webhook_verified_at = datetime.now(UTC)
            if row.status == "pending_webhook" and (row.config or {}).get("credentials_valid"):
                row.status = "live"
            await session.commit()
    wa_webhook_total.labels("verified").inc()
    return PlainTextResponse(hub_challenge)


@router.post("/{deployment_id}", dependencies=[Depends(webhook_limit)])
async def receive(deployment_id: str, request: Request) -> Response:
    length = int(request.headers.get("content-length") or 0)
    if length > MAX_BODY:
        raise Problem(413, "Payload too large")
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise Problem(413, "Payload too large")
    dep = await _deployment(deployment_id)
    if dep is None or dep.deleted_at is not None:
        wa_webhook_total.labels("unknown_deployment").inc()
        return Response(status_code=200)  # drop silently; no enumeration
    if not dep.encrypted_app_secret:
        wa_webhook_total.labels("no_secret").inc()
        raise Problem(401, "Invalid signature")
    signature = request.headers.get("x-hub-signature-256", "")
    expected = (
        "sha256="
        + hmac.new(decrypt_secret(dep.encrypted_app_secret).encode(), raw, hashlib.sha256).hexdigest()
    )
    if not signature or not hmac.compare_digest(signature, expected):
        wa_webhook_total.labels("bad_signature").inc()
        log.warning("wa_webhook_bad_signature", deployment_id=deployment_id)
        raise Problem(401, "Invalid signature")
    if dep.status in ("paused", "error", "pending_webhook") and dep.status != "pending_webhook":
        wa_webhook_total.labels("dropped_" + dep.status).inc()
        return Response(status_code=200)
    try:
        payload = await request.json()
    except ValueError:
        wa_webhook_total.labels("malformed").inc()
        return Response(status_code=200)
    r = get_redis()
    queued = 0
    now = time.time()
    for entry in (payload.get("entry") or []) if isinstance(payload, dict) else []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            if change.get("field") != "messages":
                continue
            contacts = {c.get("wa_id"): c for c in value.get("contacts") or []}
            for message in value.get("messages") or []:
                wamid = message.get("id")
                if not wamid:
                    continue
                if not await r.set(f"wa:msg:{wamid}", "1", ex=48 * 3600, nx=True):
                    wa_webhook_total.labels("duplicate").inc()
                    continue
                try:
                    ts = int(message.get("timestamp") or now)
                except (TypeError, ValueError):
                    ts = int(now)
                if now - ts > MAX_AGE_SECONDS:
                    wa_webhook_total.labels("stale").inc()
                    continue
                contact = contacts.get(message.get("from")) or {"wa_id": message.get("from"), "profile": {}}
                await enqueue(
                    "process_whatsapp_message", str(dep.id), message, contact, _job_id=f"wa-{wamid}"
                )
                queued += 1
    wa_webhook_total.labels("accepted").inc()
    log.info("wa_webhook_received", deployment_id=deployment_id, queued=queued)
    return Response(status_code=200)


__all__ = ["Any", "router"]
