"""Application data model (PRD §15). One file on purpose: every tenant table has org_id."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, OrgScopedMixin, PKMixin, SoftDeleteMixin, TimestampMixin

ROLES = ("owner", "admin", "editor", "viewer")
ROLE_RANK = {r: i for i, r in enumerate(reversed(ROLES))}  # owner=3 ... viewer=0


# ---------------------------------------------------------------- identity & tenancy
class User(PKMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user", lazy="selectin", cascade="all, delete-orphan", passive_deletes=True
    )


class OAuthAccount(PKMixin, TimestampMixin, Base):
    __tablename__ = "oauth_accounts"
    __table_args__ = (UniqueConstraint("provider", "provider_account_id"),)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    provider_account_id: Mapped[str] = mapped_column(String(255))


class RefreshToken(PKMixin, TimestampMixin, Base):
    __tablename__ = "refresh_tokens"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    user_agent: Mapped[str | None] = mapped_column(String(400))
    ip: Mapped[str | None] = mapped_column(String(64))


class EmailToken(PKMixin, TimestampMixin, Base):
    __tablename__ = "email_tokens"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(20))  # verify | reset
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Organization(PKMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "organizations"
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    industry: Mapped[str | None] = mapped_column(String(80))
    size: Mapped[str | None] = mapped_column(String(40))
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    memberships: Mapped[list[Membership]] = relationship(
        back_populates="org", lazy="selectin", cascade="all, delete-orphan", passive_deletes=True
    )


class Membership(PKMixin, TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("org_id", "user_id"),)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    org: Mapped[Organization] = relationship(back_populates="memberships", lazy="joined")
    user: Mapped[User] = relationship(back_populates="memberships", lazy="joined")


class Invitation(PKMixin, TimestampMixin, OrgScopedMixin, Base):
    __tablename__ = "invitations"
    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[str] = mapped_column(String(20))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProviderCredential(PKMixin, TimestampMixin, OrgScopedMixin, Base):
    __tablename__ = "provider_credentials"
    provider: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(80))
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    api_base: Mapped[str | None] = mapped_column(String(400))
    last4: Mapped[str] = mapped_column(String(8))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(PKMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_org_created", "org_id", "created_at"),)
    org_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(80))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FeatureFlag(PKMixin, TimestampMixin, Base):
    __tablename__ = "feature_flags"
    __table_args__ = (UniqueConstraint("org_id", "key"),)
    org_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)  # NULL = global default
    key: Mapped[str] = mapped_column(String(60))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Notification(PKMixin, TimestampMixin, OrgScopedMixin, Base):
    __tablename__ = "notifications"
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------- agents
class Template(PKMixin, TimestampMixin, Base):
    __tablename__ = "templates"
    key: Mapped[str] = mapped_column(String(60), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40))
    icon: Mapped[str] = mapped_column(String(40), default="bot")
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Agent(PKMixin, TimestampMixin, OrgScopedMixin, SoftDeleteMixin, Base):
    __tablename__ = "agents"
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    template_key: Mapped[str | None] = mapped_column(String(60))
    draft_config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    draft_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    latest_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class AgentVersion(PKMixin, OrgScopedMixin, Base):
    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version_number"),)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    schema_snapshot_ids: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    changelog: Mapped[str | None] = mapped_column(Text)
    published_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------- knowledge
class KnowledgeBase(PKMixin, TimestampMixin, OrgScopedMixin, SoftDeleteMixin, Base):
    __tablename__ = "knowledge_bases"
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    embedding_model: Mapped[str] = mapped_column(String(120))
    embedding_dim: Mapped[int] = mapped_column(Integer)
    chunk_config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    qdrant_collection: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="ready")
    doc_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)


class Document(PKMixin, TimestampMixin, OrgScopedMixin, SoftDeleteMixin, Base):
    __tablename__ = "documents"
    kb_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(400))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    error: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    parse_strategy: Mapped[str] = mapped_column(String(20), default="auto")
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IngestionJob(PKMixin, TimestampMixin, OrgScopedMixin, Base):
    __tablename__ = "ingestion_jobs"
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    kb_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    job_type: Mapped[str] = mapped_column(String(20))  # ingest | reindex | delete
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


# ---------------------------------------------------------------- datasets
class Dataset(PKMixin, TimestampMixin, OrgScopedMixin, SoftDeleteMixin, Base):
    __tablename__ = "datasets"
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(20))  # postgres | spreadsheet
    status: Mapped[str] = mapped_column(String(20), default="connecting")
    connection_config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    encrypted_password: Mapped[str | None] = mapped_column(Text)
    allow_sampling: Mapped[bool] = mapped_column(Boolean, default=True)
    current_schema_version: Mapped[int] = mapped_column(Integer, default=0)
    storage_key: Mapped[str | None] = mapped_column(String(400))
    storage_etag: Mapped[str | None] = mapped_column(String(80))
    error: Mapped[str | None] = mapped_column(Text)
    tables: Mapped[list[DatasetTable]] = relationship(
        back_populates="dataset", lazy="noload", cascade="all, delete-orphan", passive_deletes=True
    )


class DatasetSchemaVersion(PKMixin, Base):
    __tablename__ = "dataset_schema_versions"
    __table_args__ = (UniqueConstraint("dataset_id", "version"),)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    raw_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    diff: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DatasetTable(PKMixin, TimestampMixin, Base):
    __tablename__ = "dataset_tables"
    __table_args__ = (UniqueConstraint("dataset_id", "schema_name", "table_name"),)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    schema_name: Mapped[str] = mapped_column(String(120), default="public")
    table_name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(10), default="table")
    row_estimate: Mapped[int | None] = mapped_column(BigInteger)
    description: Mapped[str | None] = mapped_column(Text)
    description_source: Mapped[str | None] = mapped_column(String(20))  # db_comment | ai_draft | user
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    exposed: Mapped[bool] = mapped_column(Boolean, default=True)
    embedding_point_id: Mapped[str | None] = mapped_column(String(80))
    dataset: Mapped[Dataset] = relationship(back_populates="tables")
    columns: Mapped[list[DatasetColumn]] = relationship(
        back_populates="table",
        lazy="selectin",
        order_by="DatasetColumn.ordinal",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class DatasetColumn(PKMixin, TimestampMixin, Base):
    __tablename__ = "dataset_columns"
    __table_args__ = (UniqueConstraint("table_id", "column_name"),)
    table_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dataset_tables.id", ondelete="CASCADE"), index=True
    )
    column_name: Mapped[str] = mapped_column(String(200))
    data_type: Mapped[str] = mapped_column(String(120))
    nullable: Mapped[bool] = mapped_column(Boolean, default=True)
    is_pk: Mapped[bool] = mapped_column(Boolean, default=False)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str | None] = mapped_column(Text)
    description_source: Mapped[str | None] = mapped_column(String(20))
    semantic_type: Mapped[str | None] = mapped_column(String(40))
    sample_values: Mapped[list[Any] | None] = mapped_column(JSONB)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    table: Mapped[DatasetTable] = relationship(back_populates="columns")


class DatasetRelationship(PKMixin, TimestampMixin, Base):
    __tablename__ = "dataset_relationships"
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    from_table_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dataset_tables.id", ondelete="CASCADE"))
    from_column: Mapped[str] = mapped_column(String(200))
    to_table_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dataset_tables.id", ondelete="CASCADE"))
    to_column: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(10), default="fk")  # fk | inferred | user
    confidence: Mapped[float | None] = mapped_column(Float)


class DatasetGlossary(PKMixin, TimestampMixin, Base):
    __tablename__ = "dataset_glossary"
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    term: Mapped[str] = mapped_column(String(120))
    definition: Mapped[str] = mapped_column(Text)


class DatasetExampleQuery(PKMixin, TimestampMixin, Base):
    __tablename__ = "dataset_example_queries"
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    question: Mapped[str] = mapped_column(Text)
    sql: Mapped[str] = mapped_column(Text)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------- channels & conversations
class Deployment(PKMixin, TimestampMixin, OrgScopedMixin, SoftDeleteMixin, Base):
    __tablename__ = "deployments"
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    agent_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_versions.id", ondelete="RESTRICT"))
    channel: Mapped[str] = mapped_column(String(20), default="whatsapp")
    status: Mapped[str] = mapped_column(String(20), default="pending_webhook")
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    encrypted_access_token: Mapped[str | None] = mapped_column(Text)
    encrypted_app_secret: Mapped[str | None] = mapped_column(Text)
    verify_token_hash: Mapped[str | None] = mapped_column(String(64))
    encrypted_verify_token: Mapped[str | None] = mapped_column(Text)
    webhook_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rate_limits: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    message_count: Mapped[int] = mapped_column(Integer, default=0)


class Contact(PKMixin, TimestampMixin, OrgScopedMixin, Base):
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("deployment_id", "wa_id"),)
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("deployments.id", ondelete="CASCADE"), index=True
    )
    wa_id: Mapped[str] = mapped_column(String(32))
    wa_id_hash: Mapped[str] = mapped_column(String(64))
    profile_name: Mapped[str | None] = mapped_column(String(200))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)


class Conversation(PKMixin, TimestampMixin, OrgScopedMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_org_last", "org_id", "last_message_at"),)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    channel: Mapped[str] = mapped_column(String(20))  # playground | whatsapp
    playground_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    flags: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Message(PKMixin, OrgScopedMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conv_created", "conversation_id", "created_at"),)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(12))
    content: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(120), unique=True)
    status: Mapped[str] = mapped_column(String(12), default="received")
    run_id: Mapped[str | None] = mapped_column(String(40), index=True)
    route: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    tool_summary: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[list[Any] | None] = mapped_column(JSONB)
    guardrail_events: Mapped[list[Any] | None] = mapped_column(JSONB)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RunTrace(PKMixin, OrgScopedMixin, Base):
    __tablename__ = "run_traces"
    message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    node_timings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    rag_chunks: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    sql_attempts: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    web_results: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    usage: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    langsmith_run_url: Mapped[str | None] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Feedback(PKMixin, TimestampMixin, OrgScopedMixin, Base):
    __tablename__ = "feedback"
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    rating: Mapped[int] = mapped_column(Integer)  # +1 / -1
    comment: Mapped[str | None] = mapped_column(Text)
    saved_as_eval: Mapped[bool] = mapped_column(Boolean, default=False)


class EvalExample(PKMixin, TimestampMixin, OrgScopedMixin, Base):
    __tablename__ = "eval_examples"
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    question: Mapped[str] = mapped_column(Text)
    expected_answer: Mapped[str | None] = mapped_column(Text)
    expected_route: Mapped[list[str] | None] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------- usage
class UsageEvent(PKMixin, OrgScopedMixin, Base):
    # ponytail: plain table; partition by month (PRD §15.6) once volume makes retention deletes slow
    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_events_org_created", "org_id", "created_at"),)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    run_id: Mapped[str | None] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(
        String(20)
    )  # llm | embedding | web_search | sql | message_in | message_out
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(120))
    credential_mode: Mapped[str] = mapped_column(String(12), default="platform")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    units: Mapped[int] = mapped_column(Integer, default=1)
    est_cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UsageDaily(Base):
    __tablename__ = "usage_daily"
    org_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, primary_key=True, default=uuid.UUID(int=0))
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    model: Mapped[str] = mapped_column(String(120), primary_key=True, default="")
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    units: Mapped[int] = mapped_column(BigInteger, default=0)
    est_cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0)


ORG_SCOPED_TABLES = [
    Invitation,
    ProviderCredential,
    Agent,
    AgentVersion,
    KnowledgeBase,
    Document,
    IngestionJob,
    Dataset,
    Deployment,
    Contact,
    Conversation,
    Message,
    RunTrace,
    Feedback,
    EvalExample,
    UsageEvent,
    Notification,
]
