"""Platform operator console (PRD §6.14): orgs, users, usage, errors, suspend, feature flags."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, require_superadmin
from app.core.errors import Problem
from app.core.rate_limit import client_ip
from app.db.models import (
    Agent,
    Deployment,
    Document,
    FeatureFlag,
    Membership,
    Message,
    Organization,
    UsageEvent,
    User,
)
from app.db.repo import audit

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_superadmin)])


@router.get("/orgs")
async def admin_orgs(session: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    orgs = (
        (await session.execute(select(Organization).order_by(Organization.created_at.desc()).limit(500)))
        .scalars()
        .all()
    )
    month = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    out = []
    for o in orgs:
        members = (
            await session.execute(
                select(func.count()).select_from(Membership).where(Membership.org_id == o.id)
            )
        ).scalar_one()
        agents = (
            await session.execute(
                select(func.count())
                .select_from(Agent)
                .where(Agent.org_id == o.id, Agent.deleted_at.is_(None))
            )
        ).scalar_one()
        deployments = (
            await session.execute(
                select(func.count())
                .select_from(Deployment)
                .where(Deployment.org_id == o.id, Deployment.deleted_at.is_(None))
            )
        ).scalar_one()
        usage = (
            await session.execute(
                select(func.count(), func.coalesce(func.sum(UsageEvent.est_cost_usd), 0)).where(
                    UsageEvent.org_id == o.id, UsageEvent.created_at >= month
                )
            )
        ).one()
        out.append(
            {
                "id": o.id,
                "name": o.name,
                "slug": o.slug,
                "suspended_at": o.suspended_at,
                "deleted_at": o.deleted_at,
                "members": members,
                "agents": agents,
                "deployments": deployments,
                "usage_events_month": usage[0],
                "est_cost_month": float(usage[1]),
                "created_at": o.created_at,
            }
        )
    return out


@router.post("/orgs/{org_id}/suspend")
async def suspend_org(
    org_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_superadmin),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    org = await session.get(Organization, org_id)
    if org is None:
        raise Problem(404, "Organisation not found")
    org.suspended_at = datetime.now(UTC)
    deps = (
        (
            await session.execute(
                select(Deployment).where(Deployment.org_id == org.id, Deployment.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    for d in deps:
        if d.status == "live":
            d.status = "paused"
            d.last_error = "Organisation suspended by platform operator"
    await audit(
        session,
        org_id=org.id,
        actor_user_id=user.id,
        action="admin.org_suspended",
        target_type="org",
        target_id=org.id,
        ip=client_ip(request),
    )
    return {"id": org.id, "suspended_at": org.suspended_at, "paused_deployments": len(deps)}


@router.post("/orgs/{org_id}/unsuspend")
async def unsuspend_org(
    org_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_superadmin),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    org = await session.get(Organization, org_id)
    if org is None:
        raise Problem(404, "Organisation not found")
    org.suspended_at = None
    await audit(
        session,
        org_id=org.id,
        actor_user_id=user.id,
        action="admin.org_unsuspended",
        target_type="org",
        target_id=org.id,
        ip=client_ip(request),
    )
    return {"id": org.id, "suspended_at": None}


@router.get("/users")
async def admin_users(session: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    users = (await session.execute(select(User).order_by(User.created_at.desc()).limit(500))).scalars().all()
    return [
        {
            "id": u.id,
            "email": u.email,
            "name": u.name,
            "email_verified": u.email_verified_at is not None,
            "is_superadmin": u.is_superadmin,
            "last_login_at": u.last_login_at,
            "orgs": [m.org.name for m in u.memberships],
            "created_at": u.created_at,
        }
        for u in users
    ]


@router.get("/errors")
async def admin_errors(session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=7)
    failed_msgs = (
        (
            await session.execute(
                select(Message)
                .where(Message.error.is_not(None), Message.created_at >= since)
                .order_by(Message.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    failed_docs = (
        (
            await session.execute(
                select(Document)
                .where(Document.status == "failed")
                .order_by(Document.updated_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    error_deps = (
        (await session.execute(select(Deployment).where(Deployment.status == "error"))).scalars().all()
    )
    return {
        "failed_messages": [
            {"id": m.id, "org_id": m.org_id, "error": m.error, "created_at": m.created_at}
            for m in failed_msgs
        ],
        "failed_documents": [
            {"id": d.id, "org_id": d.org_id, "filename": d.filename, "error": d.error} for d in failed_docs
        ],
        "deployments_in_error": [
            {"id": d.id, "org_id": d.org_id, "last_error": d.last_error, "last_error_at": d.last_error_at}
            for d in error_deps
        ],
    }


class FlagIn(BaseModel):
    org_id: uuid.UUID | None = None
    key: str
    enabled: bool


@router.get("/feature-flags")
async def list_flags(session: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (await session.execute(select(FeatureFlag).order_by(FeatureFlag.key))).scalars().all()
    return [{"id": f.id, "org_id": f.org_id, "key": f.key, "enabled": f.enabled} for f in rows]


@router.put("/feature-flags")
async def set_flag(body: FlagIn, session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    row = (
        await session.execute(
            select(FeatureFlag).where(FeatureFlag.org_id == body.org_id, FeatureFlag.key == body.key)
        )
    ).scalar_one_or_none()
    if row is None:
        row = FeatureFlag(org_id=body.org_id, key=body.key, enabled=body.enabled)
        session.add(row)
    else:
        row.enabled = body.enabled
    await session.flush()
    return {"id": row.id, "org_id": row.org_id, "key": row.key, "enabled": row.enabled}
