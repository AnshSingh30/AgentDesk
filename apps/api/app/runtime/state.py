"""LangGraph state (PRD §9.1) with reducers for parallel fan-out."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def merge_dicts(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    return {**(a or {}), **(b or {})}


class AgentState(TypedDict, total=False):
    run_id: str
    org_id: str
    channel: str  # playground | whatsapp
    user_message: str
    history: list[dict[str, str]]
    standalone_query: str | None
    guardrail_input: dict[str, Any] | None
    blocked: bool
    route: dict[str, Any] | None
    rag_chunks: Annotated[list[dict[str, Any]], operator.add]
    rag_ran: bool
    sql_attempts: Annotated[list[dict[str, Any]], operator.add]
    sql_result: dict[str, Any] | None
    sql_status: str | None
    sql_explanation: str | None
    sql_assumptions: list[str]
    web_results: Annotated[list[dict[str, Any]], operator.add]
    web_ran: bool
    fallback_used: str | None
    no_context: bool
    answer: str
    citations: list[dict[str, Any]]
    guardrail_output: dict[str, Any] | None
    guardrail_events: Annotated[list[dict[str, Any]], operator.add]
    usage: Annotated[list[dict[str, Any]], operator.add]
    timings: Annotated[dict[str, float], merge_dicts]
    tool_errors: Annotated[list[dict[str, Any]], operator.add]
    formatted: list[str]
    no_answer: bool
