"""SQL generation for individual Hex-style notebook steps."""
from __future__ import annotations

import re
from typing import Any

from chain_pipeline import try_compose_chain_step_sql
from join_compose import try_compose_join_sql


def _find_table(tables: list[Any], needle: str) -> Any | None:
    n = needle.lower()
    for t in tables:
        short = t.full_table_id.rsplit(".", 1)[-1].lower()
        if n in short or short == n:
            return t
    return None


def _compose_dimension_preview(question: str, tables: list[Any]) -> str | None:
    m = re.search(r"\bby\s+([a-z][a-z0-9_\s]{1,40})", question or "", re.I)
    dim = re.sub(r"\s+", "_", (m.group(1) if m else "gender").strip().lower())
    master = _find_table(tables, "master_data") or _find_table(tables, "profile_basic")
    if not master:
        return None
    fq = master.full_table_id
    col = dim if dim in ("gender", "state", "city", "region") else "gender"
    return f"""SELECT
  `{col}` AS `{col}`,
  COUNT(DISTINCT `user_id`) AS `user_count`
FROM `{fq}`
WHERE `{col}` IS NOT NULL
GROUP BY `{col}`
ORDER BY `user_count` DESC
LIMIT 50"""


def _compose_base_explore(question: str, tables: list[Any]) -> str | None:
    if not tables:
        return None
    t = tables[0]
    fq = t.full_table_id
    short = fq.rsplit(".", 1)[-1].lower()
    q = question.lower()
    if "nps" in short or re.search(r"\bnps\b", q):
        rating = "rating_on_scale_of_0_to_10"
        return f"""SELECT
  COUNT(*) AS `response_count`,
  COUNT(DISTINCT `user_id`) AS `respondent_count`,
  AVG(CAST(`{rating}` AS FLOAT64)) AS `avg_rating`
FROM `{fq}`
WHERE `{rating}` IS NOT NULL"""
    if "master" in short:
        from table_business_rules import table_skips_default_filters

        if table_skips_default_filters(t):
            return f"""SELECT
  COUNT(DISTINCT `user_id`) AS `user_count`
FROM `{fq}`"""
        return f"""SELECT
  COUNT(DISTINCT `user_id`) AS `user_count`
FROM `{fq}`
WHERE pause_status IS NULL
  AND learning_portal_onboarding_access_given_datetime IS NOT NULL"""
    if "attendance" in short or "live_class" in short:
        return f"""SELECT
  COUNT(DISTINCT `user_id`) AS `attendee_count`
FROM `{fq}`
WHERE `attendance_status` = 'JOINED'"""
    if "placement" in short:
        return f"""SELECT
  COUNT(DISTINCT `user_id`) AS `placed_users`
FROM `{fq}`"""
    return f"SELECT COUNT(*) AS `row_count`\nFROM `{fq}`\nLIMIT 1"


def compose_notebook_step_sql(
    step: dict[str, str],
    question: str,
    selected_tables: list,
    hints_map: dict,
    inferred: dict,
    columns_by_table: dict[str, set[str]],
    *,
    catalog_tables: list | None = None,
    schema_entities: list | None = None,
) -> tuple[str | None, str]:
    """Compose SQL for one notebook cell. Returns (sql, source_label)."""
    kind = (step.get("kind") or "metric").lower()
    step_q = (step.get("question") or question).strip()
    pool = list(catalog_tables or selected_tables)

    if kind == "final":
        join_sql = try_compose_join_sql(step_q, pool)
        if join_sql:
            return join_sql, "join_template"
        if re.search(r"\bportal\b", step_q, re.I) and re.search(
            r"\bactivity|attend|percent",
            step_q,
            re.I,
        ):
            from join_compose import compose_portal_activity_attendance_pct_sql

            pct_sql = compose_portal_activity_attendance_pct_sql(question, pool)
            if pct_sql:
                return pct_sql, "portal_attendance_join"
        sql, reason = try_compose_chain_step_sql(
            step_q,
            selected_tables,
            hints_map,
            inferred,
            columns_by_table,
            catalog_tables=pool,
            schema_entities=schema_entities,
        )
        if sql:
            return sql, reason or "rag"

    if kind == "explore":
        from join_compose import compose_portal_activity_by_page_sql

        portal_sql = compose_portal_activity_by_page_sql(step_q, pool)
        if portal_sql and (
            "portal" in step.get("label", "").lower()
            or "activity" in step_q.lower()
        ):
            return portal_sql, "portal_activity"
        preview = _compose_base_explore(step_q, pool)
        if preview and ("base" in step.get("label", "").lower() or step.get("label", "").startswith("1.")):
            return preview, "explore_base"
        dim_preview = _compose_dimension_preview(step_q, pool)
        if dim_preview:
            return dim_preview, "explore_dimension"

    sql, reason = try_compose_chain_step_sql(
        step_q,
        selected_tables,
        hints_map,
        inferred,
        columns_by_table,
        catalog_tables=pool,
        schema_entities=schema_entities,
    )
    if sql:
        return sql, reason or "rag"

    if kind == "final":
        join_sql = try_compose_join_sql(question, pool)
        if join_sql:
            return join_sql, "join_template"

    return None, ""
