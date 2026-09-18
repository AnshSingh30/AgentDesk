"""`make seed` (PRD §22.1): superadmin + demo orgs, 3 agents, ingested KBs, reviewed HR dataset, glossary,
example queries, sample conversations and feedback. Re-running replaces the demo data.

Run from apps/api (needs its .venv and .env): python ../../infra/scripts/seed_demo.py
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "infra" / "scripts"))

import asyncpg  # noqa: E402
import structlog  # noqa: E402
from app.agents.schemas import AgentConfig  # noqa: E402
from app.agents.service import publish_agent  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.crypto import encrypt_secret  # noqa: E402
from app.core.ids import uuid7  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.core.s3 import ensure_bucket, put_bytes  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.models import (  # noqa: E402
    Agent,
    Conversation,
    Dataset,
    DatasetExampleQuery,
    DatasetGlossary,
    DatasetTable,
    Document,
    Feedback,
    KnowledgeBase,
    Membership,
    Message,
    Organization,
    RunTrace,
    UsageEvent,
    User,
)
from app.db.session import get_sessionmaker, session_scope  # noqa: E402
from app.rag import qdrant  # noqa: E402
from app.rag.embeddings import dimension  # noqa: E402
from app.rag.ingestion import ingest_document  # noqa: E402
from app.sql.executor import SqlExecutionError, execute  # noqa: E402
from app.sql.introspect import run_introspection  # noqa: E402
from app.templates.loader import load_templates, sync_templates, template_config  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from generate_demo_docs import generate_all  # noqa: E402
from generate_hr_demo import OUT as HR_SQL  # noqa: E402
from generate_hr_demo import generate as generate_hr

log = structlog.get_logger()
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "Demo-password-123!")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin-password-123!")
HR_DEMO_URL = os.environ.get("HR_DEMO_DATABASE_URL", "postgresql://hr_demo:hr_demo@localhost:5434/hr_demo")
HR_DEMO_HOST = os.environ.get("HR_DEMO_HOST", "localhost")
MIME = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

TABLE_DOCS: dict[str, tuple[str, dict[str, str]]] = {
    "departments": (
        "Company departments (cost centres). head_employee_id points to the department head.",
        {
            "department_id": "Department identifier",
            "name": "Department name, e.g. Engineering, Sales",
            "cost_center": "Finance cost centre code",
            "head_employee_id": "Employee id of the department head",
        },
    ),
    "locations": (
        "Office locations.",
        {"location_id": "Location identifier", "city": "Office city", "country": "Country"},
    ),
    "employees": (
        "One row per employee, current and former. status is active, exited or on_leave; exit_date is NULL while employed.",
        {
            "employee_id": "Employee identifier",
            "full_name": "Employee full name",
            "gender": "Self-reported gender: female, male, other",
            "date_of_birth": "Date of birth (sensitive; hidden from the agent)",
            "department_id": "Department the employee belongs to",
            "location_id": "Office location",
            "job_title": "Job title",
            "level": "Career level L1 (junior) to L6 (director)",
            "manager_id": "employee_id of the reporting manager",
            "employment_type": "full_time, contract or intern",
            "hire_date": "Date of joining",
            "exit_date": "Last working day; NULL if still employed",
            "exit_reason": "Reason for leaving, only for exited employees",
            "status": "active, exited or on_leave",
        },
    ),
    "compensation": (
        "Salary history: one row per change; the row with the latest effective_date per employee is the current salary.",
        {
            "employee_id": "Employee",
            "effective_date": "Date the salary took effect",
            "base_salary": "Annual base salary (sensitive; hidden)",
            "currency": "Currency code, always INR",
        },
    ),
    "leave_requests": (
        "Leave applications. days is the number of leave days; status is approved, rejected or pending.",
        {
            "leave_id": "Leave request identifier",
            "employee_id": "Employee",
            "leave_type": "casual, sick, earned, maternity or paternity",
            "start_date": "First day of leave",
            "end_date": "Last day of leave",
            "days": "Number of days (0.5 = half day)",
            "status": "approved, rejected or pending",
        },
    ),
    "attendance": (
        "Daily attendance for the last 60 working days: present, absent, wfh or leave.",
        {"employee_id": "Employee", "date": "Attendance date", "status": "present, absent, wfh or leave"},
    ),
    "job_openings": (
        "Open and closed job requisitions.",
        {
            "opening_id": "Opening identifier",
            "department_id": "Hiring department",
            "title": "Role title",
            "opened_at": "Date the role opened",
            "closed_at": "Date the role closed (NULL if still open)",
            "status": "open or closed",
        },
    ),
    "candidates": (
        "Hiring funnel: one row per candidate per opening. stage is applied, screen, interview, offer, hired or rejected.",
        {
            "candidate_id": "Candidate identifier",
            "opening_id": "Job opening",
            "source": "Sourcing channel (LinkedIn, Referral, Naukri, Campus, Agency, Careers page)",
            "stage": "Current funnel stage",
            "applied_at": "Application date",
            "stage_updated_at": "Last stage change",
        },
    ),
    "performance_reviews": (
        "Half-yearly performance reviews. rating 1 (lowest) to 5 (highest).",
        {
            "review_id": "Review identifier",
            "employee_id": "Employee reviewed",
            "cycle": "Review cycle, e.g. 2025-H2",
            "rating": "Rating 1-5",
            "reviewer_id": "employee_id of the reviewer",
        },
    ),
}
HIDDEN_COLUMNS = {("employees", "date_of_birth"), ("compensation", "base_salary")}
GLOSSARY = [
    (
        "attrition rate",
        "Number of employees who exited in a period divided by the average headcount in that period (exits / avg headcount).",
    ),
    ("voluntary attrition", "Exits where exit_reason is not Performance or Restructuring."),
    ("active headcount", "Count of employees with status = 'active' (or on_leave) on a given date."),
    ("time-to-hire", "Days between job_openings.opened_at and the hired candidate's stage_updated_at."),
    ("offer acceptance rate", "Candidates hired divided by candidates who reached the offer or hired stage."),
    ("tenure", "Years between hire_date and exit_date (or today for active employees)."),
]
EXAMPLES = [
    (
        "How many active employees do we have?",
        "SELECT count(*) AS active_employees FROM employees WHERE status = 'active'",
    ),
    (
        "Headcount by department",
        "SELECT d.name AS department, count(*) AS headcount FROM employees e JOIN departments d ON d.department_id = e.department_id WHERE e.status <> 'exited' GROUP BY d.name ORDER BY headcount DESC",
    ),
    (
        "Attrition rate by department for the last 12 months",
        "SELECT d.name AS department, count(*) FILTER (WHERE e.exit_date >= CURRENT_DATE - INTERVAL '12 months') AS exits, count(*) FILTER (WHERE e.status <> 'exited') AS current_headcount, ROUND(100.0 * count(*) FILTER (WHERE e.exit_date >= CURRENT_DATE - INTERVAL '12 months') / NULLIF(count(*) FILTER (WHERE e.status <> 'exited') + count(*) FILTER (WHERE e.exit_date >= CURRENT_DATE - INTERVAL '12 months'), 0), 1) AS attrition_pct FROM employees e JOIN departments d ON d.department_id = e.department_id GROUP BY d.name ORDER BY attrition_pct DESC",
    ),
    (
        "How many open positions are there and what is the average time to hire?",
        "SELECT (SELECT count(*) FROM job_openings WHERE status = 'open') AS open_positions, ROUND(AVG(c.stage_updated_at - o.opened_at), 1) AS avg_days_to_hire FROM candidates c JOIN job_openings o ON o.opening_id = c.opening_id WHERE c.stage = 'hired'",
    ),
    (
        "Top 5 departments by sick leave days this quarter",
        "SELECT d.name AS department, SUM(l.days) AS sick_leave_days FROM leave_requests l JOIN employees e ON e.employee_id = l.employee_id JOIN departments d ON d.department_id = e.department_id WHERE l.leave_type = 'sick' AND l.status = 'approved' AND l.start_date >= date_trunc('quarter', CURRENT_DATE) GROUP BY d.name ORDER BY sick_leave_days DESC LIMIT 5",
    ),
    (
        "Headcount trend by month in 2026",
        "SELECT to_char(m, 'YYYY-MM') AS month, count(e.employee_id) AS headcount FROM generate_series(date '2026-01-01', date '2026-09-01', interval '1 month') m LEFT JOIN employees e ON e.hire_date <= m AND (e.exit_date IS NULL OR e.exit_date > m) GROUP BY m ORDER BY m",
    ),
    (
        "Average tenure of employees in Engineering",
        "SELECT ROUND(AVG(EXTRACT(EPOCH FROM (COALESCE(e.exit_date, CURRENT_DATE) - e.hire_date)) / 86400 / 365.25)::numeric, 2) AS avg_tenure_years FROM employees e JOIN departments d ON d.department_id = e.department_id WHERE d.name = 'Engineering'",
    ),
    (
        "Gender split by department for active employees",
        "SELECT d.name AS department, e.gender, count(*) AS employees FROM employees e JOIN departments d ON d.department_id = e.department_id WHERE e.status = 'active' GROUP BY d.name, e.gender ORDER BY d.name, employees DESC",
    ),
    (
        "Distribution of performance ratings in the latest cycle",
        "SELECT rating, count(*) AS reviews FROM performance_reviews WHERE cycle = (SELECT max(cycle) FROM performance_reviews) GROUP BY rating ORDER BY rating",
    ),
    (
        "Candidates by source and stage for open roles",
        "SELECT c.source, c.stage, count(*) AS candidates FROM candidates c JOIN job_openings o ON o.opening_id = c.opening_id WHERE o.status = 'open' GROUP BY c.source, c.stage ORDER BY c.source, c.stage",
    ),
]
SAMPLE_QA = {
    "hr_onboarding": [
        (
            "How many casual leaves do I get?",
            "You get *12 casual leaves* per calendar year, credited at 1 day per month (Leave Policy, p.1). They can't be carried forward. [1]",
            ["rag"],
        ),
        (
            "What's the notice period?",
            "The notice period is *60 days* after probation and 15 days during probation (Employee Handbook, section 3). [1]",
            ["rag"],
        ),
        (
            "Can I work from home 3 days a week?",
            "Standard WFH is up to *2 days per week*; more than that needs department head approval (WFH & Travel Policy). [1]",
            ["rag"],
        ),
        (
            "What is the capital of France?",
            "I couldn't find that in our HR documents. Please reach out to the HR team for help.",
            ["rag"],
        ),
    ],
    "hr_analytics": [
        (
            "How many active employees do we have?",
            "We currently have *418 active employees*.\n\n| status | employees |\n|---|---|\n| active | 418 |",
            ["sql"],
        ),
        (
            "Attrition rate by department for the last 12 months",
            "Attrition over the last 12 months is highest in Sales (14.2%) and lowest in Finance (5.1%).",
            ["sql"],
        ),
        ("hi", "Hello! Ask me about headcount, attrition, hiring or leave trends.", []),
    ],
    "school_parent_helpdesk": [
        (
            "When is the last date for Q3 fee payment?",
            "The last date for Q3 (October-December) fee payment without late fee is *15 December 2026* (Fee Structure). [1]",
            ["rag"],
        ),
        (
            "Kal school khula hai kya 2 October ko?",
            "Nahi, 2 October (Gandhi Jayanti) ko school *band* rahega (Academic Calendar). [1]",
            ["rag"],
        ),
        (
            "Which bus route covers Gomti Nagar?",
            "Gomti Nagar is covered by *Route 3* (first pick-up 7:05 AM, Rs. 6,000 per quarter). [1]",
            ["rag"],
        ),
    ],
}


async def reset_demo(session: object) -> None:
    for email in ("demo@agentdesk.local", "admin@agentdesk.local"):
        user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            continue
        for m in list(user.memberships):
            org = m.org
            if org.slug.startswith(("northwind-tech", "greenwood-public-school")):
                kbs = (
                    (await session.execute(select(KnowledgeBase).where(KnowledgeBase.org_id == org.id)))
                    .scalars()
                    .all()
                )
                for kb in kbs:
                    await qdrant.delete_kb_points(kb.qdrant_collection, str(org.id), str(kb.id))
                await session.delete(org)
        await session.delete(user)
    await session.flush()


async def load_hr_demo() -> None:
    sql = generate_hr()
    HR_SQL.write_text(sql)
    conn = await asyncpg.connect(HR_DEMO_URL)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


async def ingest_folder(session: object, org: Organization, kb: KnowledgeBase, files: list[Path]) -> None:
    for path in files:
        mime = MIME.get(path.suffix.lower())
        if not mime:
            continue
        doc_id = uuid7()
        key = f"orgs/{org.id}/kb/{kb.id}/{doc_id}/{path.name}"
        data = path.read_bytes()
        await put_bytes(key, data, mime)
        session.add(
            Document(
                id=doc_id,
                org_id=org.id,
                kb_id=kb.id,
                filename=path.name,
                mime_type=mime,
                size_bytes=len(data),
                storage_key=key,
                status="queued",
            )
        )
    await session.flush()


async def main() -> None:
    configure_logging("WARNING", service="seed", env=settings.app_env)
    await ensure_bucket()
    docs = generate_all(ROOT / "demo")
    print("== loading hr_demo Postgres")
    await load_hr_demo()
    async with session_scope() as session:
        await sync_templates(session)
        await reset_demo(session)
        admin = User(
            email="admin@agentdesk.local",
            password_hash=hash_password(ADMIN_PASSWORD),
            name="Platform Admin",
            is_superadmin=True,
            email_verified_at=datetime.now(UTC),
        )
        demo = User(
            email="demo@agentdesk.local",
            password_hash=hash_password(DEMO_PASSWORD),
            name="Simar (Demo)",
            email_verified_at=datetime.now(UTC),
        )
        session.add_all([admin, demo])
        await session.flush()
        northwind = Organization(
            name="Northwind Tech",
            slug="northwind-tech-demo",
            industry="Software",
            size="201-500",
            settings={"retention_days": 180, "tracing_enabled": True},
        )
        greenwood = Organization(
            name="Greenwood Public School",
            slug="greenwood-public-school-demo",
            industry="Education",
            size="51-200",
            settings={"retention_days": 180, "tracing_enabled": True},
        )
        session.add_all([northwind, greenwood])
        await session.flush()
        session.add_all(
            [
                Membership(org_id=northwind.id, user_id=demo.id, role="owner"),
                Membership(org_id=greenwood.id, user_id=demo.id, role="owner"),
                Membership(org_id=northwind.id, user_id=admin.id, role="admin"),
            ]
        )
        emb = settings.default_embedding_model
        dim = dimension(emb)
        collection = qdrant.collection_name(emb, dim)
        await qdrant.ensure_collection(collection, dim)
        nw_kb = KnowledgeBase(
            org_id=northwind.id,
            name="HR Policies",
            description="Employee handbook, leave, WFH, benefits, holidays, IT onboarding",
            embedding_model=emb,
            embedding_dim=dim,
            qdrant_collection=collection,
            chunk_config={},
        )
        gw_kb = KnowledgeBase(
            org_id=greenwood.id,
            name="School Information",
            description="Parent handbook, fees, calendar, admissions, transport, uniform",
            embedding_model=emb,
            embedding_dim=dim,
            qdrant_collection=collection,
            chunk_config={},
        )
        session.add_all([nw_kb, gw_kb])
        await session.flush()
        await ingest_folder(session, northwind, nw_kb, docs["northwind"])
        await ingest_folder(session, greenwood, gw_kb, docs["greenwood"])
        parsed = urlparse(HR_DEMO_URL)
        ds = Dataset(
            org_id=northwind.id,
            name="HRIS (hr_demo)",
            type="postgres",
            status="introspecting",
            allow_sampling=True,
            connection_config={
                "host": HR_DEMO_HOST,
                "port": parsed.port or 5432,
                "database": parsed.path.lstrip("/"),
                "username": parsed.username,
                "sslmode": "disable",
                "schemas": ["public"],
            },
            encrypted_password=encrypt_secret(parsed.password or ""),
        )
        session.add(ds)
        await session.flush()
        ids = {
            "northwind": northwind.id,
            "greenwood": greenwood.id,
            "nw_kb": nw_kb.id,
            "gw_kb": gw_kb.id,
            "ds": ds.id,
            "demo": demo.id,
        }
    print("== ingesting documents (local embeddings; first run downloads the model)")
    async with get_sessionmaker()() as session:
        doc_ids = (
            (await session.execute(select(Document.id).where(Document.status == "queued"))).scalars().all()
        )
    for d in doc_ids:
        await ingest_document(str(d))
        print(f"   ingested {d}")
    print("== introspecting hr_demo and applying reviewed descriptions")
    async with session_scope() as session:
        ds = await session.get(Dataset, ids["ds"])
        assert ds is not None
        await run_introspection(session, ds)
    async with session_scope() as session:
        ds = await session.get(Dataset, ids["ds"])
        assert ds is not None
        tables = (
            (await session.execute(select(DatasetTable).where(DatasetTable.dataset_id == ds.id)))
            .scalars()
            .all()
        )
        for t in tables:
            desc, cols = TABLE_DOCS.get(t.table_name, (None, {}))
            if desc:
                t.description, t.description_source, t.reviewed = desc, "user", True
            for c in t.columns:
                if c.column_name in cols:
                    c.description, c.description_source, c.reviewed = cols[c.column_name], "user", True
                c.hidden = (t.table_name, c.column_name) in HIDDEN_COLUMNS
                if c.hidden:
                    c.semantic_type = "sensitive_personal"
        for term, definition in GLOSSARY:
            session.add(DatasetGlossary(dataset_id=ds.id, term=term, definition=definition))
        ds.status = "ready"
        for question, sql in EXAMPLES:
            ex = DatasetExampleQuery(dataset_id=ds.id, question=question, sql=sql)
            try:
                await execute(ds, sql + (" LIMIT 200" if "limit" not in sql.lower() else ""), 50)
                ex.validated_at = datetime.now(UTC)
            except SqlExecutionError as e:
                ex.last_error = str(e)
                print(f"   example query failed: {question}: {e}")
            session.add(ex)
        print("== creating agents")
        templates = load_templates()
        agents: dict[str, Agent] = {}
        for key, org_id, name in (
            ("hr_onboarding", ids["northwind"], "Northwind HR Buddy"),
            ("hr_analytics", ids["northwind"], "Northwind People Analytics"),
            ("school_parent_helpdesk", ids["greenwood"], "Greenwood Parent Helpdesk"),
        ):
            cfg = AgentConfig.model_validate(template_config(templates[key]))
            if key in ("hr_onboarding", "school_parent_helpdesk"):
                cfg.tools.rag.kb_ids = [ids["nw_kb"] if org_id == ids["northwind"] else ids["gw_kb"]]
            if key == "hr_analytics":
                cfg.tools.sql.dataset_id = ids["ds"]
                cfg.tools.rag.enabled = True
                cfg.tools.rag.kb_ids = [ids["nw_kb"]]
            agent = Agent(
                org_id=org_id,
                name=name,
                description=templates[key]["description"],
                template_key=key,
                draft_config=cfg.model_dump(mode="json"),
                created_by=ids["demo"],
                draft_updated_at=datetime.now(UTC),
            )
            session.add(agent)
            await session.flush()
            await publish_agent(session, org_id, agent, ids["demo"], "Initial version (seeded)")
            agents[key] = agent
        print("== seeding sample conversations, feedback and usage")
        rng = random.Random(7)
        now = datetime.now(UTC)
        for key, agent in agents.items():
            for day in range(14):
                for question, answer, tools in SAMPLE_QA[key]:
                    if rng.random() < 0.6:
                        continue
                    ts = now - timedelta(days=day, hours=rng.randint(0, 20), minutes=rng.randint(0, 59))
                    conv = Conversation(
                        org_id=agent.org_id,
                        agent_id=agent.id,
                        agent_version_id=agent.latest_version_id,
                        channel="playground",
                        playground_user_id=ids["demo"],
                        started_at=ts,
                        last_message_at=ts + timedelta(seconds=8),
                        message_count=2,
                        flags={"no_answer": "couldn't find" in answer},
                    )
                    session.add(conv)
                    await session.flush()
                    run_id = uuid.uuid4().hex[:24]
                    session.add(
                        Message(
                            org_id=agent.org_id,
                            conversation_id=conv.id,
                            role="user",
                            content=question,
                            run_id=run_id,
                            created_at=ts,
                        )
                    )
                    latency = rng.randint(900, 6500)
                    m = Message(
                        org_id=agent.org_id,
                        conversation_id=conv.id,
                        role="assistant",
                        content=answer,
                        run_id=run_id,
                        status="sent",
                        route={"tools": tools, "reason": "seeded"},
                        tool_summary=(
                            "KB: 3 chunks" if "rag" in tools else "SQL: 8 rows" if "sql" in tools else None
                        ),
                        citations=[],
                        guardrail_events=[],
                        latency_ms=latency,
                        created_at=ts + timedelta(seconds=8),
                    )
                    session.add(m)
                    await session.flush()
                    session.add(
                        RunTrace(
                            org_id=agent.org_id,
                            message_id=m.id,
                            run_id=run_id,
                            node_timings={"generate_answer": latency / 1000},
                            sql_attempts=[{"sql": "SELECT count(*) FROM employees", "error": None, "rows": 1}]
                            if "sql" in tools
                            else [],
                            created_at=ts,
                        )
                    )
                    session.add(
                        UsageEvent(
                            org_id=agent.org_id,
                            agent_id=agent.id,
                            conversation_id=conv.id,
                            run_id=run_id,
                            kind="llm",
                            provider="openai",
                            model="gpt-5-mini",
                            input_tokens=rng.randint(900, 3000),
                            output_tokens=rng.randint(80, 400),
                            est_cost_usd=round(rng.uniform(0.0008, 0.004), 6),
                            created_at=ts,
                        )
                    )
                    session.add(
                        UsageEvent(
                            org_id=agent.org_id,
                            agent_id=agent.id,
                            conversation_id=conv.id,
                            run_id=run_id,
                            kind="message_in",
                            created_at=ts,
                        )
                    )
                    session.add(
                        UsageEvent(
                            org_id=agent.org_id,
                            agent_id=agent.id,
                            conversation_id=conv.id,
                            run_id=run_id,
                            kind="message_out",
                            created_at=ts,
                        )
                    )
                    if rng.random() < 0.4:
                        session.add(
                            Feedback(
                                org_id=agent.org_id,
                                message_id=m.id,
                                user_id=ids["demo"],
                                rating=1 if rng.random() < 0.8 else -1,
                                created_at=ts,
                            )
                        )
        await session.execute(delete(Message).where(Message.content == "__never__"))
    print("\nSeed complete.")
    print(
        f"  Demo owner:  demo@agentdesk.local / {DEMO_PASSWORD}  (orgs: Northwind Tech, Greenwood Public School)"
    )
    print(f"  Superadmin:  admin@agentdesk.local / {ADMIN_PASSWORD}")
    print(
        f"  Documents:   {len(docs['northwind'])} Northwind + {len(docs['greenwood'])} Greenwood files in demo/"
    )
    print(f"  HR dataset:  {HR_DEMO_URL} ({HR_SQL.relative_to(ROOT)})")


if __name__ == "__main__":
    asyncio.run(main())
