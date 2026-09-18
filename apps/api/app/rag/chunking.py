"""chunk_by_title with metadata enrichment (PRD §10.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from unstructured.chunking.title import chunk_by_title
from unstructured.documents.elements import Element, Table, Title

DEFAULT_CHUNK_CONFIG: dict[str, Any] = {
    "chunk_size": 1500,
    "new_after_n_chars": 1200,
    "chunk_overlap": 200,
    "combine_under": 300,
    "parsing_strategy": "auto",
    "contextual_headers": True,
}


@dataclass
class Chunk:
    text: str
    chunk_index: int
    page_start: int | None
    page_end: int | None
    section_title: str | None
    element_types: list[str] = field(default_factory=list)

    def embed_text(self, filename: str, contextual: bool) -> str:
        if contextual and (self.section_title or filename):
            return f"{filename} > {self.section_title or ''}\n{self.text}".strip()
        return self.text


def chunk_elements(elements: list[Element], cfg: dict[str, Any] | None = None) -> list[Chunk]:
    cfg = {**DEFAULT_CHUNK_CONFIG, **(cfg or {})}
    max_chars = int(cfg["chunk_size"])
    composites = chunk_by_title(
        elements,
        max_characters=max_chars,
        new_after_n_chars=min(int(cfg["new_after_n_chars"]), max_chars),
        overlap=min(int(cfg["chunk_overlap"]), max_chars // 2),
        combine_text_under_n_chars=min(int(cfg["combine_under"]), max_chars),
        multipage_sections=True,
    )
    chunks: list[Chunk] = []
    current_title: str | None = None
    for i, comp in enumerate(composites):
        origs = list(comp.metadata.orig_elements or [])
        pages = [e.metadata.page_number for e in origs if e.metadata.page_number] or (
            [comp.metadata.page_number] if comp.metadata.page_number else []
        )
        titles = [e.text for e in origs if isinstance(e, Title)]
        if titles:
            current_title = titles[0][:200]
        types = sorted({type(e).__name__ for e in origs}) or [type(comp).__name__]
        text = comp.text
        if isinstance(comp, Table) and getattr(comp.metadata, "text_as_html", None):
            text = comp.metadata.text_as_html
        chunks.append(
            Chunk(
                text=text,
                chunk_index=i,
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                section_title=current_title,
                element_types=types,
            )
        )
    return chunks
