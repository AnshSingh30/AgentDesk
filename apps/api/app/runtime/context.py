"""Per-run context: everything the graph nodes need that must not live in serialised state."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from langgraph.config import get_stream_writer

from app.agents.schemas import AgentConfig
from app.core.redis import get_redis
from app.db.models import Dataset
from app.llm.gateway import ResolvedModel
from app.sql.context import DatasetContext


class RunCancelled(Exception):
    pass


@dataclass
class RunContext:
    run_id: str
    org_id: uuid.UUID
    org_name: str
    agent_id: uuid.UUID
    agent_name: str
    conversation_id: uuid.UUID
    channel: str
    cfg: AgentConfig
    main_model: ResolvedModel
    utility_model: ResolvedModel
    history: list[dict[str, str]] = field(default_factory=list)
    agent_version_id: uuid.UUID | None = None
    deployment_id: uuid.UUID | None = None
    user_message_id: uuid.UUID | None = None
    dataset: Dataset | None = None
    dataset_ctx: DatasetContext | None = None
    kb_summary: str = ""
    dataset_summary: str = ""
    tavily_key: str | None = None
    rerank_key: str | None = None
    confirm_sql: bool = False
    annotations: list[dict[str, Any]] = field(default_factory=list)  # {id, answer, vector}
    metadata: dict[str, Any] = field(default_factory=dict)

    async def check_cancelled(self) -> None:
        if await get_redis().get(f"run:cancel:{self.run_id}"):
            raise RunCancelled(self.run_id)


def emit(event: str, data: dict[str, Any]) -> None:
    """Send a custom stream event (no-op outside a streaming run)."""
    try:
        writer = get_stream_writer()
    except Exception:  # noqa: BLE001
        return
    writer({"event": event, "data": data})


def ctx_from(config: dict[str, Any]) -> RunContext:
    return config["configurable"]["ctx"]
