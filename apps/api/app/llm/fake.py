"""Deterministic fake LLM so the whole product runs (and tests pass) without any API key.

It mirrors the structured-output contracts used by the runtime. Real answers need a real model.
"""

from __future__ import annotations

import json
import re
from typing import Any

_SQL_WORDS = re.compile(
    r"\b(how many|count|number of|average|avg|rate|trend|top \d+|total|sum|percent|headcount|"
    r"attrition|tenure|hire|open positions|leave|salary|salaries|department|month|quarter|year)\b",
    re.I,
)
_WEB_WORDS = re.compile(
    r"\b(latest|news|today|current|weather|price of|stock|who won|search the web|online)\b", re.I
)
_GREETING = re.compile(
    r"^\s*(hi|hello|hey|thanks|thank you|good morning|good evening|namaste|ok|okay)\b[\s!.]*$", re.I
)


def _last_user(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            content = str(m.get("content") or "")
            embedded = re.search(
                r"(?:User message|Question|Latest user message):\s*(.+?)\s*(?:\n\nRespond with ONLY|$)",
                content,
                re.S,
            )
            return embedded.group(1).strip() if embedded else content
    return ""


def _all_text(messages: list[dict[str, Any]]) -> str:
    return "\n".join(str(m.get("content") or "") for m in messages)


def fake_chat(messages: list[dict[str, Any]]) -> str:
    text = _all_text(messages)
    user = _last_user(messages)
    if "<knowledge_base>" in text:
        m = re.search(r"<chunk[^>]*>(.*?)</chunk>", text, re.S)
        snippet = " ".join((m.group(1) if m else "").split())[:300]
        return f"Based on the documents: {snippet} [1]" if snippet else "[NO_ANSWER]"
    if "<sql_result" in text:
        m = re.search(r"<sql_result[^>]*rows=\"(\d+)\"", text)
        rows = m.group(1) if m else "some"
        return f"The query returned {rows} rows. See the table in the debug panel for details."
    if "<web_results>" in text:
        m = re.search(r"<result[^>]*url=\"([^\"]+)\"", text)
        return f"According to the web: {m.group(1) if m else 'a source'} covers this topic."
    if "ONLY using" in text or "only using" in text:
        return "[NO_ANSWER]"
    if _GREETING.match(user):
        return "Hello! I'm a demo assistant. Ask me about your documents or data."
    return f"(demo model) You asked: {user.strip()[:200]}. Connect a real model in the Model step for real answers."


def fake_structured(
    messages: list[dict[str, Any]], schema_name: str, schema: dict[str, Any]
) -> dict[str, Any]:
    user = _last_user(messages)
    text = _all_text(messages)
    name = schema_name.lower()
    if "route" in name:
        tools: list[str] = []
        available = re.findall(r"- (rag|sql|web_search):", text)
        if _GREETING.match(user):
            return {"tools": [], "reason": "Greeting or small talk; no tool needed."}
        if "sql" in available and _SQL_WORDS.search(user):
            tools.append("sql")
        if "web_search" in available and _WEB_WORDS.search(user):
            tools.append("web_search")
        if "rag" in available and (
            not tools or re.search(r"\b(policy|document|handbook|guide|rule)\b", user, re.I)
        ):
            tools.append("rag")
        if not tools and available:
            tools = [available[0]]
        return {"tools": tools, "reason": f"Keyword heuristics matched {tools or 'nothing'} (demo router)."}
    if "rewrite" in name:
        return {"query": user.strip()}
    if "intent" in name:
        return {"intent": "chit_chat" if _GREETING.match(user) else "data_question"}
    if "sqlgeneration" in name or name.startswith("sql"):
        m = re.search(r"^TABLE (\w+)", text, re.M)
        table = m.group(1) if m else "employees"
        return {
            "sql": f"SELECT * FROM {table} LIMIT 10",  # nosec B608
            "explanation": "Demo query over the first exposed table.",
            "assumptions": ["demo model cannot write real SQL"],
        }
    if "describ" in name or "tabledescription" in name:
        cols = re.findall(r"^\s*-\s*(\w+)\s*\(([^)]+)\)", text, re.M)
        return {
            "table_description": "Auto-drafted (demo): table with " + ", ".join(c for c, _ in cols[:6]),
            "columns": [
                {
                    "name": c,
                    "description": f"{c.replace('_', ' ').capitalize()} ({t})",
                    "semantic_type": _semantic(c, t),
                }
                for c, t in cols
            ],
            "suggested_relationships": [],
        }
    if "classif" in name:
        return {"label": "safe"}
    if "ground" in name:
        return {"grounded": True, "issues": []}
    return {k: None for k in schema.get("properties", {})}


def _semantic(col: str, typ: str) -> str:
    c = col.lower()
    if c.endswith("_id") or c == "id":
        return "id" if c == "id" or c == f"{c}" and not c.endswith("_id") else "foreign_key"
    if any(k in c for k in ("email",)):
        return "email"
    if any(k in c for k in ("phone", "mobile")):
        return "phone"
    if any(k in c for k in ("salary", "dob", "date_of_birth", "aadhaar", "pan", "ssn")):
        return "sensitive_personal"
    if "date" in typ.lower() or c.endswith("_at") or c.endswith("_date"):
        return "date"
    if any(k in c for k in ("name", "title")):
        return "name"
    if any(k in typ.lower() for k in ("int", "numeric", "double", "float", "decimal")):
        return "numeric_measure"
    if "bool" in typ.lower():
        return "boolean"
    return "category"


def dumps(obj: Any) -> str:
    return json.dumps(obj)
