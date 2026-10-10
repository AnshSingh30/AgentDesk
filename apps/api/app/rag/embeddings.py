"""Dense + sparse embeddings: local FastEmbed (no key) or provider models via LiteLLM (PRD §10.5)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any

import litellm
import structlog

from app.core.config import settings
from app.core.redis import get_redis
from app.llm.catalog import embedding_dim as catalog_dim
from app.llm.gateway import LLMError, map_exception

log = structlog.get_logger()
FASTEMBED_PREFIX = "fastembed/"
BM25_MODEL = "Qdrant/bm25"
_dense_models: dict[str, Any] = {}
_sparse_model: Any = None
_lock = asyncio.Lock()


@dataclass
class SparseVec:
    indices: list[int]
    values: list[float]


def is_local(model: str) -> bool:
    return model.startswith(FASTEMBED_PREFIX)


def _load_dense(name: str) -> Any:
    from fastembed import TextEmbedding

    if name not in _dense_models:
        _dense_models[name] = TextEmbedding(model_name=name)
    return _dense_models[name]


def _load_sparse() -> Any:
    global _sparse_model
    from fastembed import SparseTextEmbedding

    if _sparse_model is None:
        _sparse_model = SparseTextEmbedding(model_name=BM25_MODEL)
    return _sparse_model


async def warm_up(model: str | None = None) -> None:
    model = model or settings.default_embedding_model
    if is_local(model):
        await asyncio.to_thread(_load_dense, model[len(FASTEMBED_PREFIX) :])
    await asyncio.to_thread(_load_sparse)


def dimension(model: str) -> int:
    dim = catalog_dim(model)
    if dim:
        return dim
    if is_local(model):
        from fastembed import TextEmbedding

        name = model[len(FASTEMBED_PREFIX) :]
        for m in TextEmbedding.list_supported_models():
            if m["model"] == name:
                return int(m["dim"])
    raise ValueError(f"Unknown embedding dimension for {model}; add it to catalog.yaml")


def _cache_key(model: str, text: str) -> str:
    return "emb:" + hashlib.sha256(f"{model}\x00{text}".encode()).hexdigest()


async def embed_dense(
    texts: list[str], model: str | None = None, *, api_key: str | None = None
) -> list[list[float]]:
    """Batch-embed with Redis cache (7 days) so re-uploads don't re-embed."""
    model = model or settings.default_embedding_model
    if not texts:
        return []
    r = get_redis()
    keys = [_cache_key(model, t) for t in texts]
    cached = await r.mget(keys)
    out: list[list[float] | None] = [json.loads(c) if c else None for c in cached]
    todo = [i for i, v in enumerate(out) if v is None]
    if todo:
        batch = [texts[i] for i in todo]
        vectors = await _embed_uncached(batch, model, api_key)
        async with r.pipeline(transaction=False) as pipe:
            for i, vec in zip(todo, vectors, strict=True):
                out[i] = vec
                pipe.setex(keys[i], 7 * 24 * 3600, json.dumps(vec))
            await pipe.execute()
    return [v for v in out if v is not None]


async def _embed_uncached(texts: list[str], model: str, api_key: str | None) -> list[list[float]]:
    if is_local(model):
        name = model[len(FASTEMBED_PREFIX) :]

        def _run() -> list[list[float]]:
            m = _load_dense(name)
            return [v.tolist() for v in m.embed(texts, batch_size=64)]

        return await asyncio.to_thread(_run)
    vectors: list[list[float]] = []
    for i in range(0, len(texts), 64):
        chunk = texts[i : i + 64]
        for attempt in range(3):
            try:
                resp = await litellm.aembedding(
                    model=model,
                    input=chunk,
                    api_key=api_key or settings.openai_api_key or "sk-no-key",
                    api_base=settings.embedding_api_base if model.startswith("openai/") else None,
                    timeout=60,
                )
                vectors.extend([d["embedding"] for d in resp.data])
                break
            except Exception as e:  # noqa: BLE001
                err = map_exception(e)
                if attempt == 2 or not err.retryable:
                    raise err from e
                await asyncio.sleep(1.5 * (attempt + 1))
    return vectors


async def embed_sparse(texts: list[str]) -> list[SparseVec]:
    if not texts:
        return []

    def _run() -> list[SparseVec]:
        m = _load_sparse()
        return [
            SparseVec(indices=[int(i) for i in v.indices], values=[float(x) for x in v.values])
            for v in m.embed(texts)
        ]

    return await asyncio.to_thread(_run)


async def embed_query(query: str, model: str, *, api_key: str | None = None) -> tuple[list[float], SparseVec]:
    dense = (await embed_dense([query], model, api_key=api_key))[0]

    def _sparse() -> SparseVec:
        v = next(iter(_load_sparse().query_embed(query)))
        return SparseVec(indices=[int(i) for i in v.indices], values=[float(x) for x in v.values])

    sparse = await asyncio.to_thread(_sparse)
    return dense, sparse


__all__ = [
    "LLMError",
    "SparseVec",
    "dimension",
    "embed_dense",
    "embed_query",
    "embed_sparse",
    "is_local",
    "warm_up",
]
