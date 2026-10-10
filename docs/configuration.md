# Configuration

All settings are environment variables, read from `.env` at the repo root (Docker Compose) and
`apps/api/.env` (API, webhook and worker run natively). `make env` creates both from
[`.env.example`](../.env.example). Outside `local`/`test`, the API refuses to start if a security setting
is left at its development default.

## Models

AgentDesk talks to every model through [LiteLLM](https://docs.litellm.ai/), so model strings use the
`provider/model` form.

| Variable | Default | Purpose |
|---|---|---|
| `DEFAULT_CHAT_MODEL` | `fake/chat` | Platform model for answers. `fake/chat` is a deterministic demo model that needs no key |
| `DEFAULT_UTILITY_MODEL` | *(chat model)* | Cheaper model for routing, query rewriting, schema drafting and classifiers |
| `JEV_API_KEY` | – | [TypeSafe JEV](https://docs.typesafe.ai) for the router and the SQL intent gate (typed yes/no and choice judgments, $0.042/M input tokens, output free). Unset: the utility model does this. If JEV errors, the router uses all enabled tools and the SQL gate assumes a data question |
| `DEFAULT_LLM_API_BASE` | – | Base URL of an OpenAI-compatible server for `openai/...` models (vLLM, Ollama, LM Studio, a LiteLLM proxy…) |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY` | – | Platform keys, used when agents run in "Platform default" mode |

Examples:

```bash
# OpenAI
DEFAULT_CHAT_MODEL=openai/gpt-5-mini
OPENAI_API_KEY=sk-...

# Anthropic for answers, a smaller model for routing
DEFAULT_CHAT_MODEL=anthropic/claude-sonnet-5
DEFAULT_UTILITY_MODEL=anthropic/claude-haiku-4-5-20251001
ANTHROPIC_API_KEY=sk-ant-...

# Any OpenAI-compatible server (self-hosted or a gateway)
DEFAULT_CHAT_MODEL=openai/qwen3-32b
DEFAULT_LLM_API_BASE=http://localhost:8000/v1
OPENAI_API_KEY=anything-the-server-accepts
```

Organisations can also bring their own keys (Settings → API keys) or point an agent at their own
OpenAI-compatible endpoint in the agent's **Model** step. Custom endpoints on private networks are
blocked unless `ALLOW_PRIVATE_ENDPOINTS=true` (SSRF protection).

The gateway copes with common OpenAI-compatible server quirks. If a server returns an empty body for
JSON mode, the call is retried without it. JSON objects that come back truncated are repaired. An empty
completion is treated as a provider error, not as an answer.

## Embeddings

| Variable | Default | Purpose |
|---|---|---|
| `DEFAULT_EMBEDDING_MODEL` | `fastembed/BAAI/bge-small-en-v1.5` | Model for new knowledge bases. Fixed per knowledge base once created |
| `EMBEDDING_API_BASE` | – | OpenAI-compatible embeddings endpoint for `openai/...` embedding models |

Three options:

1. **In-process** (default): `fastembed/BAAI/bge-small-en-v1.5`, a small ONNX model that runs inside the
   API and worker processes. No extra service.
2. **Self-hosted server** (recommended): the `embeddings` service in Docker Compose runs Hugging Face
   [text-embeddings-inference](https://github.com/huggingface/text-embeddings-inference) with
   `BAAI/bge-base-en-v1.5` (768 dimensions). It starts with `make infra-up`. Set
   ```bash
   DEFAULT_EMBEDDING_MODEL=openai/BAAI/bge-base-en-v1.5
   EMBEDDING_API_BASE=http://localhost:8082/v1   # http://embeddings:80/v1 inside compose
   ```
   To serve a different model, set `EMBEDDING_MODEL_ID` for the container and add the model's
   dimension to `apps/api/app/llm/catalog.yaml`.
3. **Hosted**: `text-embedding-3-small` with `OPENAI_API_KEY`.

Sparse BM25 vectors for hybrid search are always computed in-process.

## Services

| Variable | Default (local) |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://agentdesk:agentdesk@localhost:5433/agentdesk` |
| `REDIS_URL` | `redis://localhost:6379/0` |
| `QDRANT_URL` / `QDRANT_API_KEY` | `http://localhost:6333` / – |
| `S3_ENDPOINT`, `S3_PUBLIC_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | MinIO on `localhost:9000` |
| `TAVILY_API_KEY` | – (web search tool) |
| `SMTP_HOST` / `SMTP_PORT`, `RESEND_API_KEY`, `EMAIL_FROM` | Mailpit on `localhost:1025` |

## Security (required outside local)

| Variable | Notes |
|---|---|
| `APP_ENV` | `local`, `test`, `staging` or `production` |
| `JWT_SECRET` | 32+ random characters |
| `CSRF_SECRET` | random string |
| `ENCRYPTION_MASTER_KEYS` | Comma-separated base64 32-byte keys, first one active. Generate with `python -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())"`. See [runbooks/rotate-encryption-key.md](runbooks/rotate-encryption-key.md) |
| `ALLOW_PRIVATE_ENDPOINTS` | `false` in production |
| `EXPOSE_DOCS` | Serve OpenAPI docs at `/docs` |

## Observability

`LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `LANGSMITH_TRACING` (LLM traces), `SENTRY_DSN`
(errors), `LOG_LEVEL`. Prometheus metrics are served at `/metrics`.

## Limits

`MAX_UPLOAD_MB` (25), `ORG_STORAGE_LIMIT_MB` (500), `RUN_TIMEOUT_SECONDS` (90).
