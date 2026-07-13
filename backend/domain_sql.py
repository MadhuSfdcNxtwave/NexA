"""Deterministic SQL for domain-pinned and compound multi-table domain questions."""
from __future__ import annotations

import re
from typing import Any


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
        where_parts.append("a.`attendance_status` = 'JOINED'")
        master = by_fq.get(master_fq)
        from table_business_rules import table_skips_default_filters

        if not (master and table_skips_default_filters(master)):
            where_parts.extend(
                [
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


def _join_template_table(question: str, tables: list[Any]) -> Any | None:
    from join_compose import _find_table

    if re.search(r"\bplaced\b|\bplacement\b", question, re.I):
        return _find_table(tables, "placements_details")
    if re.search(r"\bnps\b", question, re.I):
        return _find_table(tables, "nps_form_responses") or _find_table(tables, "academy_nps")
    if re.search(r"\blive[\s_-]*class|\battend", question, re.I):
        return _find_table(tables, "live_classes_attendance")
    if re.search(r"\bjob\b|\bappli", question, re.I):
        return _find_table(tables, "jobs_details")
    return tables[0] if tables else None


def resolve_domain_sql(
    question: str,
    tables: list[Any],
) -> tuple[str, Any, str] | None:
    """
    Build validated SQL for well-known question shapes.
    Compound multi-table questions use JOIN templates when known; otherwise AI + join hints.
    Returns (sql, table_obj, reason) or None when not a pinned domain question.

    Never return COUNT aggregates when the user asked for feedback *details* / raw rows.
    """
    from agents.answer_shape import wants_raw_tabular_data

    # Contextual feedback details → row-level export, not unique_users COUNT.
    if wants_raw_tabular_data(question):
        try:
            from feedback_sql import try_build_feedback_sql

            raw = try_build_feedback_sql(
                question, tables, _columns_by_table_hint(tables), relaxed=True
            )
            if raw and "GROUP BY" not in raw.upper() and "COUNT(" not in raw.upper():
                from ask_plan import domain_table_override

                pinned = domain_table_override(question, tables) or []
                table = None
                if pinned:
                    table = next((t for t in tables if t.full_table_id == pinned[0]), None)
                if table is None:
                    table = next(
                        (
                            t
                            for t in tables
                            if "contextual_feedback" in (t.full_table_id or "").lower()
                        ),
                        tables[0] if tables else None,
                    )
                if table is not None:
                    short = table.full_table_id.rsplit(".", 1)[-1]
                    return (
                        raw,
                        table,
                        f"Feedback details (row-level) on `{short}`",
                    )
        except Exception:
            pass
        # Do not fall through to measure COUNT for details/raw questions.
        return None

    compound = resolve_compound_domain_sql(question, tables)
    if compound:
        return compound

    from join_compose import try_compose_join_sql

    join_sql = try_compose_join_sql(question, tables)
    if join_sql:
        table = _join_template_table(question, tables)
        if table:
            short = table.full_table_id.rsplit(".", 1)[-1]
            return join_sql, table, f"Join template SQL on `{short}`"

    from table_routing import is_compound_domain_question

    if is_compound_domain_question(question):
        return None

    from ask_plan import domain_table_override

    pinned = domain_table_override(question, tables)
    if not pinned:
        return None
    table = next((t for t in tables if t.full_table_id == pinned[0]), None)
    if not table:
        return None

    short = table.full_table_id.rsplit(".", 1)[-1]

    # Contextual feedback must never become unfiltered unique_users COUNT —
    # "feedback on calendar page" needs feature filters via feedback_sql.
    if "contextual_feedback" in short.lower():
        try:
            from feedback_sql import try_build_feedback_sql

            fb = try_build_feedback_sql(
                question,
                [table],
                _columns_by_table_hint([table]),
                relaxed=True,
            )
            if fb:
                return fb, table, f"Contextual feedback SQL on `{short}`"
        except Exception:
            pass
        # Refuse bare measure COUNT for this table.
        return None

    from measure_router import try_build_measure_plan
    from sql_composer import compose_sql

    plan = try_build_measure_plan(question, [table], catalog_tables=[table])
    if not plan:
        return None
    sql = compose_sql(plan, question, table)
    return sql, table, f"Domain SQL on `{short}`"


def _columns_by_table_hint(tables: list[Any]) -> dict[str, set[str]]:
    """Best-effort column sets for feedback raw SQL when full schema isn't loaded."""
    out: dict[str, set[str]] = {}
    for t in tables or []:
        fq = getattr(t, "full_table_id", "") or ""
        if not fq:
            continue
        cols: set[str] = set()
        raw = getattr(t, "column_descriptions_json", None) or ""
        if raw:
            try:
                import json

                cols = set(json.loads(raw).keys())
            except Exception:
                cols = set()
        if not cols and "contextual_feedback" in fq.lower():
            cols = {
                "user_id",
                "feedback_id",
                "feedback_trigger",
                "feedback_type",
                "question_id",
                "question_order",
                "question_type",
                "question_text",
                "user_answer",
                "emoji_rating",
                "submitted_date",
                "enroll_plans_str",
                "is_valid_question",
                "is_valid_trigger",
            }
        if cols:
            out[fq] = cols
    return out



def is_domain_question(question: str, tables: list[Any]) -> bool:
    from ask_plan import domain_table_override
    from table_routing import is_compound_domain_question

    if is_compound_domain_question(question):
        return True
    return bool(domain_table_override(question, tables))
