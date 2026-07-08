"""One-time AI table profiling — columns, sample data, date ranges, query guidance.

Runs once per table (and on demand), stores results on WorkspaceTable.ai_overview /
ai_profile_json so SQL generation always knows real data characteristics
(e.g. "this table only covers Jan-Jun 2026") — including tables added in future.
"""
from __future__ import annotations

import json
from typing import Any

import bq
import config
import llm

_DATE_TYPES = {"DATE", "DATETIME", "TIMESTAMP"}
_MAX_DATE_COLS = 4
_MAX_CATEGORY_COLS = 5
_SAMPLE_ROWS = 8


def _stats_sql(full_table_id: str, columns: list[dict]) -> tuple[str, list[str]]:
    """Single aggregate query: row count + MIN/MAX of every date-like column."""
    date_cols = [
        c["name"] for c in columns if (c.get("type") or "").upper() in _DATE_TYPES
    ][:_MAX_DATE_COLS]
    parts = ["COUNT(*) AS __total_rows"]
    for col in date_cols:
        parts.append(f"MIN(`{col}`) AS __min_{col}")
        parts.append(f"MAX(`{col}`) AS __max_{col}")
    sql = f"SELECT {', '.join(parts)} FROM `{full_table_id}`"
    return sql, date_cols


def _category_sql(full_table_id: str, col: str) -> str:
    return (
        f"SELECT CAST(`{col}` AS STRING) AS v, COUNT(*) AS n "
        f"FROM `{full_table_id}` GROUP BY v ORDER BY n DESC LIMIT 8"
    )


def _pick_category_columns(columns: list[dict]) -> list[str]:
    """Short STRING columns likely to be categorical (status, type, source...)."""
    out = []
    for c in columns:
        name = c["name"].lower()
        if (c.get("type") or "").upper() != "STRING":
            continue
        if any(k in name for k in ("status", "type", "category", "source", "stage", "gender", "state", "mode")):
            out.append(c["name"])
    return out[:_MAX_CATEGORY_COLS]


def profile_table(full_table_id: str) -> dict[str, Any]:
    """Collect metadata + samples + stats. Raises on access errors."""
    meta = bq.table_metadata(full_table_id)
    columns = meta.get("columns") or []

    profile: dict[str, Any] = {
        "full_table_id": full_table_id,
        "table_type": meta.get("table_type"),
        "num_rows_metadata": meta.get("num_rows"),
        "description": meta.get("description") or "",
        "columns": [
            {"name": c["name"], "type": c.get("type", ""), "description": c.get("description", "")}
            for c in columns
        ],
    }

    # Row count + date ranges in one scan of only those columns.
    try:
        sql, date_cols = _stats_sql(full_table_id, columns)
        df = bq.run_query(sql, max_bytes_billed=config.PREVIEW_MAX_BYTES_BILLED)
        row = df.iloc[0].to_dict() if len(df) else {}
        profile["total_rows"] = int(row.get("__total_rows") or 0)
        ranges = {}
        for col in date_cols:
            mn, mx = row.get(f"__min_{col}"), row.get(f"__max_{col}")
            if mn is not None:
                ranges[col] = {"min": str(mn), "max": str(mx)}
        profile["date_ranges"] = ranges
    except Exception as e:
        profile["stats_error"] = str(e)[:300]

    # Sample rows (reuses smart preview logic for sparse views).
    try:
        df, note = bq.preview_table(full_table_id, limit=_SAMPLE_ROWS)
        profile["sample_rows"] = json.loads(df.to_json(orient="records", date_format="iso"))
        if note:
            profile["sample_note"] = note
    except Exception as e:
        profile["sample_error"] = str(e)[:300]

    # Top values for likely-categorical columns (small GROUP BY scans).
    categories: dict[str, list] = {}
    for col in _pick_category_columns(columns):
        try:
            df = bq.run_query(
                _category_sql(full_table_id, col),
                max_bytes_billed=config.PREVIEW_MAX_BYTES_BILLED,
            )
            categories[col] = json.loads(df.to_json(orient="records"))
        except Exception:
            continue
    if categories:
        profile["category_values"] = categories

    return profile


_OVERVIEW_SYSTEM = (
    "You are a data catalog expert writing an AI-facing table overview used to "
    "generate correct BigQuery SQL. Given a table profile (columns, sample rows, "
    "row count, date ranges, category values), write a compact overview covering: "
    "1) What the table contains (one sentence). "
    "2) DATA COVERAGE: exact date range per date column (critical — queries outside "
    "this range return 0 rows; say so explicitly). "
    "3) Key columns: identifiers for joins, primary date, main metrics, text/feedback fields. "
    "4) Category values worth filtering on (exact strings). "
    "5) Query guidance: date filter format, common aggregations, join keys, gotchas "
    "(e.g. month stored as first-of-month DATE). "
    "6) TOPIC SEARCH: if the table has free-text feedback columns, include the exact "
    "expression for finding topic mentions across all of them: "
    "REGEXP_CONTAINS(CONCAT(COALESCE(col1,''),' ',COALESCE(col2,''),...), r'(?i)(topic)') "
    "with every text column listed by real name — questions like 'responses about X' "
    "must use this, never equality on one column. "
    "Plain text with short headers, under 400 words. Be precise; never invent values."
)


def enrich_profile_with_catalog(table, profile: dict[str, Any]) -> dict[str, Any]:
    """Merge workspace catalog metadata (YAML import) into a BQ profile."""
    model_desc = (getattr(table, "description", None) or "").strip()
    if model_desc:
        profile["model_description"] = model_desc
    try:
        catalog_cols = json.loads(getattr(table, "column_descriptions_json", None) or "{}")
    except json.JSONDecodeError:
        catalog_cols = {}
    if catalog_cols:
        profile["column_descriptions"] = catalog_cols
        by_name = {c["name"]: c for c in profile.get("columns") or []}
        for col_name, desc in catalog_cols.items():
            if col_name in by_name and desc:
                by_name[col_name]["description"] = desc
    return profile


def generate_overview(profile: dict[str, Any]) -> str:
    extra = ""
    if profile.get("model_description"):
        extra = (
            "IMPORTANT: model_description is authoritative business context from the "
            "data team — prioritize it over BigQuery metadata descriptions.\n\n"
        )
    prompt = (
        f"{extra}"
        f"Table profile JSON:\n{json.dumps(profile, default=str)[:8000]}\n\n"
        "Write the AI table overview:"
    )
    return llm._viz(prompt, system=_OVERVIEW_SYSTEM, temperature=0.2)


def profile_and_overview(full_table_id: str) -> tuple[dict[str, Any], str]:
    profile = profile_table(full_table_id)
    overview = generate_overview(profile)
    return profile, overview


def coverage_note_for_tables(tables: list[Any]) -> str:
    """Human-readable data-coverage line from stored profiles (for 0-row answers)."""
    parts: list[str] = []
    for t in tables:
        try:
            profile = json.loads(getattr(t, "ai_profile_json", "") or "{}")
        except json.JSONDecodeError:
            continue
        ranges = profile.get("date_ranges") or {}
        if not ranges:
            continue
        short = t.full_table_id.rsplit(".", 1)[-1]
        spans = ", ".join(
            f"{col}: {r['min'][:10]} → {r['max'][:10]}" for col, r in ranges.items()
        )
        parts.append(f"`{short}` has data for {spans}")
    if not parts:
        return ""
    return "Data coverage: " + "; ".join(parts) + ". Queries outside these ranges return 0 rows."


def ensure_table_overview(db, table, *, force: bool = False) -> bool:
    """Populate ai_overview on a WorkspaceTable if missing. Returns True if updated."""
    if not force and (table.ai_overview or "").strip():
        return False
    try:
        profile = profile_table(table.full_table_id)
        profile = enrich_profile_with_catalog(table, profile)
        overview = generate_overview(profile)
    except Exception as e:
        # Don't block table add on profiling failure; leave a breadcrumb.
        table.ai_profile_json = json.dumps({"error": str(e)[:300]})
        return False
    table.ai_profile_json = json.dumps(profile, default=str)
    table.ai_overview = (overview or "").strip()
    return bool(table.ai_overview)
