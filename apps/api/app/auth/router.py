"""Auth endpoints (PRD §6.1, §16.1)."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx
import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service as svc
from app.auth.schemas import (
    EmailIn,
    LoginIn,
    MembershipOut,
    MeOut,
    PatchMeIn,
    ResetPasswordIn,
    SignupIn,
    TokenIn,
    UserOut,
)
from app.core.config import settings
from app.core.deps import REFRESH_COOKIE, get_current_user, get_db
from app.core.errors import Problem
from app.core.rate_limit import client_ip, rate_limited
from app.core.redis import get_redis
from app.core.security import hash_password, needs_rehash, verify_password
from app.db.models import Membership, OAuthAccount, User
from app.db.repo import audit
from app.email.service import send_password_reset_email, send_verification_email

log = structlog.get_logger()
router = APIRouter(prefix="/auth", tags=["auth"])
auth_limit = rate_limited("auth", 20, 60)


def user_out(u: User) -> UserOut:
    return UserOut(
        id=u.id,
        email=u.email,
        name=u.name,
        email_verified=u.email_verified_at is not None,
        is_superadmin=u.is_superadmin,
        created_at=u.created_at,
    )


async def me_out(session: AsyncSession, user: User, csrf: str | None = None) -> MeOut:
    stmt = select(Membership).where(Membership.user_id == user.id)
    memberships = (await session.execute(stmt)).scalars().all()
    return MeOut(
        user=user_out(user),
        memberships=[
            MembershipOut(id=m.id, org_id=m.org_id, org_name=m.org.name, org_slug=m.org.slug, role=m.role)
            for m in memberships
            if m.org.deleted_at is None
        ],
        csrf_token=csrf,
    )


@router.post("/signup", status_code=status.HTTP_201_CREATED, dependencies=[Depends(auth_limit)])
async def signup(
    body: SignupIn, request: Request, response: Response, session: AsyncSession = Depends(get_db)
) -> MeOut:
    email = body.email.lower()
    existing = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        raise Problem(409, "An account with this email already exists")
    user = User(email=email, password_hash=hash_password(body.password), name=body.name.strip())
    session.add(user)
    await session.flush()
    token = await svc.create_email_token(session, user, "verify")
    await send_verification_email(user.email, user.name, token)
    csrf = await svc.issue_session(session, response, request, user)
    await audit(
        session,
        org_id=None,
        actor_user_id=user.id,
        action="user.signup",
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return await me_out(session, user, csrf)


@router.post("/login", dependencies=[Depends(auth_limit)])
async def login(
    body: LoginIn, request: Request, response: Response, session: AsyncSession = Depends(get_db)
) -> MeOut:
    email = body.email.lower()
    ip = client_ip(request)
    r = get_redis()
    fail_key = f"login_fail:{email}:{ip}"
    failures = int(await r.get(fail_key) or 0)
    if failures >= settings.login_max_failures:
        ttl = await r.ttl(fail_key)
        raise Problem(
            429, "Too many failed attempts. Try again later.", headers={"Retry-After": str(max(ttl, 1))}
        )
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    ok = user is not None and user.deleted_at is None and verify_password(body.password, user.password_hash)
    if not ok:
        await r.incr(fail_key)
        await r.expire(fail_key, settings.login_lockout_seconds)
        await audit(
            session,
            org_id=None,
            actor_user_id=user.id if user else None,
            action="user.login_failed",
            metadata={"email": email},
            ip=ip,
            user_agent=request.headers.get("user-agent"),
        )
        raise Problem(401, "Invalid email or password")
    assert user is not None
    await r.delete(fail_key)
    if user.password_hash and needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.last_login_at = datetime.now(UTC)
    csrf = await svc.issue_session(session, response, request, user)
    await audit(
        session,
        org_id=None,
        actor_user_id=user.id,
        action="user.login",
        ip=ip,
        user_agent=request.headers.get("user-agent"),
    )
    return await me_out(session, user, csrf)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, session: AsyncSession = Depends(get_db)) -> Response:
    await svc.revoke_refresh(session, request.cookies.get(REFRESH_COOKIE))
    svc.clear_auth_cookies(response)
    return Response(status_code=204, headers=dict(response.headers))


@router.post("/logout-all", status_code=204)
async def logout_all(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    await svc.revoke_refresh(session, None, all_devices=True, user_id=user.id)
    svc.clear_auth_cookies(response)
    return Response(status_code=204, headers=dict(response.headers))


@router.post("/refresh", dependencies=[Depends(auth_limit)])
async def refresh(request: Request, response: Response, session: AsyncSession = Depends(get_db)) -> MeOut:
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise Problem(401, "No refresh token")
    user, csrf = await svc.rotate_refresh(session, response, request, raw)
    return await me_out(session, user, csrf)


@router.post("/verify-email")
async def verify_email(body: TokenIn, session: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    user = await svc.consume_email_token(session, body.token, "verify")
    user.email_verified_at = user.email_verified_at or datetime.now(UTC)
    return {"verified": True}


@router.post("/resend-verification", status_code=202, dependencies=[Depends(rate_limited("resend", 3, 600))])
async def resend_verification(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    if user.email_verified_at is None:
        token = await svc.create_email_token(session, user, "verify")
        await send_verification_email(user.email, user.name, token)
    return {"status": "sent"}


@router.post("/forgot-password", status_code=202, dependencies=[Depends(auth_limit)])
async def forgot_password(body: EmailIn, session: AsyncSession = Depends(get_db)) -> dict[str, str]:
    user = (await session.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none()
    if user is not None and user.deleted_at is None:
        token = await svc.create_email_token(session, user, "reset")
        await send_password_reset_email(user.email, user.name, token)
    return {"status": "If the account exists, an email has been sent"}  # no enumeration


@router.post("/reset-password")
async def reset_password(
    body: ResetPasswordIn, request: Request, session: AsyncSession = Depends(get_db)
) -> dict[str, bool]:
    user = await svc.consume_email_token(session, body.token, "reset")
    user.password_hash = hash_password(body.password)
    user.email_verified_at = user.email_verified_at or datetime.now(UTC)
    await svc.revoke_refresh(session, None, all_devices=True, user_id=user.id)
    await audit(
        session, org_id=None, actor_user_id=user.id, action="user.password_changed", ip=client_ip(request)
    )
    return {"reset": True}


@router.get("/me")
async def me(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)) -> MeOut:
    return await me_out(session, user)


@router.patch("/me")
async def patch_me(
    body: PatchMeIn,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MeOut:
    if body.name:
        user.name = body.name.strip()
    if body.new_password:
        if not body.current_password or not verify_password(body.current_password, user.password_hash):
            raise Problem(400, "Current password is incorrect")
        user.password_hash = hash_password(body.new_password)
        await audit(
            session, org_id=None, actor_user_id=user.id, action="user.password_changed", ip=client_ip(request)
        )
    return await me_out(session, user)


# ---------------------------------------------------------------- Google OAuth (AUTH-3, PKCE + state)
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"


@router.get("/oauth/google/start")
async def google_start() -> RedirectResponse:
    if not settings.google_client_id:
        raise Problem(501, "Google sign-in is not configured")
    import base64
    import hashlib

    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    await get_redis().setex(f"oauth:google:{state}", 600, verifier)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": f"{settings.api_url}/v1/auth/oauth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return RedirectResponse(f"{GOOGLE_AUTH}?{urlencode(params)}")


@router.get("/oauth/google/callback")
async def google_callback(
    code: str, state: str, request: Request, session: AsyncSession = Depends(get_db)
) -> RedirectResponse:
    verifier = await get_redis().getdel(f"oauth:google:{state}")
    if not verifier:
        raise Problem(400, "Invalid OAuth state")
    async with httpx.AsyncClient(timeout=15) as client:
        tok = await client.post(
            GOOGLE_TOKEN,
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret or "",
                "code": code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": f"{settings.api_url}/v1/auth/oauth/google/callback",
            },
        )
        tok.raise_for_status()
        info = await client.get(
            GOOGLE_USERINFO, headers={"Authorization": f"Bearer {tok.json()['access_token']}"}
        )
        info.raise_for_status()
    profile = info.json()
    email = (profile.get("email") or "").lower()
    if not email or not profile.get("email_verified", False):
        raise Problem(400, "Google account has no verified email")
    sub = profile["sub"]
    acct = (
        await session.execute(
            select(OAuthAccount).where(
                OAuthAccount.provider == "google", OAuthAccount.provider_account_id == sub
            )
        )
    ).scalar_one_or_none()
    if acct is not None:
        user = await session.get(User, acct.user_id)
    else:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            user = User(
                email=email,
                name=profile.get("name") or email.split("@")[0],
                email_verified_at=datetime.now(UTC),
            )
            session.add(user)
            await session.flush()
        user.email_verified_at = user.email_verified_at or datetime.now(UTC)
        session.add(OAuthAccount(user_id=user.id, provider="google", provider_account_id=sub))
    assert user is not None
    user.last_login_at = datetime.now(UTC)
    response = RedirectResponse(f"{settings.app_url}/onboarding", status_code=302)
    await svc.issue_session(session, response, request, user)
    return response
