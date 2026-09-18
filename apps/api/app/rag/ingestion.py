"""Ingestion pipeline: S3 -> validate -> parse -> chunk -> embed (dense+sparse) -> Qdrant (PRD §10.1)."""

from __future__ import annotations

import hashlib
import time
import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select, update

from app.core.metrics import ingestion_jobs_total
from app.core.s3 import delete_object, get_bytes, sniff_matches
from app.db.models import Document, IngestionJob, KnowledgeBase
from app.db.session import session_scope
from app.rag import qdrant
from app.rag.chunking import chunk_elements
from app.rag.embeddings import embed_dense, embed_sparse
from app.rag.parsing import parse_bytes

log = structlog.get_logger()


async def _set_status(document_id: uuid.UUID, status: str, **fields: object) -> None:
    async with session_scope() as s:
        await s.execute(update(Document).where(Document.id == document_id).values(status=status, **fields))


async def refresh_kb_counts(kb_id: uuid.UUID) -> None:
    async with session_scope() as s:
        docs = (
            await s.execute(
                select(func.count(), func.coalesce(func.sum(Document.chunk_count), 0)).where(
                    Document.kb_id == kb_id, Document.deleted_at.is_(None), Document.status == "ready"
                )
            )
        ).one()
        await s.execute(
            update(KnowledgeBase)
            .where(KnowledgeBase.id == kb_id)
            .values(doc_count=docs[0], chunk_count=int(docs[1]))
        )


async def ingest_document(document_id: str, job_id: str | None = None) -> dict[str, object]:
    doc_uuid = uuid.UUID(document_id)
    started = time.perf_counter()
    async with session_scope() as s:
        doc = await s.get(Document, doc_uuid)
        if doc is None or doc.deleted_at is not None:
            return {"skipped": "document missing"}
        kb = await s.get(KnowledgeBase, doc.kb_id)
        if kb is None:
            return {"skipped": "kb missing"}
        org_id, kb_id, filename, mime, key, version = (
            str(doc.org_id),
            str(kb.id),
            doc.filename,
            doc.mime_type,
            doc.storage_key,
            doc.version,
        )
        collection, embedding_model, chunk_cfg, strategy = (
            kb.qdrant_collection,
            kb.embedding_model,
            dict(kb.chunk_config or {}),
            doc.parse_strategy,
        )
        if job_id:
            job = await s.get(IngestionJob, uuid.UUID(job_id))
            if job:
                job.status = "running"
                job.started_at = datetime.now(UTC)
                job.attempts += 1
    metrics: dict[str, object] = {}
    try:
        await _set_status(doc_uuid, "parsing", error=None)
        data = await get_bytes(key)
        if not sniff_matches(mime, data[:1024]):
            raise ValueError("File content does not match its declared type")
        sha = hashlib.sha256(data).hexdigest()
        parsed = parse_bytes(data, mime, chunk_cfg.get("parsing_strategy", strategy))
        if not parsed.elements:
            raise ValueError("No text could be extracted from this file")
        await _set_status(
            doc_uuid, "chunking", sha256=sha, page_count=parsed.page_count, size_bytes=len(data)
        )
        chunks = chunk_elements(parsed.elements, chunk_cfg)
        metrics["elements"] = len(parsed.elements)
        metrics["chunks"] = len(chunks)
        await _set_status(doc_uuid, "embedding")
        contextual = bool(chunk_cfg.get("contextual_headers", True))
        texts = [c.embed_text(filename, contextual) for c in chunks]
        dense = await embed_dense(texts, embedding_model)
        sparse = await embed_sparse(texts)
        now = datetime.now(UTC).isoformat()
        points = [
            qdrant.ChunkPoint(
                id=qdrant.point_id(document_id, version, c.chunk_index),
                dense=d,
                sparse=sp,
                payload={
                    "org_id": org_id,
                    "kb_id": kb_id,
                    "document_id": document_id,
                    "document_version": version,
                    "filename": filename,
                    "page_start": c.page_start,
                    "page_end": c.page_end,
                    "section_title": c.section_title,
                    "element_types": c.element_types,
                    "text": c.text,
                    "chunk_index": c.chunk_index,
                    "created_at": now,
                },
            )
            for c, d, sp in zip(chunks, dense, sparse, strict=True)
        ]
        await qdrant.ensure_collection(collection, len(dense[0]) if dense else 1)
        await qdrant.upsert_points(collection, points)
        await qdrant.delete_document_points(collection, org_id, document_id, keep_version=version)
        await _set_status(doc_uuid, "ready", chunk_count=len(chunks), ingested_at=datetime.now(UTC))
        await _retire_previous_versions(doc_uuid, uuid.UUID(kb_id), filename, collection, org_id)
        await refresh_kb_counts(uuid.UUID(kb_id))
        ingestion_jobs_total.labels("ready").inc()
        metrics["duration_ms"] = int((time.perf_counter() - started) * 1000)
        await _finish_job(job_id, "done", None, metrics)
        log.info("ingestion_done", document_id=document_id, **metrics)
        return metrics
    except Exception as e:  # noqa: BLE001
        ingestion_jobs_total.labels("failed").inc()
        msg = f"{type(e).__name__}: {str(e)[:500]}"
        log.warning("ingestion_failed", document_id=document_id, error=msg)
        await _set_status(doc_uuid, "failed", error=msg)
        await _finish_job(job_id, "failed", msg, metrics)
        raise


async def _retire_previous_versions(
    doc_id: uuid.UUID, kb_id: uuid.UUID, filename: str, collection: str, org_id: str
) -> None:
    """Re-upload with the same name = new version replaces old (KB-7)."""
    async with session_scope() as s:
        olds = (
            (
                await s.execute(
                    select(Document).where(
                        Document.kb_id == kb_id,
                        Document.filename == filename,
                        Document.id != doc_id,
                        Document.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for old in olds:
            old.deleted_at = datetime.now(UTC)
            await qdrant.delete_document_points(collection, org_id, str(old.id))
            try:
                await delete_object(old.storage_key)
            except Exception as e:  # noqa: BLE001
                log.warning("old_version_delete_failed", key=old.storage_key, error=str(e))


async def _finish_job(job_id: str | None, status: str, error: str | None, metrics: dict[str, object]) -> None:
    if not job_id:
        return
    async with session_scope() as s:
        job = await s.get(IngestionJob, uuid.UUID(job_id))
        if job:
            job.status = status
            job.error = error
            job.finished_at = datetime.now(UTC)
            job.metrics = metrics


async def delete_document_fully(document_id: str) -> None:
    async with session_scope() as s:
        doc = await s.get(Document, uuid.UUID(document_id))
        if doc is None:
            return
        kb = await s.get(KnowledgeBase, doc.kb_id)
        collection, org_id, key, kb_id = (
            (kb.qdrant_collection if kb else None),
            str(doc.org_id),
            doc.storage_key,
            doc.kb_id,
        )
    if collection:
        await qdrant.delete_document_points(collection, org_id, document_id)
    try:
        await delete_object(key)
    except Exception as e:  # noqa: BLE001
        log.warning("s3_delete_failed", key=key, error=str(e))
    await refresh_kb_counts(kb_id)


async def reindex_kb(kb_id: str) -> dict[str, object]:
    async with session_scope() as s:
        docs = (
            (
                await s.execute(
                    select(Document.id).where(
                        Document.kb_id == uuid.UUID(kb_id), Document.deleted_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
    results = {"documents": len(docs), "failed": 0}
    for d in docs:
        try:
            await ingest_document(str(d))
        except Exception:  # noqa: BLE001
            results["failed"] += 1
    return results
