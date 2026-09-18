"""Worker-side WhatsApp message processing (PRD §8.4 inbound flow, §14)."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select

from app.agents.schemas import AgentConfig
from app.core.crypto import decrypt_secret
from app.core.rate_limit import check_rate_limit
from app.core.redis import get_redis
from app.db.models import (
    Agent,
    AgentVersion,
    Contact,
    Deployment,
    Membership,
    Message,
    Notification,
    Organization,
)
from app.db.session import get_sessionmaker
from app.deployments.whatsapp.client import WhatsAppClient, WhatsAppError
from app.llm.gateway import LLMError
from app.memory.history import get_or_create_conversation, reset_conversation
from app.runtime.runner import prepare_run, run_once

log = structlog.get_logger()
LOCK_TTL = 120
LOCK_WAIT = 90


async def _acquire_lock(key: str) -> bool:
    r = get_redis()
    deadline = asyncio.get_event_loop().time() + LOCK_WAIT
    while asyncio.get_event_loop().time() < deadline:
        if await r.set(key, "1", ex=LOCK_TTL, nx=True):
            return True
        await asyncio.sleep(0.5)
    return False


async def _notify_admins(session: Any, org_id: uuid.UUID, title: str, body: str) -> None:
    admins = (
        (
            await session.execute(
                select(Membership).where(Membership.org_id == org_id, Membership.role.in_(("owner", "admin")))
            )
        )
        .scalars()
        .all()
    )
    for m in admins:
        session.add(
            Notification(org_id=org_id, user_id=m.user_id, kind="deployment_error", title=title, body=body)
        )


async def _mark_deployment_error(deployment_id: uuid.UUID, reason: str) -> None:
    async with get_sessionmaker()() as session:
        dep = await session.get(Deployment, deployment_id)
        if dep is None:
            return
        dep.status = "error"
        dep.last_error = reason[:500]
        dep.last_error_at = datetime.now(UTC)
        await _notify_admins(session, dep.org_id, "WhatsApp deployment error", reason[:500])
        await session.commit()


async def process_whatsapp_message(
    deployment_id: str, message: dict[str, Any], contact: dict[str, Any]
) -> dict[str, Any]:
    wa_id = str(message.get("from") or contact.get("wa_id") or "")
    wamid = str(message.get("id") or "")
    dep_uuid = uuid.UUID(deployment_id)
    lock_key = f"lock:contact:{deployment_id}:{wa_id}"
    if not await _acquire_lock(lock_key):
        log.warning("wa_lock_timeout", deployment_id=deployment_id)
        return {"skipped": "lock_timeout"}
    try:
        return await _process(dep_uuid, wa_id, wamid, message, contact)
    finally:
        await get_redis().delete(lock_key)


async def _process(
    dep_id: uuid.UUID, wa_id: str, wamid: str, message: dict[str, Any], contact_info: dict[str, Any]
) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        dep = await session.get(Deployment, dep_id)
        if dep is None or dep.deleted_at is not None or dep.status != "live":
            return {"skipped": f"deployment {dep.status if dep else 'missing'}"}
        org = await session.get(Organization, dep.org_id)
        agent = await session.get(Agent, dep.agent_id)
        version = await session.get(AgentVersion, dep.agent_version_id)
        if org is None or agent is None or version is None or org.suspended_at is not None:
            return {"skipped": "org/agent unavailable"}
        cfg = AgentConfig.model_validate(version.config)
        token = decrypt_secret(dep.encrypted_access_token) if dep.encrypted_access_token else ""
        client = WhatsAppClient(
            str(dep.config.get("phone_number_id")), token, dep.config.get("graph_version")
        )
        contact = (
            await session.execute(
                select(Contact).where(Contact.deployment_id == dep.id, Contact.wa_id == wa_id)
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        first_contact = contact is None
        if contact is None:
            contact = Contact(
                org_id=org.id,
                deployment_id=dep.id,
                wa_id=wa_id,
                wa_id_hash=hashlib.sha256(wa_id.encode()).hexdigest(),
                profile_name=(contact_info.get("profile") or {}).get("name"),
                first_seen_at=now,
                last_seen_at=now,
            )
            session.add(contact)
            await session.flush()
        contact.last_seen_at = now
        contact.message_count += 1
        if (contact_info.get("profile") or {}).get("name"):
            contact.profile_name = contact_info["profile"]["name"]
        dep.message_count += 1
        if contact.blocked:
            await session.commit()
            return {"skipped": "blocked contact"}
        limits = dep.rate_limits or {}
        per_min = await check_rate_limit(
            f"rl:wa:contact:{dep.id}:{wa_id}:m", int(limits.get("contact_per_minute", 20)), 60
        )
        per_day = await check_rate_limit(
            f"rl:wa:contact:{dep.id}:{wa_id}:d", int(limits.get("contact_per_day", 300)), 86400
        )
        dep_day = await check_rate_limit(
            f"rl:wa:dep:{dep.id}:d", int(limits.get("deployment_per_day", 5000)), 86400
        )
        if not (per_min.allowed and per_day.allowed and dep_day.allowed):
            await session.commit()
            r = get_redis()
            if await r.set(f"wa:ratelimited:{dep.id}:{wa_id}", "1", ex=300, nx=True):
                await _safe_send(
                    client,
                    wa_id,
                    "You're sending messages too quickly. Please wait a little and try again.",
                    dep.id,
                )
            return {"skipped": "rate_limited"}
        msg_type = message.get("type")
        text = ((message.get("text") or {}).get("body") or "").strip() if msg_type == "text" else ""
        if msg_type == "interactive":
            inter = message.get("interactive") or {}
            text = ((inter.get("button_reply") or inter.get("list_reply") or {}).get("title") or "").strip()
        await client.mark_read(wamid, typing=True)
        if msg_type not in ("text", "interactive") or not text:
            await session.commit()
            await _safe_send(client, wa_id, "I can only read text messages for now.", dep.id)
            return {"skipped": "unsupported_type"}
        conv = await get_or_create_conversation(
            session,
            org_id=org.id,
            agent_id=agent.id,
            channel="whatsapp",
            cfg=cfg.memory,
            deployment_id=dep.id,
            contact_id=contact.id,
            agent_version_id=version.id,
        )
        if text.lower() in ("/reset", "reset"):
            await reset_conversation(session, conv)
            session.add(
                Message(
                    org_id=org.id,
                    conversation_id=conv.id,
                    role="user",
                    content=text,
                    external_id=wamid,
                    status="received",
                )
            )
            await session.commit()
            await _safe_send(client, wa_id, "Starting fresh 👋", dep.id)
            return {"reset": True}
        if first_contact and cfg.instructions.welcome_message:
            welcome = cfg.instructions.welcome_message
            if cfg.instructions.ai_disclosure and "assistant" not in welcome.lower():
                welcome += "\n\n_You're chatting with an AI assistant._"
            await _safe_send(client, wa_id, welcome, dep.id)
        try:
            run_ctx = await prepare_run(
                session,
                org=org,
                agent=agent,
                cfg=cfg,
                channel="whatsapp",
                conversation=conv,
                user_text=text,
                agent_version_id=version.id,
                deployment_id=dep.id,
                external_id=wamid,
            )
        except LLMError as e:
            await session.commit()
            await _mark_deployment_error(dep.id, f"Model provider error: {e.message}")
            await _safe_send(
                client, wa_id, "I'm having trouble right now, please try again in a minute.", dep.id
            )
            return {"error": e.code}
        await session.commit()
    outcome = await run_once(run_ctx, text)
    chunks = outcome.state.get("formatted") or [
        outcome.state.get("answer") or "I'm having trouble right now, please try again in a minute."
    ]
    sent_ids: list[str] = []
    failure: WhatsAppError | None = None
    for i, chunk in enumerate(chunks):
        try:
            sent_ids.append(await client.send_text(wa_id, chunk, reply_to=wamid if i == 0 else None))
        except WhatsAppError as e:
            failure = e
            break
    async with get_sessionmaker()() as session:
        if outcome.message_id is not None:
            m = await session.get(Message, outcome.message_id)
            if m is not None:
                m.status = "sent" if failure is None else "failed"
                m.external_id = sent_ids[0] if sent_ids else None
                if failure is not None:
                    m.error = f"send failed: {failure.code}"
        await session.commit()
    if failure is not None:
        if failure.code == "token_invalid":
            await _mark_deployment_error(
                dep_id,
                "WhatsApp access token expired or invalid; update credentials in the deployment settings",
            )
        log.warning("wa_send_failed", code=failure.code, deployment_id=str(dep_id))
        return {"error": failure.code, "sent": len(sent_ids)}
    if outcome.error and "invalid_key" in outcome.error:
        await _mark_deployment_error(dep_id, f"Model provider error: {outcome.error}")
    return {"sent": len(sent_ids), "run_id": run_ctx.run_id}


async def _safe_send(client: WhatsAppClient, to: str, text: str, dep_id: uuid.UUID) -> None:
    try:
        await client.send_text(to, text)
    except WhatsAppError as e:
        log.warning("wa_send_failed", code=e.code, deployment_id=str(dep_id))
        if e.code == "token_invalid":
            await _mark_deployment_error(dep_id, "WhatsApp access token expired or invalid")
