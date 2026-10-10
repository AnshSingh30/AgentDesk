"""Run orchestration: prepare context, stream the graph, persist messages/traces/usage (PRD §8.4)."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from langgraph.types import Command
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.schemas import AgentConfig
from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.core.metrics import agent_run_duration, agent_runs_total
from app.core.redis import get_redis
from app.db.models import (
    Agent,
    AgentVersion,
    Annotation,
    Conversation,
    Dataset,
    KnowledgeBase,
    Message,
    Organization,
    ProviderCredential,
    RunTrace,
)
from app.db.session import get_sessionmaker
from app.llm.gateway import LLMError, org_llm_slot, resolve_model
from app.memory.history import load_history
from app.rag.embeddings import embed_dense
from app.runtime.context import RunCancelled, RunContext
from app.runtime.graph import get_graph
from app.sql.context import load_dataset_context
from app.usage.service import UsageRecord, record_usage

log = structlog.get_logger()


async def load_agent_config(
    session: AsyncSession, agent: Agent, version_id: uuid.UUID | None
) -> tuple[AgentConfig, uuid.UUID | None]:
    """Draft config (Redis-cached 60 s, busted on save) or an immutable published version."""
    if version_id is not None:
        v = await session.get(AgentVersion, version_id)
        if v is None or v.agent_id != agent.id:
            raise ValueError("version not found")
        return AgentConfig.model_validate(v.config), v.id
    r = get_redis()
    key = f"agentcfg:draft:{agent.id}"
    cached = await r.get(key)
    if cached:
        return AgentConfig.model_validate(json.loads(cached)), None
    cfg = AgentConfig.model_validate(agent.draft_config or {})
    await r.setex(key, 60, json.dumps(cfg.model_dump(mode="json")))
    return cfg, None


async def _credential_key(session: AsyncSession, org_id: uuid.UUID, provider: str) -> str | None:
    c = (
        (
            await session.execute(
                select(ProviderCredential)
                .where(ProviderCredential.org_id == org_id, ProviderCredential.provider == provider)
                .order_by(ProviderCredential.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    return decrypt_secret(c.encrypted_api_key) if c else None


async def prepare_run(
    session: AsyncSession,
    *,
    org: Organization,
    agent: Agent,
    cfg: AgentConfig,
    channel: str,
    conversation: Conversation,
    user_text: str,
    agent_version_id: uuid.UUID | None = None,
    deployment_id: uuid.UUID | None = None,
    run_id: str | None = None,
    confirm_sql: bool = False,
    store_user_message: bool = True,
    external_id: str | None = None,
) -> RunContext:
    run_id = run_id or uuid.uuid4().hex[:24]
    main_model = await resolve_model(session, org.id, cfg.model.model_dump(mode="json"))
    utility_model = await resolve_model(session, org.id, cfg.model.model_dump(mode="json"), utility=True)
    history = await load_history(session, conversation.id, cfg.memory)
    user_message_id = None
    if store_user_message:
        msg = Message(
            org_id=org.id,
            conversation_id=conversation.id,
            role="user",
            content=user_text,
            run_id=run_id,
            external_id=external_id,
            status="received",
        )
        session.add(msg)
        conversation.message_count += 1
        conversation.last_message_at = datetime.now(UTC)
        await session.flush()
        user_message_id = msg.id
    dataset = dataset_ctx = None
    dataset_summary = ""
    if cfg.tools.sql.enabled and cfg.tools.sql.dataset_id:
        dataset = (
            await session.execute(
                select(Dataset).where(
                    Dataset.id == cfg.tools.sql.dataset_id,
                    Dataset.org_id == org.id,
                    Dataset.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if dataset is not None:
            dataset_ctx = await load_dataset_context(session, dataset)
            dataset_summary = "; ".join(
                f"{t.name}: {(t.description or '')[:80]}" for t in dataset_ctx.tables[:25]
            )
    kb_summary = ""
    if cfg.tools.rag.enabled and cfg.tools.rag.kb_ids:
        kbs = (
            (
                await session.execute(
                    select(KnowledgeBase).where(
                        KnowledgeBase.org_id == org.id,
                        KnowledgeBase.id.in_(cfg.tools.rag.kb_ids),
                        KnowledgeBase.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        kb_summary = "; ".join(f"{k.name}: {(k.description or '')[:80]} ({k.doc_count} docs)" for k in kbs)
    annotations: list[dict[str, Any]] = []
    if cfg.annotations.enabled:
        # ponytail: linear scan over <=500 curated Q&As per agent; move to Qdrant if agents need more
        rows = (
            await session.execute(
                select(Annotation.id, Annotation.question, Annotation.answer)
                .where(Annotation.org_id == org.id, Annotation.agent_id == agent.id)
                .order_by(Annotation.created_at)
                .limit(500)
            )
        ).all()
        if rows:
            vectors = await embed_dense([r.question for r in rows])  # Redis-cached per question
            annotations = [
                {"id": str(r.id), "answer": r.answer, "vector": v} for r, v in zip(rows, vectors, strict=True)
            ]
    metadata = {
        "org_id": str(org.id),
        "agent_id": str(agent.id),
        "agent_version": str(agent_version_id or "draft"),
        "deployment_id": str(deployment_id or ""),
        "channel": channel,
        "run_id": run_id,
    }
    return RunContext(
        run_id=run_id,
        org_id=org.id,
        org_name=org.name,
        agent_id=agent.id,
        agent_name=agent.name,
        conversation_id=conversation.id,
        channel=channel,
        cfg=cfg,
        main_model=main_model,
        utility_model=utility_model,
        history=history,
        agent_version_id=agent_version_id,
        deployment_id=deployment_id,
        user_message_id=user_message_id,
        dataset=dataset,
        dataset_ctx=dataset_ctx,
        kb_summary=kb_summary,
        dataset_summary=dataset_summary,
        tavily_key=await _credential_key(session, org.id, "tavily"),
        rerank_key=await _credential_key(session, org.id, "cohere"),
        confirm_sql=confirm_sql and channel == "playground",
        annotations=annotations,
        metadata=metadata,
    )


@dataclass
class RunOutcome:
    state: dict[str, Any]
    message_id: uuid.UUID | None
    latency_ms: int
    interrupted: bool = False
    error: str | None = None


def _config(ctx: RunContext) -> dict[str, Any]:
    return {"configurable": {"thread_id": ctx.run_id, "ctx": ctx}, "recursion_limit": 60}


async def stream_run(
    ctx: RunContext,
    user_text: str,
    *,
    resume: dict[str, Any] | None = None,
    state_out: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield SSE-style events {event, data}; persists the run at the end (unless interrupted).
    `state_out`, if given, receives the final graph state (chunks, SQL attempts, usage...)."""
    graph = get_graph()
    config = _config(ctx)
    started = time.perf_counter()
    if resume is None:
        initial: Any = {
            "run_id": ctx.run_id,
            "org_id": str(ctx.org_id),
            "channel": ctx.channel,
            "user_message": user_text,
            "history": ctx.history,
            "rag_chunks": [],
            "sql_attempts": [],
            "web_results": [],
            "guardrail_events": [],
            "usage": [],
            "timings": {},
            "tool_errors": [],
        }
    else:
        initial = Command(resume=resume)
    interrupted = False
    error: str | None = None
    try:
        async with org_llm_slot(ctx.org_id):
            async with asyncio.timeout(
                settings.run_timeout_seconds
                if ctx.channel == "whatsapp"
                else settings.run_timeout_seconds * 2
            ):
                async for mode, chunk in graph.astream(initial, config, stream_mode=["custom", "updates"]):
                    if mode == "custom":
                        yield chunk
                    elif mode == "updates" and isinstance(chunk, dict) and "__interrupt__" in chunk:
                        intr = chunk["__interrupt__"]
                        value = (
                            intr[0].value
                            if isinstance(intr, list | tuple) and intr
                            else getattr(intr, "value", intr)
                        )
                        interrupted = True
                        yield {
                            "event": "sql_confirm_required",
                            "data": {**(value or {}), "run_id": ctx.run_id},
                        }
    except RunCancelled:
        error = "cancelled"
        yield {"event": "error", "data": {"code": "cancelled", "message": "Run cancelled"}}
    except TimeoutError:
        error = "timeout"
        yield {"event": "error", "data": {"code": "timeout", "message": "The run exceeded the time limit"}}
    except LLMError as e:
        error = f"{e.code}: {e.message}"
        yield {"event": "error", "data": {"code": e.code, "message": e.message}}
    except Exception as e:  # noqa: BLE001
        log.exception("run_failed", run_id=ctx.run_id)
        error = f"{type(e).__name__}: {str(e)[:300]}"
        yield {
            "event": "error",
            "data": {"code": "internal", "message": "Something went wrong while answering"},
        }
    if interrupted and error is None:
        return
    snapshot = await graph.aget_state(config)
    state = dict(snapshot.values or {})
    if state_out is not None:
        state_out.update(state)
    latency_ms = int((time.perf_counter() - started) * 1000)
    outcome = await persist_run(ctx, state, latency_ms, error)
    usage = state.get("usage") or []
    yield {
        "event": "usage",
        "data": {
            "input_tokens": sum(int(u.get("input_tokens") or 0) for u in usage),
            "output_tokens": sum(int(u.get("output_tokens") or 0) for u in usage),
            "est_cost_usd": round(sum(float(u.get("est_cost_usd") or 0) for u in usage), 6),
            "latency_ms": latency_ms,
            "timings": state.get("timings") or {},
            "records": usage,
        },
    }
    yield {
        "event": "done",
        "data": {
            "run_id": ctx.run_id,
            "message_id": str(outcome.message_id) if outcome.message_id else None,
            "answer": state.get("answer", ""),
            "formatted": state.get("formatted") or [],
            "citations": state.get("citations") or [],
            "route": state.get("route"),
            "no_answer": bool(state.get("no_answer")),
            "error": error,
            "latency_ms": latency_ms,
        },
    }
    if error is None:
        get_graph().checkpointer.delete_thread(ctx.run_id) if hasattr(
            get_graph().checkpointer, "delete_thread"
        ) else None


async def run_once(ctx: RunContext, user_text: str) -> RunOutcome:
    """Non-streaming run (WhatsApp)."""
    final: dict[str, Any] = {}
    full: dict[str, Any] = {}
    error = None
    async for ev in stream_run(ctx, user_text, state_out=full):
        if ev["event"] == "done":
            final = ev["data"]
        elif ev["event"] == "error":
            error = ev["data"].get("message")
    return RunOutcome(
        state={**full, **final},
        message_id=uuid.UUID(final["message_id"]) if final.get("message_id") else None,
        latency_ms=int(final.get("latency_ms") or 0),
        error=final.get("error") or error,
    )


def _tool_summary(state: dict[str, Any]) -> str | None:
    parts = []
    if state.get("rag_chunks"):
        parts.append(f"KB: {len(state['rag_chunks'])} chunks")
    if state.get("sql_status") == "ok" and state.get("sql_result"):
        tables = ", ".join(sorted({str(t) for a in state.get("sql_attempts") or [] for t in []}))
        parts.append(
            f"SQL: {state['sql_result'].get('row_count', 0)} rows{(' from ' + tables) if tables else ''}"
        )
    elif state.get("sql_status") in ("failed", "cancelled"):
        parts.append(f"SQL: {state['sql_status']}")
    if state.get("web_results"):
        parts.append(f"Web: {len(state['web_results'])} results")
    return "; ".join(parts) or None


async def persist_run(
    ctx: RunContext, state: dict[str, Any], latency_ms: int, error: str | None
) -> RunOutcome:
    route = (state.get("route") or {}).get("tools") or []
    agent_runs_total.labels("+".join(route) or "none", ctx.channel, "error" if error else "ok").inc()
    agent_run_duration.labels(ctx.channel).observe(latency_ms / 1000)
    async with get_sessionmaker()() as session:
        if state.get("annotation"):
            await session.execute(
                update(Annotation)
                .where(Annotation.id == uuid.UUID(state["annotation"]["id"]), Annotation.org_id == ctx.org_id)
                .values(hit_count=Annotation.hit_count + 1, last_hit_at=datetime.now(UTC))
            )
        conv = await session.get(Conversation, ctx.conversation_id)
        answer = state.get("answer") or (
            "" if error is None else "I'm having trouble right now, please try again in a minute."
        )
        msg = Message(
            org_id=ctx.org_id,
            conversation_id=ctx.conversation_id,
            role="assistant",
            content=answer,
            run_id=ctx.run_id,
            status="sent" if error is None else "failed",
            route=state.get("route"),
            tool_summary=_tool_summary(state),
            citations=state.get("citations") or [],
            guardrail_events=state.get("guardrail_events") or [],
            latency_ms=latency_ms,
            error=error,
        )
        session.add(msg)
        await session.flush()
        session.add(
            RunTrace(
                org_id=ctx.org_id,
                message_id=msg.id,
                run_id=ctx.run_id,
                node_timings=state.get("timings") or {},
                rag_chunks=state.get("rag_chunks") or [],
                sql_attempts=state.get("sql_attempts") or [],
                web_results=state.get("web_results") or [],
                usage=state.get("usage") or [],
                langsmith_run_url=None,
            )
        )
        records = [
            UsageRecord(
                kind=u.get("kind", "llm"),
                model=u.get("model"),
                provider=u.get("provider"),
                credential_mode=u.get("credential_mode", "platform"),
                input_tokens=int(u.get("input_tokens") or 0),
                output_tokens=int(u.get("output_tokens") or 0),
                units=int(u.get("units") or 1),
                est_cost_usd=float(u.get("est_cost_usd") or 0),
            )
            for u in state.get("usage") or []
        ]
        records.append(UsageRecord(kind="message_in", units=1))
        records.append(UsageRecord(kind="message_out", units=1))
        await record_usage(
            session,
            ctx.org_id,
            records,
            agent_id=ctx.agent_id,
            deployment_id=ctx.deployment_id,
            conversation_id=ctx.conversation_id,
            run_id=ctx.run_id,
        )
        if conv is not None:
            conv.message_count += 1
            conv.last_message_at = datetime.now(UTC)
            flags = dict(conv.flags or {})
            if error:
                flags["has_error"] = True
            if state.get("no_answer"):
                flags["no_answer"] = True
            if state.get("sql_status") == "failed":
                flags["sql_failed"] = True
            conv.flags = flags
        await session.commit()
        return RunOutcome(state=state, message_id=msg.id, latency_ms=latency_ms, error=error)
