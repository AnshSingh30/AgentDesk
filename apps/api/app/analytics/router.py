"""Analytics overview (PRD AN-1) computed from messages, run traces, usage events and feedback."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import Integer, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import OrgContext, get_db, require_membership
from app.db.models import Conversation, Feedback, Message, RunTrace, UsageEvent

router = APIRouter(tags=["analytics"])


@router.get("/orgs/{org_id}/analytics/overview")
async def overview(
    agent_id: uuid.UUID | None = None,
    deployment_id: uuid.UUID | None = None,
    from_: datetime | None = None,
    to: datetime | None = None,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    end = to or datetime.now(UTC)
    start = from_ or (end - timedelta(days=30))
    conv_filter = [Conversation.org_id == ctx.org_id]
    if agent_id:
        conv_filter.append(Conversation.agent_id == agent_id)
    if deployment_id:
        conv_filter.append(Conversation.deployment_id == deployment_id)
    base = (
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.org_id == ctx.org_id, Message.created_at >= start, Message.created_at < end, *conv_filter
        )
    )
    msgs = (await session.execute(base)).scalars().all()
    by_day: dict[str, dict[str, int]] = {}
    tool_split: dict[str, int] = {"rag": 0, "sql": 0, "web_search": 0, "none": 0}
    latencies: list[int] = []
    assistant = 0
    no_answer = errors = sql_failed = 0
    for m in msgs:
        day = m.created_at.date().isoformat()
        by_day.setdefault(day, {"in": 0, "out": 0})
        if m.role == "user":
            by_day[day]["in"] += 1
        elif m.role == "assistant":
            by_day[day]["out"] += 1
            assistant += 1
            tools = (m.route or {}).get("tools") or []
            for t in tools:
                tool_split[t] = tool_split.get(t, 0) + 1
            if not tools:
                tool_split["none"] += 1
            if m.latency_ms:
                latencies.append(m.latency_ms)
            if m.error:
                errors += 1
            if m.tool_summary and "SQL: failed" in m.tool_summary:
                sql_failed += 1
            if any((e or {}).get("check") == "no_answer_normalisation" for e in (m.guardrail_events or [])):
                no_answer += 1
    latencies.sort()

    def pct(p: float) -> int | None:
        if not latencies:
            return None
        return latencies[min(len(latencies) - 1, int(len(latencies) * p))]

    contacts = (
        await session.execute(
            select(func.count(func.distinct(Conversation.contact_id))).where(
                Conversation.last_message_at >= start,
                Conversation.last_message_at < end,
                Conversation.contact_id.is_not(None),
                *conv_filter,
            )
        )
    ).scalar_one()
    convs = (
        await session.execute(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.last_message_at >= start, Conversation.last_message_at < end, *conv_filter)
        )
    ).scalar_one()
    sql_ok = (
        await session.execute(
            select(func.count())
            .select_from(RunTrace)
            .where(
                RunTrace.org_id == ctx.org_id,
                RunTrace.created_at >= start,
                RunTrace.created_at < end,
                func.jsonb_array_length(RunTrace.sql_attempts) > 0,
            )
        )
    ).scalar_one()
    fb = (
        await session.execute(
            select(
                func.coalesce(func.sum(cast(Feedback.rating > 0, Integer)), 0),
                func.coalesce(func.sum(cast(Feedback.rating < 0, Integer)), 0),
            ).where(Feedback.org_id == ctx.org_id, Feedback.created_at >= start, Feedback.created_at < end)
        )
    ).one()
    usage_stmt = select(
        UsageEvent.model,
        func.coalesce(func.sum(UsageEvent.input_tokens), 0),
        func.coalesce(func.sum(UsageEvent.output_tokens), 0),
        func.coalesce(func.sum(UsageEvent.est_cost_usd), 0),
    ).where(
        UsageEvent.org_id == ctx.org_id,
        UsageEvent.kind == "llm",
        UsageEvent.created_at >= start,
        UsageEvent.created_at < end,
    )
    if agent_id:
        usage_stmt = usage_stmt.where(UsageEvent.agent_id == agent_id)
    if deployment_id:
        usage_stmt = usage_stmt.where(UsageEvent.deployment_id == deployment_id)
    usage_rows = (await session.execute(usage_stmt.group_by(UsageEvent.model))).all()
    sql_runs = sum(1 for m in msgs if m.role == "assistant" and "sql" in ((m.route or {}).get("tools") or []))
    return {
        "from": start,
        "to": end,
        "totals": {
            "messages_in": sum(d["in"] for d in by_day.values()),
            "messages_out": assistant,
            "conversations": convs,
            "unique_contacts": contacts,
            "errors": errors,
        },
        "messages_per_day": [{"date": d, **v} for d, v in sorted(by_day.items())],
        "tool_split": tool_split,
        "latency_ms": {
            "p50": pct(0.5),
            "p95": pct(0.95),
            "avg": int(sum(latencies) / len(latencies)) if latencies else None,
        },
        "sql": {
            "runs": sql_runs,
            "failed": sql_failed,
            "success_rate": round((sql_runs - sql_failed) / sql_runs, 3) if sql_runs else None,
            "traces_with_sql": sql_ok,
        },
        "no_answer_rate": round(no_answer / assistant, 3) if assistant else None,
        "feedback": {
            "positive": int(fb[0]),
            "negative": int(fb[1]),
            "ratio": round(int(fb[0]) / (int(fb[0]) + int(fb[1])), 3) if (int(fb[0]) + int(fb[1])) else None,
        },
        "usage_by_model": [
            {
                "model": r[0] or "unknown",
                "input_tokens": int(r[1]),
                "output_tokens": int(r[2]),
                "est_cost_usd": float(r[3]),
            }
            for r in usage_rows
        ],
    }


__all__ = ["case", "router"]
