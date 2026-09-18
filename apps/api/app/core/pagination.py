"""Cursor (keyset) pagination on (created_at, id) descending (PRD §16)."""

from __future__ import annotations

import base64
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Problem


def encode_cursor(created_at: datetime, id_: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{id_}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        ts, id_ = base64.urlsafe_b64decode(padded).decode().split("|")
        return datetime.fromisoformat(ts), uuid.UUID(id_)
    except Exception as e:  # noqa: BLE001
        raise Problem(400, "Invalid cursor") from e


async def keyset_page(
    session: AsyncSession, stmt: Select, model: Any, limit: int, cursor: str | None
) -> tuple[list[Any], str | None]:
    limit = max(1, min(limit, 200))
    if cursor:
        c_ts, c_id = decode_cursor(cursor)
        stmt = stmt.where(or_(model.created_at < c_ts, and_(model.created_at == c_ts, model.id < c_id)))
    stmt = stmt.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1)
    rows = list((await session.execute(stmt)).scalars().all())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_cursor(last.created_at, last.id)
    return rows, next_cursor
