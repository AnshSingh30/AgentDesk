from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import OrgContext, get_db, require_membership
from app.db.models import IngestionJob
from app.jobs.queue import job_status

router = APIRouter(tags=["jobs"])


@router.get("/orgs/{org_id}/jobs/{job_id}")
async def get_job(
    job_id: str,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    info = await job_status(job_id)
    try:
        ij = (
            await session.execute(
                select(IngestionJob).where(
                    IngestionJob.id == uuid.UUID(job_id), IngestionJob.org_id == ctx.org_id
                )
            )
        ).scalar_one_or_none()
    except ValueError:
        ij = None
    if ij is not None:
        info.update(
            {
                "job_type": ij.job_type,
                "db_status": ij.status,
                "attempts": ij.attempts,
                "error": ij.error,
                "metrics": ij.metrics,
                "document_id": ij.document_id,
                "kb_id": ij.kb_id,
            }
        )
    return info
