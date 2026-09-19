"""Annotation replies: curated Q&A pairs answered verbatim when a question is similar enough
(threshold in the agent's `annotations` config). Modelled on Dify's annotation reply."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import OrgContext, get_db, require_membership
from app.core.errors import Problem
from app.db.models import Agent, Annotation
from app.db.repo import get_scoped

router = APIRouter(tags=["annotations"])
MAX_PER_AGENT = 500


class AnnotationIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(min_length=1, max_length=8000)


class AnnotationPatch(BaseModel):
    question: str | None = Field(default=None, min_length=1, max_length=2000)
    answer: str | None = Field(default=None, min_length=1, max_length=8000)


def _out(a: Annotation) -> dict[str, Any]:
    return {
        "id": a.id,
        "question": a.question,
        "answer": a.answer,
        "hit_count": a.hit_count,
        "last_hit_at": a.last_hit_at,
        "created_at": a.created_at,
    }


async def _get(session: AsyncSession, ctx: OrgContext, agent: Agent, ann_id: uuid.UUID) -> Annotation:
    ann = (
        await session.execute(
            select(Annotation).where(
                Annotation.id == ann_id, Annotation.org_id == ctx.org_id, Annotation.agent_id == agent.id
            )
        )
    ).scalar_one_or_none()
    if ann is None:
        raise Problem(404, "Annotation not found")
    return ann


@router.get("/orgs/{org_id}/agents/{agent_id}/annotations")
async def list_annotations(
    agent_id: str,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    agent = await get_scoped(session, Agent, ctx.org_id, agent_id, what="Agent")
    rows = await session.execute(
        select(Annotation)
        .where(Annotation.org_id == ctx.org_id, Annotation.agent_id == agent.id)
        .order_by(Annotation.created_at.desc())
    )
    return {"items": [_out(a) for a in rows.scalars()]}


@router.post("/orgs/{org_id}/agents/{agent_id}/annotations", status_code=201)
async def create_annotation(
    agent_id: str,
    body: AnnotationIn,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    agent = await get_scoped(session, Agent, ctx.org_id, agent_id, what="Agent")
    count = await session.scalar(
        select(func.count()).where(Annotation.org_id == ctx.org_id, Annotation.agent_id == agent.id)
    )
    if (count or 0) >= MAX_PER_AGENT:
        raise Problem(409, f"An agent can have at most {MAX_PER_AGENT} annotations")
    ann = Annotation(
        org_id=ctx.org_id,
        agent_id=agent.id,
        question=body.question.strip(),
        answer=body.answer.strip(),
        created_by=ctx.user.id,
    )
    session.add(ann)
    await session.flush()
    return _out(ann)


@router.patch("/orgs/{org_id}/agents/{agent_id}/annotations/{ann_id}")
async def update_annotation(
    agent_id: str,
    ann_id: uuid.UUID,
    body: AnnotationPatch,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    agent = await get_scoped(session, Agent, ctx.org_id, agent_id, what="Agent")
    ann = await _get(session, ctx, agent, ann_id)
    if body.question is not None:
        ann.question = body.question.strip()
    if body.answer is not None:
        ann.answer = body.answer.strip()
    await session.flush()
    return _out(ann)


@router.delete("/orgs/{org_id}/agents/{agent_id}/annotations/{ann_id}", status_code=204)
async def delete_annotation(
    agent_id: str,
    ann_id: uuid.UUID,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> Response:
    agent = await get_scoped(session, Agent, ctx.org_id, agent_id, what="Agent")
    await session.delete(await _get(session, ctx, agent, ann_id))
    return Response(status_code=204)
