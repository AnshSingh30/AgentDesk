# syntax=docker/dockerfile:1.7
# Worker image: same code, plus system libs for document parsing and a pre-downloaded embedding model.
FROM python:3.12-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
WORKDIR /app
COPY apps/api/pyproject.toml apps/api/uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project || uv sync --no-dev --no-install-project
COPY apps/api/ ./

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH" APP_ENV=production FASTEMBED_CACHE_PATH=/app/.cache/fastembed
RUN groupadd -r app && useradd -r -g app -d /app app && apt-get update && apt-get install -y --no-install-recommends libmagic1 libxml2 libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
USER app
# pre-download the default local embedding + BM25 models so first ingestion is fast
RUN python -c "from fastembed import TextEmbedding, SparseTextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5'); SparseTextEmbedding('Qdrant/bm25')" || true
CMD ["arq", "app.worker.WorkerSettings"]
