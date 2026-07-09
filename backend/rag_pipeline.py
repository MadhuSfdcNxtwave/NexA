"""
Universal RAG SQL path: question → resolve intent → compose SQL → validate.

Glossary + semantic layer drive SQL generation; LLM is not used on this path.
"""
from __future__ import annotations

from typing import Any

import bq
import config
from memory_lookup import sql_matches_question_intent
from query_compose import compose_query_plan, tables_and_columns_for_plan
from query_planner import QueryPlan, analyze_question
from sql_guard import validate_sql
from term_resolver import ResolvedQuery, resolve


def try_rag_compose_sql(
    question: str,
    selected_tables: list,
    hints_map: dict,
    inferred: dict,
    columns_by_table: dict[str, set[str]],
    *,
    catalog_tables: list | None = None,
    schema_entities: list | None = None,
    query_plan: QueryPlan | None = None,
) -> tuple[str | None, str, list | None, QueryPlan | None, ResolvedQuery | None]:
    """
    RAG compose: glossary resolve → query plan → deterministic SQL.
    Returns (sql, reason, tables_for_run, plan, resolved).
    """
    if not config.GLOSSARY_ENABLED or not config.HEX_STYLE_PIPELINE:
        return None, "", None, None, None

    pool = list(catalog_tables or selected_tables)
    resolved: ResolvedQuery | None = resolve(
        question,
        selected_tables,
        catalog_tables=pool,
        columns_by_table=columns_by_table,
    )

    plan = query_plan
    if query_plan and query_plan.intent in (
        "compound",
        "topic_search",
        "survey_distribution",
    ):
        plan = query_plan
        reason = query_plan.reason or f"Query plan ({query_plan.intent})"
    elif resolved and resolved.glossary_terms:
        plan = resolved.to_query_plan()
        reason = resolved.reason or "Glossary RAG resolution"
    elif resolved and resolved.confidence >= 0.75:
        plan = resolved.to_query_plan()
        reason = resolved.reason or "Term resolver"
    elif not plan:
        plan = analyze_question(
            question,
            selected_tables,
            catalog_tables=pool,
            columns_by_table=columns_by_table,
        )
        reason = plan.reason if plan else ""
    else:
        reason = plan.reason or "Query plan"

    if not plan:
        return None, "", None, None, resolved

    raw = compose_query_plan(
        plan,
        question,
        selected_tables,
        columns_by_table,
        catalog_tables=pool,
    )
    if not raw:
        return None, reason, None, plan, resolved

    try:
        sql = bq.validate_select_only(raw)
    except ValueError:
        return None, reason, None, plan, resolved

    if not sql_matches_question_intent(
        question,
        sql,
        schema_entities=schema_entities,
        query_plan=plan,
    ):
        return None, reason, None, plan, resolved

    planner_tables, planner_cols = tables_and_columns_for_plan(
        plan, selected_tables, pool, columns_by_table
    )
    violations = validate_sql(
        sql,
        question,
        planner_tables,
        hints_map,
        inferred,
        columns_by_table=planner_cols,
    )

    if violations:
        return None, reason, None, plan, resolved

    if resolved and resolved.glossary_terms:
        reason = f"RAG: {resolved.reason}"
    elif plan.reason:
        reason = f"RAG: {plan.reason}"

    return sql, reason, planner_tables, plan, resolved
