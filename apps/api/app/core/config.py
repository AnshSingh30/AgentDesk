"""Application settings (PRD §21.4). Loaded once; fails fast outside local/test."""

from __future__ import annotations

import base64
from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-jwt-secret-change-me"
DEV_CSRF_SECRET = "dev-only-csrf-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_env: Literal["local", "test", "staging", "production"] = "local"
    app_name: str = "AgentDesk"
    app_url: str = "http://localhost:3000"
    api_url: str = "http://localhost:8000"
    webhook_base_url: str = "http://localhost:8001"
    expose_docs: bool = False
    log_level: str = "INFO"
    git_sha: str = "dev"

    # Security
    jwt_secret: str = DEV_JWT_SECRET
    encryption_master_keys: str = ""  # comma-separated base64 32-byte keys, first = active
    csrf_secret: str = DEV_CSRF_SECRET
    allow_private_endpoints: bool = False
    access_token_ttl_seconds: int = 15 * 60
    refresh_token_ttl_seconds: int = 30 * 24 * 3600
    verify_token_ttl_seconds: int = 24 * 3600
    reset_token_ttl_seconds: int = 30 * 60
    invite_ttl_seconds: int = 7 * 24 * 3600
    login_max_failures: int = 5
    login_lockout_seconds: int = 15 * 60

    # Data
    database_url: str = "postgresql+asyncpg://agentdesk:agentdesk@localhost:5433/agentdesk"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    s3_endpoint: str = "http://localhost:9000"
    s3_public_endpoint: str | None = None  # browser-facing endpoint for presigned URLs
    s3_bucket: str = "agentdesk"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_region: str = "us-east-1"

    # LLM
    default_chat_model: str = "fake/chat"
    default_utility_model: str = ""
    default_embedding_model: str = "fastembed/BAAI/bge-small-en-v1.5"
    # Optional OpenAI-compatible base URLs for the platform models (vLLM, Ollama, TEI, LiteLLM proxy...).
    # Only applied to `openai/...` model strings; the key comes from OPENAI_API_KEY.
    default_llm_api_base: str | None = None
    embedding_api_base: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    openrouter_api_key: str | None = None
    tavily_api_key: str | None = None
    llm_timeout_seconds: int = 60
    llm_concurrency_per_org: int = 10
    run_token_ceiling: int = 50_000

    # Observability
    langsmith_api_key: str | None = None
    langsmith_project: str = "agentdesk-local"
    langsmith_tracing: bool = False
    sentry_dsn: str | None = None

    # Email
    resend_api_key: str | None = None
    email_from: str = "AgentDesk <no-reply@agentdesk.local>"
    smtp_host: str = "localhost"
    smtp_port: int = 1025

    # OAuth
    google_client_id: str | None = None
    google_client_secret: str | None = None

    # WhatsApp
    wa_graph_api_version: str = "v21.0"
    wa_graph_base_url: str = "https://graph.facebook.com"

    # Limits
    max_upload_mb: int = 25
    max_files_per_kb: int = 200
    org_storage_limit_mb: int = 500
    max_spreadsheet_mb: int = 50
    run_timeout_seconds: int = 90
    retention_traces_days: int = 30
    retention_messages_days_default: int = 180

    @field_validator("default_utility_model", mode="after")
    @classmethod
    def _utility_default(cls, v: str) -> str:
        return v  # resolved lazily via utility_model property

    @property
    def utility_model(self) -> str:
        return self.default_utility_model or self.default_chat_model

    @property
    def is_prod_like(self) -> bool:
        return self.app_env in ("staging", "production")

    @property
    def cookie_secure(self) -> bool:
        return self.is_prod_like

    @property
    def master_keys(self) -> list[bytes]:
        keys = [k.strip() for k in self.encryption_master_keys.split(",") if k.strip()]
        out: list[bytes] = []
        for k in keys:
            raw = base64.b64decode(k)
            if len(raw) != 32:
                raise ValueError("ENCRYPTION_MASTER_KEYS entries must be base64 of 32 bytes")
            out.append(raw)
        if not out:
            if self.is_prod_like:
                raise ValueError("ENCRYPTION_MASTER_KEYS is required outside local/test")
            # deterministic dev key so restarts can decrypt local data
            out.append(b"agentdesk-dev-master-key-32bytes".ljust(32, b"0")[:32])
        return out

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @model_validator(mode="after")
    def _fail_fast(self) -> Settings:
        if self.is_prod_like:
            problems = []
            if self.jwt_secret == DEV_JWT_SECRET or len(self.jwt_secret) < 32:
                problems.append("JWT_SECRET must be set (>= 32 chars)")
            if self.csrf_secret == DEV_CSRF_SECRET:
                problems.append("CSRF_SECRET must be set")
            if not self.encryption_master_keys:
                problems.append("ENCRYPTION_MASTER_KEYS must be set")
            if self.s3_secret_key == "minioadmin":
                problems.append("S3_SECRET_KEY must not be the default")
            if problems:
                raise ValueError("Invalid production configuration: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
