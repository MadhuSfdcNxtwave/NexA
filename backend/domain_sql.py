"""Deterministic SQL for domain-pinned and compound multi-table domain questions."""
from __future__ import annotations

from typing import Any

from ask_plan import domain_table_override


def resolve_compound_domain_sql(
    question: str,
    tables: list[Any],
) -> tuple[str, Any, str] | None:
    """
    JOIN SQL for well-known compound domain pairs (attendance ∩ portal, etc.).
    Falls back to None so AI + join hints can handle unknown compounds.
    """
    from question_dates import date_filter_sql, pick_date_column, resolve_relative_range
    from table_routing import (
        compound_domain_table_ids,
        detect_domain_signals,
        is_compound_domain_question,
    )

    if not is_compound_domain_question(question):
        return None

    ids = compound_domain_table_ids(question, tables)
    if len(ids) < 2:
        return None

    by_fq = {t.full_table_id: t for t in tables}
    signals = detect_domain_signals(question)

    if "live_class_attendance" in signals and "learning_portal" in signals:
        attend_fq = next(
            (fq for fq in ids if "live_classes_attendance" in fq),
            ids[0],
        )
        master_fq = next(
            (fq for fq in ids if "master_data" in fq),
            ids[1] if len(ids) > 1 else None,
        )
        if not master_fq:
            return None
        attend = by_fq.get(attend_fq)
        if not attend:
            return None

        where_parts: list[str] = []
        rel = resolve_relative_range(question)
        date_col = pick_date_column(attend) or "slot_date"
        if rel:
            filt = date_filter_sql(date_col, rel[0], rel[1])
            where_parts.append(filt.replace(f"`{date_col}`", f"a.`{date_col}`"))
        where_parts.extend(
            [
                "a.`attendance_status` = 'JOINED'",
                "m.`pause_status` IS NULL",
                "m.`learning_portal_onboarding_access_given_datetime` IS NOT NULL",
            ]
        )
        sql = (
            "SELECT COUNT(DISTINCT a.`user_id`) AS `unique_users`\n"
            f"FROM `{attend_fq}` a\n"
            f"INNER JOIN `{master_fq}` m\n"
            "  ON REPLACE(a.`user_id`, '-', '') = m.`user_id`\n"
            "WHERE " + "\n  AND ".join(where_parts)
        )
        short = attend_fq.rsplit(".", 1)[-1]
        return sql, attend, f"Compound domain SQL (attendance ∩ portal) on `{short}`"

    return None


def resolve_domain_sql(
    question: str,
    tables: list[Any],
) -> tuple[str, Any, str] | None:
    """
    Build validated SQL for well-known question shapes.
    Compound multi-table questions use JOIN templates when known; otherwise AI + join hints.
    Returns (sql, table_obj, reason) or None when not a pinned domain question.
    """
    compound = resolve_compound_domain_sql(question, tables)
    if compound:
        return compound

    from table_routing import is_compound_domain_question

    if is_compound_domain_question(question):
        return None

    pinned = domain_table_override(question, tables)
    if not pinned:
        return None
    table = next((t for t in tables if t.full_table_id == pinned[0]), None)
    if not table:
        return None

    from measure_router import try_build_measure_plan
    from sql_composer import compose_sql

    plan = try_build_measure_plan(question, [table], catalog_tables=[table])
    if not plan:
        return None
    sql = compose_sql(plan, question, table)
    short = table.full_table_id.rsplit(".", 1)[-1]
    return sql, table, f"Domain SQL on `{short}`"


def is_domain_question(question: str, tables: list[Any]) -> bool:
    from table_routing import is_compound_domain_question

    if is_compound_domain_question(question):
        return True
    return bool(domain_table_override(question, tables))
