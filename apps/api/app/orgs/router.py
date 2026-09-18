"""Organisations, members, invitations, provider credentials, audit log, usage, export (PRD §6.2, §16.2)."""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import encrypt_secret, last4
from app.core.deps import OrgContext, get_current_user, get_db, get_verified_user, require_membership
from app.core.errors import Problem
from app.core.pagination import keyset_page
from app.core.rate_limit import client_ip
from app.core.security import hash_token, new_opaque_token
from app.core.ssrf import validate_public_url
from app.db.models import ROLES, AuditLog, Invitation, Membership, Organization, ProviderCredential, User
from app.db.repo import audit, get_scoped
from app.email.service import send_invitation_email
from app.usage.service import usage_summary

log = structlog.get_logger()
router = APIRouter(tags=["orgs"])


class OrgCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    industry: str | None = Field(default=None, max_length=80)
    size: str | None = Field(default=None, max_length=40)


class OrgPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    industry: str | None = Field(default=None, max_length=80)
    size: str | None = Field(default=None, max_length=40)
    settings: dict[str, Any] | None = None


class OrgOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    industry: str | None
    size: str | None
    settings: dict[str, Any]
    role: str | None = None
    created_at: datetime


def org_out(org: Organization, role: str | None = None) -> OrgOut:
    return OrgOut(
        id=org.id,
        name=org.name,
        slug=org.slug,
        industry=org.industry,
        size=org.size,
        settings=org.settings or {},
        role=role,
        created_at=org.created_at,
    )


def slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "org"
    return f"{base}-{secrets.token_hex(2)}"


@router.post("/orgs", status_code=status.HTTP_201_CREATED)
async def create_org(
    body: OrgCreate,
    request: Request,
    user: User = Depends(get_verified_user),
    session: AsyncSession = Depends(get_db),
) -> OrgOut:
    org = Organization(
        name=body.name.strip(),
        slug=slugify(body.name),
        industry=body.industry,
        size=body.size,
        settings={"retention_days": settings.retention_messages_days_default, "tracing_enabled": True},
    )
    session.add(org)
    await session.flush()
    session.add(Membership(org_id=org.id, user_id=user.id, role="owner"))
    await audit(
        session,
        org_id=org.id,
        actor_user_id=user.id,
        action="org.created",
        target_type="org",
        target_id=org.id,
        ip=client_ip(request),
    )
    return org_out(org, "owner")


@router.get("/orgs")
async def list_orgs(
    user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)
) -> list[OrgOut]:
    stmt = select(Membership).where(Membership.user_id == user.id)
    ms = (await session.execute(stmt)).scalars().all()
    return [org_out(m.org, m.role) for m in ms if m.org.deleted_at is None]


@router.get("/orgs/{org_id}")
async def get_org(ctx: OrgContext = Depends(require_membership("viewer"))) -> OrgOut:
    return org_out(ctx.org, ctx.role)


@router.patch("/orgs/{org_id}")
async def patch_org(
    body: OrgPatch,
    ctx: OrgContext = Depends(require_membership("admin")),
    session: AsyncSession = Depends(get_db),
) -> OrgOut:
    org = await session.get(Organization, ctx.org_id)
    assert org is not None
    if body.name:
        org.name = body.name.strip()
    if body.industry is not None:
        org.industry = body.industry
    if body.size is not None:
        org.size = body.size
    if body.settings is not None:
        allowed = {"retention_days", "tracing_enabled", "pii_redaction", "limits"}
        merged = {**(org.settings or {}), **{k: v for k, v in body.settings.items() if k in allowed}}
        if "retention_days" in merged:
            merged["retention_days"] = max(7, min(int(merged["retention_days"]), 3650))
        org.settings = merged
    return org_out(org, ctx.role)


@router.delete("/orgs/{org_id}", status_code=204)
async def delete_org(
    request: Request,
    ctx: OrgContext = Depends(require_membership("owner")),
    session: AsyncSession = Depends(get_db),
) -> None:
    org = await session.get(Organization, ctx.org_id)
    assert org is not None
    org.deleted_at = datetime.now(UTC)  # hard delete after 14 days via cleanup job (ORG-6)
    await audit(
        session,
        org_id=org.id,
        actor_user_id=ctx.user.id,
        action="org.deleted",
        target_type="org",
        target_id=org.id,
        ip=client_ip(request),
    )


# ---------------------------------------------------------------- members
class MemberOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    name: str
    role: str
    created_at: datetime


class MemberPatch(BaseModel):
    role: str


@router.get("/orgs/{org_id}/members")
async def list_members(
    ctx: OrgContext = Depends(require_membership("viewer")), session: AsyncSession = Depends(get_db)
) -> list[MemberOut]:
    ms = (await session.execute(select(Membership).where(Membership.org_id == ctx.org_id))).scalars().all()
    return [
        MemberOut(
            id=m.id,
            user_id=m.user_id,
            email=m.user.email,
            name=m.user.name,
            role=m.role,
            created_at=m.created_at,
        )
        for m in ms
    ]


async def _owner_count(session: AsyncSession, org_id: uuid.UUID) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(Membership)
            .where(Membership.org_id == org_id, Membership.role == "owner")
        )
    ).scalar_one()


@router.patch("/orgs/{org_id}/members/{membership_id}")
async def patch_member(
    membership_id: uuid.UUID,
    body: MemberPatch,
    request: Request,
    ctx: OrgContext = Depends(require_membership("admin")),
    session: AsyncSession = Depends(get_db),
) -> MemberOut:
    if body.role not in ROLES:
        raise Problem(422, f"Role must be one of {', '.join(ROLES)}")
    m = (
        await session.execute(
            select(Membership).where(Membership.id == membership_id, Membership.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if m is None:
        raise Problem(404, "Member not found")
    if (m.role == "owner" or body.role == "owner") and ctx.role != "owner":
        raise Problem(403, "Only owners can change ownership")
    if m.role == "owner" and body.role != "owner" and await _owner_count(session, ctx.org_id) <= 1:
        raise Problem(409, "An organisation must keep at least one owner")
    m.role = body.role
    await audit(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action="org.member_role_changed",
        target_type="membership",
        target_id=m.id,
        metadata={"role": body.role},
        ip=client_ip(request),
    )
    return MemberOut(
        id=m.id, user_id=m.user_id, email=m.user.email, name=m.user.name, role=m.role, created_at=m.created_at
    )


@router.delete("/orgs/{org_id}/members/{membership_id}", status_code=204)
async def remove_member(
    membership_id: uuid.UUID,
    request: Request,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> None:
    m = (
        await session.execute(
            select(Membership).where(Membership.id == membership_id, Membership.org_id == ctx.org_id)
        )
    ).scalar_one_or_none()
    if m is None:
        raise Problem(404, "Member not found")
    is_self = m.user_id == ctx.user.id
    if not is_self and not ctx.has_role("admin"):
        raise Problem(403, "This action requires the admin role or higher")
    if m.role == "owner" and (ctx.role != "owner" or await _owner_count(session, ctx.org_id) <= 1):
        raise Problem(409, "Cannot remove the last owner")
    await session.delete(m)
    await audit(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action="org.member_removed",
        target_type="membership",
        target_id=m.id,
        ip=client_ip(request),
    )


# ---------------------------------------------------------------- invitations
class InviteCreate(BaseModel):
    email: EmailStr
    role: str = "editor"


class InviteOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    expires_at: datetime
    accepted_at: datetime | None
    created_at: datetime


@router.post("/orgs/{org_id}/invitations", status_code=201)
async def create_invite(
    body: InviteCreate,
    request: Request,
    ctx: OrgContext = Depends(require_membership("admin")),
    session: AsyncSession = Depends(get_db),
) -> InviteOut:
    if body.role not in ROLES or (body.role == "owner" and ctx.role != "owner"):
        raise Problem(422, "Invalid role for invitation")
    raw, token_hash = new_opaque_token()
    inv = Invitation(
        org_id=ctx.org_id,
        email=body.email.lower(),
        role=body.role,
        token_hash=token_hash,
        invited_by=ctx.user.id,
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.invite_ttl_seconds),
    )
    session.add(inv)
    await session.flush()
    await send_invitation_email(inv.email, ctx.org.name, inv.role, raw)
    await audit(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action="org.member_invited",
        target_type="invitation",
        target_id=inv.id,
        metadata={"email": inv.email, "role": inv.role},
        ip=client_ip(request),
    )
    return InviteOut(
        id=inv.id,
        email=inv.email,
        role=inv.role,
        expires_at=inv.expires_at,
        accepted_at=None,
        created_at=inv.created_at,
    )


@router.get("/orgs/{org_id}/invitations")
async def list_invites(
    ctx: OrgContext = Depends(require_membership("admin")), session: AsyncSession = Depends(get_db)
) -> list[InviteOut]:
    invs = (
        (
            await session.execute(
                select(Invitation)
                .where(Invitation.org_id == ctx.org_id, Invitation.accepted_at.is_(None))
                .order_by(Invitation.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        InviteOut(
            id=i.id,
            email=i.email,
            role=i.role,
            expires_at=i.expires_at,
            accepted_at=i.accepted_at,
            created_at=i.created_at,
        )
        for i in invs
    ]


@router.delete("/orgs/{org_id}/invitations/{invite_id}", status_code=204)
async def delete_invite(
    invite_id: uuid.UUID,
    ctx: OrgContext = Depends(require_membership("admin")),
    session: AsyncSession = Depends(get_db),
) -> None:
    inv = await get_scoped(session, Invitation, ctx.org_id, invite_id, what="Invitation")
    await session.delete(inv)


@router.get("/invitations/{token}")
async def preview_invite(token: str, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    inv = (
        await session.execute(select(Invitation).where(Invitation.token_hash == hash_token(token)))
    ).scalar_one_or_none()
    if inv is None or inv.accepted_at is not None or inv.expires_at < datetime.now(UTC):
        raise Problem(404, "Invitation not found or expired")
    org = await session.get(Organization, inv.org_id)
    return {"org_name": org.name if org else "", "email": inv.email, "role": inv.role}


@router.post("/invitations/{token}/accept")
async def accept_invite(
    token: str,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> OrgOut:
    inv = (
        await session.execute(select(Invitation).where(Invitation.token_hash == hash_token(token)))
    ).scalar_one_or_none()
    if inv is None or inv.accepted_at is not None or inv.expires_at < datetime.now(UTC):
        raise Problem(404, "Invitation not found or expired")
    if inv.email != user.email.lower():
        raise Problem(403, f"This invitation was sent to {inv.email}. Sign in with that email to accept it.")
    org = await session.get(Organization, inv.org_id)
    if org is None or org.deleted_at is not None:
        raise Problem(404, "Organisation not found")
    existing = (
        await session.execute(
            select(Membership).where(Membership.org_id == org.id, Membership.user_id == user.id)
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(Membership(org_id=org.id, user_id=user.id, role=inv.role))
    inv.accepted_at = datetime.now(UTC)
    user.email_verified_at = user.email_verified_at or datetime.now(
        UTC
    )  # invite link proves mailbox ownership
    await audit(
        session,
        org_id=org.id,
        actor_user_id=user.id,
        action="org.invite_accepted",
        target_type="invitation",
        target_id=inv.id,
        ip=client_ip(request),
    )
    return org_out(org, existing.role if existing else inv.role)


# ---------------------------------------------------------------- provider credentials (MD-8)
PROVIDERS = ("openai", "anthropic", "gemini", "groq", "openrouter", "custom", "tavily", "cohere")


class CredentialCreate(BaseModel):
    provider: str
    label: str = Field(min_length=1, max_length=80)
    api_key: str = Field(min_length=1, max_length=4000)
    api_base: str | None = Field(default=None, max_length=400)


class CredentialOut(BaseModel):
    id: uuid.UUID
    provider: str
    label: str
    last4: str
    api_base: str | None
    created_at: datetime
    last_used_at: datetime | None


def cred_out(c: ProviderCredential) -> CredentialOut:
    return CredentialOut(
        id=c.id,
        provider=c.provider,
        label=c.label,
        last4=c.last4,
        api_base=c.api_base,
        created_at=c.created_at,
        last_used_at=c.last_used_at,
    )


@router.get("/orgs/{org_id}/provider-credentials")
async def list_credentials(
    ctx: OrgContext = Depends(require_membership("viewer")), session: AsyncSession = Depends(get_db)
) -> list[CredentialOut]:
    rows = (
        (
            await session.execute(
                select(ProviderCredential)
                .where(ProviderCredential.org_id == ctx.org_id)
                .order_by(ProviderCredential.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [cred_out(c) for c in rows]


@router.post("/orgs/{org_id}/provider-credentials", status_code=201)
async def create_credential(
    body: CredentialCreate,
    request: Request,
    ctx: OrgContext = Depends(require_membership("admin")),
    session: AsyncSession = Depends(get_db),
) -> CredentialOut:
    if body.provider not in PROVIDERS:
        raise Problem(422, f"provider must be one of {', '.join(PROVIDERS)}")
    if body.api_base:
        validate_public_url(body.api_base)
    c = ProviderCredential(
        org_id=ctx.org_id,
        provider=body.provider,
        label=body.label.strip(),
        encrypted_api_key=encrypt_secret(body.api_key),
        api_base=body.api_base,
        last4=last4(body.api_key),
        created_by=ctx.user.id,
    )
    session.add(c)
    await session.flush()
    await audit(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action="credential.created",
        target_type="credential",
        target_id=c.id,
        metadata={"provider": c.provider},
        ip=client_ip(request),
    )
    return cred_out(c)


@router.delete("/orgs/{org_id}/provider-credentials/{cred_id}", status_code=204)
async def delete_credential(
    cred_id: uuid.UUID,
    request: Request,
    ctx: OrgContext = Depends(require_membership("admin")),
    session: AsyncSession = Depends(get_db),
) -> None:
    c = await get_scoped(session, ProviderCredential, ctx.org_id, cred_id, what="Credential")
    await session.delete(c)
    await audit(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action="credential.deleted",
        target_type="credential",
        target_id=cred_id,
        ip=client_ip(request),
    )


@router.post("/orgs/{org_id}/provider-credentials/{cred_id}/test")
async def test_credential(
    cred_id: uuid.UUID,
    ctx: OrgContext = Depends(require_membership("admin")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from app.llm.gateway import test_model_config

    c = await get_scoped(session, ProviderCredential, ctx.org_id, cred_id, what="Credential")
    result = await test_model_config(
        session,
        ctx.org_id,
        {
            "mode": "byo" if c.provider != "custom" else "custom",
            "credential_id": str(c.id),
            "provider": c.provider,
            "model": None,
            "api_base": c.api_base,
        },
    )
    if result.get("ok"):
        c.last_used_at = datetime.now(UTC)
    return result


# ---------------------------------------------------------------- audit log & usage
class AuditOut(BaseModel):
    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    metadata: dict[str, Any]
    ip: str | None
    created_at: datetime


@router.get("/orgs/{org_id}/audit-logs")
async def list_audit(
    limit: int = 50,
    cursor: str | None = None,
    action: str | None = None,
    ctx: OrgContext = Depends(require_membership("admin")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    stmt = select(AuditLog).where(AuditLog.org_id == ctx.org_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    rows, next_cursor = await keyset_page(session, stmt, AuditLog, limit, cursor)
    return {
        "items": [
            AuditOut(
                id=r.id,
                actor_user_id=r.actor_user_id,
                action=r.action,
                target_type=r.target_type,
                target_id=r.target_id,
                metadata=r.metadata_ or {},
                ip=r.ip,
                created_at=r.created_at,
            )
            for r in rows
        ],
        "next_cursor": next_cursor,
    }


@router.get("/orgs/{org_id}/usage")
async def get_usage(
    from_: datetime | None = None,
    to: datetime | None = None,
    group_by: str = "kind",
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    now = datetime.now(UTC)
    start = from_ or now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = to or now
    rows = await usage_summary(session, ctx.org_id, start, end, group_by)
    limits = (ctx.org.settings or {}).get("limits", {})
    return {
        "from": start,
        "to": end,
        "group_by": group_by,
        "rows": rows,
        "limits": {
            "messages_per_month": limits.get("messages_per_month", 10000),
            "storage_mb": limits.get("storage_mb", settings.org_storage_limit_mb),
        },
    }


@router.post("/orgs/{org_id}/export", status_code=202)
async def export_org(
    request: Request,
    ctx: OrgContext = Depends(require_membership("owner")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    from app.jobs.queue import enqueue

    job_id = await enqueue("export_org", str(ctx.org_id), str(ctx.user.id))
    await audit(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action="org.data_exported",
        ip=client_ip(request),
    )
    return {"job_id": job_id, "status": "queued"}
