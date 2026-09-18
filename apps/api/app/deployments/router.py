"""Deployments (WhatsApp channel) management (PRD §6.12, §14.2, §16.7)."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret, last4
from app.core.deps import OrgContext, get_db, require_membership
from app.core.errors import Problem
from app.core.rate_limit import client_ip
from app.core.security import hash_token
from app.db.models import Agent, AgentVersion, Contact, Deployment
from app.db.repo import audit, get_scoped, scoped
from app.deployments.whatsapp.client import WhatsAppClient, WhatsAppError

log = structlog.get_logger()
router = APIRouter(tags=["deployments"])


class DeploymentCreate(BaseModel):
    agent_id: uuid.UUID
    agent_version_id: uuid.UUID | None = None
    phone_number_id: str = Field(min_length=5, max_length=40)
    waba_id: str = Field(min_length=5, max_length=40)
    access_token: str = Field(min_length=20, max_length=1000)
    app_secret: str = Field(min_length=10, max_length=200)
    graph_version: str | None = Field(default=None, max_length=10)


class DeploymentPatch(BaseModel):
    agent_version_id: uuid.UUID | None = None
    status: str | None = Field(default=None, pattern="^(live|paused)$")
    access_token: str | None = Field(default=None, min_length=20, max_length=1000)
    app_secret: str | None = Field(default=None, min_length=10, max_length=200)
    rate_limits: dict[str, int] | None = None


class TestMessageIn(BaseModel):
    to: str = Field(min_length=8, max_length=20, pattern=r"^\+?[0-9]+$")
    text: str = Field("Hello from AgentDesk 👋 This is a test message.", max_length=1000)


def webhook_url(dep: Deployment) -> str:
    return f"{settings.webhook_base_url}/webhooks/whatsapp/{dep.id}"


def dep_out(
    dep: Deployment, agent: Agent | None = None, version: AgentVersion | None = None
) -> dict[str, Any]:
    cfg = dep.config or {}
    return {
        "id": dep.id,
        "agent_id": dep.agent_id,
        "agent_name": agent.name if agent else None,
        "agent_version_id": dep.agent_version_id,
        "version_number": version.version_number if version else None,
        "channel": dep.channel,
        "status": dep.status,
        "phone_number_id": cfg.get("phone_number_id"),
        "waba_id": cfg.get("waba_id"),
        "display_phone": cfg.get("display_phone"),
        "verified_name": cfg.get("verified_name"),
        "quality_rating": cfg.get("quality_rating"),
        "graph_version": cfg.get("graph_version"),
        "credentials_valid": bool(cfg.get("credentials_valid")),
        "token_last4": cfg.get("token_last4"),
        "webhook_url": webhook_url(dep),
        "webhook_verified_at": dep.webhook_verified_at,
        "last_error": dep.last_error,
        "last_error_at": dep.last_error_at,
        "rate_limits": dep.rate_limits or {},
        "message_count": dep.message_count,
        "created_at": dep.created_at,
        "updated_at": dep.updated_at,
    }


@router.post("/orgs/{org_id}/deployments", status_code=status.HTTP_201_CREATED)
async def create_deployment(
    body: DeploymentCreate,
    request: Request,
    ctx: OrgContext = Depends(require_membership("admin")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    agent = await get_scoped(session, Agent, ctx.org_id, body.agent_id, what="Agent")
    version_id = body.agent_version_id or agent.latest_version_id
    if version_id is None:
        raise Problem(422, "Publish the agent before deploying it")
    version = await session.get(AgentVersion, version_id)
    if version is None or version.agent_id != agent.id:
        raise Problem(404, "Version not found")
    clash = (
        (
            await session.execute(
                select(Deployment).where(
                    Deployment.deleted_at.is_(None),
                    Deployment.config["phone_number_id"].astext == body.phone_number_id,
                )
            )
        )
        .scalars()
        .first()
    )
    if clash is not None:
        raise Problem(409, "This phone number already has an active deployment")
    verify_token = secrets.token_hex(32)
    dep = Deployment(
        org_id=ctx.org_id,
        agent_id=agent.id,
        agent_version_id=version.id,
        channel="whatsapp",
        status="pending_webhook",
        config={
            "phone_number_id": body.phone_number_id,
            "waba_id": body.waba_id,
            "graph_version": body.graph_version or settings.wa_graph_api_version,
            "token_last4": last4(body.access_token),
        },
        encrypted_access_token=encrypt_secret(body.access_token),
        encrypted_app_secret=encrypt_secret(body.app_secret),
        verify_token_hash=hash_token(verify_token),
        encrypted_verify_token=encrypt_secret(verify_token),
        rate_limits={"contact_per_minute": 20, "contact_per_day": 300, "deployment_per_day": 5000},
    )
    session.add(dep)
    await session.flush()
    await audit(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action="deployment.created",
        target_type="deployment",
        target_id=dep.id,
        ip=client_ip(request),
    )
    out = dep_out(dep, agent, version)
    out["verify_token"] = verify_token
    return out


@router.get("/orgs/{org_id}/deployments")
async def list_deployments(
    agent_id: uuid.UUID | None = None,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = scoped(Deployment, ctx.org_id).order_by(Deployment.created_at.desc())
    if agent_id:
        stmt = stmt.where(Deployment.agent_id == agent_id)
    deps = (await session.execute(stmt)).scalars().all()
    out = []
    for d in deps:
        out.append(
            dep_out(
                d, await session.get(Agent, d.agent_id), await session.get(AgentVersion, d.agent_version_id)
            )
        )
    return out


@router.get("/orgs/{org_id}/deployments/{dep_id}")
async def get_deployment(
    dep_id: str,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    d = await get_scoped(session, Deployment, ctx.org_id, dep_id, what="Deployment")
    return dep_out(
        d, await session.get(Agent, d.agent_id), await session.get(AgentVersion, d.agent_version_id)
    )


@router.patch("/orgs/{org_id}/deployments/{dep_id}")
async def patch_deployment(
    dep_id: str,
    body: DeploymentPatch,
    request: Request,
    ctx: OrgContext = Depends(require_membership("admin")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    d = await get_scoped(session, Deployment, ctx.org_id, dep_id, what="Deployment")
    if body.agent_version_id is not None:
        v = await session.get(AgentVersion, body.agent_version_id)
        if v is None or v.agent_id != d.agent_id:
            raise Problem(404, "Version not found")
        d.agent_version_id = v.id
        await audit(
            session,
            org_id=ctx.org_id,
            actor_user_id=ctx.user.id,
            action="deployment.version_changed",
            target_type="deployment",
            target_id=d.id,
            metadata={"version": v.version_number},
            ip=client_ip(request),
        )
    if body.status is not None:
        if body.status == "live" and not (
            d.webhook_verified_at and (d.config or {}).get("credentials_valid")
        ):
            raise Problem(409, "Verify credentials and the webhook before going live")
        d.status = body.status
        if body.status == "live":
            d.last_error = None
        await audit(
            session,
            org_id=ctx.org_id,
            actor_user_id=ctx.user.id,
            action="deployment.resumed" if body.status == "live" else "deployment.paused",
            target_type="deployment",
            target_id=d.id,
            ip=client_ip(request),
        )
    if body.access_token or body.app_secret:
        cfg = dict(d.config or {})
        if body.access_token:
            d.encrypted_access_token = encrypt_secret(body.access_token)
            cfg["token_last4"] = last4(body.access_token)
            cfg["credentials_valid"] = False
        if body.app_secret:
            d.encrypted_app_secret = encrypt_secret(body.app_secret)
        d.config = cfg
        if d.status == "error":
            d.status = "paused"
        await audit(
            session,
            org_id=ctx.org_id,
            actor_user_id=ctx.user.id,
            action="deployment.credentials_updated",
            target_type="deployment",
            target_id=d.id,
            ip=client_ip(request),
        )
    if body.rate_limits is not None:
        allowed = {"contact_per_minute", "contact_per_day", "deployment_per_day", "monthly_cap"}
        d.rate_limits = {
            **(d.rate_limits or {}),
            **{k: max(1, int(v)) for k, v in body.rate_limits.items() if k in allowed},
        }
    return dep_out(
        d, await session.get(Agent, d.agent_id), await session.get(AgentVersion, d.agent_version_id)
    )


@router.delete("/orgs/{org_id}/deployments/{dep_id}", status_code=204)
async def delete_deployment(
    dep_id: str,
    request: Request,
    ctx: OrgContext = Depends(require_membership("admin")),
    session: AsyncSession = Depends(get_db),
) -> None:
    d = await get_scoped(session, Deployment, ctx.org_id, dep_id, what="Deployment")
    d.deleted_at = datetime.now(UTC)
    d.status = "paused"
    await audit(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action="deployment.deleted",
        target_type="deployment",
        target_id=d.id,
        ip=client_ip(request),
    )


@router.post("/orgs/{org_id}/deployments/{dep_id}/verify")
async def verify_deployment(
    dep_id: str,
    ctx: OrgContext = Depends(require_membership("admin")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    d = await get_scoped(session, Deployment, ctx.org_id, dep_id, what="Deployment")
    cfg = dict(d.config or {})
    client = WhatsAppClient(
        str(cfg.get("phone_number_id")),
        decrypt_secret(d.encrypted_access_token or ""),
        cfg.get("graph_version"),
    )
    try:
        info = await client.get_phone_info()
        cfg.update(
            {
                "credentials_valid": True,
                "display_phone": info.get("display_phone_number"),
                "verified_name": info.get("verified_name"),
                "quality_rating": info.get("quality_rating"),
            }
        )
        d.config = cfg
        if d.webhook_verified_at and d.status in ("pending_webhook", "error"):
            d.status = "live"
            d.last_error = None
        checks = {
            "credentials_valid": True,
            "webhook_verified": d.webhook_verified_at is not None,
            "live": d.status == "live",
        }
        return {"ok": True, "checks": checks, "phone": info, "status": d.status}
    except WhatsAppError as e:
        cfg["credentials_valid"] = False
        d.config = cfg
        return {
            "ok": False,
            "code": e.code,
            "message": e.message,
            "checks": {
                "credentials_valid": False,
                "webhook_verified": d.webhook_verified_at is not None,
                "live": False,
            },
            "status": d.status,
        }


@router.post("/orgs/{org_id}/deployments/{dep_id}/test-message")
async def test_message(
    dep_id: str,
    body: TestMessageIn,
    ctx: OrgContext = Depends(require_membership("admin")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    d = await get_scoped(session, Deployment, ctx.org_id, dep_id, what="Deployment")
    client = WhatsAppClient(
        str((d.config or {}).get("phone_number_id")),
        decrypt_secret(d.encrypted_access_token or ""),
        (d.config or {}).get("graph_version"),
    )
    try:
        mid = await client.send_text(body.to.lstrip("+"), body.text)
        return {"ok": True, "message_id": mid}
    except WhatsAppError as e:
        return {"ok": False, "code": e.code, "message": e.message}


@router.post("/orgs/{org_id}/deployments/{dep_id}/rotate-verify-token")
async def rotate_verify_token(
    dep_id: str,
    ctx: OrgContext = Depends(require_membership("admin")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    d = await get_scoped(session, Deployment, ctx.org_id, dep_id, what="Deployment")
    token = secrets.token_hex(32)
    d.verify_token_hash = hash_token(token)
    d.encrypted_verify_token = encrypt_secret(token)
    d.webhook_verified_at = None
    if d.status == "live":
        d.status = "pending_webhook"
    return {"verify_token": token, "webhook_url": webhook_url(d)}


@router.get("/orgs/{org_id}/deployments/{dep_id}/verify-token")
async def reveal_verify_token(
    dep_id: str,
    ctx: OrgContext = Depends(require_membership("admin")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    d = await get_scoped(session, Deployment, ctx.org_id, dep_id, what="Deployment")
    if not d.encrypted_verify_token:
        raise Problem(404, "No verify token; rotate to create one")
    return {"verify_token": decrypt_secret(d.encrypted_verify_token), "webhook_url": webhook_url(d)}


@router.get("/orgs/{org_id}/deployments/{dep_id}/contacts")
async def list_contacts(
    dep_id: str,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    d = await get_scoped(session, Deployment, ctx.org_id, dep_id, what="Deployment")
    rows = (
        (
            await session.execute(
                select(Contact)
                .where(Contact.deployment_id == d.id)
                .order_by(Contact.last_seen_at.desc())
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    mask = not ctx.has_role("editor")
    return [
        {
            "id": c.id,
            "wa_id": mask_phone(c.wa_id) if mask else c.wa_id,
            "profile_name": c.profile_name,
            "first_seen_at": c.first_seen_at,
            "last_seen_at": c.last_seen_at,
            "message_count": c.message_count,
            "blocked": c.blocked,
        }
        for c in rows
    ]


@router.patch("/orgs/{org_id}/deployments/{dep_id}/contacts/{contact_id}")
async def patch_contact(
    dep_id: str,
    contact_id: uuid.UUID,
    body: dict[str, Any],
    ctx: OrgContext = Depends(require_membership("admin")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    d = await get_scoped(session, Deployment, ctx.org_id, dep_id, what="Deployment")
    c = (
        await session.execute(select(Contact).where(Contact.id == contact_id, Contact.deployment_id == d.id))
    ).scalar_one_or_none()
    if c is None:
        raise Problem(404, "Contact not found")
    if "blocked" in body:
        c.blocked = bool(body["blocked"])
    return {"id": c.id, "blocked": c.blocked}


def mask_phone(wa_id: str) -> str:
    if len(wa_id) <= 4:
        return "••••"
    return f"+{wa_id[:2]}•••••{wa_id[-4:]}"
