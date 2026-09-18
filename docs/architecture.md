# Architecture

AgentDesk is a multi-tenant SaaS: a Next.js web app talks to a FastAPI API; a separate webhook process receives WhatsApp events and hands them to an ARQ worker; PostgreSQL holds application data, Redis handles queues/rate limits/locks/caches, Qdrant stores vectors, MinIO/S3 stores files and DuckDB datasets. LLM calls go through LiteLLM so platform keys, customer keys and custom OpenAI-compatible endpoints share one code path.

```
browser ──> web (Next.js) ──> api (FastAPI) ──┬─> postgres (app data)
                                              ├─> redis (queues, rate limits, locks, caches)
                                              ├─> qdrant (chunks, hybrid dense+BM25)
                                              ├─> minio/s3 (documents, duckdb files, exports)
                                              └─> LLM providers via LiteLLM, Tavily
Meta Graph API ──> webhook (FastAPI) ──> redis queue ──> worker (ARQ) ──> agent graph ──> Meta send API
```

## Request flows

**Playground message** (`POST /v1/orgs/{org}/playground/sessions/{sid}/messages`, SSE): membership + CSRF + rate limit → load draft config (Redis 60 s cache, busted on save) or a published version → load trimmed history → `prepare_run` builds a `RunContext` (models resolved, dataset context, KB summaries) → the LangGraph run streams `route`, `tool_start`, `tool_result`, `token`, `citations`, `guardrail`, `usage`, `done` events → messages, run trace and usage events are persisted.

**WhatsApp inbound**: `POST /webhooks/whatsapp/{deployment_id}` verifies `X-Hub-Signature-256` with the deployment's app secret (constant-time), dedupes by `wamid` (Redis `SETNX`, 48 h), drops stale (>10 min) events and enqueues `process_whatsapp_message`, answering 200 in milliseconds. The worker serialises per contact with a Redis lock, applies per-contact/per-deployment limits, marks the message read with a typing indicator, runs the graph (non-streaming), formats Markdown → WhatsApp, splits at ≤4,000 chars and sends with retries.

## Agent runtime (`app/runtime`)

One compiled LangGraph handles 0, 1 or many tools:

`input_guardrails → (blocked_response) | query_rewrite → router → [rag_retrieve ‖ sql_agent ‖ web_search] → collect_context → generate_answer → output_guardrails → channel_formatter`

- The router is skipped logically for 0/1 tools (fixed decisions) and uses the utility model with structured output for ≥2 tools; multi-tool routes fan out with `Send` and join in `collect_context`, which also applies the fallback chain KB → web → no-answer.
- `sql_agent` invokes the SQL subgraph (`app/sql/agent.py`): intent check → schema selection (≤15 tables inline, otherwise embedded table cards + FK hop) → structured SQL generation → `validate_sql` → optional `interrupt()` for confirm-before-run → read-only execution → reflection/retry (max 3 attempts).
- Prompts are assembled in PRD order with a token budget (history ≥2 turns kept → chunks → SQL rows → web results trimmed).
- Non-serialisable dependencies travel in `config["configurable"]["ctx"]`; state is a `TypedDict` with reducers for parallel branches.
- Checkpointer is `MemorySaver` (single process). Use `langgraph-checkpoint-postgres` to run confirm-before-run across multiple API workers.

## Knowledge base (`app/rag`)

Presigned PUT to S3 → magic-byte validation → parse (pdfminer for PDF with title/table heuristics and page numbers; Unstructured for DOCX/MD/TXT) → `chunk_by_title` (1,500/1,200/200/300 defaults) → contextual header → dense (FastEmbed local or provider via LiteLLM, Redis-cached) + BM25 sparse → Qdrant upsert with tenant payload indexes (`org_id` is_tenant, `kb_id`, `document_id`). Retrieval is hybrid RRF with a dense-cosine threshold deciding relevance; optional Cohere/local reranking.

## Datasets (`app/sql`)

Postgres introspection reads `pg_catalog`/`information_schema` (tables, views, columns, PKs, FKs, indexes, comments, enums, samples with sensitive-name skipping and cardinality checks) into a versioned snapshot; `apply_snapshot` diffs versions and preserves descriptions. Spreadsheets are previewed with pandas, typed and loaded into a per-dataset DuckDB file. The validator (`validator.py`) parses with sqlglot, rejects anything but a single read-only SELECT, blocks catalogs/functions/table functions, enforces the exposed-table allowlist and hidden columns (star expansion via `qualify`), and injects/caps LIMIT. Execution runs `BEGIN READ ONLY` with 15 s statement timeouts (Postgres) or a read-only DuckDB connection with external access disabled.

## Security

See README "Security notes" and `docs/adr`. Cross-tenant tests parametrise every org-scoped endpoint.

## Observability

structlog JSON logs with secret redaction and request ids, Prometheus metrics at `/metrics`, optional LangSmith tracing via LiteLLM callbacks (`LANGSMITH_TRACING=true`), Sentry DSN hooks in config. `/readyz` checks Postgres, Redis, Qdrant and S3.
