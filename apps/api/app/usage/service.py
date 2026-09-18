"""Usage metering (PRD G6, §15.6)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import llm_tokens_total
from app.db.models import UsageEvent


@dataclass
class UsageRecord:
    kind: str  # llm | embedding | web_search | sql | message_in | message_out
    model: str | None = None
    provider: str | None = None
    credential_mode: str = "platform"
    input_tokens: int = 0
    output_tokens: int = 0
    units: int = 1
    est_cost_usd: float = 0.0
    node: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "model": self.model,
            "provider": self.provider,
            "credential_mode": self.credential_mode,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "units": self.units,
            "est_cost_usd": round(self.est_cost_usd, 6),
            "node": self.node,
        }


async def record_usage(
    session: AsyncSession,
    org_id: uuid.UUID,
    records: list[UsageRecord],
    *,
    agent_id: uuid.UUID | None = None,
    deployment_id: uuid.UUID | None = None,
    conversation_id: uuid.UUID | None = None,
    run_id: str | None = None,
) -> None:
    for rec in records:
        session.add(
            UsageEvent(
                org_id=org_id,
                agent_id=agent_id,
                deployment_id=deployment_id,
                conversation_id=conversation_id,
                run_id=run_id,
                kind=rec.kind,
                provider=rec.provider,
                model=rec.model,
                credential_mode=rec.credential_mode,
                input_tokens=rec.input_tokens,
                output_tokens=rec.output_tokens,
                units=rec.units,
                est_cost_usd=rec.est_cost_usd,
            )
        )
        if rec.kind == "llm" and rec.model:
            llm_tokens_total.labels(rec.model, "input").inc(rec.input_tokens)
            llm_tokens_total.labels(rec.model, "output").inc(rec.output_tokens)


async def usage_summary(
    session: AsyncSession, org_id: uuid.UUID, start: datetime, end: datetime, group_by: str = "kind"
) -> list[dict[str, Any]]:
    col = {
        "kind": UsageEvent.kind,
        "model": UsageEvent.model,
        "agent": UsageEvent.agent_id,
        "deployment": UsageEvent.deployment_id,
        "day": func.date_trunc("day", UsageEvent.created_at),
    }.get(group_by, UsageEvent.kind)
    stmt: Select[Any] = (
        select(
            col.label("key"),
            func.count().label("events"),
            func.coalesce(func.sum(UsageEvent.input_tokens), 0),
            func.coalesce(func.sum(UsageEvent.output_tokens), 0),
            func.coalesce(func.sum(UsageEvent.units), 0),
            func.coalesce(func.sum(UsageEvent.est_cost_usd), 0),
        )
        .where(UsageEvent.org_id == org_id, UsageEvent.created_at >= start, UsageEvent.created_at < end)
        .group_by(col)
        .order_by(col)
    )
    rows = (await session.execute(stmt)).all()
    out = []
    for key, events, tin, tout, units, cost in rows:
        if isinstance(key, datetime | date):
            key = key.isoformat()
        out.append(
            {
                "key": str(key) if key is not None else None,
                "events": events,
                "input_tokens": int(tin),
                "output_tokens": int(tout),
                "units": int(units),
                "est_cost_usd": float(cost),
            }
        )
    return out
