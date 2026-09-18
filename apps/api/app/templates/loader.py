"""Templates live as YAML in this package and are seeded into the templates table (PRD §7)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Template

TEMPLATE_DIR = Path(__file__).parent


@lru_cache
def load_templates() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(TEMPLATE_DIR.glob("*.yaml")):
        with path.open() as f:
            data = yaml.safe_load(f)
        out[data["key"]] = data
    return out


def template_config(tpl: dict[str, Any]) -> dict[str, Any]:
    """Build a draft_config (AgentConfig shape) from a template definition."""
    from app.agents.schemas import AgentConfig

    tools = tpl.get("enabled_tools", {})
    ri = tpl.get("router_instructions", {})
    g = tpl.get("guardrails", {})
    cfg = AgentConfig.model_validate(
        {
            "instructions": {
                "system_prompt": tpl["system_prompt"].strip(),
                "starter_questions": tpl.get("starter_questions", [])[:6],
            },
            "tools": {
                "rag": {"enabled": bool(tools.get("rag"))},
                "sql": {"enabled": bool(tools.get("sql"))},
                "web_search": {"enabled": bool(tools.get("web_search"))},
            },
            "routing": {
                "rag_instructions": ri.get("rag", ""),
                "sql_instructions": ri.get("sql", ""),
                "web_instructions": ri.get("web", ""),
            },
            "guardrails": {
                k: v
                for k, v in g.items()
                if k
                in (
                    "no_answer_message",
                    "allowed_topics",
                    "blocked_topics",
                    "blocked_keywords",
                    "language",
                    "tone",
                    "web_fallback",
                )
            },
            "memory": tpl.get("memory", {}),
        }
    )
    return cfg.model_dump(mode="json")


async def sync_templates(session: AsyncSession) -> None:
    existing = {t.key: t for t in (await session.execute(select(Template))).scalars().all()}
    for key, tpl in load_templates().items():
        payload = {
            "name": tpl["name"],
            "description": tpl["description"],
            "category": tpl.get("category", "general"),
            "icon": tpl.get("icon", "bot"),
            "version": int(tpl.get("version", 1)),
            "config": {
                **template_config(tpl),
                "meta": {
                    "recommended_kb_docs": tpl.get("recommended_kb_docs", []),
                    "recommended_dataset_schema": tpl.get("recommended_dataset_schema", []),
                    "student_identifying_columns": tpl.get("student_identifying_columns", []),
                    "eval_set": tpl.get("eval_set", []),
                },
            },
        }
        if key in existing:
            for k, v in payload.items():
                setattr(existing[key], k, v)
        else:
            session.add(Template(key=key, is_active=True, **payload))
