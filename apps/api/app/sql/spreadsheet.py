"""Spreadsheet datasets: XLSX/CSV -> preview -> typed DuckDB file (PRD §11.6, DS-12/13)."""

from __future__ import annotations

import io
import re
from typing import Any

import pandas as pd

RESERVED = {
    "select",
    "from",
    "where",
    "table",
    "order",
    "group",
    "by",
    "limit",
    "join",
    "on",
    "as",
    "and",
    "or",
    "not",
    "null",
    "user",
    "date",
    "time",
    "year",
    "month",
    "day",
    "index",
    "key",
    "default",
    "values",
    "into",
    "update",
    "delete",
    "insert",
    "create",
    "drop",
    "alter",
    "desc",
    "asc",
    "case",
    "when",
    "then",
    "else",
    "end",
    "in",
    "is",
    "like",
    "between",
    "union",
    "all",
    "distinct",
    "having",
    "with",
    "over",
    "partition",
    "primary",
    "foreign",
    "references",
    "check",
    "constraint",
    "cast",
    "column",
    "row",
    "rows",
    "offset",
    "fetch",
    "first",
    "last",
    "only",
    "using",
    "natural",
    "cross",
    "inner",
    "outer",
    "left",
    "right",
    "full",
    "exists",
    "any",
    "some",
    "true",
    "false",
    "unknown",
    "type",
    "count",
    "sum",
    "avg",
    "min",
    "max",
}
MAX_SHEETS = 20
MAX_ROWS = 1_000_000
TYPES = ("int", "float", "bool", "date", "datetime", "text")


def sanitize_column(name: Any, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_") or "column"
    if base[0].isdigit():
        base = f"c_{base}"
    if base in RESERVED:
        base = f"{base}_col"
    candidate, i = base, 2
    while candidate in used:
        candidate = f"{base}_{i}"
        i += 1
    used.add(candidate)
    return candidate


def sanitize_table(name: str, used: set[str]) -> str:
    return sanitize_column(name, used)


def detect_header_row(df: pd.DataFrame) -> int:
    for i in range(min(10, len(df))):
        row = df.iloc[i]
        non_null = row.dropna()
        if len(non_null) == 0:
            continue
        strings = sum(1 for v in non_null if isinstance(v, str) and not _is_number(v))
        if len(non_null) >= max(1, int(df.shape[1] * 0.5)) and strings / len(non_null) >= 0.6:
            return i
    return 0


def _is_number(v: Any) -> bool:
    try:
        float(str(v).replace(",", ""))
        return True
    except ValueError:
        return False


def infer_type(series: pd.Series) -> str:
    s = series.dropna()
    if len(s) == 0:
        return "text"
    lowered = {str(v).strip().lower() for v in s.head(200)}
    if lowered <= {"true", "false", "yes", "no", "y", "n", "0", "1"} and lowered & {
        "true",
        "false",
        "yes",
        "no",
        "y",
        "n",
    }:
        return "bool"
    numeric = pd.to_numeric(s.astype(str).str.replace(",", "", regex=False), errors="coerce")
    if numeric.notna().mean() >= 0.95:
        return "int" if (numeric.dropna() % 1 == 0).all() and numeric.abs().max() < 9e18 else "float"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    parsed = pd.to_datetime(s.astype(str).head(300), errors="coerce", format="mixed")
    if parsed.notna().mean() >= 0.9:
        has_time = parsed.dropna().dt.normalize().ne(parsed.dropna()).any()
        return "datetime" if has_time else "date"
    return "text"


def read_workbook(data: bytes, filename: str) -> dict[str, pd.DataFrame]:
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None)
        return dict(list(sheets.items())[:MAX_SHEETS])
    from charset_normalizer import from_bytes

    best = from_bytes(data).best()
    text = str(best) if best else data.decode("utf-8", errors="replace")
    df = pd.read_csv(
        io.StringIO(text), header=None, sep=None, engine="python", dtype=object, skip_blank_lines=True
    )
    return {re.sub(r"\.csv$", "", filename, flags=re.I): df}


def preview(data: bytes, filename: str) -> dict[str, Any]:
    sheets = read_workbook(data, filename)
    out: list[dict[str, Any]] = []
    used_tables: set[str] = set()
    for sheet_name, raw in sheets.items():
        raw = raw.dropna(how="all").dropna(axis=1, how="all").reset_index(drop=True)
        if raw.empty:
            continue
        header_row = detect_header_row(raw)
        headers = list(raw.iloc[header_row])
        body = raw.iloc[header_row + 1 :].reset_index(drop=True)
        used_cols: set[str] = set()
        columns = []
        for idx, h in enumerate(headers):
            col_name = sanitize_column(h if pd.notna(h) else f"column_{idx + 1}", used_cols)
            series = body.iloc[:, idx] if idx < body.shape[1] else pd.Series(dtype=object)
            columns.append(
                {
                    "source_index": idx,
                    "source_name": str(h) if pd.notna(h) else "",
                    "name": col_name,
                    "type": infer_type(series),
                    "samples": [str(v)[:40] for v in series.dropna().head(3)],
                }
            )
        out.append(
            {
                "name": str(sheet_name),
                "table_name": sanitize_table(str(sheet_name), used_tables),
                "header_row": header_row,
                "row_count": int(len(body)),
                "columns": columns,
                "rows": [
                    [None if pd.isna(v) else str(v)[:120] for v in row]
                    for row in body.head(50).itertuples(index=False)
                ],
                "warnings": (
                    [f"Sheet has {len(body)} rows; only the first {MAX_ROWS} will be imported"]
                    if len(body) > MAX_ROWS
                    else []
                ),
            }
        )
    return {"sheets": out, "filename": filename}


def _cast(series: pd.Series, typ: str) -> tuple[pd.Series, int]:
    before = series.notna().sum()
    if typ == "int":
        s = (
            pd.to_numeric(
                series.astype(str).str.replace(",", "", regex=False).where(series.notna()), errors="coerce"
            )
            .round()
            .astype("Int64")
        )
    elif typ == "float":
        s = pd.to_numeric(
            series.astype(str).str.replace(",", "", regex=False).where(series.notna()), errors="coerce"
        ).astype("Float64")
    elif typ == "bool":
        mapping = {
            "true": True,
            "yes": True,
            "y": True,
            "1": True,
            "false": False,
            "no": False,
            "n": False,
            "0": False,
        }
        s = series.map(lambda v: mapping.get(str(v).strip().lower()) if pd.notna(v) else None).astype(
            "boolean"
        )
    elif typ in ("date", "datetime"):
        s = pd.to_datetime(series.astype(str).where(series.notna()), errors="coerce", format="mixed")
        if typ == "date":
            s = s.dt.normalize()
    else:
        s = series.map(lambda v: None if pd.isna(v) else str(v)).astype("string")
    return s, int(before - s.notna().sum())


def build_duckdb(data: bytes, filename: str, plan: dict[str, Any], out_path: str) -> dict[str, Any]:
    """plan = {"sheets": [{"name", "table_name", "header_row", "include", "columns": [{"source_index","name","type"}]}]}"""
    import duckdb

    sheets = read_workbook(data, filename)
    report: dict[str, Any] = {"tables": [], "warnings": []}
    con = duckdb.connect(out_path)
    try:
        used_tables: set[str] = set()
        for spec in plan.get("sheets", []):
            if not spec.get("include", True):
                continue
            raw = sheets.get(spec["name"])
            if raw is None:
                report["warnings"].append(f"Sheet '{spec['name']}' not found")
                continue
            raw = raw.dropna(how="all").dropna(axis=1, how="all").reset_index(drop=True)
            header_row = int(spec.get("header_row", 0))
            body = raw.iloc[header_row + 1 :].reset_index(drop=True).head(MAX_ROWS)
            table = sanitize_table(spec.get("table_name") or spec["name"], used_tables)
            frame = pd.DataFrame()
            used_cols: set[str] = set()
            failed = 0
            for col in spec.get("columns", []):
                idx = int(col["source_index"])
                if idx >= body.shape[1]:
                    continue
                name = sanitize_column(col.get("name") or f"column_{idx + 1}", used_cols)
                typ = col.get("type") if col.get("type") in TYPES else "text"
                series, bad = _cast(body.iloc[:, idx], typ)
                failed += bad
                frame[name] = series
            if frame.empty:
                continue
            con.register("frame_df", frame)
            # table comes from sanitize_table ([a-z0-9_] only)
            con.execute(f'CREATE OR REPLACE TABLE "{table}" AS SELECT * FROM frame_df')  # nosec B608
            con.unregister("frame_df")
            report["tables"].append(
                {
                    "table": table,
                    "rows": int(len(frame)),
                    "columns": list(frame.columns),
                    "failed_casts": failed,
                }
            )
            if failed:
                report["warnings"].append(
                    f"{table}: {failed} value(s) could not be converted and were set to null"
                )
    finally:
        con.close()
    return report
