"""WhatsApp worker: runs the published agent for an inbound message and replies via the Graph API
(the Graph client is faked; everything else is real)."""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
import pytest
from app.db.models import Contact, Deployment, Message
from app.db.session import get_sessionmaker
from app.deployments.whatsapp import processing
from app.deployments.whatsapp.client import WhatsAppClient, WhatsAppError
from sqlalchemy import select

from tests.integration.test_webhook import _deployment

pytestmark = pytest.mark.asyncio
WA_ID = "919999922222"


@pytest.fixture
def graph(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"sent": [], "read": [], "fail": None}

    async def send_text(self: WhatsAppClient, to: str, body: str, reply_to: str | None = None) -> str:
        if state["fail"]:
            raise WhatsAppError(state["fail"], "fake failure", status=401)
        state["sent"].append((to, body))
        return f"wamid.out.{uuid.uuid4().hex}"

    async def mark_read(self: WhatsAppClient, message_id: str, typing: bool = True) -> None:
        state["read"].append(message_id)

    async def get_phone_info(self: WhatsAppClient) -> dict[str, Any]:
        return {"display_phone_number": "+1 555 0100", "verified_name": "Test Co", "quality_rating": "GREEN"}

    monkeypatch.setattr(WhatsAppClient, "get_phone_info", get_phone_info)
    monkeypatch.setattr(WhatsAppClient, "send_text", send_text)
    monkeypatch.setattr(WhatsAppClient, "mark_read", mark_read)
    return state


async def _live_deployment(client: httpx.AsyncClient) -> str:
    owner, org, dep = await _deployment(client)
    r = await client.get(
        f"/webhooks/whatsapp/{dep['id']}",
        params={"hub.mode": "subscribe", "hub.verify_token": dep["verify_token"], "hub.challenge": "1"},
    )
    assert r.status_code == 200
    r = await owner.post(f"/v1/orgs/{org}/deployments/{dep['id']}/verify")  # credentials check -> live
    assert r.status_code == 200 and r.json()["status"] == "live", r.text
    return dep["id"]


def _msg(text: str | None = "hello", mtype: str = "text") -> dict[str, Any]:
    m: dict[str, Any] = {
        "from": WA_ID,
        "id": f"wamid.in.{uuid.uuid4().hex}",
        "timestamp": str(int(time.time())),
        "type": mtype,
    }
    if text is not None:
        m["text"] = {"body": text}
    return m


CONTACT = {"wa_id": WA_ID, "profile": {"name": "Parent"}}


async def test_text_message_gets_an_answer(client: httpx.AsyncClient, graph: dict[str, Any]) -> None:
    dep_id = await _live_deployment(client)
    inbound = _msg("hi there")
    result = await processing.process_whatsapp_message(dep_id, inbound, CONTACT)
    assert result.get("sent", 0) >= 1, result
    assert graph["read"] == [inbound["id"]]
    assert graph["sent"] and graph["sent"][-1][0] == WA_ID and graph["sent"][-1][1]

    async with get_sessionmaker()() as session:
        contact = (await session.execute(select(Contact).where(Contact.wa_id == WA_ID))).scalars().first()
        assert contact is not None and contact.profile_name == "Parent"
        msgs = (
            (
                await session.execute(
                    select(Message).where(Message.org_id == contact.org_id).order_by(Message.created_at)
                )
            )
            .scalars()
            .all()
        )
        assert [m.role for m in msgs][-2:] == ["user", "assistant"]
        assert msgs[-1].status == "sent" and msgs[-1].external_id.startswith("wamid.out")


async def test_reset_and_unsupported_types(client: httpx.AsyncClient, graph: dict[str, Any]) -> None:
    dep_id = await _live_deployment(client)
    assert (await processing.process_whatsapp_message(dep_id, _msg("/reset"), CONTACT)) == {"reset": True}
    assert graph["sent"][-1][1] == "Starting fresh 👋"
    r = await processing.process_whatsapp_message(dep_id, _msg(None, mtype="image"), CONTACT)
    assert r == {"skipped": "unsupported_type"}
    assert "only read text" in graph["sent"][-1][1]


async def test_expired_token_marks_deployment_error(client: httpx.AsyncClient, graph: dict[str, Any]) -> None:
    dep_id = await _live_deployment(client)
    graph["fail"] = "token_invalid"
    r = await processing.process_whatsapp_message(dep_id, _msg("hello"), CONTACT)
    assert r.get("error") == "token_invalid"
    async with get_sessionmaker()() as session:
        dep = await session.get(Deployment, uuid.UUID(dep_id))
        assert dep is not None and dep.status == "error" and "token" in (dep.last_error or "")
    # paused/errored deployments don't run the agent at all
    assert (await processing.process_whatsapp_message(dep_id, _msg("again"), CONTACT))["skipped"].startswith(
        "deployment"
    )
