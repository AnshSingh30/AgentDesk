"""Annotation reply: curated answers are returned verbatim for similar questions, without the LLM."""

from __future__ import annotations

import httpx
import pytest

from tests.integration.conftest import make_actor, make_org, new_client

pytestmark = pytest.mark.asyncio
ANSWER = "Our office is open 9:00-17:00, Monday to Friday."


async def test_annotation_reply(client: httpx.AsyncClient) -> None:
    owner = await make_actor(client)
    org = await make_org(owner, "Ann Org")
    agent = (await owner.post(f"/v1/orgs/{org}/agents", json={"template_key": "blank", "name": "A"})).json()[
        "id"
    ]
    base = f"/v1/orgs/{org}/agents/{agent}/annotations"
    r = await owner.post(base, json={"question": "What are your office hours?", "answer": ANSWER})
    assert r.status_code == 201, r.text
    ann_id = r.json()["id"]
    assert (
        await owner.post(f"/v1/orgs/{org}/agents/{agent}/publish", json={"changelog": "v1"})
    ).status_code in (
        200,
        201,
    )
    key = (await owner.post(f"/v1/orgs/{org}/agents/{agent}/api-keys", json={"name": "t"})).json()["key"]

    async with new_client() as api:
        auth = {"Authorization": f"Bearer {key}"}
        for q in ("What are your office hours?", "what are your office hours"):
            hit = (await api.post("/v1/service/chat", json={"message": q}, headers=auth)).json()
            assert hit["answer"] == ANSWER, hit
            assert hit["route"]["reason"].startswith("annotation reply")
            assert hit["usage"]["input_tokens"] == 0  # no LLM call
        miss = (
            await api.post("/v1/service/chat", json={"message": "Tell me about pricing plans"}, headers=auth)
        ).json()
        assert miss["answer"] != ANSWER

    items = (await owner.get(base)).json()["items"]
    assert items[0]["hit_count"] == 2

    # disabling annotations in the draft + publishing turns the feature off
    cfg = (await owner.get(f"/v1/orgs/{org}/agents/{agent}")).json()
    etag = cfg.get("etag") or cfg.get("draft_updated_at")
    draft = cfg["draft_config"]
    draft["annotations"] = {"enabled": False, "threshold": 0.9}
    r = await owner.patch(
        f"/v1/orgs/{org}/agents/{agent}", json={"draft_config": draft}, headers={"If-Match": str(etag)}
    )
    assert r.status_code == 200, r.text
    await owner.post(f"/v1/orgs/{org}/agents/{agent}/publish", json={"changelog": "off"})
    async with new_client() as api:
        off = (
            await api.post(
                "/v1/service/chat",
                json={"message": "What are your office hours?"},
                headers={"Authorization": f"Bearer {key}"},
            )
        ).json()
        assert off["answer"] != ANSWER

    assert (await owner.patch(f"{base}/{ann_id}", json={"answer": "Changed"})).json()["answer"] == "Changed"
    assert (await owner.delete(f"{base}/{ann_id}")).status_code == 204
    assert (await owner.get(base)).json()["items"] == []

    intruder = await make_actor(client)
    await make_org(intruder, "Other")
    assert (await intruder.get(base)).status_code == 404
