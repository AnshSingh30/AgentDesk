from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.runtime.prompts import CHANNEL_RULES, CITATION_RULES, PLATFORM_PREAMBLE
from app.templates.loader import load_templates, template_config

router = APIRouter(tags=["templates"])


@router.get("/templates/platform-preamble")
async def platform_preamble() -> dict[str, Any]:
    """Read-only platform instructions prepended to every agent prompt (PR-5)."""
    return {"preamble": PLATFORM_PREAMBLE, "citation_rules": CITATION_RULES, "channel_rules": CHANNEL_RULES}


@router.get("/templates")
async def list_templates() -> list[dict[str, Any]]:
    out = []
    for key, tpl in load_templates().items():
        cfg = template_config(tpl)
        out.append(
            {
                "key": key,
                "name": tpl["name"],
                "description": tpl["description"],
                "icon": tpl.get("icon", "bot"),
                "category": tpl.get("category", "general"),
                "version": tpl.get("version", 1),
                "enabled_tools": tpl.get("enabled_tools", {}),
                "starter_questions": tpl.get("starter_questions", []),
                "recommended_kb_docs": tpl.get("recommended_kb_docs", []),
                "recommended_dataset_schema": tpl.get("recommended_dataset_schema", []),
                "system_prompt": cfg["instructions"]["system_prompt"],
            }
        )
    return out
