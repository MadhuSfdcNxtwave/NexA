"""BigQuery access. Schema is built per-project from that project's
configured table list, so each workspace only ever sees its own tables.

Security: the real guard is the service account's read-only IAM role. The
SELECT-only check here is a secondary net.
"""
from __future__ import annotations

import re

import pandas as pd
from google.cloud import bigquery

import config
import sql_parse

_RATING_LIKE = re.compile(r"(rating|nps|recommend|scale|likelihood)", re.IGNORECASE)
_FEEDBACK_LIKE = re.compile(
    r"(feedback|improvement|improve|suggest|comment|verbatim|job_ready|feel_more|"
    r"what_.*would|reason_why|open_text|tell_us)",
    re.IGNORECASE,
)
_RATING_SURVEY_DUPLICATE = re.compile(
    r"(recommend|likelihood|scale_of|scale of|0_10|0_to_10|how_likely)",
    re.IGNORECASE,
)

_CANONICAL_RATING_COLUMNS = (
    "rating_on_scale_of_0_to_10",
    "rating_on_scale_of_0_10",
    "nps_score",
    "nps_rating",
    "rating",
)

_HINT_LABELS = {
    "primary_field": "[PRIMARY FIELD — use for rating/NPS/score filters and aggregations]",
    "primary_key": "[PRIMARY KEY — use for joins and user identity]",
    "primary_date": "[PRIMARY DATE — NPS: form_submission_month = DATE 'YYYY-MM-01'; jobs/applications: DATE_TRUNC(applied_datetime, MONTH)]",
    "feedback_field": "[FEEDBACK FIELD — use for open-text feedback / improvement suggestions]",
}


def _is_feedback_column(name: str) -> bool:
    return bool(_FEEDBACK_LIKE.search(name))


def _should_deprecate_rating_duplicate(name: str, preferred: str) -> bool:
    """Only deprecate long duplicate survey *rating* columns, not feedback text."""
    if name == preferred or _is_feedback_column(name):
        return False
    if not _RATING_LIKE.search(name):
        return False
    return len(name) > 35 or bool(_RATING_SURVEY_DUPLICATE.search(name))


def infer_column_hints(
    column_names: list[str],
    existing: dict[str, str] | None = None,
) -> dict[str, str]:
    """Resolve duplicate survey-style columns and mark canonical date/rating fields."""
    hints = dict(existing or {})

    if "form_submission_month" in column_names:
        hints.setdefault("form_submission_month", "primary_date")

    if "applied_datetime" in column_names:
        hints.setdefault("applied_datetime", "primary_date")

    if "user_id" in column_names:
        hints.setdefault("user_id", "primary_key")

    for name in column_names:
        if _is_feedback_column(name):
            hints.setdefault(name, "feedback_field")

    if any(v == "primary_field" for v in hints.values()):
        preferred = next(k for k, v in hints.items() if v == "primary_field")
        for name in column_names:
            if _should_deprecate_rating_duplicate(name, preferred):
                hints.setdefault(name, "deprecated_duplicate")
        return hints

    rating_like = [n for n in column_names if _RATING_LIKE.search(n)]
    if len(rating_like) < 2:
        return hints

    preferred = next((n for n in _CANONICAL_RATING_COLUMNS if n in column_names), None)
    if not preferred:
        rating_prefixed = sorted(
            [n for n in rating_like if n.lower().startswith("rating_")],
            key=len,
        )
        preferred = rating_prefixed[0] if rating_prefixed else min(rating_like, key=len)

    if not preferred:
        return hints

    hints.setdefault(preferred, "primary_field")
    for name in rating_like:
        if _should_deprecate_rating_duplicate(name, preferred):
            hints.setdefault(name, "deprecated_duplicate")
    return hints


def _hint_annotation(role: str, hints: dict[str, str]) -> str:
    if role in _HINT_LABELS:
        return _HINT_LABELS[role]
    if role == "deprecated_duplicate":
        preferred = next((k for k, v in hints.items() if v == "primary_field"), None)
        if preferred:
            return f"[DO NOT USE — prefer `{preferred}` for rating/NPS/score questions]"
        return "[DO NOT USE — prefer the shorter canonical rating column]"
    return ""


def _column_sort_key(name: str, hints: dict[str, str]) -> tuple[int, str]:
    role = hints.get(name, "")
    order = {
        "primary_key": 0,
        "primary_field": 1,
        "primary_date": 2,
        "feedback_field": 3,
        "": 4,
        "deprecated_duplicate": 5,
    }
    return (order.get(role, 2), name.lower())

_client: bigquery.Client | None = None


def reset_client() -> None:
    """Drop cached client (after credential or region config changes)."""
    global _client
    _client = None


def client() -> bigquery.Client:
    global _client
    if _client is None:
        _client = bigquery.Client(
            project=config.GCP_PROJECT or None, location=config.BQ_LOCATION
        )
    return _client


def schema_for_tables(
    full_table_ids: list[str],
    join_hints: str = "",
    table_notes: dict[str, str] | None = None,
    column_notes: dict[str, dict[str, str]] | None = None,
    column_hints: dict[str, dict[str, str]] | None = None,
    highlight_columns: dict[str, set[str]] | None = None,
    max_columns_per_table: int = 0,
) -> str:
    """Build the model-facing schema text for a specific set of tables.

    table_notes: optional project-stored descriptions keyed by full_table_id.
    column_notes: optional per-column descriptions keyed by full_table_id then column name.
    column_hints: optional per-column roles (primary_field, primary_key, deprecated_duplicate).
    These are shown to the model even when BigQuery has no table/column docs.
    """
    bq = client()
    notes = table_notes or {}
    col_notes = column_notes or {}
    col_hints_in = column_hints or {}
    highlights = highlight_columns or {}
    blocks: list[str] = []
    for fq in full_table_ids:
        project_note = notes.get(fq, "").strip()
        table_col_notes = col_notes.get(fq, {})
        table_col_hints = dict(col_hints_in.get(fq, {}))
        matched_cols = highlights.get(fq, set())
        try:
            tbl = bq.get_table(fq)
            field_names = [f.name for f in tbl.schema]
            merged_hints = infer_column_hints(field_names, table_col_hints)

            def _sort_key(field: bigquery.SchemaField) -> tuple[int, tuple[int, str]]:
                base = _column_sort_key(field.name, merged_hints)
                if field.name in matched_cols:
                    return (0, base)
                return (1, base)

            cols = []
            for f in sorted(tbl.schema, key=_sort_key):
                custom = (table_col_notes.get(f.name) or "").strip()
                bq_desc = (f.description or "").strip()
                desc_text = custom or bq_desc
                role = merged_hints.get(f.name, "")
                hint = _hint_annotation(role, merged_hints)
                match_tag = "[MATCHES QUESTION — prefer this column] " if f.name in matched_cols else ""
                parts = [p for p in [match_tag + hint if match_tag else hint, desc_text] if p]
                desc = f" -- {' '.join(parts)}" if parts else ""
                cols.append(f"  {f.name} {f.field_type}{desc}")

            if max_columns_per_table > 0 and len(cols) > max_columns_per_table:
                cols = cols[:max_columns_per_table]
                cols.append(f"  … ({len(tbl.schema)} columns total — showing top {max_columns_per_table})")

            bq_desc = (tbl.description or "").strip()
            header_parts = [p for p in [bq_desc, project_note] if p]
            canonical = [
                name for name, role in merged_hints.items() if role in ("primary_field", "primary_date")
            ]
            if canonical:
                header_parts.append(
                    "Canonical fields for queries: " + ", ".join(f"`{c}`" for c in canonical)
                )
            tdesc = f"  # {' | '.join(header_parts)}\n" if header_parts else ""
            blocks.append(f"TABLE `{fq}`\n{tdesc}" + "\n".join(cols))
        except Exception as e:
            block = f"TABLE `{fq}`"
            if project_note:
                block += f"\n  # {project_note}"
            block += f"\n  (could not load from BigQuery: {e})"
            blocks.append(block)

    schema = "\n\n".join(blocks) if blocks else "(no tables configured for this project)"
    if join_hints.strip():
        schema += "\n\n# How these tables relate (use these for JOINs)\n" + join_hints.strip()
    return schema


_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


def _strip_quoted_literals(sql: str) -> str:
    """Remove string literals so keyword guards do not match words inside quotes."""
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            out.append(" ")
            i += 1
            while i < n:
                if sql[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch == '"':
            out.append(" ")
            i += 1
            while i < n and sql[i] != '"':
                if sql[i] == "\\":
                    i += 1
                i += 1
            i += 1 if i < n else 0
            continue
        if ch == "`":
            out.append(" ")
            i += 1
            while i < n and sql[i] != "`":
                i += 1
            i += 1 if i < n else 0
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _strip_sql(sql: str) -> str:
    sql = re.sub(r"^```(?:sql)?|```$", "", sql.strip(), flags=re.IGNORECASE | re.MULTILINE)
    return sql.strip().rstrip(";").strip()


def validate_select_only(sql: str) -> str:
    sql = _strip_sql(sql)
    sql = sql_parse.normalize_llm_sql(sql)
    head = sql.lstrip("(").lstrip().upper()
    if not (head.startswith("SELECT") or head.startswith("WITH")):
        raise ValueError("Only SELECT / WITH queries are allowed.")
    if ";" in sql:
        raise ValueError("Multiple statements are not allowed.")
    expr = sql_parse.parse_bigquery(sql)
    sql_parse.ensure_read_only(expr)
    if _FORBIDDEN.search(_strip_quoted_literals(sql)):
        raise ValueError("Query contains a non-read keyword and was blocked.")
    return sql


def _default_table_catalog(extra: dict[str, str] | None = None) -> dict[str, str]:
    catalog: dict[str, str] = {}
    for fq in (config.DEFAULT_WORKSPACE_TABLES or "").split(","):
        fq = fq.strip()
        if fq:
            catalog[fq.rsplit(".", 1)[-1].lower()] = fq
    if extra:
        for short, fq in extra.items():
            catalog[short.lower()] = fq
    return catalog


def prepare_sql(sql: str, *, table_catalog: dict[str, str] | None = None) -> str:
    """Validate and auto-fix common LLM table-reference mistakes."""
    sql = validate_select_only(sql)
    catalog = _default_table_catalog(table_catalog)
    return sql_parse.normalize_sql_tables(sql, catalog)


def format_query_error(exc: Exception) -> str:
    """Turn BigQuery exceptions into actionable messages for recovery / UI."""
    msg = str(exc).strip()
    low = msg.lower()
    if "access denied" in low or "permission" in low:
        return (
            f"BigQuery access denied — {msg}. "
            "Confirm the dataset is spelled `worksapce` (not `workspace`) and the service account has read access."
        )
    if "not found" in low or "404" in low:
        return f"BigQuery resource not found — {msg}. Check table path in the workspace catalog."
    if "maximum bytes billed" in low or "bytes billed" in low:
        return (
            f"Query exceeded the bytes cap — {msg}. "
            "Add filters (date range, LIMIT) or raise MAX_BYTES_BILLED in backend/.env."
        )
    if "unrecognized name" in low:
        return f"SQL references an unknown column or table — {msg}"
    if "no matching signature" in low or "cannot coerce" in low:
        return (
            f"SQL type mismatch — {msg}. "
            "For month filters use DATE 'YYYY-MM-01' on form_submission_month."
        )
    return f"BigQuery query failed — {msg}"


def dry_run_bytes(sql: str, *, table_catalog: dict[str, str] | None = None) -> int:
    sql = prepare_sql(sql, table_catalog=table_catalog)
    cfg = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    return (
        client()
        .query(sql, job_config=cfg, location=config.BQ_LOCATION or None)
        .total_bytes_processed
        or 0
    )


def run_query(
    sql: str,
    row_limit: int = 2000,
    *,
    max_bytes_billed: int | None = None,
    table_catalog: dict[str, str] | None = None,
) -> pd.DataFrame:
    from debug_session import debug_log

    sql = prepare_sql(sql, table_catalog=table_catalog)
    cap = max_bytes_billed if max_bytes_billed is not None else config.MAX_BYTES_BILLED
    cfg = bigquery.QueryJobConfig(maximum_bytes_billed=cap)
    try:
        df = (
            client()
            .query(sql, job_config=cfg, location=config.BQ_LOCATION or None)
            .result()
            .to_dataframe()
        )
        out = df.head(row_limit)
        debug_log(
            "bq.py:run_query",
            "ok",
            {"row_count": len(out), "sql": sql[:400]},
            hypothesis_id="H3",
        )
        return out
    except Exception as e:
        debug_log(
            "bq.py:run_query",
            "error",
            {"error": str(e)[:500], "sql": sql[:400]},
            hypothesis_id="H4",
        )
        raise


def list_datasets() -> list[dict]:
    """Datasets visible to the configured service account."""
    bq_client = client()
    project = (config.GCP_PROJECT or "").strip()
    if project in ("", "your-gcp-project-id"):
        project = bq_client.project
    if not project:
        raise ValueError(
            "Set GCP_PROJECT in backend/.env, then run: gcloud auth application-default login"
        )
    out = []
    for ds in bq_client.list_datasets(project=project):
        out.append({
            "dataset_id": ds.dataset_id,
            "project_id": ds.project,
            "full_id": f"{ds.project}.{ds.dataset_id}",
        })
    return sorted(out, key=lambda d: d["dataset_id"].lower())


def warehouse_datasets() -> list[dict]:
    """Datasets shown in the warehouse browser.

    When BQ_DEFAULT_DATASET is set, only that dataset is used (the team's
    dedicated dataset). It is included even if project-wide dataset listing
    does not return it yet.
    """
    focus = (config.BQ_DEFAULT_DATASET or "").strip()
    if not focus:
        return list_datasets()

    project = (config.GCP_PROJECT or "").strip() or client().project
    full_id = f"{project}.{focus}"
    listed = list_datasets()
    match = next((d for d in listed if d["dataset_id"].lower() == focus.lower()), None)
    if match:
        return [match]
    return [{"dataset_id": focus, "project_id": project, "full_id": full_id}]


def warehouse_catalog() -> tuple[list[dict], dict[str, list[dict]]]:
    """Datasets + tables for the warehouse browser."""
    datasets = warehouse_datasets()
    tables_by_dataset: dict[str, list[dict]] = {}
    for ds in datasets:
        full_id = ds["full_id"]
        try:
            tables_by_dataset[full_id] = list_tables_in_dataset(full_id)
        except Exception:
            tables_by_dataset[full_id] = []
    return datasets, tables_by_dataset


def list_tables_in_dataset(dataset_full_id: str) -> list[dict]:
    """Tables in a dataset the service account can list."""
    bq = client()
    out = []
    for tbl in bq.list_tables(dataset_full_id):
        fq = f"{tbl.project}.{tbl.dataset_id}.{tbl.table_id}"
        out.append({
            "table_id": tbl.table_id,
            "full_table_id": fq,
            "table_type": tbl.table_type or "TABLE",
        })
    return sorted(out, key=lambda t: t["table_id"].lower())


def table_metadata(full_table_id: str) -> dict:
    tbl = client().get_table(full_table_id)
    table_type = tbl.table_type or "TABLE"
    num_rows = tbl.num_rows
    preview_note = None
    if table_type.upper() == "VIEW" and (num_rows is None or num_rows == 0):
        preview_note = (
            "This is a BigQuery VIEW — metadata may show 0 rows even when the view "
            "contains data. Use the Preview tab to sample rows."
        )
    return {
        "full_table_id": full_table_id,
        "description": tbl.description or "",
        "num_rows": num_rows,
        "num_bytes": tbl.num_bytes,
        "table_type": table_type,
        "preview_note": preview_note,
        "columns": [
            {
                "name": f.name,
                "type": f.field_type,
                "description": f.description or "",
                "mode": f.mode,
            }
            for f in tbl.schema
        ],
    }


def _build_preview_sql(
    full_table_id: str,
    field_names: list[str],
    limit: int,
) -> tuple[str, str | None]:
    """Build preview SQL that avoids empty-looking sample rows on sparse views."""
    cols = {n.lower() for n in field_names}
    lim = int(limit)

    if "job_id" in cols:
        order = ""
        if "applied_datetime" in cols:
            order = " ORDER BY applied_datetime DESC"
        elif "job_creation_datetime" in cols:
            order = " ORDER BY job_creation_datetime DESC"
        sql = f"SELECT * FROM `{full_table_id}` WHERE job_id IS NOT NULL{order} LIMIT {lim}"
        return sql, (
            "Showing rows where job_id is set — this view has many user rows "
            "without job details."
        )

    if "applied_datetime" in cols:
        return (
            f"SELECT * FROM `{full_table_id}` ORDER BY applied_datetime DESC LIMIT {lim}",
            None,
        )
    if "form_submission_month" in cols:
        return (
            f"SELECT * FROM `{full_table_id}` ORDER BY form_submission_month DESC LIMIT {lim}",
            None,
        )
    return f"SELECT * FROM `{full_table_id}` LIMIT {lim}", None


def preview_table(full_table_id: str, limit: int = 25) -> tuple[pd.DataFrame, str | None]:
    meta = table_metadata(full_table_id)
    field_names = [c["name"] for c in meta["columns"]]
    sql, note = _build_preview_sql(full_table_id, field_names, limit)
    df = run_query(
        sql,
        row_limit=limit,
        max_bytes_billed=config.PREVIEW_MAX_BYTES_BILLED,
    )
    notes: list[str] = []
    if meta.get("preview_note"):
        notes.append(meta["preview_note"])
    if note:
        notes.append(note)
    return df, " ".join(notes) if notes else None


def probe_access(full_table_id: str) -> dict:
    """Check what the configured identity can do on a table."""
    result = {"metadata_ok": False, "query_ok": False, "metadata_error": None, "query_error": None}
    try:
        table_metadata(full_table_id)
        result["metadata_ok"] = True
    except Exception as e:
        result["metadata_error"] = str(e)
    try:
        preview_table(full_table_id, limit=1)
        result["query_ok"] = True
    except Exception as e:
        result["query_error"] = str(e)
    return result
