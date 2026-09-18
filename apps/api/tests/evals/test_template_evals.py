"""Template eval sets (PRD §20.1): route accuracy, expected facts, no-answer correctness.

Runs the seeded demo agents through the real graph. With the demo model (`fake/chat`) only routing and
no-answer behaviour are meaningful, so thresholds are enforced only when a real model is configured.
Run: make seed && make evals
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pytest
from app.agents.schemas import AgentConfig
from app.core.config import settings
from app.db.models import Agent, Conversation, Organization, User
from app.db.session import get_sessionmaker
from app.runtime.runner import prepare_run, run_once
from app.templates.loader import load_templates
from sqlalchemy import select

pytestmark = pytest.mark.asyncio
REAL_MODEL = not settings.default_chat_model.startswith("fake/")
THRESHOLDS = {"route": 0.90, "facts": 0.85}


async def _seeded_agent(template_key: str) -> tuple[Organization, Agent, User] | None:
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


@pytest.mark.parametrize("template_key", ["hr_onboarding", "hr_analytics", "school_parent_helpdesk"])
async def test_template_eval_set(template_key: str) -> None:
    seeded = await _seeded_agent(template_key)
    if seeded is None:
        pytest.skip("run `make seed` first")
    org, agent, user = seeded
    eval_set = load_templates()[template_key].get("eval_set", [])
    cfg = AgentConfig.model_validate(agent.draft_config)
    results = []
    for case in eval_set:
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
                session,
                org=org,
                agent=agent,
                cfg=cfg,
                channel="playground",
                conversation=conv,
                user_text=case["question"],
            )
            await session.commit()
        outcome = await run_once(ctx, case["question"])
        answer = (outcome.state.get("answer") or "").lower()
        tools = (outcome.state.get("route") or {}).get("tools") or []
        route_ok = (
            sorted(tools) == sorted(case.get("expected_route", [])) if "expected_route" in case else True
        )
        facts = case.get("expected_facts", [])
        facts_ok = all(f.lower() in answer for f in facts) if facts else None
        no_answer_ok = outcome.state.get("no_answer") if case.get("expected_no_answer") else None
        results.append(
            {
                "question": case["question"],
                "route": tools,
                "route_ok": route_ok,
                "facts_ok": facts_ok,
                "no_answer_ok": no_answer_ok,
                "error": outcome.error,
                "latency_ms": outcome.latency_ms,
            }
        )
    route_acc = sum(1 for r in results if r["route_ok"]) / max(1, len(results))
    fact_cases = [r for r in results if r["facts_ok"] is not None]
    fact_acc = sum(1 for r in fact_cases if r["facts_ok"]) / max(1, len(fact_cases))
    report = {
        "template": template_key,
        "model": settings.default_chat_model,
        "cases": len(results),
        "route_accuracy": round(route_acc, 3),
        "fact_accuracy": round(fact_acc, 3) if fact_cases else None,
        "run_at": datetime.now(UTC).isoformat(),
        "results": results,
    }
    out_dir = os.path.join(os.path.dirname(__file__), "reports")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"{template_key}.json"), "w") as f:
        json.dump(report, f, indent=1, default=str)
    print(
        f"\n[{template_key}] route accuracy {route_acc:.0%}, fact accuracy {fact_acc:.0%} over {len(results)} cases (model {settings.default_chat_model})"
    )
    assert all(r["error"] is None for r in results), [r for r in results if r["error"]]
    if REAL_MODEL:
        assert route_acc >= THRESHOLDS["route"], report
        if fact_cases:
            assert fact_acc >= THRESHOLDS["facts"], report
