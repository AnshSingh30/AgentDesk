"""Markdown -> WhatsApp formatting, table rendering and message splitting (PRD §14.4, PG-4)."""

from __future__ import annotations

import re

WA_MAX = 4096
WA_SOFT = 4000
TABLE_MAX_ROWS = 15
TABLE_MAX_WIDTH = 60
_BOLD_PLACEHOLDER = "\x00B\x00"


def _render_table(lines: list[str]) -> str:
    rows = []
    for line in lines:
        if re.match(r"^\s*\|?\s*:?-{2,}", line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)
    if not rows:
        return ""
    header, body = rows[0], rows[1:]
    truncated = len(body) > TABLE_MAX_ROWS
    body = body[:TABLE_MAX_ROWS]
    ncols = len(header)
    if ncols > 4:  # too wide for a phone: bullet list per row
        out = []
        for r in body:
            out.append("- " + "; ".join(f"{h}: {v}" for h, v in zip(header, r, strict=False)))
        if truncated:
            out.append(f"- … {len(rows) - 1 - TABLE_MAX_ROWS} more rows")
        return "\n".join(out)
    widths = [
        min(max(len(str(r[i])) if i < len(r) else 0 for r in [header, *body]), TABLE_MAX_WIDTH // ncols)
        for i in range(ncols)
    ]

    def fmt(r: list[str]) -> str:
        return "  ".join(
            (str(r[i]) if i < len(r) else "")[: widths[i]].ljust(widths[i]) for i in range(ncols)
        ).rstrip()

    lines_out = [fmt(header), "  ".join("-" * w for w in widths), *[fmt(r) for r in body]]
    if truncated:
        lines_out.append(f"… {len(rows) - 1 - TABLE_MAX_ROWS} more rows")
    return "```\n" + "\n".join(lines_out) + "\n```"


def markdown_to_whatsapp(text: str) -> str:
    text = text.replace("\r\n", "\n")
    out_lines: list[str] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if (
            line.strip().startswith("|")
            and i + 1 < len(lines)
            and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1])
        ):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            out_lines.append(_render_table(block))
            continue
        if re.match(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", line):
            i += 1
            continue
        m = re.match(r"^\s{0,3}#{1,6}\s+(.*)$", line)
        if m:
            line = f"{_BOLD_PLACEHOLDER}{m.group(1).strip().rstrip('#').strip()}{_BOLD_PLACEHOLDER}"
        line = re.sub(r"^(\s*)[*+]\s+", r"\1- ", line)
        line = re.sub(r"^(\s*)>\s?", r"\1> ", line)
        out_lines.append(line)
        i += 1
    text = "\n".join(out_lines)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1 (\2)", text)
    text = re.sub(r"\*\*(.+?)\*\*", _BOLD_PLACEHOLDER + r"\1" + _BOLD_PLACEHOLDER, text)
    text = re.sub(r"__(.+?)__", _BOLD_PLACEHOLDER + r"\1" + _BOLD_PLACEHOLDER, text)
    text = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"_\1_", text)
    text = text.replace(_BOLD_PLACEHOLDER, "*")
    text = re.sub(r"~~(.+?)~~", r"~\1~", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_message(text: str, limit: int = WA_SOFT) -> list[str]:
    """Split on paragraph boundaries (then lines, then hard) and add (i/n) suffixes."""
    if len(text) <= WA_MAX:
        return [text]
    parts: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        candidate = para if not current else current + "\n\n" + para
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
            current = ""
        if len(para) <= limit:
            current = para
            continue
        for line in para.split("\n"):
            while len(line) > limit:
                parts.append(line[:limit])
                line = line[limit:]
            candidate = line if not current else current + "\n" + line
            if len(candidate) <= limit:
                current = candidate
            else:
                parts.append(current)
                current = line
    if current:
        parts.append(current)
    n = len(parts)
    return [f"{p} ({i}/{n})" for i, p in enumerate(parts, start=1)]


def citations_footer(citations: list[dict[str, object]]) -> str:
    seen: list[str] = []
    for c in citations:
        label = str(c.get("filename", "")).rsplit(".", 1)[0]
        if c.get("page_start"):
            label += f" (p.{c['page_start']})"
        if label and label not in seen:
            seen.append(label)
    return ("📄 Sources: " + ", ".join(seen[:4])) if seen else ""


def format_for_whatsapp(answer: str, citations: list[dict[str, object]] | None = None) -> list[str]:
    text = markdown_to_whatsapp(answer)
    footer = citations_footer(citations or [])
    if footer:
        text = f"{text}\n\n{footer}"
    return split_message(text)
