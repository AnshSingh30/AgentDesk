"""Cross-tenant access returns 404 (not 403) for every org-scoped endpoint (PRD ORG-4, §18.1)."""

from __future__ import annotations

import httpx
import pytest

from tests.integration.conftest import make_actor, make_org

pytestmark = pytest.mark.asyncio


async def _seed_resources(actor, org: str) -> dict[str, str]:
    agent = (
        await actor.post(f"/v1/orgs/{org}/agents", json={"template_key": "hr_onboarding", "name": "A"})
    ).json()["id"]
    kb = (await actor.post(f"/v1/orgs/{org}/knowledge-bases", json={"name": "KB"})).json()["id"]
    ds = (await actor.post(f"/v1/orgs/{org}/datasets", json={"type": "spreadsheet", "name": "DS"})).json()[
        "id"
    ]
    sess = (await actor.post(f"/v1/orgs/{org}/agents/{agent}/playground/sessions", json={})).json()[
        "session_id"
    ]
    return {"agent": agent, "kb": kb, "ds": ds, "session": sess}


async def test_other_org_member_gets_404_everywhere(client: httpx.AsyncClient) -> None:
    owner = await make_actor(client)
    org_a = await make_org(owner, "Org A")
    res = await _seed_resources(owner, org_a)
    intruder = await make_actor(client)
    org_b = await make_org(intruder, "Org B")
    endpoints = [
        ("GET", f"/v1/orgs/{org_a}"),
        ("GET", f"/v1/orgs/{org_a}/agents"),
        ("GET", f"/v1/orgs/{org_a}/agents/{res['agent']}"),
        ("PATCH", f"/v1/orgs/{org_a}/agents/{res['agent']}"),
        ("POST", f"/v1/orgs/{org_a}/agents/{res['agent']}/publish"),
        ("GET", f"/v1/orgs/{org_a}/knowledge-bases/{res['kb']}"),
        ("GET", f"/v1/orgs/{org_a}/knowledge-bases/{res['kb']}/documents"),
        ("GET", f"/v1/orgs/{org_a}/datasets/{res['ds']}/schema"),
        ("GET", f"/v1/orgs/{org_a}/members"),
        ("GET", f"/v1/orgs/{org_a}/conversations"),
        ("GET", f"/v1/orgs/{org_a}/analytics/overview"),
        ("GET", f"/v1/orgs/{org_a}/usage"),
        ("GET", f"/v1/orgs/{org_a}/audit-logs"),
        ("GET", f"/v1/orgs/{org_a}/deployments"),
        ("GET", f"/v1/orgs/{org_a}/provider-credentials"),
        ("POST", f"/v1/orgs/{org_a}/playground/sessions/{res['session']}/messages"),
    ]
    for method, path in endpoints:
        r = await intruder.request(method, path, json={} if method in ("POST", "PATCH") else None)
        assert r.status_code == 404, f"{method} {path} -> {r.status_code}: {r.text}"
    # resources of org A are also invisible through org B's scope
    r = await intruder.get(f"/v1/orgs/{org_b}/agents/{res['agent']}")
    assert r.status_code == 404
    r = await intruder.get(f"/v1/orgs/{org_b}/knowledge-bases/{res['kb']}")
    assert r.status_code == 404


async def test_roles_are_enforced(client: httpx.AsyncClient) -> None:
    owner = await make_actor(client)
    org = await make_org(owner)
    viewer = await make_actor(client)
    r = await owner.post(f"/v1/orgs/{org}/invitations", json={"email": viewer.email, "role": "viewer"})
    assert r.status_code == 201
    # viewer accepts via token from the DB (email delivery is tested elsewhere)
    from app.db.models import Invitation
    from app.db.session import get_sessionmaker
    from sqlalchemy import select

    async with get_sessionmaker()() as session:
        inv = (await session.execute(select(Invitation).where(Invitation.email == viewer.email))).scalar_one()
        inv_id = str(inv.id)
    # invitations are hashed; accept through a direct membership for the role test
    import uuid

    from app.db.models import Membership

    async with get_sessionmaker()() as session:
        session.add(Membership(org_id=uuid.UUID(org), user_id=uuid.UUID(viewer.user_id), role="viewer"))
        await session.commit()
    assert (await viewer.get(f"/v1/orgs/{org}/agents")).status_code == 200
    assert (await viewer.post(f"/v1/orgs/{org}/agents", json={"name": "x"})).status_code == 403
    assert (await viewer.get(f"/v1/orgs/{org}/audit-logs")).status_code == 403
    assert (await viewer.delete(f"/v1/orgs/{org}/invitations/{inv_id}")).status_code == 403


async def test_unverified_user_cannot_create_org(client: httpx.AsyncClient) -> None:
    actor = await make_actor(client, verified=False)
    r = await actor.post("/v1/orgs", json={"name": "Nope"})
    assert r.status_code == 403 and r.json()["type"].endswith("email-unverified")


async def test_csrf_required_for_cookie_auth(client: httpx.AsyncClient) -> None:
    actor = await make_actor(client)
    r = await actor.client.post("/v1/orgs", json={"name": "No CSRF"})  # cookies present, header missing
    assert r.status_code == 403
