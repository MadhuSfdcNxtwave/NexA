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


def _normalize_clause(clause: str) -> str:
    c = (clause or "").strip().lower()
    c = c.removeprefix("(").removesuffix(")")
    c = re.sub(r"\s+", " ", c)
    return c


def _dedupe_clauses(clauses: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for c in clauses:
        if not c:
            continue
        key = _normalize_clause(c)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def compose_sql(plan: MeasurePlan, question: str, table: object) -> str:
    """Build a read-only SELECT from a measure plan."""
    sem = semantic_for_table(table)
    fq = plan.table_fq
    agg = _aggregate_sql(plan, sem)
    alias = _alias_for_measure(plan)

    where_parts: list[str] = list(plan.filters)
    from table_business_rules import table_skips_default_filters

    skip_defaults = table_skips_default_filters(table)
    if sem and not skip_defaults:
        for m in sem.measures:
            if m.id == plan.measure.id and m.filters:
                for fid in m.filters:
                    dim = sem.dimension_by_id(fid)
                    if not dim:
                        continue
                    expr = sem.dim_sql(dim.id)
                    if dim.dim_type == "boolean" and dim.expr_sql:
                        where_parts.append(f"({expr})")
                    else:
                        where_parts.append(f"{expr} IS NOT NULL")
    rel = resolve_relative_range(question)
    if rel:
        date_col = pick_date_column(table)
        if not date_col:
            short = plan.table_short.lower()
            if "live_classes_attendance" in short:
                date_col = "slot_date"
        if date_col:
            where_parts.append(date_filter_sql(date_col, rel[0], rel[1]))

    from table_routing import sql_filters_for_table

    if not skip_defaults:
        for clause in sql_filters_for_table(question, table):
            if clause not in where_parts:
                where_parts.append(clause)

    where_parts = _dedupe_clauses(where_parts)

    try:
        from user_id_filter import extract_user_ids_from_text, user_id_in_sql

        pasted = extract_user_ids_from_text(question or "")
        if pasted:
            clause = user_id_in_sql(pasted)
            if clause and clause not in where_parts:
                where_parts.append(clause)
    except Exception:
        pass

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


def compose_breakdown_with_join(
    plan: MeasurePlan,
    question: str,
    base_sem,
    target_sem,
    relation,
    dim_id: str,
    *,
    base_alias: str = "b",
    target_alias: str = "t",
) -> str:
    """GROUP BY a dimension from a related table (RAG compose path)."""
    from join_resolver import materialize_join_on

    fq = plan.table_fq
    target_fq = target_sem.full_table_id
    join_on = materialize_join_on(
        relation.join_sql,
        base_alias=base_alias,
        base_sem=base_sem,
        target_alias=target_alias,
        target_sem=target_sem,
    )

    m = plan.measure
    func = (m.func or "count").lower().replace(" ", "_")
    col = m.of_column
    if func in ("avg", "average") and col:
        agg = f"AVG(CAST({base_alias}.`{col}` AS FLOAT64))"
    elif func == "count_distinct" and col:
        agg = f"COUNT(DISTINCT {base_alias}.`{col}`)"
    elif func == "count":
        agg = "COUNT(*)"
    else:
        agg = _aggregate_sql(plan, base_sem)

    dim_expr = f"{target_alias}.`{dim_id}`"
    dim_def = target_sem.dimension_by_id(dim_id)
    if dim_def and dim_def.expr_sql:
        dim_expr = target_sem.dim_sql(dim_id).replace("`", f"{target_alias}.`")
    alias = _alias_for_measure(plan)

    where_parts: list[str] = list(plan.filters)
    if col and func in ("avg", "average"):
        where_parts.append(f"{base_alias}.`{col}` IS NOT NULL")
    where_parts.append(f"{dim_expr} IS NOT NULL")

    rel = resolve_relative_range(question)
    if rel:
        date_col = pick_date_column(type("T", (), {"full_table_id": fq})())
        if date_col:
            clause = date_filter_sql(date_col, rel[0], rel[1])
            where_parts.append(clause.replace(f"`{date_col}`", f"{base_alias}.`{date_col}`"))

    where_parts = _dedupe_clauses(where_parts)
    where_sql = "\n  AND ".join(where_parts) if where_parts else ""

    sql = (
        f"SELECT {dim_expr} AS `{dim_id}`, {agg} AS `{alias}`\n"
        f"FROM `{fq}` AS {base_alias}\n"
        f"INNER JOIN `{target_fq}` AS {target_alias}\n"
        f"  ON {join_on}"
    )
    if where_sql:
        sql += f"\nWHERE {where_sql}"
    sql += f"\nGROUP BY `{dim_id}`\nORDER BY `{alias}` DESC"
    return sql


def enrich_schema_with_measures(schema_text: str, table: object) -> str:
    from semantic_layer import measures_block, semantic_for_table
    from table_business_rules import get_table_business_rules

    sem = semantic_for_table(table)
    if not sem:
        return schema_text
    block = measures_block(sem)
    rules = get_table_business_rules(table)
    if rules:
        short = getattr(table, "full_table_id", "").rsplit(".", 1)[-1]
        rule_lines = [
            f"# Table business rules for `{short}` (follow when composing measures):",
            *[f"#   {ln.strip()[:240]}" for ln in rules.splitlines() if ln.strip()][:20],
        ]
        block = "\n".join(rule_lines) + (("\n" + block) if block else "")
    if block and block not in schema_text:
        return f"{schema_text}\n\n{block}"
    return schema_text
