"""Schema context for the SQL agent: exposed tables, compact DDL-like text, table selection, few-shots (PRD §11.5)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.db.models import Dataset, DatasetExampleQuery, DatasetGlossary, DatasetRelationship, DatasetTable
from app.sql.validator import ExposedTable

MAX_TABLES_INLINE = 15


@dataclass
class TableInfo:
    id: uuid.UUID
    schema: str
    name: str
    description: str | None
    columns: list[dict[str, Any]]
    row_estimate: int | None

    @property
    def card(self) -> str:
        return (
            f"{self.name}: {self.description or ''} | columns: {', '.join(c['name'] for c in self.columns)}"
        )


@dataclass
class DatasetContext:
    dataset: Dataset
    dialect: str
    tables: list[TableInfo]
    relationships: list[dict[str, str]]
    glossary: list[dict[str, str]]
    examples: list[dict[str, str]]
    exposed: list[ExposedTable] = field(default_factory=list)

    def schema_text(self, table_names: list[str] | None = None) -> str:
        chosen = [t for t in self.tables if not table_names or t.name in table_names]
        rel_by_col = {
            (r["from_table"], r["from_column"]): f"{r['to_table']}.{r['to_column']}"
            for r in self.relationships
        }
        lines: list[str] = []
        for t in chosen:
            lines.append(f"TABLE {t.name}" + (f" -- {t.description}" if t.description else ""))
            for c in t.columns:
                tags = []
                if c.get("is_pk"):
                    tags.append("PK")
                ref = rel_by_col.get((t.name, c["name"]))
                if ref:
                    tags.append(f"FK -> {ref}")
                if c.get("nullable"):
                    tags.append("NULL")
                desc = c.get("description") or ""
                samples = c.get("samples")
                if (
                    samples
                    and isinstance(samples, list)
                    and not (len(samples) == 1 and str(samples[0]).startswith("sampling"))
                ):
                    listed = (
                        str(samples[0])
                        if str(samples[0]).startswith("range: ")
                        else "values: " + ", ".join(repr(str(s)) for s in samples[:30])
                    )
                    desc = (desc + " " if desc else "") + listed
                lines.append(
                    f"  {c['name']} {c['type'].upper()}{' ' + ' '.join(tags) if tags else ''}{' -- ' + desc if desc else ''}"
                )
            lines.append("")
        names = {t.name for t in chosen}
        rels = [
            f"{r['from_table']}.{r['from_column']} -> {r['to_table']}.{r['to_column']} ({r['type']})"
            for r in self.relationships
            if r["from_table"] in names and r["to_table"] in names
        ]
        if rels:
            lines.append("RELATIONSHIPS: " + "; ".join(rels))
        if self.glossary:
            lines.append(
                "BUSINESS GLOSSARY: "
                + "; ".join(f"{g['term']} = {g['definition']}" for g in self.glossary[:30])
            )
        return "\n".join(lines)


async def load_dataset_context(session: AsyncSession, ds: Dataset) -> DatasetContext:
    tables = (
        (
            await session.execute(
                select(DatasetTable)
                .where(DatasetTable.dataset_id == ds.id, DatasetTable.exposed.is_(True))
                .order_by(DatasetTable.table_name)
            )
        )
        .scalars()
        .all()
    )
    by_id = {t.id: t for t in tables}
    rels = (
        (await session.execute(select(DatasetRelationship).where(DatasetRelationship.dataset_id == ds.id)))
        .scalars()
        .all()
    )
    glossary = (
        (await session.execute(select(DatasetGlossary).where(DatasetGlossary.dataset_id == ds.id)))
        .scalars()
        .all()
    )
    examples = (
        (
            await session.execute(
                select(DatasetExampleQuery).where(
                    DatasetExampleQuery.dataset_id == ds.id, DatasetExampleQuery.validated_at.is_not(None)
                )
            )
        )
        .scalars()
        .all()
    )
    infos: list[TableInfo] = []
    exposed: list[ExposedTable] = []
    for t in tables:
        visible = [c for c in t.columns if not c.hidden]
        infos.append(
            TableInfo(
                id=t.id,
                schema=t.schema_name,
                name=t.table_name,
                description=t.description,
                row_estimate=t.row_estimate,
                columns=[
                    {
                        "name": c.column_name,
                        "type": c.data_type,
                        "nullable": c.nullable,
                        "is_pk": c.is_pk,
                        "description": c.description,
                        "samples": c.sample_values,
                    }
                    for c in visible
                ],
            )
        )
        exposed.append(
            ExposedTable(
                name=t.table_name,
                schema=t.schema_name,
                columns={c.column_name: c.data_type for c in t.columns},
                hidden={c.column_name for c in t.columns if c.hidden},
            )
        )
    rel_dicts = [
        {
            "from_table": by_id[r.from_table_id].table_name,
            "from_column": r.from_column,
            "to_table": by_id[r.to_table_id].table_name,
            "to_column": r.to_column,
            "type": r.type,
        }
        for r in rels
        if r.from_table_id in by_id and r.to_table_id in by_id
    ]
    return DatasetContext(
        dataset=ds,
        dialect="postgres" if ds.type == "postgres" else "duckdb",
        tables=infos,
        relationships=rel_dicts,
        glossary=[{"term": g.term, "definition": g.definition} for g in glossary],
        examples=[{"question": e.question, "sql": e.sql} for e in examples],
        exposed=exposed,
    )


def _tokens(text: str) -> set[str]:
    return {w for w in "".join(ch if ch.isalnum() else " " for ch in text.lower()).split() if len(w) > 2}


async def select_tables(ctx: DatasetContext, question: str) -> list[str]:
    """<= 15 tables: all. Otherwise embed table cards, take top 8 by similarity, expand one FK hop."""
    if len(ctx.tables) <= MAX_TABLES_INLINE:
        return [t.name for t in ctx.tables]
    from app.rag.embeddings import embed_dense

    r = get_redis()
    cache_key = f"tablecards:{ctx.dataset.id}:{ctx.dataset.current_schema_version}"
    cached = await r.get(cache_key)
    cards = [t.card for t in ctx.tables]
    if cached:
        vectors = json.loads(cached)
    else:
        vectors = await embed_dense(cards)
        await r.setex(cache_key, 7 * 24 * 3600, json.dumps(vectors))
    qv = (await embed_dense([question]))[0]

    def cos(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=False))

    scored = sorted(zip(ctx.tables, vectors, strict=True), key=lambda tv: cos(qv, tv[1]), reverse=True)
    chosen = {t.name for t, _ in scored[:8]}
    for rel in ctx.relationships:
        if rel["from_table"] in chosen:
            chosen.add(rel["to_table"])
        elif rel["to_table"] in chosen:
            chosen.add(rel["from_table"])
    return [t.name for t in ctx.tables if t.name in chosen]


def top_examples(ctx: DatasetContext, question: str, k: int = 3) -> list[dict[str, str]]:
    q = _tokens(question)
    scored = sorted(ctx.examples, key=lambda e: len(q & _tokens(e["question"])), reverse=True)
    return [e for e in scored[:k] if q & _tokens(e["question"])] or ctx.examples[:k]
