"""Compose BigQuery SQL from QueryPlan — format strategies driven by model metadata."""
from __future__ import annotations

import re
from typing import Any

from measure_router import MeasurePlan, try_build_measure_plan
from query_planner import QueryPlan
from semantic_layer import MeasureDef, TableSemantic, load_semantic_catalog, semantic_by_model_id, semantic_for_table
from sql_composer import compose_breakdown_with_join, compose_sql

# Default NPS union alignment when logical model metadata is loaded.
_NPS_UNION_ALIGN: dict[str, list[str]] = {
    "user_id": ["user_id", "user_id"],
    "submitted_at": ["form_submission_datetime", "submitted_at"],
    "form_submission_month": ["form_submission_month", "_derive_month"],
    "nps_rating": [
        "rating_on_scale_of_0_to_10",
        "on_a_scale_of_0_10_how_likely_are_you_to_recommend_nxtwaves_academy_program_to_a_friend_or_peer",
    ],
    "worked_well": [
        "please_share_a_short_noteA_about_what_worked_well_for_you",
        "please_share_a_short_note_about_what_worked_well_for_you",
    ],
    "aspects_job_ready": ["what_aspects_helped_you_feel_job_ready", "what_aspects_helped_you_feel_job_ready"],
    "improvements_job_ready": [
        "what_improvements_would_help_you_feel_more_job_ready",
        "what_improvements_would_help_you_feel_more_job_ready",
    ],
    "limited_value": [
        "what_limited_the_value_you_expected_from_the_program",
        "what_limited_the_value_you_expected_from_the_program",
    ],
    "improvements_recommend": [
        "what_improvements_would_help_you_recommend_nxtwave_with_more_confidence",
        "what_improvements_would_help_you_recommend_nxtwave_with_more_confidence",
    ],
    "helped_journey": [
        "please_share_what_specifically_helped_in_your_job_readiness_journey",
        "please_share_what_specifically_helped_in_your_job_readiness_journey",
    ],
    "aspects_recommend": [
        "what_aspects_of_the_program_made_you_feel_confident_to_recommend_us",
        "what_aspects_of_the_program_made_you_feel_confident_to_recommend_us",
    ],
    "value_for_money": [
        "do_you_feel_the_program_delivers_value_for_the_time_and_money_you_have_invested",
        "do_you_feel_the_program_delivers_value_for_the_time_and_money_you_have_invested",
    ],
}

_NPS_MEMBER_IDS = (
    "academy_nps_form_responses",
    "nps_form_responses_nov_and_dec_2025",
)


def _pick_col(cols: set[str], candidates: list[str]) -> str | None:
    lower_map = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand in cols:
            return cand
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None


def _align_map(semantic: TableSemantic) -> dict[str, list[str]]:
    if semantic.union_align:
        return {a.alias: a.columns for a in semantic.union_align}
    if semantic.model_id == "nps_all_form_responses":
        return _NPS_UNION_ALIGN
    return {}


def _member_semantics(member_ids: list[str]) -> list[tuple[str, TableSemantic]]:
    out: list[tuple[str, TableSemantic]] = []
    for mid in member_ids:
        sem = semantic_by_model_id(mid)
        if sem and sem.full_table_id:
            out.append((mid, sem))
    return out


def _sql_col_expr(cols: set[str], col_spec: str, submitted_col: str | None) -> str:
    if col_spec == "_derive_month" and submitted_col:
        return f"DATE_TRUNC(DATE(`{submitted_col}`), MONTH)"
    col = _pick_col(cols, [col_spec]) if col_spec != "_derive_month" else None
    if col:
        return f"`{col}`"
    return "CAST(NULL AS STRING)"


def _build_union_cte(
    cte_name: str,
    fq: str,
    cols: set[str],
    align: dict[str, list[str]],
    member_idx: int,
    source_label: str,
) -> str:
    submitted_spec = align.get("submitted_at", ["", ""])[member_idx] if "submitted_at" in align else ""
    submitted_col = _pick_col(cols, [submitted_spec]) if submitted_spec and submitted_spec != "_derive_month" else None

    select_parts = ["user_id"]
    for alias, specs in align.items():
        if alias == "user_id":
            continue
        spec = specs[member_idx] if member_idx < len(specs) else ""
        if alias == "submitted_at":
            select_parts.append(f"{_sql_col_expr(cols, spec, submitted_col)} AS submitted_at")
        elif alias == "form_submission_month":
            if spec == "_derive_month":
                select_parts.append(f"{_sql_col_expr(cols, spec, submitted_col)} AS form_submission_month")
            else:
                select_parts.append(f"{_sql_col_expr(cols, spec, submitted_col)} AS form_submission_month")
        elif alias == "nps_rating":
            expr = _sql_col_expr(cols, spec, submitted_col)
            select_parts.append(f"CAST({expr} AS FLOAT64) AS nps_rating")
        else:
            select_parts.append(f"{_sql_col_expr(cols, spec, submitted_col)} AS {alias}")

    select_parts.append(f"'{source_label}' AS source_table")
    body = ",\n    ".join(select_parts)
    return f"""{cte_name} AS (
  SELECT
    {body}
  FROM `{fq}`
)"""


def _topic_mention_array(
    field_aliases: list[str],
    topic_regex: str,
    *,
    topic_label: str | None = None,
) -> str:
    parts: list[str] = []
    for alias in field_aliases:
        parts.append(
            f"IF(REGEXP_CONTAINS(LOWER(CAST({alias} AS STRING)), r'{topic_regex}'), "
            f"CONCAT('[{alias}] ', CAST({alias} AS STRING)), NULL)"
        )
    inner = ",\n                ".join(parts)
    slug = re.sub(r"[^a-z0-9]+", "_", (topic_label or topic_regex))[:40].strip("_") or "topic"
    return f"""ARRAY_TO_STRING(
    ARRAY(
      SELECT x FROM UNNEST([
        {inner}
      ]) AS x WHERE x IS NOT NULL
    ),
    ' || '
  ) AS {slug}_mentions"""


def compose_topic_search_union(
    plan: QueryPlan,
    semantic: TableSemantic,
    columns_by_table: dict[str, set[str]],
) -> str | None:
    member_ids = semantic.union_members or list(_NPS_MEMBER_IDS)
    members = _member_semantics(member_ids)
    if len(members) < 1:
        return None

    align = _align_map(semantic)
    if not align:
        return None

    topic_regex = plan.topic_regex or ""
    if not topic_regex:
        return None

    text_aliases = [
        a for a in align if a not in ("user_id", "submitted_at", "form_submission_month", "nps_rating")
    ]

    ctes: list[str] = []
    cte_names: list[str] = []
    for idx, (mid, mem_sem) in enumerate(members):
        cols = columns_by_table.get(mem_sem.full_table_id) or {d.id for d in mem_sem.dimensions}
        if semantic.model_id == "nps_all_form_responses":
            cte_name = "endorsed" if mid == "academy_nps_form_responses" else "old"
        else:
            cte_name = "src_" + mid.replace("nps_form_responses", "nps").replace("academy_", "a_")[:30]
            cte_name = re.sub(r"[^a-z0-9_]", "_", cte_name.lower())
        ctes.append(_build_union_cte(cte_name, mem_sem.full_table_id, cols, align, idx, mid))
        cte_names.append(cte_name)

    union_body = f"SELECT * FROM {cte_names[0]}"
    for name in cte_names[1:]:
        union_body += f"\nUNION ALL\nSELECT * FROM {name}"
    concat_parts = " || ".join(f"COALESCE(CAST({a} AS STRING), '')" for a in text_aliases)
    mention_expr = _topic_mention_array(text_aliases, topic_regex, topic_label=plan.topic)

    return f"""WITH {", ".join(ctes)},
unioned AS (
  {union_body}
)
SELECT
  source_table,
  form_submission_month,
  submitted_at,
  user_id,
  nps_rating,
  {mention_expr}
FROM unioned
WHERE REGEXP_CONTAINS(LOWER(CONCAT({concat_parts})), r'{topic_regex}')
ORDER BY submitted_at DESC"""


def compose_topic_search_single(
    plan: QueryPlan,
    semantic: TableSemantic,
    columns_by_table: dict[str, set[str]],
) -> str | None:
    fq = semantic.full_table_id
    if not fq:
        return None
    cols = columns_by_table.get(fq) or {d.id for d in semantic.dimensions}
    text_cols = semantic.inferred_text_columns()
    if not text_cols:
        return None

    topic_regex = plan.topic_regex or ""
    aliases = [f"`{c}`" for c in text_cols if c in cols or c.lower() in {x.lower() for x in cols}]
    if not aliases:
        aliases = [f"`{c}`" for c in text_cols]

    concat_parts = " || ".join(f"COALESCE(CAST({a} AS STRING), '')" for a in aliases)
    mention_parts: list[str] = []
    for a in aliases:
        name = a.strip("`")
        mention_parts.append(
            f"IF(REGEXP_CONTAINS(LOWER(CAST({a} AS STRING)), r'{topic_regex}'), "
            f"CONCAT('[{name}] ', CAST({a} AS STRING)), NULL)"
        )
    slug = re.sub(r"[^a-z0-9]+", "_", (plan.topic or "topic"))[:40].strip("_")
    mention_expr = f"""ARRAY_TO_STRING(
    ARRAY(SELECT x FROM UNNEST([{", ".join(mention_parts)}]) AS x WHERE x IS NOT NULL),
    ' || '
  ) AS {slug}_mentions"""

    select_cols = ["*"] if not semantic.survey_long else []
    if semantic.survey_long:
        long = semantic.survey_long
        for c in (long.question_col, long.answer_col, long.trigger_col, "user_id"):
            if c and c in cols:
                select_cols.append(f"`{c}`")
        if not select_cols:
            select_cols = ["*"]

    sel = ", ".join(select_cols) if select_cols != ["*"] else "*"
    return f"""SELECT
  {sel},
  {mention_expr}
FROM `{fq}`
WHERE REGEXP_CONTAINS(LOWER(CONCAT({concat_parts})), r'{topic_regex}')
ORDER BY 1 DESC
LIMIT 200"""


def compose_survey_distribution(
    plan: QueryPlan,
    question: str,
    tables: list[Any],
    columns_by_table: dict[str, set[str]],
) -> str | None:
    from feedback_sql import try_build_feedback_sql

    return try_build_feedback_sql(question, tables, columns_by_table)


def tables_and_columns_for_plan(
    plan: QueryPlan,
    selected_tables: list[Any],
    pool: list[Any],
    columns_by_table: dict[str, set[str]],
) -> tuple[list[Any], dict[str, set[str]]]:
    """Tables + column map needed to validate SQL for a query plan."""
    from types import SimpleNamespace

    from join_resolver import resolve_dimension_join

    planner_tables = list(selected_tables)
    cols = dict(columns_by_table or {})
    pool_fqs = {getattr(t, "full_table_id", "") for t in pool}

    def _ensure_model(model_id: str) -> None:
        sem = semantic_by_model_id(model_id)
        if not sem or not sem.full_table_id:
            return
        fq = sem.full_table_id
        cols.setdefault(fq, {d.id for d in sem.dimensions})
        if any(getattr(t, "full_table_id", "") == fq for t in planner_tables):
            return
        if fq in pool_fqs:
            planner_tables.append(next(t for t in pool if t.full_table_id == fq))
        else:
            planner_tables.append(
                SimpleNamespace(
                    full_table_id=fq,
                    column_hints_json="{}",
                    column_descriptions_json="{}",
                    ai_profile_json="{}",
                )
            )

    _ensure_model(plan.model_id)
    sem = semantic_by_model_id(plan.model_id)
    if sem:
        for dim in plan.group_by or plan.dimensions:
            joined = resolve_dimension_join(sem, dim)
            if joined:
                _ensure_model(joined[0].model_id)

    for mid in list(getattr(plan, "union_member_ids", None) or []):
        _ensure_model(mid)

    return planner_tables, cols


def _measure_for_plan(plan: QueryPlan, semantic: TableSemantic) -> MeasureDef | None:
    if not plan.measure_id:
        return None
    for m in semantic.measures:
        if m.id == plan.measure_id:
            return m
    return None


def _table_for_model(model_id: str, tables: list[Any]) -> Any | None:
    for t in tables:
        sem = semantic_for_table(t)
        if sem and sem.model_id == model_id:
            return t
    sem = semantic_by_model_id(model_id)
    if sem and sem.full_table_id:
        from types import SimpleNamespace

        return SimpleNamespace(full_table_id=sem.full_table_id)
    return None


def _plan_to_measure_plan(plan: QueryPlan, semantic: TableSemantic) -> MeasurePlan | None:
    measure = _measure_for_plan(plan, semantic)
    if not measure:
        return None
    return MeasurePlan(
        table_fq=semantic.full_table_id,
        table_short=semantic.short_name,
        measure=measure,
        group_by=list(plan.group_by or plan.dimensions),
        filters=list(plan.filters),
        reason=plan.reason,
    )


def compose_scalar_from_plan(
    plan: QueryPlan,
    question: str,
    tables: list[Any],
) -> str | None:
    """Aggregate SQL directly from resolved QueryPlan (no measure-router rescan)."""
    sem = semantic_by_model_id(plan.model_id)
    if not sem or not sem.full_table_id:
        return None
    mp = _plan_to_measure_plan(plan, sem)
    if not mp:
        return compose_aggregate(plan, question, tables, tables)
    table_obj = _table_for_model(plan.model_id, tables)
    if not table_obj:
        return None
    return compose_sql(mp, question, table_obj)


def compose_breakdown_from_plan(
    plan: QueryPlan,
    question: str,
    tables: list[Any],
    *,
    catalog_tables: list[Any] | None = None,
) -> str | None:
    """Breakdown SQL — local dimension or join via workspace model relations."""
    from join_resolver import resolve_dimension_join

    sem = semantic_by_model_id(plan.model_id)
    if not sem or not sem.full_table_id:
        return None
    mp = _plan_to_measure_plan(plan, sem)
    if not mp:
        pool = list(catalog_tables or tables)
        return compose_aggregate(plan, question, tables, pool)

    group_by = plan.group_by or plan.dimensions
    if not group_by:
        return compose_scalar_from_plan(plan, question, tables)

    dim_id = group_by[0]
    table_obj = _table_for_model(plan.model_id, tables)
    if not table_obj:
        return None

    if sem.has_dimension(dim_id):
        mp.group_by = group_by
        return compose_sql(mp, question, table_obj)

    joined = resolve_dimension_join(sem, dim_id)
    if joined:
        target_sem, rel = joined
        return compose_breakdown_with_join(
            mp, question, sem, target_sem, rel, dim_id
        )

    return compose_aggregate(plan, question, tables, catalog_tables)


def compose_aggregate(
    plan: QueryPlan,
    question: str,
    tables: list[Any],
    catalog_tables: list[Any] | None,
) -> str | None:
    sem = semantic_by_model_id(plan.model_id)
    if not sem or not sem.full_table_id:
        return None
    pool = list(tables)
    table_obj = next(
        (t for t in pool if semantic_for_table(t) and semantic_for_table(t).model_id == plan.model_id),
        None,
    )
    if not table_obj:
        return None
    mp = try_build_measure_plan(question, [table_obj], catalog_tables=catalog_tables or pool)
    if not mp:
        return None
    return compose_sql(mp, question, table_obj)


def compose_compound(
    plan: QueryPlan,
    question: str,
    tables: list[Any],
) -> str | None:
    from domain_sql import resolve_compound_domain_sql

    pool = list(tables)
    resolved = resolve_compound_domain_sql(question, pool)
    if resolved:
        return resolved[0]
    return None


def compose_query_plan(
    plan: QueryPlan,
    question: str,
    tables: list[Any],
    columns_by_table: dict[str, set[str]] | None = None,
    *,
    catalog_tables: list[Any] | None = None,
) -> str | None:
    """Turn a QueryPlan into executable BigQuery SQL."""
    columns_by_table = columns_by_table or {}

    if plan.intent == "compound":
        return compose_compound(plan, question, catalog_tables or tables)

    pool = list(catalog_tables or tables)
    from join_compose import try_compose_join_sql

    join_sql = try_compose_join_sql(question, pool)
    if join_sql:
        return join_sql

    semantic = semantic_by_model_id(plan.model_id)
    if not semantic:
        return None

    if plan.intent == "topic_search":
        if semantic.is_logical_union or plan.union_member_ids:
            return compose_topic_search_union(plan, semantic, columns_by_table)
        return compose_topic_search_single(plan, semantic, columns_by_table)

    if plan.intent == "survey_distribution":
        return compose_survey_distribution(plan, question, tables, columns_by_table)

    if plan.intent in ("aggregate", "breakdown"):
        if plan.intent == "breakdown":
            return compose_breakdown_from_plan(
                plan, question, tables, catalog_tables=catalog_tables
            )
        return compose_scalar_from_plan(plan, question, tables)

    return None
