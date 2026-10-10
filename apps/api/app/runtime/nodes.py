"""Graph nodes (PRD §9.4)."""

from __future__ import annotations

import math
import re
import time
from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.types import Send
from pydantic import BaseModel, Field

from app.guardrails.input import check_input
from app.guardrails.output import grounding_check, normalise_output
from app.llm import jev
from app.llm.gateway import LLMError, StreamDone, chat, stream_chat, structured, usage_record
from app.rag.embeddings import embed_dense
from app.rag.retrieval import retrieve
from app.runtime.context import RunContext, ctx_from, emit
from app.runtime.formatter import format_for_whatsapp
from app.runtime.prompts import (
    build_messages,
    format_chunks,
    format_sql_result,
    format_web,
    rewrite_prompt,
    router_prompt,
)
from app.runtime.state import AgentState
from app.sql.agent import SqlRuntime, sql_graph
from app.websearch.tavily import WebSearchError, search

log = structlog.get_logger()
CITATION_MARK = re.compile(r"\[(\d{1,2})\]")


class RouteDecision(BaseModel):
    tools: list[str] = Field(default_factory=list)
    reason: str = Field("", max_length=500)


class QueryRewrite(BaseModel):
    query: str = Field(max_length=2000)


def _usage(rm: Any, result: Any, node: str) -> dict[str, Any]:
    return usage_record(rm, result, node).as_dict()


async def input_guardrails(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    ctx = ctx_from(config)
    await ctx.check_cancelled()
    t0 = time.perf_counter()
    utility = ctx.utility_model if ctx.cfg.guardrails.advanced.input_classifier else None
    res, usage = await check_input(state["user_message"], ctx.cfg, ctx.channel, utility, ctx.metadata)
    for ev in res.events:
        emit("guardrail", {"stage": "input", **ev})
    return {
        "guardrail_input": res.as_dict(),
        "blocked": res.blocked,
        "answer": res.message or "" if res.blocked else "",
        "guardrail_events": [{"stage": "input", **e} for e in res.events],
        "usage": usage,
        "timings": {"input_guardrails": round(time.perf_counter() - t0, 4)},
    }


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


async def annotation_reply(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Answer verbatim from a curated annotation when the question is close enough (no LLM call)."""
    ctx = ctx_from(config)
    if not ctx.annotations:
        return {}
    t0 = time.perf_counter()
    try:
        query = (await embed_dense([state["user_message"]]))[0]
    except Exception as e:  # noqa: BLE001 - embeddings down: fall through to the normal graph
        log.warning("annotation_embed_failed", error=str(e)[:200])
        return {}
    score, best = max(((_cosine(query, a["vector"]), a) for a in ctx.annotations), key=lambda x: x[0])
    timings = {"annotation_reply": round(time.perf_counter() - t0, 4)}
    if score < ctx.cfg.annotations.threshold:
        return {"timings": timings}
    route = {"tools": [], "reason": f"annotation reply (similarity {score:.2f})"}
    emit("route", route)
    emit("token", {"text": best["answer"]})
    return {
        "answer": best["answer"],
        "annotation": {"id": best["id"], "score": round(score, 4)},
        "route": route,
        "citations": [],
        "no_answer": False,
        "timings": timings,
    }


async def blocked_response(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    emit("token", {"text": state.get("answer", "")})
    return {
        "citations": [],
        "no_answer": False,
        "route": {"tools": [], "reason": "blocked by input guardrails"},
    }


async def query_rewrite(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    ctx = ctx_from(config)
    await ctx.check_cancelled()
    tools = ctx.cfg.tools.enabled_names()
    if not ctx.history or not tools or not ctx.cfg.tools.rag.query_rewrite:
        return {"standalone_query": state["user_message"]}
    t0 = time.perf_counter()
    try:
        res, chat_result = await structured(
            ctx.utility_model,
            [{"role": "user", "content": rewrite_prompt(ctx.history, state["user_message"])}],
            QueryRewrite,
            max_tokens=300,
            metadata={**ctx.metadata, "node": "query_rewrite"},
        )
        query = res.query.strip() or state["user_message"]
        usage = [_usage(ctx.utility_model, chat_result, "query_rewrite")]
    except Exception as e:  # noqa: BLE001
        log.warning("query_rewrite_failed", error=str(e))
        query, usage = state["user_message"], []
    if query != state["user_message"]:
        emit("debug", {"node": "query_rewrite", "standalone_query": query})
    return {
        "standalone_query": query,
        "usage": usage,
        "timings": {"query_rewrite": round(time.perf_counter() - t0, 4)},
    }


async def router(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    ctx = ctx_from(config)
    await ctx.check_cancelled()
    tools = ctx.cfg.tools.enabled_names()
    t0 = time.perf_counter()
    usage: list[dict[str, Any]] = []
    if not tools:
        decision = {"tools": [], "reason": "No tools are enabled; answering directly."}
    elif len(tools) == 1:
        decision = {"tools": tools, "reason": f"Single tool enabled ({tools[0]}); tool-gated flow."}
    elif jev.enabled():
        query = state.get("standalone_query") or state["user_message"]
        try:
            decision, jev_usage = await _jev_route(ctx, query, tools)
            usage.append(jev_usage)
        except Exception as e:  # noqa: BLE001 - JEV down: fall back to all tools
            log.warning("jev_router_failed", error=str(e))
            decision = {
                "tools": tools,
                "reason": f"Router unavailable ({type(e).__name__}); using all enabled tools.",
            }
    else:
        query = state.get("standalone_query") or state["user_message"]
        try:
            res, chat_result = await structured(
                ctx.utility_model,
                [
                    {
                        "role": "user",
                        "content": router_prompt(ctx.cfg, query, tools, ctx.kb_summary, ctx.dataset_summary),
                    }
                ],
                RouteDecision,
                max_tokens=200,
                metadata={**ctx.metadata, "node": "router"},
            )
            chosen = [t for t in res.tools if t in tools]
            decision = {"tools": chosen, "reason": res.reason}
            usage.append(_usage(ctx.utility_model, chat_result, "router"))
        except Exception as e:  # noqa: BLE001 - fall back to all tools
            log.warning("router_failed", error=str(e))
            decision = {
                "tools": tools,
                "reason": f"Router unavailable ({type(e).__name__}); using all enabled tools.",
            }
    emit("route", decision)
    return {"route": decision, "usage": usage, "timings": {"router": round(time.perf_counter() - t0, 4)}}


JEV_TOOL_QUESTIONS = {
    "rag": (
        "Answering the user message requires looking up the organisation's documents (policies, handbooks, "
        "procedures, rules, entitlements, contacts).",
        "Needs a fact, rule, entitlement or explanation written in the organisation's documents",
        "Nothing in it needs the documents (pure data/numbers, greeting, thanks or small talk)",
    ),
    "sql": (
        "Answering the user message requires querying the connected dataset (counting, listing, totals, averages, "
        "trends or comparisons over records).",
        "Needs numbers, lists or comparisons computed from records in the dataset tables",
        "Nothing in it needs the dataset (a policy/document question, greeting, thanks or small talk)",
    ),
    "web_search": (
        "Answering the user message requires searching the public web for external information.",
        "Needs current or external information that is not in the organisation's documents or dataset",
        "Can be answered from the documents, the dataset, or needs no lookup",
    ),
}


async def _jev_route(ctx: RunContext, query: str, tools: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """One Noul per enabled tool (multi-label: a question may need several); code picks those >= 0.5."""
    sources: dict[str, str] = {}
    if "rag" in tools:
        sources["documents"] = (
            f"{ctx.cfg.routing.rag_instructions} Knowledge bases: {ctx.kb_summary or 'n/a'}"
        )
    if "sql" in tools:
        sources["dataset"] = f"{ctx.cfg.routing.sql_instructions} Tables: {ctx.dataset_summary or 'n/a'}"
    if "web_search" in tools:
        sources["web_search"] = ctx.cfg.routing.web_instructions
    questions = {
        t: {"type": "noul", "instructions": q, "criteria": {"true": yes, "false": no}}
        for t, (q, yes, no) in JEV_TOOL_QUESTIONS.items()
        if t in tools
    }
    answers, usage = await jev.ask(
        {"user_message": query, "available_sources": sources}, questions, node="router"
    )
    probs = {t: float(answers[t]["noul"]) for t in questions}
    chosen = [t for t in tools if probs.get(t, 0.0) >= 0.5]
    reason = "JEV: " + ", ".join(f"{t} p={p:.2f}" for t, p in probs.items())
    return {"tools": chosen, "reason": reason, "probabilities": probs}, usage


def dispatch(state: AgentState) -> list[Send] | str:
    tools = (state.get("route") or {}).get("tools") or []
    node_for = {"rag": "rag_retrieve", "sql": "sql_agent", "web_search": "web_search"}
    sends = [Send(node_for[t], state) for t in tools if t in node_for]
    return sends or "collect_context"


async def rag_retrieve(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    ctx = ctx_from(config)
    await ctx.check_cancelled()
    t0 = time.perf_counter()
    rag = ctx.cfg.tools.rag
    query = state.get("standalone_query") or state["user_message"]
    emit("tool_start", {"tool": "rag", "query": query})
    from app.db.session import get_sessionmaker

    try:
        async with get_sessionmaker()() as session:
            chunks = await retrieve(
                session,
                ctx.org_id,
                rag.kb_ids,
                query,
                top_k=rag.top_k,
                threshold=rag.threshold,
                mode=rag.search_mode,
                rerank=rag.rerank.model_dump(),
                rerank_api_key=ctx.rerank_key,
            )
        data = [c.as_dict() for c in chunks]
        emit("tool_result", {"tool": "rag", "count": len(data), "chunks": data})
        return {
            "rag_chunks": data,
            "rag_ran": True,
            "timings": {"rag_retrieve": round(time.perf_counter() - t0, 4)},
            "usage": [
                {
                    "kind": "embedding",
                    "model": "query",
                    "units": 1,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "est_cost_usd": 0.0,
                    "provider": None,
                    "credential_mode": "platform",
                    "node": "rag_retrieve",
                }
            ],
        }
    except Exception as e:  # noqa: BLE001 - tool failure: continue with remaining tools (PRD §9.6)
        log.warning("rag_failed", error=str(e))
        emit("tool_result", {"tool": "rag", "error": str(e)[:200]})
        return {
            "rag_ran": True,
            "tool_errors": [{"tool": "rag", "error": str(e)[:300]}],
            "timings": {"rag_retrieve": round(time.perf_counter() - t0, 4)},
        }


async def sql_agent(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    ctx = ctx_from(config)
    await ctx.check_cancelled()
    t0 = time.perf_counter()
    if ctx.dataset_ctx is None or ctx.dataset is None:
        return {"sql_status": "failed", "tool_errors": [{"tool": "sql", "error": "dataset not available"}]}
    question = state.get("standalone_query") or state["user_message"]
    emit("tool_start", {"tool": "sql", "query": question})
    rt = SqlRuntime(
        ctx.dataset_ctx,
        ctx.main_model,
        ctx.utility_model,
        max_attempts=ctx.cfg.tools.sql.max_attempts,
        max_rows=ctx.cfg.tools.sql.max_rows,
        confirm=ctx.confirm_sql,
        metadata={**ctx.metadata, "node": "sql_agent"},
    )
    hint = "; ".join(f"{m['role']}: {m['content'][:200]}" for m in ctx.history[-4:])
    sub_config = {**config, "configurable": {**config["configurable"], "sql_runtime": rt}}
    # With several tools the router already judged this a data question; the SQL intent gate only
    # decides alone in the single-tool flow (where the router is skipped).
    routed = {"intent": "data_question"} if len(ctx.cfg.tools.enabled_names()) > 1 else {}
    result = await sql_graph.ainvoke(
        {"question": question, "history_hint": hint, "attempts": [], "usage": [], "timings": {}, **routed},
        sub_config,
    )
    status = result.get("status") or ("ok" if result.get("result") else "failed")
    attempts = result.get("attempts") or []
    payload = {
        "tool": "sql",
        "status": status,
        "attempts": attempts,
        "sql": result.get("validated_sql") or result.get("sql"),
        "explanation": result.get("explanation"),
        "assumptions": result.get("assumptions") or [],
        "result": result.get("result"),
        "intent": result.get("intent"),
    }
    emit("tool_result", payload)
    timings = {f"sql.{k}": round(v, 4) for k, v in (result.get("timings") or {}).items()}
    timings["sql_agent"] = round(time.perf_counter() - t0, 4)
    return {
        "sql_attempts": attempts,
        "sql_result": result.get("result"),
        "sql_status": status,
        "sql_explanation": result.get("explanation"),
        "sql_assumptions": result.get("assumptions") or [],
        "usage": result.get("usage") or [],
        "timings": timings,
    }


async def web_search(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    ctx = ctx_from(config)
    await ctx.check_cancelled()
    t0 = time.perf_counter()
    query = state.get("standalone_query") or state["user_message"]
    emit("tool_start", {"tool": "web_search", "query": query})
    w = ctx.cfg.tools.web_search
    try:
        results = await search(
            query,
            api_key=ctx.tavily_key,
            max_results=w.max_results,
            depth=w.depth,
            include_domains=w.include_domains,
            exclude_domains=w.exclude_domains,
        )
        data = [r.as_dict() for r in results]
        emit("tool_result", {"tool": "web_search", "count": len(data), "results": data})
        return {
            "web_results": data,
            "web_ran": True,
            "timings": {"web_search": round(time.perf_counter() - t0, 4)},
            "usage": [
                {
                    "kind": "web_search",
                    "model": "tavily",
                    "units": 1,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "est_cost_usd": 0.0,
                    "provider": "tavily",
                    "credential_mode": "byo" if ctx.tavily_key else "platform",
                    "node": "web_search",
                }
            ],
        }
    except WebSearchError as e:
        emit("tool_result", {"tool": "web_search", "error": str(e)})
        return {
            "web_ran": True,
            "tool_errors": [{"tool": "web_search", "error": str(e)}],
            "timings": {"web_search": round(time.perf_counter() - t0, 4)},
        }


async def collect_context(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Join fan-out results; apply fallback chain KB -> web -> no-answer (RT-4)."""
    ctx = ctx_from(config)
    await ctx.check_cancelled()
    tools = list((state.get("route") or {}).get("tools") or [])
    out: dict[str, Any] = {}
    rag_empty = "rag" in tools and not state.get("rag_chunks")
    if (
        rag_empty
        and ctx.cfg.guardrails.web_fallback
        and ctx.cfg.tools.web_search.enabled
        and not state.get("web_ran")
    ):
        emit("debug", {"node": "collect_context", "fallback": "web_search"})
        res = await web_search(state, config)
        out.update(res)
        out["fallback_used"] = "web"
        state = {**state, **{k: v for k, v in res.items() if k != "timings"}}
    sql_not_data = state.get("sql_status") == "not_data"
    effective = [t for t in tools if not (t == "sql" and sql_not_data)]
    have_context = (
        bool(state.get("rag_chunks"))
        or (state.get("sql_status") == "ok" and state.get("sql_result"))
        or bool(state.get("web_results"))
    )
    out["no_context"] = bool(effective) and not have_context
    return out


def _citations(answer: str, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not chunks:
        return []
    refs = sorted({int(m) for m in CITATION_MARK.findall(answer) if 0 < int(m) <= len(chunks)})
    picked = refs or list(range(1, min(3, len(chunks)) + 1))
    return [
        {
            "n": n,
            **{
                k: chunks[n - 1].get(k)
                for k in ("chunk_id", "document_id", "filename", "page_start", "page_end", "section_title")
            },
            "snippet": (chunks[n - 1].get("text") or "")[:240],
        }
        for n in picked
    ]


async def generate_answer(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    ctx = ctx_from(config)
    await ctx.check_cancelled()
    t0 = time.perf_counter()
    cfg = ctx.cfg
    tools = list((state.get("route") or {}).get("tools") or [])
    if state.get("no_context"):
        if state.get("sql_status") == "failed" or state.get("sql_status") == "cancelled":
            msg = (
                "Query cancelled."
                if state.get("sql_status") == "cancelled"
                else f"{cfg.guardrails.no_answer_message} (I couldn't build a working query for that.)"
            )
        else:
            msg = cfg.guardrails.no_answer_message
        emit("token", {"text": msg})
        return {
            "answer": msg,
            "citations": [],
            "no_answer": True,
            "timings": {"generate_answer": round(time.perf_counter() - t0, 4)},
        }
    chunks = list(state.get("rag_chunks") or [])
    sql_result = state.get("sql_result") if state.get("sql_status") == "ok" else None
    sql_sql = (state.get("sql_attempts") or [{}])[-1].get("sql") if sql_result else None
    tools_enabled = bool(tools) and not (
        len(tools) == 1 and tools[0] == "sql" and state.get("sql_status") == "not_data"
    )
    messages, trimmed = build_messages(
        cfg,
        org_name=ctx.org_name,
        agent_name=ctx.agent_name,
        channel=ctx.channel,
        user_message=state["user_message"],
        history=ctx.history,
        chunks=chunks,
        sql_sql=sql_sql,
        sql_result=sql_result,
        sql_explanation=state.get("sql_explanation"),
        web_results=list(state.get("web_results") or []),
        context_window=ctx.main_model.context_window,
        max_output=cfg.model.max_output_tokens,
        tools_enabled=tools_enabled,
        injection_suspected=bool((state.get("guardrail_input") or {}).get("injection_suspected")),
    )
    emit("debug", {"node": "generate_answer", "prompt": trimmed})
    used_chunks = chunks[: trimmed.get("chunks_used", len(chunks))]
    meta = {**ctx.metadata, "node": "generate_answer"}
    try:
        if ctx.channel == "playground":
            parts: list[str] = []
            result = None
            async for item in stream_chat(
                ctx.main_model,
                messages,
                temperature=cfg.model.temperature,
                max_tokens=cfg.model.max_output_tokens,
                metadata=meta,
            ):
                if isinstance(item, StreamDone):
                    result = item.result
                else:
                    parts.append(item)
                    emit("token", {"text": item})
            assert result is not None
            answer = result.content or "".join(parts)
        else:
            result = await chat(
                ctx.main_model,
                messages,
                temperature=cfg.model.temperature,
                max_tokens=cfg.model.max_output_tokens,
                metadata=meta,
            )
            answer = result.content
    except LLMError as e:
        log.warning("generate_failed", code=e.code, error=e.message)
        friendly = "I'm having trouble right now, please try again in a minute."
        emit("error", {"code": e.code, "message": e.message})
        return {
            "answer": friendly,
            "citations": [],
            "no_answer": False,
            "tool_errors": [{"tool": "llm", "error": f"{e.code}: {e.message}"}],
            "timings": {"generate_answer": round(time.perf_counter() - t0, 4)},
        }
    citations = _citations(answer, used_chunks) if cfg.tools.rag.citations else []
    return {
        "answer": answer,
        "citations": citations,
        "usage": [_usage(ctx.main_model, result, "generate_answer")],
        "timings": {"generate_answer": round(time.perf_counter() - t0, 4), "first_token": None},
    }


async def output_guardrails(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    ctx = ctx_from(config)
    t0 = time.perf_counter()
    cfg = ctx.cfg
    context_used = (
        bool(state.get("rag_chunks")) or bool(state.get("sql_result")) or bool(state.get("web_results"))
    )
    res = normalise_output(
        state.get("answer", ""),
        no_answer_message=cfg.guardrails.no_answer_message,
        system_prompt=cfg.instructions.system_prompt,
        context_used=context_used,
    )
    usage: list[dict[str, Any]] = []
    events = [{"stage": "output", **e} for e in res.events]
    answer, no_answer = res.answer, res.no_answer or bool(state.get("no_answer"))
    if cfg.guardrails.advanced.grounding_check and context_used and not no_answer and not res.replaced:
        context = "\n\n".join(
            filter(
                None,
                [
                    format_chunks(state.get("rag_chunks") or []) if state.get("rag_chunks") else "",
                    format_sql_result(None, state["sql_result"]) if state.get("sql_result") else "",
                    format_web(state.get("web_results") or []) if state.get("web_results") else "",
                ],
            )
        )
        grounded, issues, u = await grounding_check(answer, context, ctx.utility_model, ctx.metadata)
        if u:
            usage.append(u)
        events.append(
            {
                "stage": "output",
                "check": "grounding",
                "action": "pass" if grounded else "regenerate",
                "detail": issues[:3],
            }
        )
        if not grounded:
            stricter = await generate_answer(
                {
                    **state,
                    "user_message": state["user_message"]
                    + "\n\n(Only state facts that are explicitly supported by the provided context. Do not add anything else.)",
                },
                config,
            )
            answer2 = stricter.get("answer", "")
            usage.extend(stricter.get("usage") or [])
            grounded2, _, u2 = await grounding_check(answer2, context, ctx.utility_model, ctx.metadata)
            if u2:
                usage.append(u2)
            if grounded2:
                answer = answer2
            else:
                answer, no_answer = cfg.guardrails.no_answer_message, True
                events.append({"stage": "output", "check": "grounding", "action": "no_answer"})
    for ev in events:
        emit("guardrail", ev)
    if res.replaced:
        emit("token", {"text": answer, "replace": True})
    return {
        "answer": answer,
        "no_answer": no_answer,
        "guardrail_output": {"replaced": res.replaced, "no_answer": no_answer},
        "guardrail_events": events,
        "usage": usage,
        "citations": [] if no_answer else state.get("citations", []),
        "timings": {"output_guardrails": round(time.perf_counter() - t0, 4)},
    }


async def channel_formatter(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    ctx = ctx_from(config)
    answer = state.get("answer", "")
    if ctx.channel == "whatsapp":
        formatted = format_for_whatsapp(answer, state.get("citations") if ctx.cfg.tools.rag.citations else [])
    else:
        formatted = [answer]
    if state.get("citations"):
        emit("citations", {"citations": state["citations"]})
    return {"formatted": formatted}


__all__ = [
    "RunContext",
    "blocked_response",
    "channel_formatter",
    "collect_context",
    "dispatch",
    "generate_answer",
    "input_guardrails",
    "output_guardrails",
    "query_rewrite",
    "rag_retrieve",
    "router",
    "sql_agent",
    "web_search",
]
