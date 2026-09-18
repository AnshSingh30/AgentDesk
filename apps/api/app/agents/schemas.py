"""AgentConfig - the validated draft_config / version snapshot shape (PRD §15.2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RerankCfg(BaseModel):
    enabled: bool = False
    provider: Literal["cohere", "local"] | None = None
    candidates: int = Field(20, ge=10, le=50)


class RagCfg(BaseModel):
    enabled: bool = False
    kb_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)
    top_k: int = Field(5, ge=1, le=20)
    threshold: float = Field(0.30, ge=0.0, le=1.0)
    search_mode: Literal["hybrid", "dense", "sparse"] = "hybrid"
    rerank: RerankCfg = Field(default_factory=RerankCfg)
    query_rewrite: bool = True
    citations: bool = True
    mmr: bool = False


class SqlCfg(BaseModel):
    enabled: bool = False
    dataset_id: uuid.UUID | None = None
    max_rows: int = Field(200, ge=1, le=5000)
    max_attempts: int = Field(3, ge=1, le=5)
    confirm_in_playground: bool = False


class WebCfg(BaseModel):
    enabled: bool = False
    max_results: int = Field(5, ge=1, le=10)
    depth: Literal["basic", "advanced"] = "basic"
    include_domains: list[str] = Field(default_factory=list, max_length=50)
    exclude_domains: list[str] = Field(default_factory=list, max_length=50)


class ToolsCfg(BaseModel):
    rag: RagCfg = Field(default_factory=RagCfg)
    sql: SqlCfg = Field(default_factory=SqlCfg)
    web_search: WebCfg = Field(default_factory=WebCfg)

    def enabled_names(self) -> list[str]:
        return [
            n for n, t in (("rag", self.rag), ("sql", self.sql), ("web_search", self.web_search)) if t.enabled
        ]


class RoutingCfg(BaseModel):
    rag_instructions: str = Field("", max_length=2000)
    sql_instructions: str = Field("", max_length=2000)
    web_instructions: str = Field("", max_length=2000)


class ModelCfg(BaseModel):
    mode: Literal["platform", "byo", "custom"] = "platform"
    credential_id: uuid.UUID | None = None
    provider: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=200)
    api_base: str | None = Field(default=None, max_length=400)
    temperature: float = Field(0.2, ge=0.0, le=2.0)
    max_output_tokens: int = Field(1024, ge=64, le=16000)
    utility_model: str | None = Field(default=None, max_length=200)
    context_window: int | None = Field(default=None, ge=2000, le=2_000_000)
    price_in_per_m: float | None = Field(default=None, ge=0)
    price_out_per_m: float | None = Field(default=None, ge=0)


class GuardrailAdvanced(BaseModel):
    input_classifier: bool = False
    grounding_check: bool = False
    injection_sensitivity: Literal["low", "medium", "high"] = "medium"
    custom_instructions: str = Field("", max_length=4000)
    log_pii_redaction: bool = False


class GuardrailsCfg(BaseModel):
    no_answer_message: str = Field("I'm not able to answer that from the information I have.", max_length=500)
    allowed_topics: str = Field("", max_length=2000)
    blocked_topics: list[str] = Field(default_factory=list, max_length=50)
    blocked_keywords: list[str] = Field(default_factory=list, max_length=200)
    language: str = Field("auto", max_length=40)
    tone: Literal["friendly", "professional", "concise"] = "friendly"
    web_fallback: bool = False
    max_input_chars: int = Field(2000, ge=50, le=10000)
    citations: bool = True
    advanced: GuardrailAdvanced = Field(default_factory=GuardrailAdvanced)


class MemoryCfg(BaseModel):
    max_turns: int = Field(10, ge=0, le=50)
    history_token_budget: int = Field(8000, ge=1000, le=32000)
    session_timeout_minutes: int = Field(1440, ge=5, le=43200)


class InstructionsCfg(BaseModel):
    system_prompt: str = Field("", max_length=12000)
    starter_questions: list[str] = Field(default_factory=list)
    welcome_message: str | None = Field(default=None, max_length=1000)
    ai_disclosure: bool = True

    @field_validator("starter_questions")
    @classmethod
    def _trim(cls, v: list[str]) -> list[str]:
        return [q.strip()[:200] for q in v if q.strip()][:6]


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    instructions: InstructionsCfg = Field(default_factory=InstructionsCfg)
    tools: ToolsCfg = Field(default_factory=ToolsCfg)
    routing: RoutingCfg = Field(default_factory=RoutingCfg)
    model: ModelCfg = Field(default_factory=ModelCfg)
    guardrails: GuardrailsCfg = Field(default_factory=GuardrailsCfg)
    memory: MemoryCfg = Field(default_factory=MemoryCfg)
    meta: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- API shapes
class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    template_key: str | None = Field(default=None, max_length=60)
    description: str | None = Field(default=None, max_length=1000)


class AgentPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    draft_config: dict[str, Any] | None = None


class AgentSummary(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    template_key: str | None
    status: Literal["draft", "published", "deployed"]
    latest_version_number: int | None
    tools: list[str]
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    draft_updated_at: datetime


class AgentOut(AgentSummary):
    draft_config: dict[str, Any]
    etag: str
    latest_version_id: uuid.UUID | None


class PublishIn(BaseModel):
    changelog: str | None = Field(default=None, max_length=2000)


class VersionOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    version_number: int
    changelog: str | None
    published_by: uuid.UUID | None
    created_at: datetime
    config: dict[str, Any] | None = None


class ValidationOut(BaseModel):
    ok: bool
    errors: list[dict[str, str]]
    warnings: list[dict[str, str]]
