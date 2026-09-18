"""WhatsApp webhook: verify handshake, signatures, dedupe, paused deployment, non-text, malformed (PRD §20.2)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from typing import Any

import httpx
import pytest
from app.deployments.whatsapp import webhook as wh

from tests.integration.conftest import make_actor, make_org

pytestmark = pytest.mark.asyncio


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    async def fake_enqueue(function: str, *args: Any, **kwargs: Any) -> str:
        calls.append((function, *args))
        return "job-1"

    monkeypatch.setattr(wh, "enqueue", fake_enqueue)
    return calls


async def _deployment(client: httpx.AsyncClient) -> tuple[Any, str, dict[str, Any]]:
    owner = await make_actor(client)
    org = await make_org(owner)
    agent = (
        await owner.post(f"/v1/orgs/{org}/agents", json={"template_key": "blank", "name": "Bot"})
    ).json()["id"]
    assert (await owner.post(f"/v1/orgs/{org}/agents/{agent}/publish", json={})).status_code == 201
    r = await owner.post(
        f"/v1/orgs/{org}/deployments",
        json={
            "agent_id": agent,
            "phone_number_id": f"10{int(time.time() * 1000)}",
            "waba_id": "9876543210",
            "access_token": "EAAG" + "x" * 40,
            "app_secret": "app-secret-123",
        },
    )
    assert r.status_code == 201, r.text
    return owner, org, r.json()


def _sign(body: bytes, secret: str = "app-secret-123") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _payload(wamid: str, text: str = "hello", ts: int | None = None, mtype: str = "text") -> dict[str, Any]:
    msg: dict[str, Any] = {
        "from": "919999911111",
        "id": wamid,
        "timestamp": str(ts or int(time.time())),
        "type": mtype,
    }
    if mtype == "text":
        msg["text"] = {"body": text}
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "contacts": [{"wa_id": "919999911111", "profile": {"name": "Tester"}}],
                            "messages": [msg],
                        },
                    }
                ],
            }
        ],
    }


async def test_verify_handshake(client: httpx.AsyncClient) -> None:
    owner, org, dep = await _deployment(client)
    url = f"/webhooks/whatsapp/{dep['id']}"
    r = await client.get(
        url,
        params={"hub.mode": "subscribe", "hub.verify_token": dep["verify_token"], "hub.challenge": "12345"},
    )
    assert r.status_code == 200 and r.text == "12345"
    r = await client.get(
        url, params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"}
    )
    assert r.status_code == 403
    r = await owner.get(f"/v1/orgs/{org}/deployments/{dep['id']}")
    assert r.json()["webhook_verified_at"] is not None


async def test_signed_messages_are_queued_once(client: httpx.AsyncClient, queued: list) -> None:
    owner, org, dep = await _deployment(client)
    await client.get(
        f"/webhooks/whatsapp/{dep['id']}",
        params={"hub.mode": "subscribe", "hub.verify_token": dep["verify_token"], "hub.challenge": "1"},
    )
    body = json.dumps(_payload(f"wamid.{time.time()}")).encode()
    url = f"/webhooks/whatsapp/{dep['id']}"
    r = await client.post(
        url, content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body)}
    )
    assert r.status_code == 200 and len(queued) == 1 and queued[0][0] == "process_whatsapp_message"
    r = await client.post(
        url, content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body)}
    )
    assert r.status_code == 200 and len(queued) == 1  # duplicate wamid ignored


async def test_bad_signature_rejected(client: httpx.AsyncClient, queued: list) -> None:
    _, _, dep = await _deployment(client)
    body = json.dumps(_payload(f"wamid.sig.{uuid.uuid4().hex}")).encode()
    r = await client.post(
        f"/webhooks/whatsapp/{dep['id']}", content=body, headers={"X-Hub-Signature-256": _sign(body, "other")}
    )
    assert r.status_code == 401 and not queued
    r = await client.post(f"/webhooks/whatsapp/{dep['id']}", content=body)
    assert r.status_code == 401


async def test_paused_deployment_drops_and_malformed_is_200(client: httpx.AsyncClient, queued: list) -> None:
    owner, org, dep = await _deployment(client)
    await owner.patch(f"/v1/orgs/{org}/deployments/{dep['id']}", json={"status": "paused"})
    body = json.dumps(_payload(f"wamid.paused.{uuid.uuid4().hex}")).encode()
    r = await client.post(
        f"/webhooks/whatsapp/{dep['id']}", content=body, headers={"X-Hub-Signature-256": _sign(body)}
    )
    assert r.status_code == 200 and not queued
    await owner.patch(
        f"/v1/orgs/{org}/deployments/{dep['id']}", json={"rate_limits": {"contact_per_minute": 5}}
    )
    bad = b"{not json"
    r = await client.post(
        f"/webhooks/whatsapp/{dep['id']}", content=bad, headers={"X-Hub-Signature-256": _sign(bad)}
    )
    assert r.status_code == 200
    r = await client.post(
        "/webhooks/whatsapp/not-a-uuid", content=body, headers={"X-Hub-Signature-256": _sign(body)}
    )
    assert r.status_code == 200


async def test_stale_and_non_text_messages(client: httpx.AsyncClient, queued: list) -> None:
    _, _, dep = await _deployment(client)
    stale = json.dumps(_payload(f"wamid.stale.{uuid.uuid4().hex}", ts=int(time.time()) - 3600)).encode()
    r = await client.post(
        f"/webhooks/whatsapp/{dep['id']}", content=stale, headers={"X-Hub-Signature-256": _sign(stale)}
    )
    assert r.status_code == 200 and not queued
    image = json.dumps(_payload(f"wamid.img.{uuid.uuid4().hex}", mtype="image")).encode()
    r = await client.post(
        f"/webhooks/whatsapp/{dep['id']}", content=image, headers={"X-Hub-Signature-256": _sign(image)}
    )
    assert r.status_code == 200 and len(queued) == 1  # worker replies with the unsupported-type message
