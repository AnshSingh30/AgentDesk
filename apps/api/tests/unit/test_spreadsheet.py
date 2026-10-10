"""Spreadsheet datasets: header detection, name sanitising, type inference, DuckDB import."""

from __future__ import annotations

import io
from pathlib import Path

import duckdb
import pandas as pd
from app.sql.spreadsheet import build_duckdb, preview, sanitize_column

CSV = b"""Quarterly headcount report,,,
,,,
Employee Name,Dept.,Hire Date,Salary (INR)
Asha Rao,Engineering,2023-04-01,1200000
Vikram Shah,Sales,2024-01-15,900000
Neha Iyer,Engineering,2022-11-20,not disclosed
"""


def test_sanitize_column_names() -> None:
    used: set[str] = set()
    assert sanitize_column("Salary (INR)", used) == "salary_inr"
    assert sanitize_column("Salary (INR)", used) == "salary_inr_2"  # deduplicated
    assert sanitize_column("2024 total", used) == "c_2024_total"  # no leading digit
    assert sanitize_column("select", used) == "select_col"  # reserved word
    assert sanitize_column("  ", used) == "column_col"  # empty -> "column", itself reserved


def test_preview_detects_header_and_types() -> None:
    sheet = preview(CSV, "report.csv")["sheets"][0]
    cols = {c["name"]: c["type"] for c in sheet["columns"]}
    assert list(cols) == ["employee_name", "dept", "hire_date", "salary_inr"]
    assert cols["hire_date"] in ("date", "datetime")
    assert sheet["row_count"] == 3


def test_build_duckdb_imports_rows_and_counts_bad_casts(tmp_path: Path) -> None:
    plan = preview(CSV, "report.csv")
    for c in plan["sheets"][0]["columns"]:
        if c["name"] == "salary_inr":
            c["type"] = "int"  # "not disclosed" can't be cast
    out = tmp_path / "ds.duckdb"
    report = build_duckdb(CSV, "report.csv", plan, str(out))
    table = report["tables"][0]
    assert table["rows"] == 3 and table["failed_casts"] == 1 and report["warnings"]
    con = duckdb.connect(str(out), read_only=True)
    try:
        rows = con.execute(f'SELECT dept, count(*) FROM "{table["table"]}" GROUP BY 1 ORDER BY 1').fetchall()
    finally:
        con.close()
    assert rows == [("Engineering", 2), ("Sales", 1)]


def test_xlsx_sheets_become_tables(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        pd.DataFrame({"Route": ["R1", "R2"], "Fee": [9000, 7500]}).to_excel(
            xl, sheet_name="Bus Routes", index=False
        )
        pd.DataFrame({"Class": ["1-5"], "Total": [75000]}).to_excel(xl, sheet_name="Fees", index=False)
    plan = preview(buf.getvalue(), "school.xlsx")
    assert [s["table_name"] for s in plan["sheets"]] == ["bus_routes", "fees"]
    report = build_duckdb(buf.getvalue(), "school.xlsx", plan, str(tmp_path / "s.duckdb"))
    assert {t["table"]: t["rows"] for t in report["tables"]} == {"bus_routes": 2, "fees": 1}
