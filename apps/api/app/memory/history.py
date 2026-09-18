"""Conversation memory: sessions, turn window, token budget trimming, /reset (PRD §13.4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.schemas import MemoryCfg
from app.db.models import Conversation, Message
from app.runtime.prompts import count_tokens


def trim_history(messages: list[dict[str, str]], max_turns: int, token_budget: int) -> list[dict[str, str]]:
    """Keep the last max_turns turns, then drop oldest turns until within the token budget."""
    if max_turns <= 0:
        return []
    msgs = messages[-(max_turns * 2) :]
    while len(msgs) > 2 and sum(count_tokens(m["content"]) + 4 for m in msgs) > token_budget:
        msgs = msgs[2:]
    if len(msgs) <= 2 and sum(count_tokens(m["content"]) + 4 for m in msgs) > token_budget:
        msgs = [{**m, "content": m["content"][: max(200, token_budget * 3)]} for m in msgs]
    return msgs


async def load_history(
    session: AsyncSession, conversation_id: uuid.UUID, cfg: MemoryCfg
) -> list[dict[str, str]]:
    if cfg.max_turns <= 0:
        return []
    rows = (
        (
            await session.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.role.in_(("user", "assistant")),
                    Message.error.is_(None),
                )
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(cfg.max_turns * 2 + 2)
            )
        )
        .scalars()
        .all()
    )
    rows = list(reversed(rows))
    out: list[dict[str, str]] = []
    for m in rows:
        content = m.content
        if m.role == "assistant" and m.tool_summary:
            content = f"{content}\n({m.tool_summary})"
        out.append({"role": m.role, "content": content})
    if out and out[-1]["role"] == "user":  # the in-flight message is stored before the run; exclude it
        out = out[:-1]
    if out and out[0]["role"] == "assistant":
        out = out[1:]
    return trim_history(out, cfg.max_turns, cfg.history_token_budget)


async def get_or_create_conversation(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    agent_id: uuid.UUID,
    channel: str,
    cfg: MemoryCfg,
    deployment_id: uuid.UUID | None = None,
    contact_id: uuid.UUID | None = None,
    agent_version_id: uuid.UUID | None = None,
    playground_user_id: uuid.UUID | None = None,
    existing_id: uuid.UUID | None = None,
) -> Conversation:
    now = datetime.now(UTC)
    stmt = select(Conversation).where(
        Conversation.org_id == org_id,
        Conversation.agent_id == agent_id,
        Conversation.channel == channel,
        Conversation.closed_at.is_(None),
    )
    if existing_id is not None:
        stmt = stmt.where(Conversation.id == existing_id)
    elif contact_id is not None:
        stmt = stmt.where(Conversation.contact_id == contact_id, Conversation.deployment_id == deployment_id)
    else:
        stmt = stmt.where(Conversation.playground_user_id == playground_user_id)
    conv = (await session.execute(stmt.order_by(Conversation.last_message_at.desc()))).scalars().first()
    if conv is not None and conv.last_message_at < now - timedelta(minutes=cfg.session_timeout_minutes):
        conv.closed_at = now
        conv.flags = {**(conv.flags or {}), "closed_reason": "session_timeout"}
        conv = None
    if conv is None:
        conv = Conversation(
            org_id=org_id,
            agent_id=agent_id,
            agent_version_id=agent_version_id,
            deployment_id=deployment_id,
            contact_id=contact_id,
            channel=channel,
            playground_user_id=playground_user_id,
            started_at=now,
            last_message_at=now,
            flags={},
        )
        session.add(conv)
        await session.flush()
    return conv


async def reset_conversation(session: AsyncSession, conv: Conversation) -> None:
    conv.closed_at = datetime.now(UTC)
    conv.flags = {**(conv.flags or {}), "closed_reason": "reset"}
