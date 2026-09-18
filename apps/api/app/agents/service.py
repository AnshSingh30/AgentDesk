from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.schemas import AgentConfig
from app.core.config import settings
from app.core.errors import Problem
from app.db.models import (
    Agent,
    AgentVersion,
    Dataset,
    DatasetColumn,
    DatasetTable,
    Deployment,
    KnowledgeBase,
    ProviderCredential,
)
from app.templates.loader import load_templates

STUDENT_COLS = re.compile(r"(student|roll|admission|guardian|parent_phone|dob|date_of_birth)", re.I)


def etag_for(agent: Agent) -> str:
    return f'W/"{int(agent.draft_updated_at.timestamp() * 1000)}"'


def parse_config(raw: dict[str, Any]) -> AgentConfig:
    try:
        return AgentConfig.model_validate(raw)
    except ValidationError as e:
        raise Problem(
            422,
            "Invalid agent configuration",
            errors=[{"loc": [str(p) for p in err["loc"]], "msg": err["msg"]} for err in e.errors()],
        ) from e


async def agent_status(session: AsyncSession, agent: Agent) -> str:
    if agent.latest_version_id is None:
        return "draft"
    live = (
        await session.execute(
            select(func.count())
            .select_from(Deployment)
            .where(
                Deployment.agent_id == agent.id,
                Deployment.deleted_at.is_(None),
                Deployment.status.in_(("live", "pending_webhook")),
            )
        )
    ).scalar_one()
    return "deployed" if live else "published"


async def latest_version_number(session: AsyncSession, agent: Agent) -> int | None:
    if agent.latest_version_id is None:
        return None
    v = await session.get(AgentVersion, agent.latest_version_id)
    return v.version_number if v else None


async def validate_agent(
    session: AsyncSession, org_id: uuid.UUID, agent: Agent
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Publish-time validation (AG-6): blocking errors + warnings."""
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    try:
        cfg = AgentConfig.model_validate(agent.draft_config or {})
    except ValidationError as e:
        return [
            {"field": ".".join(str(p) for p in err["loc"]), "message": err["msg"]} for err in e.errors()
        ], []

    if not cfg.instructions.system_prompt.strip():
        errors.append(
            {"field": "instructions.system_prompt", "message": "Instructions (system prompt) are empty"}
        )
    tools = cfg.tools
    if tools.rag.enabled:
        if not tools.rag.kb_ids:
            errors.append(
                {"field": "tools.rag.kb_ids", "message": "Knowledge Base is enabled but none is attached"}
            )
        else:
            kbs = (
                (
                    await session.execute(
                        select(KnowledgeBase).where(
                            KnowledgeBase.org_id == org_id,
                            KnowledgeBase.id.in_(tools.rag.kb_ids),
                            KnowledgeBase.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if len(kbs) != len(set(tools.rag.kb_ids)):
                errors.append(
                    {"field": "tools.rag.kb_ids", "message": "An attached Knowledge Base no longer exists"}
                )
            for kb in kbs:
                if kb.doc_count == 0:
                    warnings.append(
                        {
                            "field": "tools.rag.kb_ids",
                            "message": f"Knowledge Base '{kb.name}' has no documents yet",
                        }
                    )
    if tools.sql.enabled:
        if not tools.sql.dataset_id:
            errors.append(
                {"field": "tools.sql.dataset_id", "message": "Dataset is enabled but not connected"}
            )
        else:
            ds = (
                await session.execute(
                    select(Dataset).where(
                        Dataset.org_id == org_id,
                        Dataset.id == tools.sql.dataset_id,
                        Dataset.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if ds is None:
                errors.append(
                    {"field": "tools.sql.dataset_id", "message": "The connected dataset no longer exists"}
                )
            elif ds.status not in ("ready", "needs_review"):
                errors.append(
                    {
                        "field": "tools.sql.dataset_id",
                        "message": f"Dataset is not ready (status: {ds.status})",
                    }
                )
            else:
                tables = (
                    (
                        await session.execute(
                            select(DatasetTable).where(
                                DatasetTable.dataset_id == ds.id, DatasetTable.exposed.is_(True)
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                if not tables:
                    errors.append(
                        {"field": "tools.sql.dataset_id", "message": "No tables are exposed to the agent"}
                    )
                unreviewed = [t.table_name for t in tables if not t.reviewed]
                if unreviewed:
                    warnings.append(
                        {
                            "field": "tools.sql.dataset_id",
                            "message": f"Dataset has {len(unreviewed)} table(s) without reviewed descriptions: "
                            + ", ".join(unreviewed[:5]),
                        }
                    )
                if agent.template_key == "school_parent_helpdesk":
                    cols = (
                        (
                            await session.execute(
                                select(DatasetColumn.column_name)
                                .join(DatasetTable)
                                .where(DatasetTable.dataset_id == ds.id, DatasetColumn.hidden.is_(False))
                            )
                        )
                        .scalars()
                        .all()
                    )
                    suspicious = [c for c in cols if STUDENT_COLS.search(c)]
                    if suspicious:
                        warnings.append(
                            {
                                "field": "tools.sql.dataset_id",
                                "message": "Dataset contains student-identifying columns ("
                                + ", ".join(suspicious[:5])
                                + "). Per-child data requires the authentication add-on; hide these columns.",
                            }
                        )
    if tools.web_search.enabled and not settings.tavily_api_key:
        warnings.append(
            {
                "field": "tools.web_search",
                "message": "Web search is enabled but no Tavily key is configured on the platform",
            }
        )
    if len(tools.enabled_names()) >= 2:
        missing = [
            n
            for n, v in (
                ("rag", cfg.routing.rag_instructions),
                ("sql", cfg.routing.sql_instructions),
                ("web_search", cfg.routing.web_instructions),
            )
            if n in tools.enabled_names() and not v.strip()
        ]
        if missing:
            warnings.append(
                {"field": "routing", "message": "Routing instructions are empty for: " + ", ".join(missing)}
            )
    if cfg.guardrails.web_fallback and not tools.web_search.enabled:
        warnings.append(
            {"field": "guardrails.web_fallback", "message": "Web fallback requires web search to be enabled"}
        )
    if cfg.model.mode in ("byo", "custom"):
        if cfg.model.credential_id:
            cred = await session.get(ProviderCredential, cfg.model.credential_id)
            if cred is None or cred.org_id != org_id:
                errors.append(
                    {"field": "model.credential_id", "message": "The selected API key no longer exists"}
                )
        elif cfg.model.mode == "byo":
            errors.append(
                {"field": "model.credential_id", "message": "Select an API key for bring-your-own-key mode"}
            )
        if cfg.model.mode == "custom" and not cfg.model.api_base:
            errors.append({"field": "model.api_base", "message": "Custom endpoint URL is required"})
        if cfg.model.mode == "custom":
            warnings.append(
                {
                    "field": "model",
                    "message": "Custom endpoints: tool calling / structured output support is unknown",
                }
            )
        if cfg.model.mode == "byo" and not cfg.model.model:
            errors.append({"field": "model.model", "message": "Select a model"})
    elif settings.default_chat_model.startswith("fake/"):
        warnings.append(
            {
                "field": "model",
                "message": "Platform default model is the demo model; answers are canned until a provider key is configured",
            }
        )
    return errors, warnings


async def publish_agent(
    session: AsyncSession, org_id: uuid.UUID, agent: Agent, user_id: uuid.UUID, changelog: str | None
) -> AgentVersion:
    errors, _ = await validate_agent(session, org_id, agent)
    if errors:
        raise Problem(
            422,
            "Agent configuration has blocking errors",
            errors=[{"loc": [e["field"]], "msg": e["message"]} for e in errors],
        )
    cfg = AgentConfig.model_validate(agent.draft_config)
    n = (
        await session.execute(
            select(func.coalesce(func.max(AgentVersion.version_number), 0)).where(
                AgentVersion.agent_id == agent.id
            )
        )
    ).scalar_one() + 1
    snapshot_ids: dict[str, Any] = {}
    if cfg.tools.sql.enabled and cfg.tools.sql.dataset_id:
        ds = await session.get(Dataset, cfg.tools.sql.dataset_id)
        if ds:
            snapshot_ids["dataset_schema_version"] = ds.current_schema_version
    snapshot = cfg.model_dump(mode="json")
    snapshot["meta"] = {
        **snapshot.get("meta", {}),
        "agent_name": agent.name,
        "template_key": agent.template_key,
        "published_at": datetime.now(UTC).isoformat(),
    }
    version = AgentVersion(
        org_id=org_id,
        agent_id=agent.id,
        version_number=n,
        config=snapshot,
        schema_snapshot_ids=snapshot_ids,
        changelog=changelog,
        published_by=user_id,
    )
    session.add(version)
    await session.flush()
    agent.latest_version_id = version.id
    return version


def config_from_template(template_key: str | None) -> tuple[dict[str, Any], str | None]:
    from app.templates.loader import template_config

    templates = load_templates()
    if template_key and template_key in templates:
        return template_config(templates[template_key]), templates[template_key]["description"]
    return template_config(templates["blank"]), None
