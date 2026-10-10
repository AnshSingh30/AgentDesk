"""Generate the synthetic hr_demo Postgres schema + data (PRD §22.2): ~500 employees, 3 years, deterministic seed."""

from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "demo" / "hr_demo.sql"
R = random.Random(42)
TODAY = date(2026, 9, 16)
START = date(2023, 9, 1)

FIRST = [
    "Aarav",
    "Vivaan",
    "Aditya",
    "Vihaan",
    "Arjun",
    "Sai",
    "Reyansh",
    "Ayaan",
    "Krishna",
    "Ishaan",
    "Ananya",
    "Diya",
    "Aadhya",
    "Saanvi",
    "Myra",
    "Anika",
    "Kiara",
    "Navya",
    "Pari",
    "Riya",
    "Rahul",
    "Priya",
    "Neha",
    "Amit",
    "Sneha",
    "Rohit",
    "Pooja",
    "Karan",
    "Meera",
    "Nikhil",
    "Sana",
    "Farhan",
    "Zara",
    "Imran",
    "Kavya",
    "Dev",
    "Tara",
    "Kabir",
    "Ira",
    "Yash",
    "John",
    "Maria",
    "David",
    "Sarah",
    "Wei",
    "Li",
    "Ahmed",
]
LAST = [
    "Sharma",
    "Verma",
    "Gupta",
    "Iyer",
    "Nair",
    "Reddy",
    "Rao",
    "Menon",
    "Patel",
    "Shah",
    "Singh",
    "Kaur",
    "Khan",
    "Ali",
    "Das",
    "Bose",
    "Chatterjee",
    "Mukherjee",
    "Joshi",
    "Kulkarni",
    "Deshpande",
    "Pillai",
    "Naidu",
    "Mehta",
    "Agarwal",
    "Mishra",
    "Pandey",
    "Tiwari",
    "Yadav",
    "Chauhan",
    "Fernandes",
    "D'Souza",
    "Thomas",
    "George",
    "Mathew",
    "Zhang",
    "Wang",
    "Hussain",
]
DEPARTMENTS = [
    (1, "Engineering", "CC-100"),
    (2, "Product", "CC-110"),
    (3, "Sales", "CC-200"),
    (4, "Customer Success", "CC-210"),
    (5, "Marketing", "CC-220"),
    (6, "Finance", "CC-300"),
    (7, "People Operations", "CC-310"),
    (8, "IT", "CC-320"),
]
DEPT_WEIGHTS = [38, 8, 16, 12, 8, 6, 6, 6]
LOCATIONS = [
    (1, "Bengaluru", "India"),
    (2, "Pune", "India"),
    (3, "Hyderabad", "India"),
    (4, "Gurugram", "India"),
]
TITLES = {
    1: [
        "Software Engineer",
        "Senior Software Engineer",
        "Staff Engineer",
        "Engineering Manager",
        "QA Engineer",
        "DevOps Engineer",
    ],
    2: ["Product Manager", "Senior Product Manager", "Product Designer"],
    3: ["Account Executive", "Sales Development Rep", "Sales Manager"],
    4: ["Customer Success Manager", "Support Engineer", "Implementation Specialist"],
    5: ["Marketing Manager", "Content Specialist", "Growth Analyst"],
    6: ["Accountant", "Financial Analyst", "Finance Manager"],
    7: ["HR Business Partner", "Recruiter", "People Ops Specialist"],
    8: ["IT Support Engineer", "Systems Administrator", "Security Analyst"],
}
LEVELS = ["L1", "L2", "L3", "L4", "L5", "L6"]
LEVEL_SALARY = {
    "L1": (600000, 900000),
    "L2": (900000, 1400000),
    "L3": (1400000, 2200000),
    "L4": (2200000, 3400000),
    "L5": (3400000, 5000000),
    "L6": (5000000, 8000000),
}
EXIT_REASONS = [
    "Better opportunity",
    "Relocation",
    "Higher studies",
    "Personal reasons",
    "Performance",
    "Compensation",
    "Restructuring",
]
SOURCES = ["LinkedIn", "Referral", "Naukri", "Campus", "Agency", "Careers page"]
STAGES = ["applied", "screen", "interview", "offer", "hired", "rejected"]


def rd(a: date, b: date) -> date:
    return a + timedelta(days=R.randint(0, max(0, (b - a).days)))


def q(v: object) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int | float):
        return str(v)
    if isinstance(v, date):
        return f"'{v.isoformat()}'"
    return "'" + str(v).replace("'", "''") + "'"


def insert(name: str, cols: list[str], rows: list[list[object]], out: list[str], batch: int = 500) -> None:
    for i in range(0, len(rows), batch):
        values = ",\n".join("(" + ", ".join(q(v) for v in r) + ")" for r in rows[i : i + batch])
        out.append(f"INSERT INTO {name} ({', '.join(cols)}) VALUES\n{values};")


def generate() -> str:
    out: list[str] = [
        "-- hr_demo: synthetic HR data for the AgentDesk demo (generated, fictional).",
        "DROP TABLE IF EXISTS performance_reviews, candidates, job_openings, attendance, leave_requests, compensation, employees, locations, departments CASCADE;",
        """CREATE TABLE departments (department_id INT PRIMARY KEY, name TEXT NOT NULL, cost_center TEXT, head_employee_id INT);
CREATE TABLE locations (location_id INT PRIMARY KEY, city TEXT NOT NULL, country TEXT NOT NULL);
CREATE TABLE employees (
  employee_id INT PRIMARY KEY, full_name TEXT NOT NULL, gender TEXT, date_of_birth DATE,
  department_id INT REFERENCES departments(department_id), location_id INT REFERENCES locations(location_id),
  job_title TEXT, level TEXT, manager_id INT REFERENCES employees(employee_id), employment_type TEXT,
  hire_date DATE NOT NULL, exit_date DATE, exit_reason TEXT, status TEXT NOT NULL);
CREATE TABLE compensation (employee_id INT REFERENCES employees(employee_id), effective_date DATE NOT NULL, base_salary NUMERIC(12,2) NOT NULL, currency TEXT NOT NULL DEFAULT 'INR');
CREATE TABLE leave_requests (leave_id INT PRIMARY KEY, employee_id INT REFERENCES employees(employee_id), leave_type TEXT, start_date DATE, end_date DATE, days NUMERIC(4,1), status TEXT);
CREATE TABLE attendance (employee_id INT REFERENCES employees(employee_id), date DATE NOT NULL, status TEXT NOT NULL);
CREATE TABLE job_openings (opening_id INT PRIMARY KEY, department_id INT REFERENCES departments(department_id), title TEXT, opened_at DATE, closed_at DATE, status TEXT);
CREATE TABLE candidates (candidate_id INT PRIMARY KEY, opening_id INT REFERENCES job_openings(opening_id), source TEXT, stage TEXT, applied_at DATE, stage_updated_at DATE);
CREATE TABLE performance_reviews (review_id INT PRIMARY KEY, employee_id INT REFERENCES employees(employee_id), cycle TEXT, rating INT, reviewer_id INT);
COMMENT ON TABLE employees IS 'One row per employee, current and former';
COMMENT ON COLUMN employees.status IS 'active, exited or on_leave';
COMMENT ON COLUMN employees.exit_date IS 'Last working day; NULL while employed';
COMMENT ON TABLE compensation IS 'Salary history; the latest effective_date per employee is the current salary';
COMMENT ON TABLE leave_requests IS 'Leave applications with status approved, rejected or pending';
COMMENT ON TABLE attendance IS 'Daily attendance for the last 60 days: present, absent, wfh or leave';
COMMENT ON TABLE candidates IS 'Hiring funnel: one row per candidate per opening';
CREATE INDEX ON employees(department_id); CREATE INDEX ON leave_requests(employee_id); CREATE INDEX ON attendance(employee_id, date);
CREATE INDEX ON candidates(opening_id); CREATE INDEX ON compensation(employee_id, effective_date);""",
    ]
    insert(
        "departments", ["department_id", "name", "cost_center"], [[d, n, c] for d, n, c in DEPARTMENTS], out
    )
    insert("locations", ["location_id", "city", "country"], [list(loc) for loc in LOCATIONS], out)
    employees: list[list[object]] = []
    emp_dept: dict[int, int] = {}
    emp_level: dict[int, str] = {}
    emp_hire: dict[int, date] = {}
    emp_exit: dict[int, date | None] = {}
    n = 520
    for eid in range(1, n + 1):
        dept = R.choices([d[0] for d in DEPARTMENTS], weights=DEPT_WEIGHTS)[0]
        gender = R.choices(["female", "male", "other"], weights=[42, 56, 2])[0]
        name = f"{R.choice(FIRST)} {R.choice(LAST)}"
        hire = (
            rd(date(2016, 1, 1), TODAY - timedelta(days=20))
            if eid <= 200
            else rd(START, TODAY - timedelta(days=20))
        )
        level = R.choices(LEVELS, weights=[25, 30, 22, 13, 7, 3])[0]
        exit_d: date | None = None
        reason = None
        if R.random() < (0.22 if hire < START else 0.16):
            exit_d = rd(max(hire + timedelta(days=90), START), TODAY)
            reason = R.choice(EXIT_REASONS)
        status = "exited" if exit_d else ("on_leave" if R.random() < 0.03 else "active")
        dob = rd(date(1968, 1, 1), date(2003, 12, 31))
        etype = R.choices(["full_time", "contract", "intern"], weights=[88, 9, 3])[0]
        employees.append(
            [
                eid,
                name,
                gender,
                dob,
                dept,
                R.randint(1, 4),
                R.choice(TITLES[dept]),
                level,
                None,
                etype,
                hire,
                exit_d,
                reason,
                status,
            ]
        )
        emp_dept[eid], emp_level[eid], emp_hire[eid], emp_exit[eid] = dept, level, hire, exit_d
    managers = {
        d: [e[0] for e in employees if e[4] == d and e[7] in ("L4", "L5", "L6") and e[11] is None][:6]
        for d in emp_dept.values()
    }
    for e in employees:
        pool = managers.get(e[4]) or [1]
        cand = [m for m in pool if m != e[0]]
        e[8] = R.choice(cand) if cand else None
    insert(
        "employees",
        [
            "employee_id",
            "full_name",
            "gender",
            "date_of_birth",
            "department_id",
            "location_id",
            "job_title",
            "level",
            "manager_id",
            "employment_type",
            "hire_date",
            "exit_date",
            "exit_reason",
            "status",
        ],
        employees,
        out,
    )
    out.append(
        "UPDATE departments d SET head_employee_id = (SELECT MIN(employee_id) FROM employees e WHERE e.department_id = d.department_id AND e.level IN ('L5','L6') AND e.status='active');"
    )
    comp: list[list[object]] = []
    for eid in range(1, n + 1):
        lo, hi = LEVEL_SALARY[emp_level[eid]]
        base = R.randint(lo // 10000, hi // 10000) * 10000
        eff = emp_hire[eid]
        comp.append([eid, eff, base, "INR"])
        for _ in range(R.randint(0, 2)):
            eff = eff + timedelta(days=365)
            if eff > TODAY or (emp_exit[eid] and eff > emp_exit[eid]):
                break
            base = int(base * R.uniform(1.06, 1.15) // 1000 * 1000)
            comp.append([eid, eff, base, "INR"])
    insert("compensation", ["employee_id", "effective_date", "base_salary", "currency"], comp, out)
    leaves: list[list[object]] = []
    lid = 1
    for eid in range(1, n + 1):
        last = emp_exit[eid] or TODAY
        for _ in range(R.randint(2, 12)):
            s = rd(max(emp_hire[eid], START), last)
            days = R.choices([0.5, 1, 2, 3, 5], weights=[10, 45, 25, 12, 8])[0]
            ltype = R.choices(
                ["casual", "sick", "earned", "maternity", "paternity"], weights=[45, 30, 22, 1, 2]
            )[0]
            status = R.choices(["approved", "rejected", "pending"], weights=[86, 6, 8])[0]
            leaves.append(
                [lid, eid, ltype, s, s + timedelta(days=int(days) - 1 if days >= 1 else 0), days, status]
            )
            lid += 1
    insert(
        "leave_requests",
        ["leave_id", "employee_id", "leave_type", "start_date", "end_date", "days", "status"],
        leaves,
        out,
    )
    att: list[list[object]] = []
    active = [eid for eid in range(1, n + 1) if emp_exit[eid] is None]
    day = TODAY - timedelta(days=60)
    while day <= TODAY:
        if day.weekday() < 5:
            for eid in active:
                if emp_hire[eid] <= day:
                    att.append(
                        [
                            eid,
                            day,
                            R.choices(["present", "wfh", "leave", "absent"], weights=[62, 30, 6, 2])[0],
                        ]
                    )
        day += timedelta(days=1)
    insert("attendance", ["employee_id", "date", "status"], att, out, batch=2000)
    openings: list[list[object]] = []
    for oid in range(1, 46):
        dept = R.choices([d[0] for d in DEPARTMENTS], weights=DEPT_WEIGHTS)[0]
        opened = rd(START, TODAY - timedelta(days=10))
        closed = opened + timedelta(days=R.randint(20, 90)) if R.random() < 0.7 else None
        if closed and closed > TODAY:
            closed = None
        openings.append([oid, dept, R.choice(TITLES[dept]), opened, closed, "closed" if closed else "open"])
    insert(
        "job_openings",
        ["opening_id", "department_id", "title", "opened_at", "closed_at", "status"],
        openings,
        out,
    )
    cands: list[list[object]] = []
    cid = 1
    for o in openings:
        for _ in range(R.randint(5, 16)):
            applied = rd(o[3], o[4] or TODAY)
            stage = R.choices(STAGES, weights=[20, 25, 20, 8, 7, 20])[0]
            if o[5] == "closed" and stage in ("applied", "screen", "interview", "offer"):
                stage = R.choice(["rejected", "hired"])
            cands.append(
                [cid, o[0], R.choice(SOURCES), stage, applied, applied + timedelta(days=R.randint(1, 30))]
            )
            cid += 1
    insert(
        "candidates",
        ["candidate_id", "opening_id", "source", "stage", "applied_at", "stage_updated_at"],
        cands,
        out,
    )
    reviews: list[list[object]] = []
    rid = 1
    for cycle, cutoff in (
        ("2024-H1", date(2024, 4, 1)),
        ("2024-H2", date(2024, 10, 1)),
        ("2025-H1", date(2025, 4, 1)),
        ("2025-H2", date(2025, 10, 1)),
        ("2026-H1", date(2026, 4, 1)),
    ):
        for eid in range(1, n + 1):
            if emp_hire[eid] < cutoff - timedelta(days=120) and (
                emp_exit[eid] is None or emp_exit[eid] > cutoff
            ):
                mgr = next((e[8] for e in employees if e[0] == eid), None)
                reviews.append(
                    [rid, eid, cycle, R.choices([1, 2, 3, 4, 5], weights=[3, 12, 45, 32, 8])[0], mgr]
                )
                rid += 1
    insert(
        "performance_reviews", ["review_id", "employee_id", "cycle", "rating", "reviewer_id"], reviews, out
    )
    out.append("ANALYZE;")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(generate(), encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
    sys.exit(0)
