"""Template eval suite (PRD §2.3, §20.1).

Runs every case in tests/evals/datasets/<template>.yaml through the seeded demo agent's real graph and
scores it on:
  route      router picked the expected tool set
  facts      expected facts / reference-SQL values appear in the answer
  no_answer  out-of-scope questions get the no-answer policy instead of a guess
  sql        executed successfully on the first try / within retries (cases that expect SQL)
  privacy    hidden columns never reach an executed query
  grounded   LLM judge: every claim in the answer is supported by the retrieved context

Writes tests/evals/reports/<template>.json and a combined SUMMARY.md. PRD thresholds are enforced only
with a real model. Run: `make seed && make evals` (sets AGENTDESK_LIVE=1).
Env: EVAL_JUDGE_MODEL (default: DEFAULT_CHAT_MODEL), EVAL_CONCURRENCY (default 4), EVAL_RETRIES (default 2,
for provider errors), EVAL_ONLY (substring).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import statistics
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import yaml
from app.agents.schemas import AgentConfig
from app.core.config import settings
from app.db.models import Agent, Conversation, Organization, User
from app.db.session import get_sessionmaker
from app.llm import gateway
from app.runtime.runner import prepare_run, run_once
from pydantic import BaseModel
from sqlalchemy import select

pytestmark = pytest.mark.asyncio
if os.environ.get("AGENTDESK_LIVE") != "1":
    pytest.skip("live evals: run `make evals` (AGENTDESK_LIVE=1)", allow_module_level=True)
HERE = Path(__file__).parent
REPORTS = HERE / "reports"
REAL_MODEL = not settings.default_chat_model.startswith("fake/")
HR_DEMO_URL = os.environ.get("HR_DEMO_DATABASE_URL", "postgresql://hr_demo:hr_demo@localhost:5434/hr_demo")
JUDGE_MODEL = os.environ.get("EVAL_JUDGE_MODEL") or settings.default_chat_model
CONCURRENCY = int(os.environ.get("EVAL_CONCURRENCY", "4"))
RETRIES = int(os.environ.get("EVAL_RETRIES", "2"))
THRESHOLDS = {  # PRD §2.3
    "route_accuracy": 0.90,
    "fact_accuracy": 0.85,
    "groundedness": 0.85,
    "sql_first_try": 0.85,
    "sql_with_retries": 0.95,
    "no_answer_accuracy": 0.80,
    "privacy": 1.0,
}
TEMPLATES = ["hr_onboarding", "hr_analytics", "school_parent_helpdesk"]


# ---------------------------------------------------------------- matching helpers
def normalize(text: str) -> str:
    text = text.lower().replace("*", "").replace("`", "").replace("’", "'")
    text = re.sub(r"(?<=\d),(?=\d)", "", text)  # 5,00,000 -> 500000
    return re.sub(r"\s+", " ", text)


def value_forms(v: Any) -> set[str]:
    """Acceptable renderings of a reference-SQL value inside a prose answer."""
    if isinstance(v, bool) or v is None:
        return {str(v).lower()}
    if isinstance(v, int | float | Decimal):
        f = float(v)
        if f == int(f):
            return {str(int(f))}
        forms = {f"{f:.2f}", f"{f:.1f}", f"{f:.2f}".rstrip("0").rstrip(".")}
        if 0 < abs(f) < 1:
            forms |= {f"{f * 100:.1f}", f"{f * 100:.0f}"}
        return forms
    return {normalize(str(v))}


def contains(answer: str, form: str) -> bool:
    if re.fullmatch(r"[\d.]+", form):
        return re.search(rf"(?<![\d.]){re.escape(form)}(?![\d])", answer) is not None
    return form in answer


def fact_ok(answer: str, fact: str | list[str]) -> bool:
    options = fact if isinstance(fact, list) else [fact]
    return any(contains(answer, normalize(o)) for o in options)


async def reference_rows(conn: asyncpg.Connection, checks: str | list[str]) -> list[list[Any]]:
    out = []
    for sql in [checks] if isinstance(checks, str) else checks:
        row = await conn.fetchrow(sql)
        out.append(list(row.values()) if row else [])
    return out


class Declined(BaseModel):
    declined: bool


DECLINE_PROMPT = """Did the ASSISTANT decline, redirect, or say it does not have that information, INSTEAD of actually answering the QUESTION? A refusal that points to a contact or lists what it can help with counts as declining. Providing the requested answer does not.

QUESTION: {question}

ASSISTANT: {answer}"""


async def judge_declined(question: str, answer: str) -> bool | None:
    rm = gateway.platform_model(model_string=JUDGE_MODEL)
    msg = DECLINE_PROMPT.format(question=question, answer=answer)
    try:
        verdict, _ = await gateway.structured(
            rm, [{"role": "user", "content": msg}], Declined, max_tokens=200
        )
    except gateway.LLMError:
        return None
    return verdict.declined


class Judgement(BaseModel):
    grounded: bool
    unsupported_claims: list[str] = []


JUDGE_PROMPT = """You are grading a company assistant. Decide whether EVERY factual claim in the ANSWER is \
supported by the CONTEXT (documents or SQL results the assistant was given). Greetings, offers to help, \
and pointing the user to a contact that appears in the context are fine. Rephrasing and unit conversion \
are fine. Anything stated that is not in the context is unsupported.

QUESTION: {question}

CONTEXT:
{context}

ANSWER:
{answer}"""


async def judge(question: str, state: dict[str, Any], answer: str) -> tuple[bool | None, list[str]]:
    parts = [
        f"[{i}] {c.get('filename')} (page {c.get('page_start')}, {c.get('section_title') or ''}): {c.get('text', '')}"
        for i, c in enumerate(state.get("rag_chunks") or [], 1)
    ]
    if state.get("sql_result"):
        parts.append("SQL result: " + json.dumps(state["sql_result"], default=str)[:6000])
    context = "\n---\n".join(p for p in parts if p)[:24000]
    if not context:
        return None, []
    rm = gateway.platform_model(model_string=JUDGE_MODEL)
    try:
        verdict, _ = await gateway.structured(
            rm,
            [
                {
                    "role": "user",
                    "content": JUDGE_PROMPT.format(question=question, context=context, answer=answer),
                }
            ],
            Judgement,
            max_tokens=800,
        )
    except gateway.LLMError:
        return None, ["judge failed"]
    return verdict.grounded, verdict.unsupported_claims


# ---------------------------------------------------------------- running
async def seeded_agent(template_key: str) -> tuple[Organization, Agent, User] | None:
    async with get_sessionmaker()() as session:
        agent = (
            (
                await session.execute(
                    select(Agent)
                    .where(Agent.template_key == template_key, Agent.deleted_at.is_(None))
                    .order_by(Agent.created_at.desc())
                )
            )
            .scalars()
            .first()
        )
        if agent is None:
            return None
        org = await session.get(Organization, agent.org_id)
        user = (
            await session.execute(select(User).where(User.email == "demo@agentdesk.local"))
        ).scalar_one_or_none()
        return (org, agent, user) if org and user else None


async def run_case(
    case: dict[str, Any],
    org: Organization,
    agent: Agent,
    user: User,
    cfg: AgentConfig,
    refs: list[list[Any]] | None,
) -> dict[str, Any]:
    q = case["question"]
    async with get_sessionmaker()() as session:
        conv = Conversation(
            org_id=org.id,
            agent_id=agent.id,
            channel="playground",
            playground_user_id=user.id,
            flags={"eval": True},
        )
        session.add(conv)
        await session.flush()
        ctx = await prepare_run(
            session, org=org, agent=agent, cfg=cfg, channel="playground", conversation=conv, user_text=q
        )
        await session.commit()
    outcome = await run_once(ctx, q)
    state = outcome.state
    llm_errors = [e["error"] for e in state.get("tool_errors") or [] if e.get("tool") == "llm"]
    if outcome.error or llm_errors:  # provider failure: not a model-quality result
        return {
            "question": q,
            "passed": False,
            "checks": {},
            "error": outcome.error or llm_errors[0],
            "route": [],
            "sql": [],
            "answer": state.get("answer") or "",
            "unsupported_claims": [],
            "latency_ms": outcome.latency_ms,
            "tokens": 0,
        }
    raw_answer = state.get("answer") or ""
    answer = normalize(raw_answer)
    tools = sorted((state.get("route") or {}).get("tools") or [])
    checks: dict[str, bool | None] = {}

    routes = case.get("acceptable_routes") or ([case["expected_route"]] if "expected_route" in case else None)
    checks["route"] = tools in [sorted(r) for r in routes] if routes is not None else None

    fact_results = [fact_ok(answer, f) for f in case.get("expected_facts", [])]
    if refs is not None:  # any acceptable reference query whose whole first row is in the answer
        fact_results.append(
            any(row and all(any(contains(answer, f) for f in value_forms(v)) for v in row) for row in refs)
        )
    checks["facts"] = all(fact_results) if fact_results else None

    said_no = bool(state.get("no_answer") or state.get("blocked")) or (
        normalize(cfg.guardrails.no_answer_message)[:40] in answer
    )
    if case.get("expected_no_answer"):
        checks["no_answer"] = said_no or bool(REAL_MODEL and await judge_declined(q, raw_answer))
    else:
        checks["no_answer"] = None

    attempts = state.get("sql_attempts") or []
    if "sql" in (case.get("expected_route") or []) and "sql" in tools:
        ok = state.get("sql_status") == "ok"
        checks["sql_first_try"] = ok and len(attempts) == 1
        checks["sql_with_retries"] = ok
    else:
        checks["sql_first_try"] = checks["sql_with_retries"] = None

    if case.get("forbid_sql"):
        executed = [a.get("sql") or "" for a in attempts if not a.get("error") and a.get("rows") is not None]
        checks["privacy"] = not any(
            col.lower() in sql.lower() for sql in executed for col in case["forbid_sql"]
        )
    else:
        checks["privacy"] = None

    grounded, unsupported = (None, [])
    if REAL_MODEL and not said_no and raw_answer:
        grounded, unsupported = await judge(q, state, raw_answer)
    checks["grounded"] = grounded

    tokens = sum(
        int(u.get("input_tokens", 0)) + int(u.get("output_tokens", 0)) for u in state.get("usage") or []
    )
    return {
        "question": q,
        "passed": outcome.error is None and all(v for v in checks.values() if v is not None),
        "checks": checks,
        "route": tools,
        "sql": [a.get("sql") for a in attempts],
        "answer": raw_answer[:600],
        "unsupported_claims": unsupported,
        "error": outcome.error,
        "latency_ms": outcome.latency_ms,
        "tokens": tokens,
    }


def rate(results: list[dict[str, Any]], key: str) -> float | None:
    vals = [r["checks"][key] for r in results if r["checks"].get(key) is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def write_summary() -> None:
    reports = [json.loads(p.read_text()) for p in sorted(REPORTS.glob("*.json"))]
    metrics = list(THRESHOLDS)
    lines = [
        "# Eval results",
        "",
        "Generated by `make evals` (`apps/api/tests/evals/test_template_evals.py`). "
        "Method: [docs/evaluation.md](../../../../../docs/evaluation.md).",
        "",
        "| Template | Model | Cases | Passed | " + " | ".join(metrics) + " | p50 / p95 latency |",
        "|---" * (len(metrics) + 5) + "|",
    ]
    for r in reports:
        m = r["metrics"]
        cells = []
        for k in metrics:
            ok = m[k] is None or m[k] >= THRESHOLDS[k]
            cells.append(pct(m[k]) + ("" if ok else " ⚠️"))
        lines.append(
            f"| {r['template']} | `{r['model']}` | {r['cases']} | {r['passed']} | "
            + " | ".join(cells)
            + f" | {m['latency_p50_ms'] / 1000:.1f}s / {m['latency_p95_ms'] / 1000:.1f}s |"
        )
    lines += ["", "Thresholds: " + ", ".join(f"{k} ≥ {v:.0%}" for k, v in THRESHOLDS.items()), ""]
    for r in reports:
        failed = [x for x in r["results"] if not x["passed"]]
        lines += [f"## {r['template']} — {len(failed)} failing case(s)", f"Run at {r['run_at']}", ""]
        for x in failed:
            bad = [k for k, v in x["checks"].items() if v is False] or (["error"] if x["error"] else [])
            snippet = x["answer"].replace("\n", " ")[:220]
            lines.append(
                f"- **{x['question']}** — failed: {', '.join(bad)}; route `{x['route']}`; answer: _{snippet}_"
            )
        lines.append("")
    (REPORTS / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")


@pytest.mark.parametrize("template_key", TEMPLATES)
async def test_template_eval_set(template_key: str) -> None:
    if os.environ.get("EVAL_ONLY") and os.environ["EVAL_ONLY"] not in template_key:
        pytest.skip("filtered by EVAL_ONLY")
    seeded = await seeded_agent(template_key)
    if seeded is None:
        pytest.skip("run `make seed` first")
    org, agent, user = seeded
    cases = yaml.safe_load((HERE / "datasets" / f"{template_key}.yaml").read_text(encoding="utf-8"))["cases"]
    cfg = AgentConfig.model_validate(agent.draft_config)

    refs: list[list[list[Any]] | None] = [None] * len(cases)
    if any("sql_check" in c for c in cases):
        conn = await asyncpg.connect(HR_DEMO_URL)
        try:
            for i, c in enumerate(cases):
                if "sql_check" in c:
                    refs[i] = await reference_rows(conn, c["sql_check"])
        finally:
            await conn.close()

    sem = asyncio.Semaphore(CONCURRENCY)

    async def bounded(i: int) -> dict[str, Any]:
        async with sem:
            for attempt in range(RETRIES + 1):  # rate-limited providers: back off and retry the case
                result = await run_case(cases[i], org, agent, user, cfg, refs[i])
                if result["error"] is None or attempt == RETRIES:
                    return result
                await asyncio.sleep(20 * (attempt + 1))
            raise AssertionError("unreachable")

    results = await asyncio.gather(*(bounded(i) for i in range(len(cases))))
    latencies = sorted(r["latency_ms"] for r in results)
    metrics: dict[str, Any] = {
        "route_accuracy": rate(results, "route"),
        "fact_accuracy": rate(results, "facts"),
        "groundedness": rate(results, "grounded"),
        "sql_first_try": rate(results, "sql_first_try"),
        "sql_with_retries": rate(results, "sql_with_retries"),
        "no_answer_accuracy": rate(results, "no_answer"),
        "privacy": rate(results, "privacy"),
        "latency_p50_ms": int(statistics.median(latencies)),
        "latency_p95_ms": latencies[max(0, round(0.95 * len(latencies)) - 1)],
        "avg_tokens": int(statistics.mean(r["tokens"] for r in results)),
    }
    report = {
        "template": template_key,
        "model": settings.default_chat_model,
        "utility_model": settings.utility_model,
        "judge_model": JUDGE_MODEL if REAL_MODEL else None,
        "embedding_model": settings.default_embedding_model,
        "cases": len(results),
        "passed": sum(r["passed"] for r in results),
        "metrics": metrics,
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "results": results,
    }
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / f"{template_key}.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    write_summary()
    print(f"\n[{template_key}] {report['passed']}/{len(results)} passed · " + json.dumps(metrics))

    assert all(r["error"] is None for r in results), [r for r in results if r["error"]]
    if REAL_MODEL:
        below = {k: metrics[k] for k, t in THRESHOLDS.items() if metrics[k] is not None and metrics[k] < t}
        assert not below, f"below PRD thresholds: {below} — see reports/SUMMARY.md"
