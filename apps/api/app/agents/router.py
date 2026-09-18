"""Agents CRUD, autosave with etag, validate, publish, versions, model test (PRD §6.3, §16.3)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import service as svc
from app.agents.schemas import (
    AgentConfig,
    AgentCreate,
    AgentOut,
    AgentPatch,
    AgentSummary,
    PublishIn,
    ValidationOut,
    VersionOut,
)
from app.core.deps import OrgContext, get_db, require_membership
from app.core.errors import Problem
from app.core.rate_limit import client_ip
from app.core.redis import get_redis
from app.db.models import Agent, AgentVersion
from app.db.repo import audit, get_scoped, scoped
from app.llm.catalog import public_catalog
from app.llm.gateway import test_model_config

router = APIRouter(tags=["agents"])


async def _summary(session: AsyncSession, a: Agent) -> AgentSummary:
    cfg = AgentConfig.model_validate(a.draft_config or {})
    return AgentSummary(
        id=a.id,
        name=a.name,
        description=a.description,
        template_key=a.template_key,
        status=await svc.agent_status(session, a),
        latest_version_number=await svc.latest_version_number(session, a),
        tools=cfg.tools.enabled_names(),
        created_by=a.created_by,
        created_at=a.created_at,
        updated_at=a.updated_at,
        draft_updated_at=a.draft_updated_at,
    )


async def _out(session: AsyncSession, a: Agent, response: Response | None = None) -> AgentOut:
    s = await _summary(session, a)
    etag = svc.etag_for(a)
    if response is not None:
        response.headers["ETag"] = etag
    return AgentOut(
        **s.model_dump(), draft_config=a.draft_config, etag=etag, latest_version_id=a.latest_version_id
    )


async def _bust_cache(agent_id: uuid.UUID) -> None:
    await get_redis().delete(f"agentcfg:draft:{agent_id}")


@router.post("/orgs/{org_id}/agents", status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AgentCreate,
    request: Request,
    response: Response,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> AgentOut:
    cfg, tpl_desc = svc.config_from_template(body.template_key)
    a = Agent(
        org_id=ctx.org_id,
        name=body.name.strip(),
        description=body.description or tpl_desc,
        template_key=body.template_key if body.template_key != "blank" else None,
        draft_config=cfg,
        created_by=ctx.user.id,
        draft_updated_at=datetime.now(UTC),
    )
    session.add(a)
    await session.flush()
    await audit(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action="agent.created",
        target_type="agent",
        target_id=a.id,
        metadata={"template_key": body.template_key},
        ip=client_ip(request),
    )
    return await _out(session, a, response)


@router.get("/orgs/{org_id}/agents")
async def list_agents(
    ctx: OrgContext = Depends(require_membership("viewer")), session: AsyncSession = Depends(get_db)
) -> list[AgentSummary]:
    rows = (
        (await session.execute(scoped(Agent, ctx.org_id).order_by(Agent.updated_at.desc()))).scalars().all()
    )
    return [await _summary(session, a) for a in rows]


@router.get("/orgs/{org_id}/agents/{agent_id}")
async def get_agent(
    agent_id: str,
    response: Response,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> AgentOut:
    a = await get_scoped(session, Agent, ctx.org_id, agent_id, what="Agent")
    return await _out(session, a, response)


@router.patch("/orgs/{org_id}/agents/{agent_id}")
async def patch_agent(
    agent_id: str,
    body: AgentPatch,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> AgentOut:
    a = await get_scoped(session, Agent, ctx.org_id, agent_id, what="Agent")
    if if_match and if_match.strip() != svc.etag_for(a) and if_match.strip() != "*":
        raise Problem(
            412,
            "The agent was modified by someone else. Reload to see the latest draft.",
            type_="urn:agentdesk:etag-conflict",
            headers={"ETag": svc.etag_for(a)},
        )
    if body.name is not None:
        a.name = body.name.strip()
    if body.description is not None:
        a.description = body.description
    if body.draft_config is not None:
        cfg = svc.parse_config(body.draft_config)
        a.draft_config = cfg.model_dump(mode="json")
    a.draft_updated_at = datetime.now(UTC)
    await session.flush()
    await _bust_cache(a.id)
    return await _out(session, a, response)


@router.delete("/orgs/{org_id}/agents/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: str,
    request: Request,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> None:
    a = await get_scoped(session, Agent, ctx.org_id, agent_id, what="Agent")
    a.deleted_at = datetime.now(UTC)
    await _bust_cache(a.id)
    await audit(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action="agent.deleted",
        target_type="agent",
        target_id=a.id,
        ip=client_ip(request),
    )


@router.post("/orgs/{org_id}/agents/{agent_id}/duplicate", status_code=201)
async def duplicate_agent(
    agent_id: str,
    response: Response,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> AgentOut:
    a = await get_scoped(session, Agent, ctx.org_id, agent_id, what="Agent")
    copy = Agent(
        org_id=ctx.org_id,
        name=f"{a.name} (copy)"[:120],
        description=a.description,
        template_key=a.template_key,
        draft_config=dict(a.draft_config),
        created_by=ctx.user.id,
        draft_updated_at=datetime.now(UTC),
    )
    session.add(copy)
    await session.flush()
    return await _out(session, copy, response)


@router.post("/orgs/{org_id}/agents/{agent_id}/validate")
async def validate_agent(
    agent_id: str,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> ValidationOut:
    a = await get_scoped(session, Agent, ctx.org_id, agent_id, what="Agent")
    errors, warnings = await svc.validate_agent(session, ctx.org_id, a)
    return ValidationOut(ok=not errors, errors=errors, warnings=warnings)


@router.post("/orgs/{org_id}/agents/{agent_id}/publish", status_code=201)
async def publish_agent(
    agent_id: str,
    body: PublishIn,
    request: Request,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> VersionOut:
    a = await get_scoped(session, Agent, ctx.org_id, agent_id, what="Agent")
    v = await svc.publish_agent(session, ctx.org_id, a, ctx.user.id, body.changelog)
    await audit(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action="agent.published",
        target_type="agent",
        target_id=a.id,
        metadata={"version": v.version_number},
        ip=client_ip(request),
    )
    return VersionOut(
        id=v.id,
        agent_id=a.id,
        version_number=v.version_number,
        changelog=v.changelog,
        published_by=v.published_by,
        created_at=v.created_at,
        config=v.config,
    )


@router.get("/orgs/{org_id}/agents/{agent_id}/versions")
async def list_versions(
    agent_id: str,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> list[VersionOut]:
    a = await get_scoped(session, Agent, ctx.org_id, agent_id, what="Agent")
    rows = (
        (
            await session.execute(
                select(AgentVersion)
                .where(AgentVersion.agent_id == a.id)
                .order_by(AgentVersion.version_number.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        VersionOut(
            id=v.id,
            agent_id=v.agent_id,
            version_number=v.version_number,
            changelog=v.changelog,
            published_by=v.published_by,
            created_at=v.created_at,
        )
        for v in rows
    ]


@router.get("/orgs/{org_id}/agents/{agent_id}/versions/{version_id}")
async def get_version(
    agent_id: str,
    version_id: uuid.UUID,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> VersionOut:
    a = await get_scoped(session, Agent, ctx.org_id, agent_id, what="Agent")
    v = (
        await session.execute(
            select(AgentVersion).where(AgentVersion.agent_id == a.id, AgentVersion.id == version_id)
        )
    ).scalar_one_or_none()
    if v is None:
        raise Problem(404, "Version not found")
    return VersionOut(
        id=v.id,
        agent_id=v.agent_id,
        version_number=v.version_number,
        changelog=v.changelog,
        published_by=v.published_by,
        created_at=v.created_at,
        config=v.config,
    )


@router.post("/orgs/{org_id}/models/test")
async def test_model(
    body: dict[str, Any],
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await test_model_config(session, ctx.org_id, body)


@router.get("/models/catalog")
async def models_catalog() -> dict[str, Any]:
    return public_catalog()
