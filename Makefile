.DEFAULT_GOAL := help
COMPOSE := docker compose -f infra/compose/docker-compose.yml
UV := uv
API_DIR := apps/api
WEB_DIR := apps/web

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

env: ## Create local env files from the example
	@test -f .env || cp .env.example .env
	@test -f $(API_DIR)/.env || cp .env.example $(API_DIR)/.env
	@test -f $(WEB_DIR)/.env.local || printf 'NEXT_PUBLIC_API_URL=http://localhost:8000\n' > $(WEB_DIR)/.env.local

install: env ## Install backend (uv) and frontend (pnpm) dependencies
	cd $(API_DIR) && $(UV) sync --python 3.12
	cd $(WEB_DIR) && pnpm install

infra-up: ## Start Postgres, Redis, Qdrant, MinIO, Mailpit, hr-demo-db
	$(COMPOSE) --profile dev up -d
	@$(COMPOSE) --profile dev ps --format '{{.Name}} {{.Status}}'

infra-down: ## Stop infra containers (keeps volumes)
	$(COMPOSE) --profile dev down

infra-reset: ## Stop infra and delete volumes (DESTROYS LOCAL DATA)
	$(COMPOSE) --profile dev down -v

migrate: ## Apply database migrations
	cd $(API_DIR) && uv run alembic upgrade head

migration: ## Autogenerate a migration: make migration m="add x"
	cd $(API_DIR) && uv run alembic revision --autogenerate -m "$(m)"

api: ## Run the API with hot reload
	cd $(API_DIR) && uv run uvicorn app.main:app --reload --port 8000

webhook: ## Run the webhook service
	cd $(API_DIR) && uv run uvicorn app.webhook_main:app --reload --port 8001

worker: ## Run the ARQ worker
	cd $(API_DIR) && uv run arq app.worker.WorkerSettings

web: ## Run the Next.js dev server
	cd $(WEB_DIR) && pnpm dev

dev: infra-up migrate ## Boot everything for local development (infra in Docker, apps natively)
	@echo "Starting api (8000), webhook (8001), worker and web (3000). Ctrl+C stops all."
	@trap 'kill 0' INT TERM; \
	  (cd $(API_DIR) && uv run uvicorn app.main:app --reload --port 8000) & \
	  (cd $(API_DIR) && uv run uvicorn app.webhook_main:app --reload --port 8001) & \
	  (cd $(API_DIR) && uv run arq app.worker.WorkerSettings) & \
	  (cd $(WEB_DIR) && pnpm dev) & \
	  wait

seed: ## Load demo orgs, agents, documents, HR dataset and conversations
	cd $(API_DIR) && uv run python ../../infra/scripts/seed_demo.py

demo-docs: ## Regenerate the demo PDFs/markdown into demo/
	cd $(API_DIR) && uv run python ../../infra/scripts/generate_demo_docs.py

test: ## Backend unit + integration tests
	cd $(API_DIR) && uv run python -m pytest -q

test-unit: ## Backend unit tests only
	cd $(API_DIR) && uv run python -m pytest -q tests/unit

evals: ## Run template eval sets against the configured models
	cd $(API_DIR) && AGENTDESK_LIVE=1 uv run python -m pytest -q tests/evals -s -p no:cacheprovider

smoke: ## End-to-end smoke test against a running stack
	bash infra/scripts/smoke.sh

lint: ## Lint + typecheck backend and frontend
	cd $(API_DIR) && uv run ruff check . && uv run ruff format --check . && uv run mypy
	cd $(WEB_DIR) && pnpm lint && pnpm typecheck

format: ## Auto-format
	cd $(API_DIR) && uv run ruff check --fix . && uv run ruff format .
	cd $(WEB_DIR) && pnpm prettier --write .

e2e: ## Playwright E2E (needs web on :3000 and api on :8000)
	cd $(WEB_DIR) && pnpm test:e2e

gen-api-types: ## Regenerate frontend API types from the running API's OpenAPI
	cd $(WEB_DIR) && pnpm gen:api

prod-up: ## Build and run the production compose profile (Caddy TLS)
	$(COMPOSE) --profile prod up -d --build

backup: ## Dump the app DB to S3/MinIO
	bash infra/scripts/backup.sh

restore: ## Restore the app DB from a dump: make restore f=backups/2026-09-16.sql.gz
	bash infra/scripts/restore.sh "$(f)"

.PHONY: help env install infra-up infra-down infra-reset migrate migration api webhook worker web dev seed demo-docs test test-unit evals smoke lint format e2e gen-api-types prod-up backup restore
