"""Integration fixtures: real Postgres/Redis/Qdrant/MinIO from docker compose, app served in-process via ASGI."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
from app.core.redis import get_redis
from app.db.models import User
from app.db.session import get_sessionmaker
from app.main import app
from sqlalchemy import select, update


@pytest.fixture(autouse=True)
async def _clear_rate_limits() -> AsyncIterator[None]:
    r = get_redis()
    keys = [k async for k in r.scan_iter("rl:*")]
    if keys:
        await r.delete(*keys)
    yield


def new_client() -> httpx.AsyncClient:
    """Each actor gets its own client (own cookie jar) so multi-user tests don't share sessions."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    from app.templates.loader import sync_templates

    async with get_sessionmaker()() as session:
        await sync_templates(session)
        await session.commit()
    async with new_client() as c:
        yield c


class Actor:
    """A signed-up, verified user with cookie auth + CSRF header handling."""

    def __init__(self, client: httpx.AsyncClient, email: str) -> None:
        self.client = client
        self.email = email
        self.csrf = ""
        self.user_id: str = ""

    def _headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf} if self.csrf else {}

    async def _refresh_csrf(self) -> None:
        self.csrf = self.client.cookies.get("csrf_token") or self.csrf

    async def request(self, method: str, path: str, **kw: object) -> httpx.Response:
        await self._refresh_csrf()
        headers = {**self._headers(), **(kw.pop("headers", None) or {})}  # type: ignore[dict-item]
        return await self.client.request(method, path, headers=headers, **kw)  # type: ignore[arg-type]

    async def get(self, path: str, **kw: object) -> httpx.Response:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, **kw: object) -> httpx.Response:
        return await self.request("POST", path, **kw)

    async def patch(self, path: str, **kw: object) -> httpx.Response:
        return await self.request("PATCH", path, **kw)

    async def delete(self, path: str, **kw: object) -> httpx.Response:
        return await self.request("DELETE", path, **kw)


async def make_actor(
    _client: httpx.AsyncClient | None = None, *, verified: bool = True, superadmin: bool = False
) -> Actor:
    client = new_client()
    email = f"it-{uuid.uuid4().hex[:10]}@example.com"
    r = await client.post(
        "/v1/auth/signup", json={"email": email, "password": "Integration-pass-1!", "name": "IT User"}
    )
    assert r.status_code == 201, r.text
    actor = Actor(client, email)
    actor.user_id = r.json()["user"]["id"]
    actor.csrf = r.json()["csrf_token"]
    if verified or superadmin:
        async with get_sessionmaker()() as session:
            await session.execute(
                update(User)
                .where(User.email == email)
                .values(email_verified_at=datetime.now(UTC), is_superadmin=superadmin)
            )
            await session.commit()
    return actor


async def make_org(actor: Actor, name: str = "IT Org") -> str:
    r = await actor.post("/v1/orgs", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def user_by_email(email: str) -> User | None:
    async with get_sessionmaker()() as session:
        return (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
