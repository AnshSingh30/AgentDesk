"""Knowledge bases & documents: presigned uploads, ingestion, chunks, test retrieval (PRD §6.5, §16.5)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import OrgContext, get_db, require_membership
from app.core.errors import Problem
from app.core.ids import uuid7
from app.core.rate_limit import client_ip, rate_limited
from app.core.s3 import (
    get_bytes,
    head_object,
    org_storage_bytes,
    presign_get,
    presign_put,
    sanitize_filename,
    sniff_matches,
)
from app.db.models import Document, IngestionJob, KnowledgeBase
from app.db.repo import audit, get_scoped, scoped
from app.jobs.queue import enqueue
from app.rag import qdrant
from app.rag.chunking import DEFAULT_CHUNK_CONFIG
from app.rag.embeddings import dimension
from app.rag.retrieval import retrieve

router = APIRouter(tags=["knowledge"])
ALLOWED_MIMES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
    "text/markdown": ".md",
}
EXT_TO_MIME = {v: k for k, v in ALLOWED_MIMES.items()}


class KBCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    embedding_model: str | None = None
    chunk_config: dict[str, Any] | None = None


class KBPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    chunk_config: dict[str, Any] | None = None


class KBOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    embedding_model: str
    embedding_dim: int
    chunk_config: dict[str, Any]
    status: str
    doc_count: int
    chunk_count: int
    created_at: datetime
    updated_at: datetime


class DocumentOut(BaseModel):
    id: uuid.UUID
    kb_id: uuid.UUID
    filename: str
    mime_type: str
    size_bytes: int
    version: int
    status: str
    error: str | None
    page_count: int | None
    chunk_count: int
    sha256: str | None
    created_at: datetime
    ingested_at: datetime | None


class UploadRequest(BaseModel):
    files: list[dict[str, Any]] = Field(min_length=1, max_length=50)


class ConfirmUpload(BaseModel):
    uploads: list[dict[str, Any]] = Field(min_length=1, max_length=50)


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(5, ge=1, le=20)
    threshold: float = Field(0.3, ge=0, le=1)
    mode: str = "hybrid"


def kb_out(kb: KnowledgeBase) -> KBOut:
    return KBOut(
        id=kb.id,
        name=kb.name,
        description=kb.description,
        embedding_model=kb.embedding_model,
        embedding_dim=kb.embedding_dim,
        chunk_config=kb.chunk_config or {},
        status=kb.status,
        doc_count=kb.doc_count,
        chunk_count=kb.chunk_count,
        created_at=kb.created_at,
        updated_at=kb.updated_at,
    )


def doc_out(d: Document) -> DocumentOut:
    return DocumentOut(
        id=d.id,
        kb_id=d.kb_id,
        filename=d.filename,
        mime_type=d.mime_type,
        size_bytes=d.size_bytes,
        version=d.version,
        status=d.status,
        error=d.error,
        page_count=d.page_count,
        chunk_count=d.chunk_count,
        sha256=d.sha256,
        created_at=d.created_at,
        ingested_at=d.ingested_at,
    )


def _validated_chunk_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    cfg = {**DEFAULT_CHUNK_CONFIG, **(raw or {})}
    cfg["chunk_size"] = max(300, min(int(cfg["chunk_size"]), 4000))
    cfg["chunk_overlap"] = max(0, min(int(cfg["chunk_overlap"]), 1000))
    cfg["new_after_n_chars"] = max(
        100, min(int(cfg.get("new_after_n_chars", cfg["chunk_size"] * 0.8)), cfg["chunk_size"])
    )
    cfg["combine_under"] = max(0, min(int(cfg["combine_under"]), cfg["chunk_size"]))
    if cfg["parsing_strategy"] not in ("auto", "hi_res", "ocr_only"):
        cfg["parsing_strategy"] = "auto"
    cfg["contextual_headers"] = bool(cfg["contextual_headers"])
    return cfg


@router.post("/orgs/{org_id}/knowledge-bases", status_code=status.HTTP_201_CREATED)
async def create_kb(
    body: KBCreate,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> KBOut:
    model = body.embedding_model or settings.default_embedding_model
    try:
        dim = dimension(model)
    except ValueError as e:
        raise Problem(422, str(e)) from e
    collection = qdrant.collection_name(model, dim)
    kb = KnowledgeBase(
        org_id=ctx.org_id,
        name=body.name.strip(),
        description=body.description,
        embedding_model=model,
        embedding_dim=dim,
        chunk_config=_validated_chunk_config(body.chunk_config),
        qdrant_collection=collection,
    )
    session.add(kb)
    await session.flush()
    await qdrant.ensure_collection(collection, dim)
    return kb_out(kb)


@router.get("/orgs/{org_id}/knowledge-bases")
async def list_kbs(
    ctx: OrgContext = Depends(require_membership("viewer")), session: AsyncSession = Depends(get_db)
) -> list[KBOut]:
    rows = (
        (await session.execute(scoped(KnowledgeBase, ctx.org_id).order_by(KnowledgeBase.created_at.desc())))
        .scalars()
        .all()
    )
    return [kb_out(k) for k in rows]


@router.get("/orgs/{org_id}/knowledge-bases/{kb_id}")
async def get_kb(
    kb_id: str,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> KBOut:
    return kb_out(await get_scoped(session, KnowledgeBase, ctx.org_id, kb_id, what="Knowledge base"))


@router.patch("/orgs/{org_id}/knowledge-bases/{kb_id}")
async def patch_kb(
    kb_id: str,
    body: KBPatch,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> KBOut:
    kb = await get_scoped(session, KnowledgeBase, ctx.org_id, kb_id, what="Knowledge base")
    if body.name:
        kb.name = body.name.strip()
    if body.description is not None:
        kb.description = body.description
    if body.chunk_config is not None:
        kb.chunk_config = _validated_chunk_config({**(kb.chunk_config or {}), **body.chunk_config})
    return kb_out(kb)


@router.delete("/orgs/{org_id}/knowledge-bases/{kb_id}", status_code=204)
async def delete_kb(
    kb_id: str,
    request: Request,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> None:
    kb = await get_scoped(session, KnowledgeBase, ctx.org_id, kb_id, what="Knowledge base")
    kb.deleted_at = datetime.now(UTC)
    docs = (
        (
            await session.execute(
                select(Document).where(Document.kb_id == kb.id, Document.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    for d in docs:
        d.deleted_at = datetime.now(UTC)
        await enqueue("delete_document_vectors", str(d.id))
    await qdrant.delete_kb_points(kb.qdrant_collection, str(ctx.org_id), str(kb.id))
    await audit(
        session,
        org_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        action="kb.deleted",
        target_type="kb",
        target_id=kb.id,
        ip=client_ip(request),
    )


@router.post(
    "/orgs/{org_id}/knowledge-bases/{kb_id}/uploads",
    dependencies=[Depends(rate_limited("uploads", 60, 3600))],
)
async def presign_uploads(
    kb_id: str,
    body: UploadRequest,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    kb = await get_scoped(session, KnowledgeBase, ctx.org_id, kb_id, what="Knowledge base")
    count = (
        (
            await session.execute(
                select(Document).where(Document.kb_id == kb.id, Document.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    if len(count) + len(body.files) > settings.max_files_per_kb:
        raise Problem(422, f"A knowledge base can hold at most {settings.max_files_per_kb} files")
    used = await org_storage_bytes(str(ctx.org_id))
    limit = (
        int((ctx.org.settings or {}).get("limits", {}).get("storage_mb", settings.org_storage_limit_mb))
        * 1024
        * 1024
    )
    out = []
    total = 0
    for f in body.files:
        name = sanitize_filename(str(f.get("name", "")))
        size = int(f.get("size", 0))
        mime = str(f.get("mime") or "").lower()
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if mime not in ALLOWED_MIMES:
            mime = EXT_TO_MIME.get(ext, "")
        if mime not in ALLOWED_MIMES:
            raise Problem(415, f"Unsupported file type for '{name}'. Allowed: PDF, DOCX, TXT, MD")
        if size <= 0 or size > settings.max_upload_bytes:
            raise Problem(413, f"'{name}' exceeds the {settings.max_upload_mb} MB limit")
        total += size
        doc_id = uuid7()
        key = f"orgs/{ctx.org_id}/kb/{kb.id}/{doc_id}/{name}"
        url = await presign_put(key, mime, size)
        out.append(
            {
                "document_id": str(doc_id),
                "name": name,
                "mime": mime,
                "size": size,
                "key": key,
                "upload_url": url,
                "headers": {"Content-Type": mime, "Content-Length": str(size)},
            }
        )
    if used + total > limit:
        raise Problem(413, "Organisation storage limit reached")
    return {"uploads": out, "expires_in": 900}


@router.post("/orgs/{org_id}/knowledge-bases/{kb_id}/documents", status_code=201)
async def confirm_documents(
    kb_id: str,
    body: ConfirmUpload,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> list[DocumentOut]:
    kb = await get_scoped(session, KnowledgeBase, ctx.org_id, kb_id, what="Knowledge base")
    docs: list[Document] = []
    for up in body.uploads:
        try:
            doc_id = uuid.UUID(str(up.get("document_id")))
        except ValueError as e:
            raise Problem(422, "Invalid document_id") from e
        name = sanitize_filename(str(up.get("name", "")))
        mime = str(up.get("mime", "")).lower()
        key = f"orgs/{ctx.org_id}/kb/{kb.id}/{doc_id}/{name}"
        if mime not in ALLOWED_MIMES:
            raise Problem(415, f"Unsupported file type for '{name}'")
        head = await head_object(key)
        if head is None:
            raise Problem(400, f"Upload for '{name}' was not found in storage")
        size = int(head.get("ContentLength", 0))
        if size > settings.max_upload_bytes:
            raise Problem(413, f"'{name}' exceeds the {settings.max_upload_mb} MB limit")
        first = await get_bytes(key, "bytes=0-1023")
        if not sniff_matches(mime, first):
            raise Problem(415, f"'{name}' does not look like a {ALLOWED_MIMES[mime][1:].upper()} file")
        prev = (
            (
                await session.execute(
                    select(Document)
                    .where(Document.kb_id == kb.id, Document.filename == name, Document.deleted_at.is_(None))
                    .order_by(Document.version.desc())
                )
            )
            .scalars()
            .first()
        )
        doc = Document(
            id=doc_id,
            org_id=ctx.org_id,
            kb_id=kb.id,
            filename=name,
            mime_type=mime,
            size_bytes=size,
            storage_key=key,
            version=(prev.version + 1) if prev else 1,
            status="queued",
            parse_strategy=(kb.chunk_config or {}).get("parsing_strategy", "auto"),
        )
        session.add(doc)
        await session.flush()
        job = IngestionJob(
            org_id=ctx.org_id, document_id=doc.id, kb_id=kb.id, job_type="ingest", status="queued"
        )
        session.add(job)
        await session.flush()
        await enqueue("ingest_document", str(doc.id), str(job.id), _job_id=f"ingest-{doc.id}-{doc.version}")
        docs.append(doc)
    return [doc_out(d) for d in docs]


@router.get("/orgs/{org_id}/knowledge-bases/{kb_id}/documents")
async def list_documents(
    kb_id: str,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> list[DocumentOut]:
    kb = await get_scoped(session, KnowledgeBase, ctx.org_id, kb_id, what="Knowledge base")
    rows = (
        (
            await session.execute(
                select(Document)
                .where(Document.kb_id == kb.id, Document.deleted_at.is_(None))
                .order_by(Document.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [doc_out(d) for d in rows]


@router.get("/orgs/{org_id}/documents/{doc_id}")
async def get_document(
    doc_id: str,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    d = await get_scoped(session, Document, ctx.org_id, doc_id, what="Document")
    out = doc_out(d).model_dump()
    out["download_url"] = await presign_get(d.storage_key, d.filename)
    return out


@router.delete("/orgs/{org_id}/documents/{doc_id}", status_code=204)
async def delete_document(
    doc_id: str,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> None:
    d = await get_scoped(session, Document, ctx.org_id, doc_id, what="Document")
    d.deleted_at = datetime.now(UTC)
    await enqueue("delete_document_vectors", str(d.id))


@router.post("/orgs/{org_id}/documents/{doc_id}/retry")
async def retry_document(
    doc_id: str,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> DocumentOut:
    d = await get_scoped(session, Document, ctx.org_id, doc_id, what="Document")
    if d.status not in ("failed", "ready"):
        raise Problem(409, f"Document is currently {d.status}")
    d.status = "queued"
    d.error = None
    job = IngestionJob(org_id=ctx.org_id, document_id=d.id, kb_id=d.kb_id, job_type="ingest", status="queued")
    session.add(job)
    await session.flush()
    await enqueue("ingest_document", str(d.id), str(job.id))
    return doc_out(d)


@router.get("/orgs/{org_id}/documents/{doc_id}/chunks")
async def document_chunks(
    doc_id: str,
    q: str | None = None,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    d = await get_scoped(session, Document, ctx.org_id, doc_id, what="Document")
    kb = await session.get(KnowledgeBase, d.kb_id)
    chunks = await qdrant.scroll_document(kb.qdrant_collection, str(ctx.org_id), str(d.id)) if kb else []
    if q:
        needle = q.lower()
        chunks = [
            c
            for c in chunks
            if needle in (c.get("text") or "").lower() or needle in (c.get("section_title") or "").lower()
        ]
    return {"document_id": str(d.id), "count": len(chunks), "chunks": chunks}


@router.post("/orgs/{org_id}/knowledge-bases/{kb_id}/reindex", status_code=202)
async def reindex(
    kb_id: str,
    ctx: OrgContext = Depends(require_membership("editor")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    kb = await get_scoped(session, KnowledgeBase, ctx.org_id, kb_id, what="Knowledge base")
    job_id = await enqueue("reindex_kb", str(kb.id))
    return {"job_id": job_id, "status": "queued"}


@router.post("/orgs/{org_id}/knowledge-bases/{kb_id}/search")
async def test_retrieval(
    kb_id: str,
    body: SearchIn,
    ctx: OrgContext = Depends(require_membership("viewer")),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    kb = await get_scoped(session, KnowledgeBase, ctx.org_id, kb_id, what="Knowledge base")
    chunks = await retrieve(
        session, ctx.org_id, [kb.id], body.query, top_k=body.top_k, threshold=body.threshold, mode=body.mode
    )
    return {"query": body.query, "results": [c.as_dict() for c in chunks]}
