from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from app.core.errors import Problem
from app.core.ids import uuid7
from app.core.rate_limit import client_ip
from app.core.security import create_access_token, hash_token, new_opaque_token
from app.db.models import EmailToken, RefreshToken, User


def now() -> datetime:
    return datetime.now(UTC)


def set_auth_cookies(response: Response, access: str, refresh: str, csrf: str) -> None:
    secure = settings.cookie_secure
    response.set_cookie(
        ACCESS_COOKIE,
        access,
        max_age=settings.access_token_ttl_seconds,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=settings.refresh_token_ttl_seconds,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/v1/auth",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=settings.refresh_token_ttl_seconds,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/v1/auth")
    response.delete_cookie(CSRF_COOKIE, path="/")


async def issue_session(
    session: AsyncSession,
    response: Response,
    request: Request,
    user: User,
    family_id: uuid.UUID | None = None,
) -> str:
    """Create refresh token (new family unless rotating) + access token; set cookies. Returns csrf token."""
    raw, token_hash = new_opaque_token()
    rt = RefreshToken(
        id=uuid7(),
        user_id=user.id,
        token_hash=token_hash,
        family_id=family_id or uuid7(),
        expires_at=now() + timedelta(seconds=settings.refresh_token_ttl_seconds),
        user_agent=(request.headers.get("user-agent") or "")[:400],
        ip=client_ip(request),
    )
    session.add(rt)
    await session.flush()
    access = create_access_token(str(user.id))
    csrf = secrets.token_urlsafe(24)
    set_auth_cookies(response, access, raw, csrf)
    return csrf


async def rotate_refresh(
    session: AsyncSession, response: Response, request: Request, raw_refresh: str
) -> tuple[User, str]:
    token_hash = hash_token(raw_refresh)
    rt = (
        await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    ).scalar_one_or_none()
    if rt is None:
        raise Problem(401, "Invalid refresh token")
    if rt.revoked_at is not None:
        # reuse detected -> revoke the whole family (AUTH-5)
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == rt.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now())
        )
        raise Problem(401, "Refresh token reuse detected; please sign in again")
    if rt.expires_at < now():
        raise Problem(401, "Refresh token expired")
    user = await session.get(User, rt.user_id)
    if user is None or user.deleted_at is not None:
        raise Problem(401, "Account unavailable")
    rt.revoked_at = now()
    csrf = await issue_session(session, response, request, user, family_id=rt.family_id)
    new_rt = (
        (
            await session.execute(
                select(RefreshToken).where(
                    RefreshToken.family_id == rt.family_id, RefreshToken.revoked_at.is_(None)
                )
            )
        )
        .scalars()
        .first()
    )
    if new_rt is not None:
        rt.replaced_by = new_rt.id
    return user, csrf


async def revoke_refresh(
    session: AsyncSession,
    raw_refresh: str | None,
    *,
    all_devices: bool = False,
    user_id: uuid.UUID | None = None,
) -> None:
    if all_devices and user_id is not None:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now())
        )
        return
    if raw_refresh:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == hash_token(raw_refresh), RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now())
        )


async def create_email_token(session: AsyncSession, user: User, purpose: str) -> str:
    ttl = settings.verify_token_ttl_seconds if purpose == "verify" else settings.reset_token_ttl_seconds
    raw, token_hash = new_opaque_token()
    session.add(
        EmailToken(
            user_id=user.id, purpose=purpose, token_hash=token_hash, expires_at=now() + timedelta(seconds=ttl)
        )
    )
    await session.flush()
    return raw


async def consume_email_token(session: AsyncSession, raw: str, purpose: str) -> User:
    et = (
        await session.execute(
            select(EmailToken).where(EmailToken.token_hash == hash_token(raw), EmailToken.purpose == purpose)
        )
    ).scalar_one_or_none()
    if et is None or et.used_at is not None or et.expires_at < now():
        raise Problem(400, "This link is invalid or has expired")
    et.used_at = now()
    user = await session.get(User, et.user_id)
    if user is None:
        raise Problem(400, "This link is invalid or has expired")
    return user
