"""Service API: call a published agent from your own backend with a per-agent API key.

Blocking JSON or SSE streaming, conversation continuity scoped to your end-user id. Modelled on
Dify's Service API. Keys are shown once and stored as SHA-256; management is Admin+ and audited.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import OrgContext, get_db, require_membership
from app.core.errors import Problem
from app.core.rate_limit import check_rate_limit, client_ip
from app.core.security import hash_token
from app.db.models import Agent, AgentApiKey, Conversation, Organization
from app.db.repo import audit, get_scoped
from app.memory.history import get_or_create_conversation
from app.playground.router import _sse
from app.runtime.runner import load_agent_config, prepare_run, run_once, stream_run

router = APIRouter(tags=["service-api"])
KEY_PREFIX = "adk_"
RATE_LIMIT_PER_MIN = 60


# ---------------------------------------------------------------- key management (dashboard)
class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def _key_out(k: AgentApiKey) -> dict[str, Any]:
    return {
        "id": k.id,
        "name": k.name,
        "last4": k.last4,
        "created_at": k.created_at,
        "last_used_at": k.last_used_at,
    }


@router.get("/orgs/{org_id}/agents/{agent_id}/api-keys")
async def list_api_keys(
    agent_id: str,
    ctx: OrgContext = Depends(require_membership("admin")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    agent = await get_scoped(session, Agent, ctx.org_id, agent_id, what="Agent")
    rows = await session.execute(
        select(AgentApiKey)
        .where(
            AgentApiKey.org_id == ctx.org_id,
            AgentApiKey.agent_id == agent.id,
            AgentApiKey.revoked_at.is_(None),
        )
        .order_by(AgentApiKey.created_at.desc())
    )
    return {"items": [_key_out(k) for k in rows.scalars()]}


@router.post("/orgs/{org_id}/agents/{agent_id}/api-keys", status_code=201)
async def create_api_key(
    agent_id: str,
    body: ApiKeyIn,
    request: Request,
    ctx: OrgContext = Depends(require_membership("admin")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    agent = await get_scoped(session, Agent, ctx.org_id, agent_id, what="Agent")
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    key = AgentApiKey(
        org_id=ctx.org_id,
        agent_id=agent.id,
        name=body.name,
        key_hash=hash_token(raw),
        last4=raw[-4:],
        created_by=ctx.user.id,
    )
    session.add(key)
    await session.flush()
    await audit(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action="api_key.created",
        target_type="agent_api_key",
        target_id=key.id,
        metadata={"agent_id": str(agent.id), "name": body.name},
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return {**_key_out(key), "key": raw}  # the only time the raw key is returned


@router.delete("/orgs/{org_id}/agents/{agent_id}/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    agent_id: str,
    key_id: uuid.UUID,
    request: Request,
    ctx: OrgContext = Depends(require_membership("admin")),
    session: AsyncSession = Depends(get_db),
) -> Response:
    agent = await get_scoped(session, Agent, ctx.org_id, agent_id, what="Agent")
    key = (
        await session.execute(
            select(AgentApiKey).where(
                AgentApiKey.id == key_id,
                AgentApiKey.org_id == ctx.org_id,
                AgentApiKey.agent_id == agent.id,
                AgentApiKey.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if key is None:
        raise Problem(404, "API key not found")
    key.revoked_at = datetime.now(UTC)
    await audit(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action="api_key.revoked",
        target_type="agent_api_key",
        target_id=key.id,
        metadata={"agent_id": str(agent.id)},
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return Response(status_code=204)


# ---------------------------------------------------------------- public service endpoint
class ServiceChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=10000)
    conversation_id: uuid.UUID | None = Field(
        default=None, description="Continue a conversation returned by a previous call"
    )
    user: str | None = Field(
        default=None, max_length=120, description="Your end-user id; conversations are scoped to it"
    )
    response_mode: Literal["blocking", "streaming"] = "blocking"


def _unauthorized(detail: str) -> Problem:
    return Problem(401, detail, title="Invalid API key", headers={"WWW-Authenticate": "Bearer"})


async def _authenticate(request: Request, session: AsyncSession) -> tuple[AgentApiKey, Agent, Organization]:
    auth = request.headers.get("authorization", "")
    raw = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not raw.startswith(KEY_PREFIX):
        raise _unauthorized("Send 'Authorization: Bearer adk_...' with an agent API key")
    key = (
        await session.execute(
            select(AgentApiKey).where(
                AgentApiKey.key_hash == hash_token(raw), AgentApiKey.revoked_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    agent = await session.get(Agent, key.agent_id) if key else None
    org = await session.get(Organization, key.org_id) if key else None
    if key is None or agent is None or agent.deleted_at or org is None or org.deleted_at:
        raise _unauthorized("The API key is unknown or has been revoked")
    if org.suspended_at is not None:
        raise Problem(403, "Organisation is suspended", type_="urn:agentdesk:org-suspended")
    limit = await check_rate_limit(f"rl:service:{key.id}", RATE_LIMIT_PER_MIN, 60)
    if not limit.allowed:
        raise Problem(429, "Rate limit exceeded", headers={**limit.headers(), "Retry-After": "60"})
    key.last_used_at = datetime.now(UTC)
    return key, agent, org


@router.post("/service/chat", response_model=None)
async def service_chat(
    body: ServiceChatIn, request: Request, session: AsyncSession = Depends(get_db)
) -> dict[str, Any] | StreamingResponse:
    """Send a message to the agent's latest published version. `blocking` returns JSON; `streaming`
    returns SSE (`conversation`, `route`, `token`, `citations`, `usage`, `done`, `error`)."""
    key, agent, org = await _authenticate(request, session)
    if agent.latest_version_id is None:
        raise Problem(409, "Publish the agent before calling it through the Service API")
    cfg, version_id = await load_agent_config(session, agent, agent.latest_version_id)
    flags = {"api_user": body.user, "api_key_id": str(key.id)}
    if body.conversation_id is not None:
        existing = (
            await session.execute(
                select(Conversation).where(
                    Conversation.id == body.conversation_id,
                    Conversation.org_id == org.id,
                    Conversation.agent_id == agent.id,
                    Conversation.channel == "api",
                )
            )
        ).scalar_one_or_none()
        if existing is None or (existing.flags or {}).get("api_user") != body.user:
            raise Problem(404, "Conversation not found")
        # returns a fresh conversation if the old one timed out (memory policy)
        conv = await get_or_create_conversation(
            session,
            org_id=org.id,
            agent_id=agent.id,
            channel="api",
            cfg=cfg.memory,
            agent_version_id=version_id,
            existing_id=existing.id,
        )
        conv.flags = {**flags, **(conv.flags or {})}
    else:
        conv = Conversation(
            org_id=org.id, agent_id=agent.id, agent_version_id=version_id, channel="api", flags=flags
        )
        session.add(conv)
        await session.flush()
    run_ctx = await prepare_run(
        session,
        org=org,
        agent=agent,
        cfg=cfg,
        channel="api",
        conversation=conv,
        user_text=body.message,
        agent_version_id=version_id,
    )
    await session.commit()

    if body.response_mode == "streaming":

        async def gen() -> AsyncIterator[str]:
            yield _sse({"event": "conversation", "data": {"conversation_id": str(conv.id)}})
            async for ev in stream_run(run_ctx, body.message):
                yield _sse(ev)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    outcome = await run_once(run_ctx, body.message)
    if outcome.error:
        raise Problem(502, f"The agent could not answer: {outcome.error}")
    state = outcome.state
    usage = state.get("usage") or []
    return {
        "conversation_id": conv.id,
        "message_id": outcome.message_id,
        "answer": state.get("answer", ""),
        "citations": state.get("citations") or [],
        "route": state.get("route"),
        "no_answer": bool(state.get("no_answer")),
        "latency_ms": outcome.latency_ms,
        "usage": {
            "input_tokens": sum(int(u.get("input_tokens") or 0) for u in usage),
            "output_tokens": sum(int(u.get("output_tokens") or 0) for u in usage),
        },
    }
