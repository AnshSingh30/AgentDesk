"""Memory: window, /reset, session timeout, history loading (PRD §13.4, §20.2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.agents.schemas import MemoryCfg
from app.db.models import Agent, Conversation, Message, Organization, User
from app.db.session import get_sessionmaker
from app.memory.history import get_or_create_conversation, load_history, reset_conversation

pytestmark = pytest.mark.asyncio


async def _fixture() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    async with get_sessionmaker()() as session:
        user = User(
            email=f"mem-{uuid.uuid4().hex[:8]}@example.com", name="M", email_verified_at=datetime.now(UTC)
        )
        org = Organization(name="Mem Org", slug=f"mem-{uuid.uuid4().hex[:6]}")
        session.add_all([user, org])
        await session.flush()
        agent = Agent(org_id=org.id, name="A", draft_config={})
        session.add(agent)
        await session.commit()
        return org.id, agent.id, user.id


async def test_window_and_reset_and_timeout() -> None:
    org_id, agent_id, user_id = await _fixture()
    cfg = MemoryCfg(max_turns=10, history_token_budget=8000, session_timeout_minutes=60)
    async with get_sessionmaker()() as session:
        conv = await get_or_create_conversation(
            session,
            org_id=org_id,
            agent_id=agent_id,
            channel="playground",
            cfg=cfg,
            playground_user_id=user_id,
        )
        for i in range(15):
            session.add(
                Message(
                    org_id=org_id,
                    conversation_id=conv.id,
                    role="user",
                    content=f"q{i}",
                    created_at=datetime.now(UTC) + timedelta(seconds=i * 2),
                )
            )
            session.add(
                Message(
                    org_id=org_id,
                    conversation_id=conv.id,
                    role="assistant",
                    content=f"a{i}",
                    tool_summary="SQL: 3 rows" if i == 14 else None,
                    created_at=datetime.now(UTC) + timedelta(seconds=i * 2 + 1),
                )
            )
        await session.commit()
        hist = await load_history(session, conv.id, cfg)
        assert len(hist) == 20 and hist[0]["content"] == "q5" and hist[-1]["content"].startswith("a14")
        assert "(SQL: 3 rows)" in hist[-1]["content"]
        # same user + agent -> same open conversation
        again = await get_or_create_conversation(
            session,
            org_id=org_id,
            agent_id=agent_id,
            channel="playground",
            cfg=cfg,
            playground_user_id=user_id,
        )
        assert again.id == conv.id
        # /reset closes it
        await reset_conversation(session, conv)
        await session.commit()
        fresh = await get_or_create_conversation(
            session,
            org_id=org_id,
            agent_id=agent_id,
            channel="playground",
            cfg=cfg,
            playground_user_id=user_id,
        )
        assert fresh.id != conv.id and await load_history(session, fresh.id, cfg) == []
        # session timeout -> new conversation
        fresh.last_message_at = datetime.now(UTC) - timedelta(hours=2)
        await session.commit()
        newer = await get_or_create_conversation(
            session,
            org_id=org_id,
            agent_id=agent_id,
            channel="playground",
            cfg=cfg,
            playground_user_id=user_id,
        )
        assert newer.id != fresh.id
        closed = await session.get(Conversation, fresh.id)
        assert (
            closed is not None
            and closed.closed_at is not None
            and closed.flags.get("closed_reason") == "session_timeout"
        )
