"""Org-scoped repository helpers (PRD ORG-4): every tenant query goes through here with org_id."""

from __future__ import annotations

import uuid
from typing import Any, TypeVar

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Problem
from app.db.base import Base

T = TypeVar("T", bound=Base)


def scoped(model: type[T], org_id: uuid.UUID, *, include_deleted: bool = False) -> Select[tuple[T]]:
    stmt = select(model).where(model.org_id == org_id)  # type: ignore[attr-defined]
    if not include_deleted and hasattr(model, "deleted_at"):
        stmt = stmt.where(model.deleted_at.is_(None))  # type: ignore[attr-defined]
    return stmt


async def get_scoped(
    session: AsyncSession,
    model: type[T],
    org_id: uuid.UUID,
    id_: uuid.UUID | str,
    *,
    what: str | None = None,
    include_deleted: bool = False,
) -> T:
    try:
        uid = uuid.UUID(str(id_))
    except ValueError as e:
        raise Problem(404, f"{what or model.__name__} not found") from e
    stmt = scoped(model, org_id, include_deleted=include_deleted).where(model.id == uid)  # type: ignore[attr-defined]
    obj = (await session.execute(stmt)).scalar_one_or_none()
    if obj is None:
        raise Problem(404, f"{what or model.__name__} not found")
    return obj


async def audit(
    session: AsyncSession,
    *,
    org_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None,
    action: str,
    target_type: str | None = None,
    target_id: Any = None,
    metadata: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    from app.db.models import AuditLog

    session.add(
        AuditLog(
            org_id=org_id,
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            metadata_=metadata or {},
            ip=ip,
            user_agent=(user_agent or "")[:400] or None,
        )
    )
