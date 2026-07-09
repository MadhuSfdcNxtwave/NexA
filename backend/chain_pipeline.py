"""RAG-based SQL compose for individual chain steps."""
from __future__ import annotations

from typing import Any

import bq
from domain_sql import resolve_domain_sql
from rag_pipeline import try_rag_compose_sql


def try_compose_chain_step_sql(
    question: str,
    selected_tables: list,
    hints_map: dict,
    inferred: dict,
    columns_by_table: dict[str, set[str]],
    *,
    catalog_tables: list | None = None,
    schema_entities: list | None = None,
) -> tuple[str | None, str]:
    """
    Compose SQL for one chain step — RAG first, then deterministic domain SQL.
    Returns (sql, source_label).
    """
    pool = list(catalog_tables or selected_tables)

    sql, reason, _tables, _plan, _resolved = try_rag_compose_sql(
        question,
        selected_tables,
        hints_map,
        inferred,
        columns_by_table,
        catalog_tables=pool,
        schema_entities=schema_entities,
    )
    if sql:
        return sql, reason or "rag"

    domain = resolve_domain_sql(question, pool)
    if domain:
        raw_sql, _table, domain_reason = domain
        try:
            return bq.validate_select_only(raw_sql), domain_reason or "domain"
        except ValueError:
            pass

    return None, ""
