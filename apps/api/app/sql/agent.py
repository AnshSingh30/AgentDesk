"""SQL agent subgraph: intent -> schema_select -> generate -> validate -> (confirm) -> execute -> reflect (PRD §11.5)."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from app.core.metrics import sql_attempts_total
from app.llm import jev
from app.llm.gateway import ResolvedModel, structured
from app.sql.context import DatasetContext, select_tables, top_examples
from app.sql.executor import SqlExecutionError, execute
from app.sql.validator import validate_sql
from app.usage.service import UsageRecord

log = structlog.get_logger()


class IntentCheck(BaseModel):
    intent: Literal["data_question", "chit_chat", "out_of_scope"]


class SqlGeneration(BaseModel):
    sql: str
    explanation: str = Field("", max_length=1000)
    assumptions: list[str] = Field(default_factory=list)


class SqlState(TypedDict, total=False):
    question: str
    history_hint: str
    intent: str
    selected_tables: list[str]
    attempts: list[dict[str, Any]]
    sql: str | None
    validated_sql: str | None
    explanation: str
    assumptions: list[str]
    result: dict[str, Any] | None
    error: str | None
    reflected: bool
    cancelled: bool
    usage: list[dict[str, Any]]
    timings: dict[str, float]
    status: str  # ok | not_data | failed | cancelled


class SqlRuntime:
    """Non-serialisable dependencies passed through config['configurable']['sql_runtime']."""

    def __init__(
        self,
        ctx: DatasetContext,
        main_model: ResolvedModel,
        utility_model: ResolvedModel,
        *,
        max_attempts: int = 3,
        max_rows: int = 200,
        confirm: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.ctx = ctx
        self.main_model = main_model
        self.utility_model = utility_model
        self.max_attempts = max_attempts
        self.max_rows = max_rows
        self.confirm = confirm
        self.metadata = metadata or {}


def _rt(config: RunnableConfig) -> SqlRuntime:
    return config["configurable"]["sql_runtime"]


def _usage(rm: ResolvedModel, chat: Any, node: str) -> dict[str, Any]:
    return UsageRecord(
        kind="llm",
        model=rm.display,
        provider=rm.provider,
        credential_mode=rm.credential_mode,
        input_tokens=chat.usage.input_tokens,
        output_tokens=chat.usage.output_tokens,
        est_cost_usd=chat.est_cost_usd,
        node=node,
    ).as_dict()


async def intent_check(state: SqlState, config: RunnableConfig) -> dict[str, Any]:
    if state.get("intent") == "data_question":  # decided upstream by the router
        return {"status": "ok"}
    rt = _rt(config)
    t0 = time.perf_counter()
    # Columns, not just table names: "most common exit reason" has no exits table, only employees.exit_reason.
    tables = "; ".join(f"{t.name}({', '.join(c['name'] for c in t.columns)})" for t in rt.ctx.tables)[:4000]
    try:
        if jev.enabled():
            answers, jev_usage = await jev.ask(
                {"user_message": state["question"], "dataset_tables": tables},
                {
                    "intent": {
                        "type": "choice",
                        "instructions": "What kind of message is this, given the tables and columns in the dataset?",
                        "criteria": {
                            "data_question": "Asks for facts, counts, lists or statistics that the dataset's columns can answer",
                            "chit_chat": "Greeting, thanks or small talk that needs no data",
                            "out_of_scope": "Asks for something the dataset's columns cannot answer",
                        },
                    }
                },
                node="intent_check",
            )
            intent, usage = answers["intent"]["choice"], [jev_usage]
        else:
            prompt = (
                f"Dataset tables and columns: {tables}\n"
                f"User message: {state['question']}\n\nClassify: is this a question answerable with data from "
                "the dataset (data_question), small talk/greeting (chit_chat), or unrelated (out_of_scope)?"
            )
            res, chat = await structured(
                rt.utility_model,
                [{"role": "user", "content": prompt}],
                IntentCheck,
                max_tokens=50,
                metadata=rt.metadata,
            )
            intent, usage = res.intent, [_usage(rt.utility_model, chat, "intent_check")]
    except Exception as e:  # noqa: BLE001 - default to trying SQL
        log.warning("intent_check_failed", error=str(e))
        intent, usage = "data_question", []
    return {
        "intent": intent,
        "usage": usage,
        "timings": {"intent_check": time.perf_counter() - t0},
        "status": "ok" if intent == "data_question" else "not_data",
    }


async def schema_select(state: SqlState, config: RunnableConfig) -> dict[str, Any]:
    rt = _rt(config)
    t0 = time.perf_counter()
    tables = await select_tables(rt.ctx, state["question"])
    return {"selected_tables": tables, "attempts": [], "timings": {"schema_select": time.perf_counter() - t0}}


def _gen_prompt(rt: SqlRuntime, state: SqlState) -> list[dict[str, str]]:
    ctx = rt.ctx
    examples = top_examples(ctx, state["question"])
    ex_text = "\n".join(f"Q: {e['question']}\nSQL: {e['sql']}" for e in examples)
    # Structured for gpt-5.4-mini: critical rules first, explicit steps, explicit ambiguity policy.
    pitfalls = (
        "<postgres_pitfalls>\n"
        "- date - date is an integer number of days: divide it directly (/ 365.25 for years); "
        "never EXTRACT(DAY FROM date - date).\n"
        "- Cast before ROUND: ROUND(x::numeric, 2). Use NULLIF(denominator, 0) in ratios and 100.0 * for percentages.\n"
        "- Use COUNT(*) FILTER (WHERE ...) for several conditional counts in one pass.\n"
        "</postgres_pitfalls>\n\n"
        if ctx.dialect == "postgres"
        else ""
    )
    system = (
        f"You translate one business question into ONE read-only {ctx.dialect.upper()} SELECT over the schema below.\n\n"
        "<rules>\n"
        "- Use only the tables and columns listed in SCHEMA; never invent any.\n"
        "- If the question also asks something that is not in the data (e.g. what a policy says), answer only the "
        "data part; another tool covers the rest.\n"
        "- If the data part itself needs a column that is not in SCHEMA (it may be hidden for privacy or not exist), "
        "return an empty sql and say what is missing in explanation. Never stand in another column or a constant.\n"
        "- Compare categorical columns only against the values listed for them (exact spelling and case).\n"
        "- One statement, no DDL/DML, no comments, no trailing semicolon. Qualify columns with aliases when joining.\n"
        f"- Add LIMIT {rt.max_rows} unless the query returns a single aggregated row.\n"
        "- Select only the columns needed to answer, with short readable aliases (e.g. active_employees).\n"
        "- Never put literal values in the SELECT list for facts that are not in the data (e.g. policy entitlements); "
        "leave that part out, another tool answers it.\n"
        "</rules>\n\n"
        "<steps>\n"
        "1. Identify the metric, the filters (status, type, department, city...) and the time period.\n"
        "2. Map each to a SCHEMA column; use the listed values and date ranges to pick exact literals and periods.\n"
        "3. Take joins from RELATIONSHIPS. Apply a BUSINESS GLOSSARY definition when its term appears.\n"
        "4. Write the query, then check every table, column and filter literal against SCHEMA.\n"
        "</steps>\n\n"
        "<ambiguity>\n"
        "- 'current', 'now', 'currently', 'active' and headcount mean rows whose status marks them current; "
        "no date filter.\n"
        "- A year or month in the question is a calendar period on the matching date column, "
        "e.g. hire_date >= '2025-01-01' AND hire_date < '2026-01-01'.\n"
        "- Only for a trend or rate with no period at all, use the last 12 months and say so in assumptions.\n"
        f"- Today's date is {datetime.now(UTC).date().isoformat()}.\n"
        "</ambiguity>\n\n"
        f"{pitfalls}"
        "<output_contract>\n"
        "- sql: the query only.\n"
        "- explanation: one plain-language sentence saying what was counted and which filters were used.\n"
        "- assumptions: interpretations you made (empty list if none).\n"
        "</output_contract>\n\n"
        f"SCHEMA:\n{ctx.schema_text(state.get('selected_tables'))}\n"
    )
    if ex_text:
        system += f"\nEXAMPLES:\n{ex_text}\n"
    messages = [{"role": "system", "content": system}]
    if state.get("history_hint"):
        messages.append(
            {"role": "system", "content": f"Conversation context: {state['history_hint'][:1500]}"}
        )
    user = f"Question: {state['question']}"
    attempts = state.get("attempts") or []
    if attempts:
        last = attempts[-1]
        user += (
            f"\n\nYour previous SQL:\n{last.get('sql')}\nfailed with: {last.get('error')}\n"
            "Fix the query: re-check table and column names against SCHEMA, the join path, filter literals and "
            "date arithmetic. Return a corrected query, not the same one."
        )
    messages.append({"role": "user", "content": user})
    return messages


async def generate_sql(state: SqlState, config: RunnableConfig) -> dict[str, Any]:
    rt = _rt(config)
    t0 = time.perf_counter()
    try:
        res, chat = await structured(
            rt.main_model, _gen_prompt(rt, state), SqlGeneration, max_tokens=1200, metadata=rt.metadata
        )
    except Exception as e:  # noqa: BLE001
        attempts = list(state.get("attempts") or []) + [
            {"sql": None, "error": f"generation failed: {e}", "rows": None, "duration_ms": None}
        ]
        return {
            "attempts": attempts,
            "sql": None,
            "error": str(e),
            "status": "failed",
            "timings": {"generate_sql": time.perf_counter() - t0},
        }
    return {
        "sql": res.sql.strip(),
        # empty sql = the model judged the data unavailable (missing or hidden column): stop, don't approximate
        **({"status": "not_data"} if not res.sql.strip() else {}),
        "explanation": res.explanation,
        "assumptions": res.assumptions,
        "usage": list(state.get("usage") or []) + [_usage(rt.main_model, chat, "generate_sql")],
        "timings": {**(state.get("timings") or {}), "generate_sql": time.perf_counter() - t0},
    }


async def validate_node(state: SqlState, config: RunnableConfig) -> dict[str, Any]:
    rt = _rt(config)
    if not state.get("sql"):
        return {"validated_sql": None}
    result = validate_sql(state["sql"], rt.ctx.exposed, dialect=rt.ctx.dialect, max_rows=rt.max_rows)
    attempts = list(state.get("attempts") or [])
    if not result.ok:
        sql_attempts_total.labels("rejected").inc()
        attempts.append(
            {"sql": state["sql"], "error": f"validator: {result.error}", "rows": None, "duration_ms": None}
        )
        return {"validated_sql": None, "attempts": attempts, "error": result.error}
    return {"validated_sql": result.sql, "error": None}


async def confirm_node(state: SqlState, config: RunnableConfig) -> dict[str, Any]:
    rt = _rt(config)
    if not rt.confirm:
        return {}
    decision = interrupt(
        {
            "type": "sql_confirm_required",
            "sql": state["validated_sql"],
            "explanation": state.get("explanation"),
            "assumptions": state.get("assumptions", []),
        }
    )
    action = (decision or {}).get("action", "cancel")
    if action == "cancel":
        return {"cancelled": True, "status": "cancelled"}
    if action == "edit" and (decision or {}).get("sql"):
        result = validate_sql(decision["sql"], rt.ctx.exposed, dialect=rt.ctx.dialect, max_rows=rt.max_rows)
        if not result.ok:
            attempts = list(state.get("attempts") or []) + [
                {
                    "sql": decision["sql"],
                    "error": f"validator: {result.error}",
                    "rows": None,
                    "duration_ms": None,
                }
            ]
            return {"validated_sql": None, "attempts": attempts, "error": result.error, "status": "failed"}
        return {"validated_sql": result.sql, "sql": decision["sql"]}
    return {}


async def execute_node(state: SqlState, config: RunnableConfig) -> dict[str, Any]:
    rt = _rt(config)
    t0 = time.perf_counter()
    attempts = list(state.get("attempts") or [])
    try:
        res = await execute(rt.ctx.dataset, state["validated_sql"], rt.max_rows)
    except SqlExecutionError as e:
        sql_attempts_total.labels("db_error").inc()
        attempts.append(
            {
                "sql": state["validated_sql"],
                "error": str(e),
                "rows": None,
                "duration_ms": int((time.perf_counter() - t0) * 1000),
            }
        )
        return {"attempts": attempts, "error": str(e), "result": None}
    sql_attempts_total.labels("ok").inc()
    attempts.append(
        {"sql": state["validated_sql"], "error": None, "rows": res.row_count, "duration_ms": res.duration_ms}
    )
    return {
        "attempts": attempts,
        "error": None,
        "result": res.as_dict(),
        "status": "ok",
        "timings": {**(state.get("timings") or {}), "execute_sql": time.perf_counter() - t0},
    }


def _after_intent(state: SqlState) -> str:
    return "schema_select" if state.get("intent") == "data_question" else END


def _after_validate(state: SqlState, config: RunnableConfig) -> str:
    if state.get("status") == "not_data":
        return END
    if state.get("validated_sql"):
        return "confirm"
    return "generate_sql" if len(state.get("attempts") or []) < _rt(config).max_attempts else "fail"


def _after_confirm(state: SqlState) -> str:
    if state.get("cancelled") or state.get("status") == "failed":
        return END
    return "execute_sql"


def _after_execute(state: SqlState, config: RunnableConfig) -> str:
    rt = _rt(config)
    attempts = state.get("attempts") or []
    if state.get("result") is None:
        return "generate_sql" if len(attempts) < rt.max_attempts else "fail"
    if (
        state["result"].get("row_count") == 0
        and not state.get("reflected")
        and len(attempts) < rt.max_attempts
    ):
        return "reflect"
    return END


async def reflect(state: SqlState, config: RunnableConfig) -> dict[str, Any]:
    attempts = list(state.get("attempts") or [])
    attempts[-1] = {
        **attempts[-1],
        "error": "query returned 0 rows - check filters, joins and value spellings (values may differ in case)",
    }
    return {"attempts": attempts, "reflected": True, "result": None}


async def fail(state: SqlState, config: RunnableConfig) -> dict[str, Any]:
    return {"status": "failed", "result": None}


def build_sql_graph() -> Any:
    g = StateGraph(SqlState)
    g.add_node("intent_check", intent_check)
    g.add_node("schema_select", schema_select)
    g.add_node("generate_sql", generate_sql)
    g.add_node("validate_sql", validate_node)
    g.add_node("confirm", confirm_node)
    g.add_node("execute_sql", execute_node)
    g.add_node("reflect", reflect)
    g.add_node("fail", fail)
    g.add_edge(START, "intent_check")
    g.add_conditional_edges("intent_check", _after_intent, {"schema_select": "schema_select", END: END})
    g.add_edge("schema_select", "generate_sql")
    g.add_edge("generate_sql", "validate_sql")
    g.add_conditional_edges(
        "validate_sql",
        _after_validate,
        {"confirm": "confirm", "generate_sql": "generate_sql", "fail": "fail", END: END},
    )
    g.add_conditional_edges("confirm", _after_confirm, {"execute_sql": "execute_sql", END: END})
    g.add_conditional_edges(
        "execute_sql",
        _after_execute,
        {"generate_sql": "generate_sql", "reflect": "reflect", "fail": "fail", END: END},
    )
    g.add_edge("reflect", "generate_sql")
    g.add_edge("fail", END)
    return g.compile()


sql_graph = build_sql_graph()
