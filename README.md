# AgentDesk

No-code custom AI agent builder for companies, deployed on WhatsApp. An organisation picks a template (HR Onboarding, HR Analytics, School Parent Helpdesk) or starts blank, uploads documents to a Knowledge Base, connects a read-only PostgreSQL database or spreadsheet, tests everything in a Playground with a full debug panel, publishes a version and deploys it to a WhatsApp Cloud API number.

Built from the PRD in [docs/PRD.md](docs/PRD.md). Architecture in [docs/architecture.md](docs/architecture.md).

## 5-minute quick start

Prerequisites: Docker Desktop, [uv](https://docs.astral.sh/uv/), Node 22 + pnpm (`corepack enable`).

```bash
make install     # backend venv (Python 3.12 via uv) + frontend deps
make dev         # Postgres, Redis, Qdrant, MinIO, Mailpit, hr-demo-db in Docker; API :8000, webhook :8001, worker, web :3000 natively
make seed        # demo orgs, 3 agents, ingested documents, reviewed HR dataset, sample conversations
```

Then open http://localhost:3000 and sign in as `demo@agentdesk.local` / `Demo-password-123!` (superadmin: `admin@agentdesk.local` / `Admin-password-123!`). Verification emails land in Mailpit at http://localhost:8025.

`make smoke` runs an end-to-end check (signup → verify via Mailpit → agent → upload → ingestion → retrieval → streamed Playground answer → publish → analytics). `make test` runs the backend unit and integration suites.

### Models and keys

Out of the box the platform default model is `fake/chat`, a deterministic demo model that needs no API key: every flow works (routing, retrieval, SQL, guardrails, WhatsApp), but answers are canned. For real answers set `DEFAULT_CHAT_MODEL` in `.env` to e.g. `openai/gpt-5-mini` or `anthropic/claude-sonnet-5` and the matching `*_API_KEY`, or add a key under Settings → API keys and pick it in an agent's Model step. Embeddings default to a local ONNX model (`fastembed/BAAI/bge-small-en-v1.5`, no key); switch to `text-embedding-3-small` with `OPENAI_API_KEY` if preferred (new knowledge bases only).

### WhatsApp locally

Expose the webhook service: `cloudflared tunnel --url http://localhost:8001` (or ngrok), set `WEBHOOK_BASE_URL` to the public URL, restart the API, then follow the Deploy page in the app. Test numbers only deliver to recipients allowed in Meta.

## Repository layout

```
apps/api      FastAPI API, webhook service and ARQ worker (Python 3.12, LangGraph, LiteLLM, Qdrant, DuckDB, sqlglot)
apps/web      Next.js 15 app (TypeScript, Tailwind 4, TanStack Query, Recharts)
infra         docker compose, Dockerfiles, Caddyfile, backup/restore/seed scripts
demo          generated demo documents and hr_demo.sql
docs          PRD, architecture, ADRs, runbooks, help pages
```

## Make targets

| Target | What |
|---|---|
| `make dev` / `make infra-up` / `make infra-down` | run everything / only the Docker services |
| `make migrate` / `make migration m="..."` | apply / autogenerate Alembic migrations |
| `make seed` / `make demo-docs` | demo data / regenerate demo PDFs |
| `make test` / `make test-unit` / `make smoke` / `make e2e` / `make evals` | test suites |
| `make lint` / `make format` | ruff, mypy, eslint, tsc, prettier |
| `make prod-up` | production compose profile (Caddy TLS, images built from `infra/docker`) |
| `make backup` / `make restore f=...` | app DB dump to S3 / restore |

## Production

`docker compose -f infra/compose/docker-compose.yml --profile prod up -d --build` with a `.env` that sets `APP_ENV=production`, `JWT_SECRET`, `CSRF_SECRET`, `ENCRYPTION_MASTER_KEYS`, real S3/Postgres credentials, `APP_DOMAIN`/`HOOKS_DOMAIN` for Caddy and your model keys. Settings validation fails fast if a production secret is left at its default. See [docs/runbooks](docs/runbooks) for operations.

## Security notes

Tenant isolation via org-scoped repositories (cross-tenant access returns 404), Argon2id passwords, rotating refresh tokens with reuse detection, CSRF double-submit, envelope-encrypted customer secrets, SSRF guard for customer hosts/endpoints, AST-validated read-only SQL with hidden-column enforcement (100 test cases), HMAC-verified WhatsApp webhooks with idempotent processing, rate limits with `RateLimit-*` headers, audit log, security headers via Caddy.
