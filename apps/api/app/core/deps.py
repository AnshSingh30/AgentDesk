"""FastAPI dependencies: DB session, current user, org membership/RBAC, CSRF (PRD §6.1, §6.2, §18)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import structlog
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Problem
from app.core.security import constant_time_equal, decode_access_token
from app.db.models import ROLE_RANK, Membership, Organization, User
from app.db.session import get_sessionmaker

log = structlog.get_logger()
ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


async def get_db() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _extract_token(request: Request) -> tuple[str | None, str]:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip(), "bearer"
    return request.cookies.get(ACCESS_COOKIE), "cookie"


async def get_optional_user(request: Request, session: AsyncSession = Depends(get_db)) -> User | None:
    token, source = _extract_token(request)
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload:
        return None
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        return None
    user = await session.get(User, user_id)
    if user is None or user.deleted_at is not None:
        return None
    if source == "cookie" and request.method not in SAFE_METHODS:
        cookie_val = request.cookies.get(CSRF_COOKIE)
        header_val = request.headers.get(CSRF_HEADER)
        if not cookie_val or not header_val or not constant_time_equal(cookie_val, header_val):
            raise Problem(403, "CSRF token missing or invalid")
    request.state.user_id = str(user.id)
    structlog.contextvars.bind_contextvars(user_id=str(user.id))
    return user


async def get_current_user(user: User | None = Depends(get_optional_user)) -> User:
    if user is None:
        raise Problem(401, "Authentication required")
    return user


async def get_verified_user(user: User = Depends(get_current_user)) -> User:
    if user.email_verified_at is None and not user.is_superadmin:
        raise Problem(403, "Email verification required", type_="urn:agentdesk:email-unverified")
    return user


async def require_superadmin(user: User = Depends(get_current_user)) -> User:
    if not user.is_superadmin:
        raise Problem(404, "Not found")
    return user


@dataclass
class OrgContext:
    org: Organization
    membership: Membership
    user: User

    @property
    def org_id(self) -> uuid.UUID:
        return self.org.id

    @property
    def role(self) -> str:
        return self.membership.role

    def has_role(self, min_role: str) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[min_role]


def require_membership(min_role: str = "viewer"):
    async def dependency(
        org_id: str, user: User = Depends(get_verified_user), session: AsyncSession = Depends(get_db)
    ) -> OrgContext:
        try:
            oid = uuid.UUID(org_id)
        except ValueError as e:
            raise Problem(404, "Organisation not found") from e
        stmt = select(Membership).where(Membership.org_id == oid, Membership.user_id == user.id)
        membership = (await session.execute(stmt)).scalar_one_or_none()
        if membership is None:
            if user.is_superadmin:
                org = await session.get(Organization, oid)
                if org is None or org.deleted_at is not None:
                    raise Problem(404, "Organisation not found")
                membership = Membership(org_id=oid, user_id=user.id, role="owner")
                membership.org = org
                membership.user = user
            else:
                raise Problem(404, "Organisation not found")  # 404, not 403: no enumeration
        org = membership.org
        if org.deleted_at is not None:
            raise Problem(404, "Organisation not found")
        if org.suspended_at is not None and not user.is_superadmin:
            raise Problem(403, "Organisation is suspended", type_="urn:agentdesk:org-suspended")
        if ROLE_RANK[membership.role] < ROLE_RANK[min_role]:
            raise Problem(403, f"This action requires the {min_role} role or higher")
        structlog.contextvars.bind_contextvars(org_id=str(oid))
        return OrgContext(org=org, membership=membership, user=user)

    return dependency
