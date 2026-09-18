"""Text-to-SQL path on the hr_demo database with the fake model: introspection, review, validator, executor, agent graph."""

from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest
from app.agents.schemas import AgentConfig
from app.core.crypto import encrypt_secret
from app.db.models import Agent, Conversation, Dataset, DatasetColumn, DatasetTable, Organization, User
from app.db.session import get_sessionmaker
from app.runtime.runner import prepare_run, run_once
from app.sql.context import load_dataset_context
from app.sql.executor import SqlExecutionError, execute
from app.sql.introspect import run_introspection
from app.sql.validator import validate_sql
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "infra" / "scripts"))
from generate_hr_demo import generate  # noqa: E402

pytestmark = pytest.mark.asyncio
HR_URL = os.environ.get("HR_DEMO_DATABASE_URL", "postgresql://hr_demo:hr_demo@localhost:5434/hr_demo")


@pytest.fixture
async def hr_dataset() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    conn = await asyncpg.connect(HR_URL)
    try:
        if await conn.fetchval("SELECT to_regclass('public.employees') IS NULL"):
            await conn.execute(generate())
    finally:
        await conn.close()
    async with get_sessionmaker()() as session:
        user = User(
            email=f"sql-{uuid.uuid4().hex[:8]}@example.com", name="S", email_verified_at=datetime.now(UTC)
        )
        org = Organization(name="SQL Org", slug=f"sql-{uuid.uuid4().hex[:6]}")
        session.add_all([user, org])
        await session.flush()
        ds = Dataset(
            org_id=org.id,
            name="hr",
            type="postgres",
            status="introspecting",
            allow_sampling=True,
            connection_config={
                "host": "localhost",
                "port": 5434,
                "database": "hr_demo",
                "username": "hr_demo",
                "sslmode": "disable",
                "schemas": ["public"],
            },
            encrypted_password=encrypt_secret("hr_demo"),
        )
        session.add(ds)
        await session.flush()
        await run_introspection(session, ds)
        ds.status = "ready"
        await session.commit()
        return org.id, ds.id, user.id


async def test_introspection_captures_schema_comments_and_fks(hr_dataset: tuple) -> None:
    org_id, ds_id, _ = hr_dataset
    async with get_sessionmaker()() as session:
        tables = (
            (await session.execute(select(DatasetTable).where(DatasetTable.dataset_id == ds_id)))
            .scalars()
            .all()
        )
        names = {t.table_name for t in tables}
        assert {"employees", "departments", "compensation", "leave_requests", "candidates"} <= names
        emp = next(t for t in tables if t.table_name == "employees")
        assert emp.description_source == "db_comment" and "employee" in (emp.description or "").lower()
        status_col = next(c for c in emp.columns if c.column_name == "status")
        assert status_col.sample_values and "active" in status_col.sample_values
        dob = next(c for c in emp.columns if c.column_name == "date_of_birth")
        assert dob.sample_values == ["sampling skipped (sensitive name)"]
        ds = await session.get(Dataset, ds_id)
        ctx = await load_dataset_context(session, ds)
        assert any(
            r["from_table"] == "employees" and r["to_table"] == "departments" for r in ctx.relationships
        )
        assert "TABLE employees" in ctx.schema_text()


async def test_hidden_column_blocked_and_query_runs(hr_dataset: tuple) -> None:
    org_id, ds_id, _ = hr_dataset
    async with get_sessionmaker()() as session:
        ds = await session.get(Dataset, ds_id)
        col = (
            await session.execute(
                select(DatasetColumn)
                .join(DatasetTable)
                .where(DatasetTable.dataset_id == ds_id, DatasetColumn.column_name == "base_salary")
            )
        ).scalar_one()
        col.hidden = True
        await session.commit()
        ctx = await load_dataset_context(session, ds)
        rejected = validate_sql(
            "SELECT full_name, base_salary FROM employees e JOIN compensation c ON c.employee_id = e.employee_id",
            ctx.exposed,
        )
        assert not rejected.ok and "hidden" in rejected.error
        ok = validate_sql(
            "SELECT d.name, count(*) AS n FROM employees e JOIN departments d ON d.department_id = e.department_id GROUP BY d.name",
            ctx.exposed,
        )
        assert ok.ok
        result = await execute(ds, ok.sql, 200)
        assert result.columns == ["name", "n"] and result.row_count >= 5
        with pytest.raises(SqlExecutionError):
            await execute(ds, "SELECT * FROM nonexistent_table", 10)
        with pytest.raises(
            SqlExecutionError
        ):  # read-only transaction refuses writes even if the validator were bypassed
            await execute(ds, "DELETE FROM employees", 10)


async def test_agent_graph_runs_sql_tool(hr_dataset: tuple) -> None:
    org_id, ds_id, user_id = hr_dataset
    async with get_sessionmaker()() as session:
        org = await session.get(Organization, org_id)
        cfg = AgentConfig.model_validate(
            {
                "instructions": {"system_prompt": "HR analytics"},
                "tools": {"sql": {"enabled": True, "dataset_id": str(ds_id)}},
            }
        )
        agent = Agent(org_id=org_id, name="Analytics", draft_config=cfg.model_dump(mode="json"))
        session.add(agent)
        await session.flush()
        conv = Conversation(
            org_id=org_id, agent_id=agent.id, channel="playground", playground_user_id=user_id, flags={}
        )
        session.add(conv)
        await session.flush()
        ctx = await prepare_run(
            session,
            org=org,
            agent=agent,
            cfg=cfg,
            channel="playground",
            conversation=conv,
            user_text="How many employees are there by department?",
        )
        await session.commit()
    outcome = await run_once(ctx, "How many employees are there by department?")
    assert outcome.error is None, outcome.error
    assert outcome.state["route"]["tools"] == ["sql"]
    assert "rows" in outcome.state["answer"].lower() or outcome.state["answer"]
    assert outcome.message_id is not None
