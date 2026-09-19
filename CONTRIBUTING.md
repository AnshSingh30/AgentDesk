# Contributing to AgentDesk

Thanks for helping. This guide covers local setup, how the code is organised, and what a pull request
needs before it can be merged.

## Local setup

Prerequisites: Docker (Desktop or Engine), [uv](https://docs.astral.sh/uv/), Node 22+ with pnpm
(`corepack enable`), and `make` (on Windows use Git Bash or WSL, or run the commands behind each target
from the [Makefile](Makefile) directly).

```bash
make install   # Python 3.12 venv via uv + web dependencies
make dev       # Postgres, Redis, Qdrant, MinIO, Mailpit, embeddings, hr-demo-db + api, webhook, worker, web
make seed      # demo orgs, agents, documents and the HR dataset
```

The app runs at http://localhost:3000 and the API at http://localhost:8000 (OpenAPI docs at `/docs`).
Without a model key everything runs on the deterministic `fake/chat` demo model. See
[docs/configuration.md](docs/configuration.md) to point it at a real model.

## Repository layout

| Path | What lives there |
|---|---|
| `apps/api/app` | FastAPI app, webhook service and ARQ worker. One package per domain (`agents`, `knowledge`, `datasets`, `deployments`, …) |
| `apps/api/app/runtime` | The LangGraph agent: state, nodes, prompts, formatter |
| `apps/api/app/sql` | Introspection, schema drafting, the SQL validator and the text-to-SQL subgraph |
| `apps/api/tests` | `unit/`, `integration/` (real Postgres/Redis/Qdrant/MinIO), `evals/` (live model evals) |
| `apps/web` | Next.js 15 app (App Router, TanStack Query, Tailwind) |
| `infra` | Docker Compose, Dockerfiles, Caddy, seed/backup scripts |
| `docs` | Public documentation, ADRs and runbooks |

## Before you open a pull request

```bash
make lint       # ruff, ruff format --check, mypy, eslint, tsc
make test       # backend unit + integration tests (needs `make infra-up`)
```

- **Tests.** New behaviour comes with a test. Bug fixes come with a test that failed before the fix.
  Integration tests use the real services from `docker compose`; unit and integration runs always use the
  demo model, so they are deterministic.
- **Evals.** If you change prompts, routing, retrieval or the SQL agent, run `make seed && make evals`
  against a real model and include the before/after table from `apps/api/tests/evals/reports/SUMMARY.md`
  in the PR. See [docs/evaluation.md](docs/evaluation.md).
- **Tenant scoping.** Every query on tenant data filters by `org_id` (use `app/db/repo.py::get_scoped`).
  Cross-tenant access must return 404, not 403.
- **Migrations.** Model changes need an Alembic migration: `make migration m="short description"`.
  Review the generated file; `uv run alembic check` must report no drift.
- **Secrets.** Never log or return secrets. Customer secrets are stored with `app/core/crypto.py`.
- **Commits.** Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`,
  `docs:`, `refactor:`, `test:`, `chore:`).

## Branches

`main` is the release branch. Day-to-day work lands on `staging` through pull requests. Branch from
`staging` and target it with your PR.

## Reporting bugs and proposing features

Use the issue templates. For anything security-related, follow [SECURITY.md](SECURITY.md) instead of
opening a public issue.

## License of contributions

AgentDesk is distributed under the [Business Source License 1.1](LICENSE). By contributing you agree
that your contribution is licensed under the same terms, including the conversion to the Apache License
2.0 on the Change Date, and that the maintainers may offer the project under other licensing
arrangements (for example, a commercial license for production use).
