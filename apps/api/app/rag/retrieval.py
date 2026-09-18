"""Hybrid retrieval across attached KBs with optional reranking (PRD §10.4)."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, dataclass
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KnowledgeBase
from app.rag import qdrant
from app.rag.embeddings import embed_query

log = structlog.get_logger()


@dataclass
class RetrievedChunk:
    chunk_id: str
    kb_id: str
    document_id: str
    filename: str
    page_start: int | None
    page_end: int | None
    section_title: str | None
    text: str
    score: float
    fused_score: float
    rerank_score: float | None = None

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["text"] = self.text[:2000]
        return d


async def retrieve(
    session: AsyncSession,
    org_id: uuid.UUID,
    kb_ids: list[uuid.UUID],
    query: str,
    *,
    top_k: int = 5,
    threshold: float = 0.3,
    mode: str = "hybrid",
    rerank: dict[str, Any] | None = None,
    rerank_api_key: str | None = None,
) -> list[RetrievedChunk]:
    if not kb_ids or not query.strip():
        return []
    kbs = (
        (
            await session.execute(
                select(KnowledgeBase).where(
                    KnowledgeBase.org_id == org_id,
                    KnowledgeBase.id.in_(kb_ids),
                    KnowledgeBase.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not kbs:
        return []
    rerank = rerank or {}
    candidates = int(rerank.get("candidates", 20)) if rerank.get("enabled") else top_k
    by_collection: dict[tuple[str, str], list[str]] = {}
    for kb in kbs:
        by_collection.setdefault((kb.qdrant_collection, kb.embedding_model), []).append(str(kb.id))
    results: list[RetrievedChunk] = []
    for (collection, model), ids in by_collection.items():
        dense, sparse = await embed_query(query, model)
        hits = await qdrant.search(
            collection,
            str(org_id),
            ids,
            dense,
            sparse,
            limit=top_k,
            mode=mode,
            threshold=threshold,
            candidates=candidates,
        )
        for h in hits:
            p = h.payload
            results.append(
                RetrievedChunk(
                    chunk_id=h.id,
                    kb_id=p.get("kb_id", ""),
                    document_id=p.get("document_id", ""),
                    filename=p.get("filename", ""),
                    page_start=p.get("page_start"),
                    page_end=p.get("page_end"),
                    section_title=p.get("section_title"),
                    text=p.get("text", ""),
                    score=h.score,
                    fused_score=h.fused_score,
                )
            )
    results.sort(key=lambda c: (c.fused_score, c.score), reverse=True)
    if rerank.get("enabled") and results:
        results = await _rerank(query, results, rerank.get("provider") or "local", rerank_api_key)
    return results[:top_k]


async def _rerank(
    query: str, chunks: list[RetrievedChunk], provider: str, api_key: str | None
) -> list[RetrievedChunk]:
    try:
        if provider == "cohere" and api_key:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(
                    "https://api.cohere.com/v2/rerank",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": "rerank-v3.5",
                        "query": query,
                        "documents": [c.text for c in chunks],
                        "top_n": len(chunks),
                    },
                )
                r.raise_for_status()
            for item in r.json().get("results", []):
                chunks[item["index"]].rerank_score = float(item["relevance_score"])
        else:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            def _run() -> list[float]:
                enc = TextCrossEncoder(model_name="Xenova/ms-marco-MiniLM-L-6-v2")
                return [float(s) for s in enc.rerank(query, [c.text for c in chunks])]

            for c, s in zip(chunks, await asyncio.to_thread(_run), strict=True):
                c.rerank_score = s
        chunks.sort(key=lambda c: c.rerank_score if c.rerank_score is not None else -1e9, reverse=True)
    except Exception as e:  # noqa: BLE001 - reranking is best-effort
        log.warning("rerank_failed", provider=provider, error=str(e))
    return chunks
