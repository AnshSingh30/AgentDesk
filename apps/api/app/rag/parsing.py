"""Layout-aware parsing into Unstructured elements (PRD §10.2).

DOCX / TXT / MD use Unstructured's partitioners. PDF uses pdfminer directly: Unstructured's own
partition_pdf hard-imports the torch-based inference stack (2+ GB), which is out of scope for this
deployment footprint. Titles are detected from font size, page numbers are kept, headers/footers
are dropped by position.
"""

from __future__ import annotations

import io
import statistics
from dataclasses import dataclass

from unstructured.documents.elements import (
    Element,
    ElementMetadata,
    Footer,
    Header,
    NarrativeText,
    PageBreak,
    Table,
    Text,
    Title,
)

DROP_TYPES = (Header, Footer, PageBreak)


@dataclass
class ParsedDocument:
    elements: list[Element]
    page_count: int | None


def _clean(elements: list[Element]) -> list[Element]:
    return [e for e in elements if not isinstance(e, DROP_TYPES) and (e.text or "").strip()]


def parse_pdf(data: bytes, strategy: str = "auto") -> ParsedDocument:
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LAParams, LTChar, LTTextBox, LTTextLine

    laparams = LAParams(line_margin=0.3, char_margin=2.0, boxes_flow=0.5 if strategy != "ocr_only" else None)
    elements: list[Element] = []
    page_count = 0
    for page_no, page in enumerate(extract_pages(io.BytesIO(data), laparams=laparams), start=1):
        page_count = page_no
        page_h = float(page.height or 0)
        boxes: list[tuple[str, float, float]] = []
        for obj in page:
            if not isinstance(obj, LTTextBox):
                continue
            text = obj.get_text().strip()
            if not text:
                continue
            sizes = [
                c.size for line in obj if isinstance(line, LTTextLine) for c in line if isinstance(c, LTChar)
            ]
            size = statistics.median(sizes) if sizes else 10.0
            boxes.append((text, size, float(obj.y1)))
        if not boxes:
            continue
        body = statistics.median(s for _, s, _ in boxes)
        for text, size, y1 in boxes:
            # header/footer: single short line at the extreme top or bottom of the page
            if page_h and (y1 > page_h * 0.94 or y1 < page_h * 0.07) and len(text) < 90 and "\n" not in text:
                continue
            meta = ElementMetadata(page_number=page_no)
            lines = text.split("\n")
            if size >= body * 1.15 and len(text) < 120 and len(lines) <= 2:
                elements.append(Title(text=text.replace("\n", " "), metadata=meta))
            elif _looks_like_table(lines):
                elements.append(Table(text=text, metadata=meta))
            else:
                elements.append(NarrativeText(text=" ".join(line.strip() for line in lines), metadata=meta))
    return ParsedDocument(elements=_clean(elements), page_count=page_count or None)


def _looks_like_table(lines: list[str]) -> bool:
    # ponytail: heuristic (>=3 lines with 3+ multi-space separated cells); replace with a layout model for scans
    if len(lines) < 3:
        return False
    tabular = sum(1 for line in lines if len([c for c in line.split("  ") if c.strip()]) >= 3)
    return tabular >= max(3, int(len(lines) * 0.6))


def parse_docx(data: bytes) -> ParsedDocument:
    from unstructured.partition.docx import partition_docx

    els = partition_docx(file=io.BytesIO(data), infer_table_structure=True)
    pages = {e.metadata.page_number for e in els if e.metadata.page_number}
    return ParsedDocument(elements=_clean(list(els)), page_count=max(pages) if pages else None)


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        from charset_normalizer import from_bytes

        best = from_bytes(data).best()
        return str(best) if best else data.decode("latin-1", errors="replace")


def parse_text(data: bytes) -> ParsedDocument:
    from unstructured.partition.text import partition_text

    return ParsedDocument(elements=_clean(list(partition_text(text=_decode(data)))), page_count=None)


def parse_markdown(data: bytes) -> ParsedDocument:
    from unstructured.partition.md import partition_md

    return ParsedDocument(elements=_clean(list(partition_md(text=_decode(data)))), page_count=None)


def parse_bytes(data: bytes, mime_type: str, strategy: str = "auto") -> ParsedDocument:
    if mime_type == "application/pdf":
        return parse_pdf(data, strategy)
    if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return parse_docx(data)
    if mime_type == "text/markdown":
        return parse_markdown(data)
    if mime_type in ("text/plain", "text/csv"):
        return parse_text(data)
    raise ValueError(f"Unsupported file type: {mime_type}")


__all__ = ["Element", "ParsedDocument", "Table", "Text", "Title", "parse_bytes"]
