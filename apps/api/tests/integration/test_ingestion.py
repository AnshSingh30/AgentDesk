"""Knowledge base pipeline end to end: presigned upload -> confirm -> ingest -> retrieve -> delete."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.core.s3 import put_bytes
from app.knowledge import router as kb_router
from app.rag.ingestion import delete_document_fully, ingest_document

from tests.integration.conftest import make_actor, make_org

pytestmark = pytest.mark.asyncio

POLICY = b"""# Leave Policy

## Casual Leave

Employees get 12 casual leaves per calendar year. Casual leave cannot be carried forward.

## Sick Leave

Employees get 8 sick leaves per year. A medical certificate is needed after 2 consecutive days.

## Work From Home

Employees may work from home up to 2 days per week with manager approval.
"""


async def test_upload_ingest_search_delete(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    queued: list[tuple[Any, ...]] = []

    async def fake_enqueue(fn: str, *args: Any, **kw: Any) -> str:  # the test runs the job itself
        queued.append((fn, *args))
        return "job"

    monkeypatch.setattr(kb_router, "enqueue", fake_enqueue)
    owner = await make_actor(client)
    org = await make_org(owner, "KB Org")
    kb = (await owner.post(f"/v1/orgs/{org}/knowledge-bases", json={"name": "Policies"})).json()["id"]

    r = await owner.post(
        f"/v1/orgs/{org}/knowledge-bases/{kb}/uploads",
        json={"files": [{"name": "leave.md", "size": len(POLICY), "mime": "text/markdown"}]},
    )
    assert r.status_code in (200, 201), r.text
    up = r.json()["uploads"][0]
    await put_bytes(up["key"], POLICY, "text/markdown")  # what the browser's presigned PUT does
    r = await owner.post(
        f"/v1/orgs/{org}/knowledge-bases/{kb}/documents",
        json={"uploads": [{"document_id": up["document_id"], "name": "leave.md", "mime": "text/markdown"}]},
    )
    assert r.status_code == 201, r.text
    doc_id = r.json()[0]["id"]
    assert queued and queued[0][0] == "ingest_document"

    await ingest_document(doc_id)
    doc = (await owner.get(f"/v1/orgs/{org}/documents/{doc_id}")).json()
    assert doc["status"] == "ready" and doc["chunk_count"] > 0, doc
    chunks = (await owner.get(f"/v1/orgs/{org}/documents/{doc_id}/chunks")).json()
    assert chunks["count"] == doc["chunk_count"]

    hits = (
        await owner.post(
            f"/v1/orgs/{org}/knowledge-bases/{kb}/search", json={"query": "How many sick leaves per year?"}
        )
    ).json()["results"]
    assert hits and "8 sick leaves" in hits[0]["text"] and hits[0]["filename"] == "leave.md"

    # another org can't retrieve these chunks even with the same query
    other = await make_actor(client)
    org_b = await make_org(other, "Other KB Org")
    r = await other.post(f"/v1/orgs/{org_b}/knowledge-bases/{kb}/search", json={"query": "sick leaves"})
    assert r.status_code == 404

    await delete_document_fully(doc_id)
    hits = (
        await owner.post(f"/v1/orgs/{org}/knowledge-bases/{kb}/search", json={"query": "sick leaves"})
    ).json()["results"]
    assert hits == []


async def test_upload_rejects_mismatched_content(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_enqueue(fn: str, *args: Any, **kw: Any) -> str:
        return "job"

    monkeypatch.setattr(kb_router, "enqueue", fake_enqueue)
    owner = await make_actor(client)
    org = await make_org(owner, "KB Org 2")
    kb = (await owner.post(f"/v1/orgs/{org}/knowledge-bases", json={"name": "P"})).json()["id"]
    fake_pdf = b"MZ\x90\x00 this is really an executable"
    up = (
        await owner.post(
            f"/v1/orgs/{org}/knowledge-bases/{kb}/uploads",
            json={"files": [{"name": "policy.pdf", "size": len(fake_pdf), "mime": "application/pdf"}]},
        )
    ).json()["uploads"][0]
    await put_bytes(up["key"], fake_pdf, "application/pdf")
    r = await owner.post(
        f"/v1/orgs/{org}/knowledge-bases/{kb}/documents",
        json={
            "uploads": [{"document_id": up["document_id"], "name": "policy.pdf", "mime": "application/pdf"}]
        },
    )
    assert r.status_code in (400, 415, 422), r.text  # magic bytes don't match the declared type
