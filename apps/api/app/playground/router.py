"""Playground: SSE streaming runs, SQL confirmation, cancel, reset, feedback, traces (PRD §6.11, §16.4)."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.schemas import AgentConfig
from app.core.deps import OrgContext, get_db, require_membership
from app.core.errors import Problem
from app.core.rate_limit import rate_limited
from app.core.redis import get_redis
from app.db.models import Agent, Conversation, EvalExample, Feedback, Message, Organization, RunTrace
from app.db.repo import get_scoped
from app.memory.history import get_or_create_conversation, reset_conversation
from app.runtime.runner import load_agent_config, prepare_run, stream_run

log = structlog.get_logger()
router = APIRouter(tags=["playground"])
playground_limit = rate_limited("playground", 30, 60, by="user")


class SessionCreate(BaseModel):
    version_id: uuid.UUID | None = None


class MessageIn(BaseModel):
    message: str = Field(min_length=1, max_length=10000)
    confirm_sql: bool = False


class SqlConfirmIn(BaseModel):
    run_id: str = Field(min_length=8, max_length=40)
    action: str = Field(pattern="^(run|edit|cancel)$")
    sql: str | None = Field(default=None, max_length=20000)


class CancelIn(BaseModel):
    run_id: str = Field(min_length=8, max_length=40)


class FormatIn(BaseModel):
    text: str = Field(max_length=20000)
    citations: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/orgs/{org_id}/playground/format-whatsapp")
async def format_whatsapp(
    body: FormatIn, ctx: OrgContext = Depends(require_membership("viewer"))
) -> dict[str, Any]:
    """WhatsApp preview mode (PG-4): render Markdown with WhatsApp formatting rules and the 4,096-char split."""
    from app.runtime.formatter import format_for_whatsapp

    return {"chunks": format_for_whatsapp(body.text, body.citations)}


class FeedbackIn(BaseModel):
    rating: int = Field(ge=-1, le=1)
    comment: str | None = Field(default=None, max_length=2000)
    save_as_eval: bool = False


def _sse(event: dict[str, Any]) -> str:
    return f"event: {event['event']}\ndata: {json.dumps(event['data'], default=str)}\n\n"


async def _session(session: AsyncSession, ctx: OrgContext, sid: uuid.UUID) -> Conversation:
    conv = (
        await session.execute(
            select(Conversation).where(
                Conversation.id == sid,
                Conversation.org_id == ctx.org_id,
                Conversation.channel == "playground",
            )
        )
    ).scalar_one_or_none()
    if conv is None:
        raise Problem(404, "Session not found")
    if conv.playground_user_id != ctx.user.id and not ctx.has_role("admin"):
        raise Problem(404, "Session not found")
    return conv


@router.post("/orgs/{org_id}/agents/{agent_id}/playground/sessions", status_code=201)
async def create_session(
    agent_id: str,
    body: SessionCreate,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    agent = await get_scoped(session, Agent, ctx.org_id, agent_id, what="Agent")
    try:
        cfg, version_id = await load_agent_config(session, agent, body.version_id)
    except ValueError as e:
        raise Problem(404, "Version not found") from e
    conv = Conversation(
        org_id=ctx.org_id,
        agent_id=agent.id,
        agent_version_id=version_id,
        channel="playground",
        playground_user_id=ctx.user.id,
        flags={},
    )
    session.add(conv)
    await session.flush()
    return {
        "session_id": conv.id,
        "agent_id": agent.id,
        "version_id": version_id,
        "starter_questions": cfg.instructions.starter_questions,
        "tools": cfg.tools.enabled_names(),
        "confirm_sql_available": cfg.tools.sql.enabled,
    }


@router.post(
    "/orgs/{org_id}/playground/sessions/{session_id}/messages", dependencies=[Depends(playground_limit)]
)
async def send_message(
    session_id: uuid.UUID,
    body: MessageIn,
    request: Request,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    conv = await _session(session, ctx, session_id)
    if conv.closed_at is not None:
        raise Problem(409, "This session was reset; start a new one")
    agent = await get_scoped(session, Agent, ctx.org_id, conv.agent_id, what="Agent")
    cfg, version_id = await load_agent_config(session, agent, conv.agent_version_id)
    org = await session.get(Organization, ctx.org_id)
    assert org is not None
    conv = await get_or_create_conversation(
        session,
        org_id=ctx.org_id,
        agent_id=agent.id,
        channel="playground",
        cfg=cfg.memory,
        playground_user_id=ctx.user.id,
        agent_version_id=version_id,
        existing_id=conv.id,
    )
    run_ctx = await prepare_run(
        session,
        org=org,
        agent=agent,
        cfg=cfg,
        channel="playground",
        conversation=conv,
        user_text=body.message,
        agent_version_id=version_id,
        confirm_sql=body.confirm_sql and cfg.tools.sql.enabled,
    )
    await session.commit()
    await get_redis().setex(
        f"run:pending:{run_ctx.run_id}",
        3600,
        json.dumps(
            {
                "org_id": str(ctx.org_id),
                "agent_id": str(agent.id),
                "session_id": str(conv.id),
                "version_id": str(version_id) if version_id else None,
                "user_text": body.message,
                "user_id": str(ctx.user.id),
            }
        ),
    )

    async def gen() -> AsyncIterator[str]:
        yield _sse({"event": "run", "data": {"run_id": run_ctx.run_id, "session_id": str(conv.id)}})
        async for ev in stream_run(run_ctx, body.message):
            if await request.is_disconnected():
                await get_redis().setex(f"run:cancel:{run_ctx.run_id}", 300, "1")
            yield _sse(ev)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/orgs/{org_id}/playground/sessions/{session_id}/sql-confirm")
async def sql_confirm(
    session_id: uuid.UUID,
    body: SqlConfirmIn,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    raw = await get_redis().get(f"run:pending:{body.run_id}")
    if not raw:
        raise Problem(404, "Run not found or expired")
    pending = json.loads(raw)
    if pending["org_id"] != str(ctx.org_id) or pending["session_id"] != str(session_id):
        raise Problem(404, "Run not found")
    conv = await _session(session, ctx, session_id)
    agent = await get_scoped(session, Agent, ctx.org_id, conv.agent_id, what="Agent")
    version_id = uuid.UUID(pending["version_id"]) if pending.get("version_id") else None
    cfg, version_id = await load_agent_config(session, agent, version_id)
    org = await session.get(Organization, ctx.org_id)
    assert org is not None
    run_ctx = await prepare_run(
        session,
        org=org,
        agent=agent,
        cfg=cfg,
        channel="playground",
        conversation=conv,
        user_text=pending["user_text"],
        agent_version_id=version_id,
        run_id=body.run_id,
        confirm_sql=True,
        store_user_message=False,
    )
    await session.commit()
    resume = {"action": body.action, "sql": body.sql}

    async def gen() -> AsyncIterator[str]:
        async for ev in stream_run(run_ctx, pending["user_text"], resume=resume):
            yield _sse(ev)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/orgs/{org_id}/playground/sessions/{session_id}/cancel", status_code=202)
async def cancel_run(
    session_id: uuid.UUID,
    body: CancelIn,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    await _session(session, ctx, session_id)
    await get_redis().setex(f"run:cancel:{body.run_id}", 300, "1")
    return {"status": "cancelling"}


@router.post("/orgs/{org_id}/playground/sessions/{session_id}/reset")
async def reset_session(
    session_id: uuid.UUID,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    conv = await _session(session, ctx, session_id)
    await reset_conversation(session, conv)
    new = Conversation(
        org_id=ctx.org_id,
        agent_id=conv.agent_id,
        agent_version_id=conv.agent_version_id,
        channel="playground",
        playground_user_id=ctx.user.id,
        flags={},
    )
    session.add(new)
    await session.flush()
    return {"session_id": new.id, "agent_id": new.agent_id, "version_id": new.agent_version_id}


@router.get("/orgs/{org_id}/playground/sessions/{session_id}/messages")
async def session_messages(
    session_id: uuid.UUID,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    conv = await _session(session, ctx, session_id)
    rows = (
        (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conv.id)
                .order_by(Message.created_at, Message.id)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "route": m.route,
            "citations": m.citations,
            "latency_ms": m.latency_ms,
            "error": m.error,
            "run_id": m.run_id,
            "created_at": m.created_at,
        }
        for m in rows
    ]


@router.post("/orgs/{org_id}/messages/{message_id}/feedback", status_code=201)
async def feedback(
    message_id: uuid.UUID,
    body: FeedbackIn,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    msg = await get_scoped(session, Message, ctx.org_id, message_id, what="Message")
    fb = Feedback(
        org_id=ctx.org_id,
        message_id=msg.id,
        user_id=ctx.user.id,
        rating=body.rating,
        comment=body.comment,
        saved_as_eval=body.save_as_eval,
    )
    session.add(fb)
    if body.save_as_eval:
        conv = await session.get(Conversation, msg.conversation_id)
        prev = (
            (
                await session.execute(
                    select(Message)
                    .where(
                        Message.conversation_id == msg.conversation_id,
                        Message.role == "user",
                        Message.created_at <= msg.created_at,
                    )
                    .order_by(Message.created_at.desc())
                )
            )
            .scalars()
            .first()
        )
        if conv and prev:
            session.add(
                EvalExample(
                    org_id=ctx.org_id,
                    agent_id=conv.agent_id,
                    question=prev.content,
                    expected_answer=msg.content,
                    expected_route=(msg.route or {}).get("tools"),
                    notes=body.comment,
                )
            )
    await session.flush()
    return {"id": fb.id, "rating": fb.rating}


@router.get("/orgs/{org_id}/messages/{message_id}/trace")
async def trace(
    message_id: uuid.UUID,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    msg = await get_scoped(session, Message, ctx.org_id, message_id, what="Message")
    tr = (await session.execute(select(RunTrace).where(RunTrace.message_id == msg.id))).scalar_one_or_none()
    return {
        "message_id": msg.id,
        "run_id": msg.run_id,
        "route": msg.route,
        "citations": msg.citations,
        "guardrail_events": msg.guardrail_events,
        "latency_ms": msg.latency_ms,
        "error": msg.error,
        "tool_summary": msg.tool_summary,
        "trace": {
            "node_timings": tr.node_timings,
            "rag_chunks": tr.rag_chunks,
            "sql_attempts": tr.sql_attempts,
            "web_results": tr.web_results,
            "usage": tr.usage,
            "langsmith_run_url": tr.langsmith_run_url
            if (ctx.user.is_superadmin or ctx.has_role("admin"))
            else None,
        }
        if tr
        else None,
        "created_at": msg.created_at,
    }


__all__ = ["AgentConfig", "datetime", "router"]
