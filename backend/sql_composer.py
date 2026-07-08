"""Compose BigQuery SQL from a semantic MeasurePlan (Hex-style)."""
from __future__ import annotations

import re

from measure_router import MeasurePlan
from question_dates import date_filter_sql, pick_date_column, resolve_relative_range
from semantic_layer import semantic_for_table


def _aggregate_sql(plan: MeasurePlan) -> str:
    m = plan.measure
    func = (m.func or "count").lower().replace(" ", "_")
    col = m.of_column
    if func == "count_distinct" and col:
        return f"COUNT(DISTINCT `{col}`)"
    if func == "count" and col:
        return f"COUNT(`{col}`)"
    if func == "count":
        return "COUNT(*)"
    if func in ("avg", "average") and col:
        return f"AVG(`{col}`)"
    if func == "max" and col:
        return f"MAX(`{col}`)"
    if func == "min" and col:
        return f"MIN(`{col}`)"
    if func == "median" and col:
        return f"APPROX_QUANTILES(`{col}`, 2)[OFFSET(1)]"
    if col:
        return f"{func.upper()}(`{col}`)"
    return "COUNT(*)"


def _alias_for_measure(plan: MeasurePlan) -> str:
    if plan.group_by:
        return plan.measure.id or "metric_value"
    return plan.measure.id or "result"


def compose_sql(plan: MeasurePlan, question: str, table: object) -> str:
    """Build a read-only SELECT from a measure plan."""
    fq = plan.table_fq
    agg = _aggregate_sql(plan)
    alias = _alias_for_measure(plan)

    where_parts: list[str] = list(plan.filters)
    rel = resolve_relative_range(question)
    if rel:
        date_col = pick_date_column(table)
        if date_col:
            where_parts.append(date_filter_sql(date_col, rel[0], rel[1]))

    from table_routing import sql_filters_for_table

    for clause in sql_filters_for_table(question, table):
        if clause not in where_parts:
            where_parts.append(clause)

    if plan.group_by:
        cols = ", ".join(f"`{c}`" for c in plan.group_by)
        sql = (
            f"SELECT {cols}, {agg} AS `{alias}`\n"
            f"FROM `{fq}`"
        )
        if where_parts:
            sql += "\nWHERE " + "\n  AND ".join(where_parts)
        sql += f"\nGROUP BY {cols}\nORDER BY `{alias}` DESC"
        if len(plan.group_by) == 1:
            sql += f", `{plan.group_by[0]}`"
        return sql

    sql = f"SELECT {agg} AS `{alias}`\nFROM `{fq}`"
    if where_parts:
        sql += "\nWHERE " + "\n  AND ".join(where_parts)
    return sql


def enrich_schema_with_measures(schema_text: str, table: object) -> str:
    from semantic_layer import measures_block, semantic_for_table

    sem = semantic_for_table(table)
    if not sem:
        return schema_text
    block = measures_block(sem)
    if block and block not in schema_text:
        return f"{schema_text}\n\n{block}"
    return schema_text
