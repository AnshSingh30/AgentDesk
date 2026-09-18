"""AI-drafted table/column descriptions and inferred relationships (PRD §11.3)."""

from __future__ import annotations

import uuid
from typing import Literal

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Dataset, DatasetColumn, DatasetRelationship, DatasetTable
from app.llm.gateway import ResolvedModel, structured
from app.usage.service import UsageRecord

log = structlog.get_logger()
SemanticType = Literal[
    "id",
    "foreign_key",
    "name",
    "email",
    "phone",
    "date",
    "datetime",
    "currency",
    "percentage",
    "category",
    "free_text",
    "boolean",
    "numeric_measure",
    "sensitive_personal",
]


class ColumnDescription(BaseModel):
    name: str
    description: str = Field(max_length=400)
    semantic_type: SemanticType = "category"


class SuggestedRelationship(BaseModel):
    from_col: str
    to_table: str
    to_col: str
    confidence: float = Field(0.5, ge=0, le=1)


class TableDescription(BaseModel):
    table_description: str = Field(max_length=600)
    columns: list[ColumnDescription]
    suggested_relationships: list[SuggestedRelationship] = Field(default_factory=list)


def _table_prompt(
    ds: Dataset, table: DatasetTable, all_tables: list[DatasetTable], rels: list[DatasetRelationship]
) -> str:
    lines = [
        f"Dataset: {ds.name} ({ds.type})",
        f"Table: {table.schema_name}.{table.table_name} ({table.kind}, ~{table.row_estimate or 0} rows)",
    ]
    if table.description:
        lines.append(f"Existing table comment: {table.description}")
    lines.append("Columns:")
    for c in table.columns:
        extra = []
        if c.is_pk:
            extra.append("PK")
        if c.description:
            extra.append(f"comment: {c.description}")
        if c.sample_values:
            extra.append("samples: " + ", ".join(str(v) for v in c.sample_values[:5]))
        lines.append(
            f"  - {c.column_name} ({c.data_type}{', nullable' if c.nullable else ''}) {' | '.join(extra)}"
        )
    fks = [r for r in rels if r.from_table_id == table.id]
    if fks:
        by_id = {t.id: t for t in all_tables}
        lines.append(
            "Foreign keys: "
            + "; ".join(
                f"{r.from_column} -> {by_id[r.to_table_id].table_name}.{r.to_column}"
                for r in fks
                if r.to_table_id in by_id
            )
        )
    lines.append(
        "Other tables in this dataset: "
        + ", ".join(t.table_name for t in all_tables if t.id != table.id)[:1500]
    )
    lines.append(
        "\nWrite a concise business description for the table and each column (what it means for an analyst, not the type). "
        "Assign a semantic_type per column. Tag personal or sensitive data as sensitive_personal. "
        "Suggest relationships to other tables only where no foreign key exists and the column names strongly imply one."
    )
    return "\n".join(lines)


async def draft_table_descriptions(
    session: AsyncSession,
    ds: Dataset,
    rm: ResolvedModel,
    table_ids: list[uuid.UUID] | None = None,
    *,
    overwrite_user: bool = False,
) -> tuple[int, list[UsageRecord]]:
    tables = (
        (
            await session.execute(
                select(DatasetTable).where(DatasetTable.dataset_id == ds.id).order_by(DatasetTable.table_name)
            )
        )
        .scalars()
        .all()
    )
    rels = (
        (await session.execute(select(DatasetRelationship).where(DatasetRelationship.dataset_id == ds.id)))
        .scalars()
        .all()
    )
    targets = [t for t in tables if not table_ids or t.id in table_ids]
    by_name = {t.table_name.lower(): t for t in tables}
    usage: list[UsageRecord] = []
    done = 0
    for table in targets:
        try:
            result, chat = await structured(
                rm,
                [
                    {
                        "role": "system",
                        "content": "You are a data analyst documenting a database schema for a text-to-SQL assistant.",
                    },
                    {"role": "user", "content": _table_prompt(ds, table, tables, rels)},
                ],
                TableDescription,
                max_tokens=2000,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("describe_failed", table=table.table_name, error=str(e))
            continue
        usage.append(
            UsageRecord(
                kind="llm",
                model=rm.display,
                provider=rm.provider,
                credential_mode=rm.credential_mode,
                input_tokens=chat.usage.input_tokens,
                output_tokens=chat.usage.output_tokens,
                est_cost_usd=chat.est_cost_usd,
                node="describe_schema",
            )
        )
        if overwrite_user or table.description_source != "user":
            table.description = result.table_description.strip()
            table.description_source = "ai_draft"
            table.reviewed = False
        cols = {c.column_name.lower(): c for c in table.columns}
        for cd in result.columns:
            col = cols.get(cd.name.lower())
            if col is None:
                continue
            if overwrite_user or col.description_source != "user":
                col.description = cd.description.strip()
                col.description_source = "ai_draft"
                col.reviewed = False
            col.semantic_type = cd.semantic_type
        existing = {
            (r.from_table_id, r.from_column.lower(), r.to_table_id, r.to_column.lower()) for r in rels
        }
        for sr in result.suggested_relationships:
            target = by_name.get(sr.to_table.lower())
            if target is None or target.id == table.id:
                continue
            key = (table.id, sr.from_col.lower(), target.id, sr.to_col.lower())
            if key in existing or sr.from_col.lower() not in cols:
                continue
            session.add(
                DatasetRelationship(
                    dataset_id=ds.id,
                    from_table_id=table.id,
                    from_column=sr.from_col,
                    to_table_id=target.id,
                    to_column=sr.to_col,
                    type="inferred",
                    confidence=sr.confidence,
                )
            )
            existing.add(key)
        done += 1
    await session.flush()
    return done, usage


__all__ = ["DatasetColumn", "TableDescription", "draft_table_descriptions"]
