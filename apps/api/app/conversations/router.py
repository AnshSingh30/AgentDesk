"""Conversations list, transcript, export (PRD §6.13, §16.7)."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import OrgContext, get_db, require_membership
from app.core.pagination import keyset_page
from app.db.models import Agent, Contact, Conversation, Feedback, Message
from app.db.repo import get_scoped, scoped
from app.deployments.router import mask_phone

router = APIRouter(tags=["conversations"])


def _apply_filters(
    stmt: Any,
    agent_id: uuid.UUID | None,
    deployment_id: uuid.UUID | None,
    channel: str | None,
    flag: str | None,
    from_: datetime | None,
    to: datetime | None,
) -> Any:
    if agent_id:
        stmt = stmt.where(Conversation.agent_id == agent_id)
    if deployment_id:
        stmt = stmt.where(Conversation.deployment_id == deployment_id)
    if channel:
        stmt = stmt.where(Conversation.channel == channel)
    if flag in ("has_error", "no_answer", "sql_failed", "negative_feedback"):
        stmt = stmt.where(Conversation.flags[flag].astext == "true")
    if flag and flag.startswith("tool:"):
        stmt = stmt.where(Conversation.flags["tools"].astext.contains(flag[5:]))
    if from_:
        stmt = stmt.where(Conversation.last_message_at >= from_)
    if to:
        stmt = stmt.where(Conversation.last_message_at <= to)
    return stmt


async def _conv_out(session: AsyncSession, c: Conversation, mask: bool) -> dict[str, Any]:
    contact = await session.get(Contact, c.contact_id) if c.contact_id else None
    agent = await session.get(Agent, c.agent_id)
    return {
        "id": c.id,
        "agent_id": c.agent_id,
        "agent_name": agent.name if agent else None,
        "deployment_id": c.deployment_id,
        "channel": c.channel,
        "contact": (
            {
                "id": contact.id,
                "wa_id": mask_phone(contact.wa_id) if mask else contact.wa_id,
                "profile_name": contact.profile_name,
            }
            if contact
            else None
        ),
        "started_at": c.started_at,
        "last_message_at": c.last_message_at,
        "closed_at": c.closed_at,
        "message_count": c.message_count,
        "flags": c.flags or {},
        "created_at": c.created_at,
    }


@router.get("/orgs/{org_id}/conversations")
async def list_conversations(
    agent_id: uuid.UUID | None = None,
    deployment_id: uuid.UUID | None = None,
    channel: str | None = None,
    flag: str | None = None,
    from_: datetime | None = None,
    to: datetime | None = None,
    limit: int = 50,
    cursor: str | None = None,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    stmt = _apply_filters(scoped(Conversation, ctx.org_id), agent_id, deployment_id, channel, flag, from_, to)
    rows, next_cursor = await keyset_page(session, stmt, Conversation, limit, cursor)
    mask = not ctx.has_role("editor")
    return {"items": [await _conv_out(session, c, mask) for c in rows], "next_cursor": next_cursor}


@router.get("/orgs/{org_id}/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    c = await get_scoped(session, Conversation, ctx.org_id, conversation_id, what="Conversation")
    msgs = (
        (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == c.id)
                .order_by(Message.created_at, Message.id)
            )
        )
        .scalars()
        .all()
    )
    fb_rows = (
        (await session.execute(select(Feedback).where(Feedback.message_id.in_([m.id for m in msgs]))))
        .scalars()
        .all()
        if msgs
        else []
    )
    fb = {f.message_id: {"rating": f.rating, "comment": f.comment} for f in fb_rows}
    out = await _conv_out(session, c, not ctx.has_role("editor"))
    out["messages"] = [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "status": m.status,
            "route": m.route,
            "tool_summary": m.tool_summary,
            "citations": m.citations,
            "guardrail_events": m.guardrail_events,
            "latency_ms": m.latency_ms,
            "error": m.error,
            "run_id": m.run_id,
            "feedback": fb.get(m.id),
            "created_at": m.created_at,
        }
        for m in msgs
    ]
    return out


@router.get("/orgs/{org_id}/conversations/export.csv")
async def export_csv(
    agent_id: uuid.UUID | None = None,
    deployment_id: uuid.UUID | None = None,
    from_: datetime | None = None,
    to: datetime | None = None,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    stmt = (
        _apply_filters(scoped(Conversation, ctx.org_id), agent_id, deployment_id, None, None, from_, to)
        .order_by(Conversation.last_message_at.desc())
        .limit(5000)
    )
    convs = (await session.execute(stmt)).scalars().all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "conversation_id",
            "channel",
            "agent_id",
            "contact",
            "message_id",
            "role",
            "content",
            "route",
            "latency_ms",
            "error",
            "created_at",
        ]
    )
    for c in convs:
        contact = await session.get(Contact, c.contact_id) if c.contact_id else None
        msgs = (
            (
                await session.execute(
                    select(Message).where(Message.conversation_id == c.id).order_by(Message.created_at)
                )
            )
            .scalars()
            .all()
        )
        for m in msgs:
            w.writerow(
                [
                    c.id,
                    c.channel,
                    c.agent_id,
                    contact.wa_id if contact else "",
                    m.id,
                    m.role,
                    m.content,
                    ",".join((m.route or {}).get("tools", [])),
                    m.latency_ms or "",
                    m.error or "",
                    m.created_at.isoformat(),
                ]
            )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="conversations.csv"'},
    )
