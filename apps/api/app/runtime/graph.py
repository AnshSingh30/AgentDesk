"""Agent graph (PRD §9.2/§9.3). One graph handles 0 / 1 / many tools via conditional routing.

ponytail: MemorySaver checkpointer (single process). Swap for langgraph-checkpoint-postgres to run
confirm-before-run across multiple API workers.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.runtime import nodes
from app.runtime.state import AgentState

_checkpointer = MemorySaver()


def _after_guardrails(state: AgentState) -> str:
    return "blocked_response" if state.get("blocked") else "annotation_reply"


def _after_annotation(state: AgentState) -> str:
    return "channel_formatter" if state.get("annotation") else "query_rewrite"


@lru_cache
def get_graph() -> Any:
    g = StateGraph(AgentState)
    g.add_node("input_guardrails", nodes.input_guardrails)
    g.add_node("blocked_response", nodes.blocked_response)
    g.add_node("annotation_reply", nodes.annotation_reply)
    g.add_node("query_rewrite", nodes.query_rewrite)
    g.add_node("router", nodes.router)
    g.add_node("rag_retrieve", nodes.rag_retrieve)
    g.add_node("sql_agent", nodes.sql_agent)
    g.add_node("web_search", nodes.web_search)
    g.add_node("collect_context", nodes.collect_context)
    g.add_node("generate_answer", nodes.generate_answer)
    g.add_node("output_guardrails", nodes.output_guardrails)
    g.add_node("channel_formatter", nodes.channel_formatter)
    g.add_edge(START, "input_guardrails")
    g.add_conditional_edges(
        "input_guardrails",
        _after_guardrails,
        {"blocked_response": "blocked_response", "annotation_reply": "annotation_reply"},
    )
    g.add_conditional_edges(
        "annotation_reply",
        _after_annotation,
        {"channel_formatter": "channel_formatter", "query_rewrite": "query_rewrite"},
    )
    g.add_edge("blocked_response", "channel_formatter")
    g.add_edge("query_rewrite", "router")
    g.add_conditional_edges(
        "router", nodes.dispatch, ["rag_retrieve", "sql_agent", "web_search", "collect_context"]
    )
    g.add_edge("rag_retrieve", "collect_context")
    g.add_edge("sql_agent", "collect_context")
    g.add_edge("web_search", "collect_context")
    g.add_edge("collect_context", "generate_answer")
    g.add_edge("generate_answer", "output_guardrails")
    g.add_edge("output_guardrails", "channel_formatter")
    g.add_edge("channel_formatter", END)
    return g.compile(checkpointer=_checkpointer)
