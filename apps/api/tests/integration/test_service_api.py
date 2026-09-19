"""Service API: per-agent keys, blocking/streaming chat, conversation scoping, revocation, RBAC."""

from __future__ import annotations

import httpx
import pytest

from tests.integration.conftest import make_actor, make_org, new_client

pytestmark = pytest.mark.asyncio


async def _published_agent(actor, org: str) -> str:
    agent = (await actor.post(f"/v1/orgs/{org}/agents", json={"template_key": "blank", "name": "Svc"})).json()
    r = await actor.post(f"/v1/orgs/{org}/agents/{agent['id']}/publish", json={"changelog": "v1"})
    assert r.status_code in (200, 201), r.text
    return agent["id"]


async def test_service_api_lifecycle(client: httpx.AsyncClient) -> None:
    owner = await make_actor(client)
    org = await make_org(owner, "Svc Org")
    agent = await _published_agent(owner, org)

    r = await owner.post(f"/v1/orgs/{org}/agents/{agent}/api-keys", json={"name": "backend"})
    assert r.status_code == 201, r.text
    raw, key_id = r.json()["key"], r.json()["id"]
    assert raw.startswith("adk_") and r.json()["last4"] == raw[-4:]
    listed = (await owner.get(f"/v1/orgs/{org}/agents/{agent}/api-keys")).json()["items"]
    assert [k["id"] for k in listed] == [key_id] and "key" not in listed[0]

    async with new_client() as api:
        auth = {"Authorization": f"Bearer {raw}"}
        assert (await api.post("/v1/service/chat", json={"message": "hi"})).status_code == 401
        bad = await api.post(
            "/v1/service/chat", json={"message": "hi"}, headers={"Authorization": "Bearer adk_x"}
        )
        assert bad.status_code == 401

        first = await api.post("/v1/service/chat", json={"message": "hello", "user": "u1"}, headers=auth)
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["answer"] and body["conversation_id"] and body["message_id"]

        again = await api.post(
            "/v1/service/chat",
            json={"message": "and again", "user": "u1", "conversation_id": body["conversation_id"]},
            headers=auth,
        )
        assert again.status_code == 200 and again.json()["conversation_id"] == body["conversation_id"]

        other_user = await api.post(
            "/v1/service/chat",
            json={"message": "peek", "user": "u2", "conversation_id": body["conversation_id"]},
            headers=auth,
        )
        assert other_user.status_code == 404

        async with api.stream(
            "POST",
            "/v1/service/chat",
            json={"message": "stream it", "response_mode": "streaming"},
            headers=auth,
        ) as s:
            assert s.status_code == 200
            text = "".join([chunk async for chunk in s.aiter_text()])
        assert text.startswith("event: conversation") and "event: done" in text

    convs = (await owner.get(f"/v1/orgs/{org}/conversations")).json()["items"]
    assert any(c.get("channel") == "api" for c in convs)
    actions = [a["action"] for a in (await owner.get(f"/v1/orgs/{org}/audit-logs")).json()["items"]]
    assert "api_key.created" in actions

    assert (await owner.delete(f"/v1/orgs/{org}/agents/{agent}/api-keys/{key_id}")).status_code == 204
    async with new_client() as api:
        r = await api.post(
            "/v1/service/chat", json={"message": "hi"}, headers={"Authorization": f"Bearer {raw}"}
        )
        assert r.status_code == 401


async def test_unpublished_agent_and_rbac(client: httpx.AsyncClient) -> None:
    owner = await make_actor(client)
    org = await make_org(owner, "Svc Org 2")
    draft = (
        await owner.post(f"/v1/orgs/{org}/agents", json={"template_key": "blank", "name": "Draft"})
    ).json()
    raw = (await owner.post(f"/v1/orgs/{org}/agents/{draft['id']}/api-keys", json={"name": "k"})).json()[
        "key"
    ]
    async with new_client() as api:
        r = await api.post(
            "/v1/service/chat", json={"message": "hi"}, headers={"Authorization": f"Bearer {raw}"}
        )
        assert r.status_code == 409

    intruder = await make_actor(client)
    await make_org(intruder, "Other")
    r = await intruder.get(f"/v1/orgs/{org}/agents/{draft['id']}/api-keys")
    assert r.status_code == 404
