"""Qdrant access: one collection per embedding model, tenant filters always on org_id/kb_id (PRD §10.4, §18.1)."""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qm

from app.core.config import settings
from app.rag.embeddings import SparseVec

_client: AsyncQdrantClient | None = None
DENSE = "dense"
SPARSE = "bm25"


def get_qdrant() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=30)
    return _client


def collection_name(embedding_model: str, dim: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", embedding_model.lower()).strip("_")[-40:]
    return f"chunks_{slug}_{dim}"


async def ensure_collection(name: str, dim: int) -> None:
    c = get_qdrant()
    if not await c.collection_exists(name):
        await c.create_collection(
            collection_name=name,
            vectors_config={DENSE: qm.VectorParams(size=dim, distance=qm.Distance.COSINE)},
            sparse_vectors_config={SPARSE: qm.SparseVectorParams(modifier=qm.Modifier.IDF)},
        )
        await c.create_payload_index(
            name, "org_id", qm.KeywordIndexParams(type=qm.KeywordIndexType.KEYWORD, is_tenant=True)
        )
        await c.create_payload_index(name, "kb_id", qm.PayloadSchemaType.KEYWORD)
        await c.create_payload_index(name, "document_id", qm.PayloadSchemaType.KEYWORD)


@dataclass
class ChunkPoint:
    id: str
    dense: list[float]
    sparse: SparseVec
    payload: dict[str, Any]


async def upsert_points(collection: str, points: list[ChunkPoint]) -> None:
    if not points:
        return
    c = get_qdrant()
    for i in range(0, len(points), 128):
        batch = points[i : i + 128]
        await c.upsert(
            collection_name=collection,
            points=[
                qm.PointStruct(
                    id=p.id,
                    vector={
                        DENSE: p.dense,
                        SPARSE: qm.SparseVector(indices=p.sparse.indices, values=p.sparse.values),
                    },
                    payload=p.payload,
                )
                for p in batch
            ],
            wait=True,
        )


def _tenant_filter(
    org_id: str,
    kb_ids: list[str] | None = None,
    document_id: str | None = None,
    extra: list[qm.FieldCondition] | None = None,
) -> qm.Filter:
    must: list[qm.Condition] = [qm.FieldCondition(key="org_id", match=qm.MatchValue(value=org_id))]
    if kb_ids:
        must.append(qm.FieldCondition(key="kb_id", match=qm.MatchAny(any=kb_ids)))
    if document_id:
        must.append(qm.FieldCondition(key="document_id", match=qm.MatchValue(value=document_id)))
    if extra:
        must.extend(extra)
    return qm.Filter(must=must)


async def delete_document_points(
    collection: str, org_id: str, document_id: str, keep_version: int | None = None
) -> None:
    c = get_qdrant()
    if not await c.collection_exists(collection):
        return
    extra = (
        [qm.FieldCondition(key="document_version", range=qm.Range(lt=keep_version))] if keep_version else None
    )
    await c.delete(
        collection_name=collection,
        points_selector=qm.FilterSelector(
            filter=_tenant_filter(org_id, document_id=document_id, extra=extra)
        ),
        wait=True,
    )


async def delete_kb_points(collection: str, org_id: str, kb_id: str) -> None:
    c = get_qdrant()
    if not await c.collection_exists(collection):
        return
    await c.delete(
        collection_name=collection,
        points_selector=qm.FilterSelector(filter=_tenant_filter(org_id, [kb_id])),
        wait=True,
    )


async def scroll_document(
    collection: str, org_id: str, document_id: str, limit: int = 500
) -> list[dict[str, Any]]:
    c = get_qdrant()
    if not await c.collection_exists(collection):
        return []
    points, _ = await c.scroll(
        collection_name=collection,
        scroll_filter=_tenant_filter(org_id, document_id=document_id),
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )
    return sorted(
        [dict(p.payload or {}, id=str(p.id)) for p in points], key=lambda p: p.get("chunk_index", 0)
    )


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


@dataclass
class Hit:
    id: str
    payload: dict[str, Any]
    score: float  # dense cosine similarity
    fused_score: float


async def search(
    collection: str,
    org_id: str,
    kb_ids: list[str],
    dense: list[float],
    sparse: SparseVec,
    *,
    limit: int,
    mode: str = "hybrid",
    threshold: float = 0.3,
    candidates: int | None = None,
) -> list[Hit]:
    """Hybrid (RRF) / dense / sparse retrieval. Tenant filter is applied in exactly one place: here (PRD §10.4)."""
    c = get_qdrant()
    if not await c.collection_exists(collection):
        return []
    flt = _tenant_filter(org_id, kb_ids)
    k = candidates or limit
    if mode == "dense":
        res = await c.query_points(
            collection,
            query=dense,
            using=DENSE,
            query_filter=flt,
            limit=k,
            with_payload=True,
            with_vectors=[DENSE],
            score_threshold=threshold,
        )
    elif mode == "sparse":
        res = await c.query_points(
            collection,
            query=qm.SparseVector(indices=sparse.indices, values=sparse.values),
            using=SPARSE,
            query_filter=flt,
            limit=k,
            with_payload=True,
            with_vectors=[DENSE],
        )
    else:
        res = await c.query_points(
            collection,
            prefetch=[
                qm.Prefetch(query=dense, using=DENSE, filter=flt, limit=k * 3),
                qm.Prefetch(
                    query=qm.SparseVector(indices=sparse.indices, values=sparse.values),
                    using=SPARSE,
                    filter=flt,
                    limit=k * 3,
                ),
            ],
            query=qm.FusionQuery(fusion=qm.Fusion.RRF),
            query_filter=flt,
            limit=k,
            with_payload=True,
            with_vectors=[DENSE],
        )
    hits: list[Hit] = []
    for p in res.points:
        vec = p.vector.get(DENSE) if isinstance(p.vector, dict) else None
        cos = _cosine(dense, vec) if vec else float(p.score)
        if mode != "sparse" and cos < threshold:
            continue  # dense-similarity threshold decides "relevant" (PRD §10.4)
        hits.append(
            Hit(id=str(p.id), payload=dict(p.payload or {}), score=round(cos, 4), fused_score=float(p.score))
        )
    return hits


def point_id(document_id: str, version: int, chunk_index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{document_id}/{version}/{chunk_index}"))
