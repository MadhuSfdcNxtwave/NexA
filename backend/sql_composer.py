"""Compose BigQuery SQL from a semantic MeasurePlan (Hex-style)."""
from __future__ import annotations

import re

from measure_router import MeasurePlan
from question_dates import date_filter_sql, pick_date_column, resolve_relative_range
from semantic_layer import semantic_for_table


def _aggregate_sql(plan: MeasurePlan, semantic=None) -> str:
    m = plan.measure
    if m.func_sql:
        return m.func_sql
    if m.func == "custom" and m.func_sql:
        return m.func_sql
    func = (m.func or "count").lower().replace(" ", "_")
    col = m.of_column
    if semantic and col:
        col_ref = semantic.dim_sql(col)
    else:
        col_ref = f"`{col}`" if col else ""
    if func == "count_distinct" and col_ref:
        return f"COUNT(DISTINCT {col_ref})"
    if func == "count" and col_ref:
        return f"COUNT({col_ref})"
    if func == "count":
        return "COUNT(*)"
    if func in ("avg", "average") and col_ref:
        return f"AVG({col_ref})"
    if func == "max" and col_ref:
        return f"MAX({col_ref})"
    if func == "min" and col_ref:
        return f"MIN({col_ref})"
    if func == "median" and col_ref:
        return f"APPROX_QUANTILES({col_ref}, 2)[OFFSET(1)]"
    if col_ref:
        return f"{func.upper()}({col_ref})"
    return "COUNT(*)"


def _alias_for_measure(plan: MeasurePlan) -> str:
    if plan.group_by:
        return plan.measure.id or "metric_value"
    return plan.measure.id or "result"


def compose_sql(plan: MeasurePlan, question: str, table: object) -> str:
    """Build a read-only SELECT from a measure plan."""
    sem = semantic_for_table(table)
    fq = plan.table_fq
    agg = _aggregate_sql(plan, sem)
    alias = _alias_for_measure(plan)

    where_parts: list[str] = list(plan.filters)
    if sem:
        for m in sem.measures:
            if m.id == plan.measure.id and m.filters:
                for fid in m.filters:
                    dim = sem.dimension_by_id(fid)
                    if dim:
                        where_parts.append(f"{sem.dim_sql(dim.id)} IS NOT NULL")
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
