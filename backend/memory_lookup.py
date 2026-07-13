"""Exact-question lookup in Thread memory before SQL generation."""
from __future__ import annotations

import re
from typing import Any

from question_intent import (
    expand_question_abbreviations,
    question_asks_growth_cycle_count,
    question_wants_breakdown,
)
from schema_entities import validate_sql_for_question, validate_result_for_question
from result_cache import (
    _cache_supports_time_series,
    _is_usable_cache_entry,
    is_time_series_question,
)

_DIMENSION_IN_QUESTION = (
    (re.compile(r"growth\s*cycle", re.I), re.compile(r"growth_cycle|cycle_title|cycle_name", re.I)),
    (
        re.compile(r"\bby month\b|\beach month\b|\bper month\b|\bmonthly trend\b|\bmonth over month\b", re.I),
        re.compile(r"month|date|period", re.I),
    ),
    (re.compile(r"\bby gender\b|\bgender wise\b|\bbreakdown.*\bgender\b|\bgender\b.*\bby\b", re.I), re.compile(r"gender", re.I)),
    (re.compile(r"\bby (state|city|region|category|segment)\b", re.I), re.compile(r"state|city|region|category|segment", re.I)),
)


_BAD_FEEDBACK_ANALYSIS = re.compile(
    r"No feedback rows matched|No rows matched that exact survey wording",
    re.I,
)
_BAD_RECOVERY_ANALYSIS = re.compile(
    r"do not contain information about|"
    r"tables available to answer your question do not|"
    r"could you clarify what you mean",
    re.I,
)


def _is_bad_stored_analysis(question: str, analysis: str) -> bool:
    """Reject stale placeholders and LLM recovery text saved as answers."""
    if not analysis:
        return False
    if _BAD_RECOVERY_ANALYSIS.search(analysis):
        return True
    if not _BAD_FEEDBACK_ANALYSIS.search(analysis):
        return False
    from nps_sql import is_nps_analytics_question

    if is_nps_analytics_question(question):
        return True
    if re.search(r"\b(average|avg|count|how many)\b.+\bby\b", question, re.I):
        return True
    return False


def normalize_question(question: str) -> str:
    """Stable key for matching repeat questions in the same project."""
    q = expand_question_abbreviations(question).strip().lower()
    q = re.sub(r"\s+", " ", q)
    return q.rstrip("?.!").strip()


def sql_intent_mismatch_reason(
    question: str,
    sql: str,
    *,
    schema_entities: list | None = None,
    query_plan: Any | None = None,
) -> str | None:
    """None when SQL shape plausibly answers the question; else a short reason."""
    q = expand_question_abbreviations((question or "").strip())
    sql_text = (sql or "").strip()
    if not q or not sql_text:
        return "empty question or SQL"

    if query_plan is not None:
        from query_planner import sql_plan_shape_mismatch_reason

        plan_reason = sql_plan_shape_mismatch_reason(q, sql_text, query_plan)
        if plan_reason:
            return plan_reason

    if schema_entities:
        ok, reason = validate_sql_for_question(q, sql_text, schema_entities)
        if not ok:
            return reason

    from query_planner import (
        active_portal_sql_shape_ok,
        is_nps_topic_feedback_question,
        nps_topic_sql_shape_ok,
    )

    if is_nps_topic_feedback_question(q) and not nps_topic_sql_shape_ok(sql_text):
        return "NPS topic feedback requires union SQL across both NPS form tables"

    # Feedback questions must not land on portal time-spent page activity SQL.
    if re.search(r"\b(feedback|emoji|survey|user_answer|question_text)\b", q, re.I) and not re.search(
        r"\bnps\b", q, re.I
    ):
        if re.search(
            r"day_and_page_wise_time_spent|time_spent_page|portal_activity",
            sql_text,
            re.I,
        ):
            return "feedback question must use contextual feedback SQL, not portal time-spent"
        # Feature-scoped feedback must not be whole-table unique_users.
        try:
            from feedback_sql import feature_scope_terms

            scope = feature_scope_terms(q)
        except Exception:
            scope = []
        if scope and re.search(r"contextual_feedback", sql_text, re.I):
            if re.search(r"COUNT\s*\(\s*DISTINCT\s+`?user_id", sql_text, re.I) and not re.search(
                r"LIKE\s+'%", sql_text, re.I
            ):
                return (
                    "feature feedback count must filter feedback_trigger/question_text "
                    f"(expected terms like {', '.join(scope[:3])})"
                )

    if re.search(
        r"\bactive\b.{0,40}\b(learning[\s_-]*portal|portal)\b|"
        r"\b(learning[\s_-]*portal|portal)\b.{0,40}\bactive\b",
        q,
        re.I,
    ) and re.search(r"\bhow many\b|\bcount\b|\bnumber of\b", q, re.I):
        if not active_portal_sql_shape_ok(sql_text, question=q):
            return "active portal user count requires master-data or lp_status SQL"

    if question_asks_growth_cycle_count(q):
        if re.search(r"\bCOUNT\s*\(\s*DISTINCT\s+`?user_id", sql_text, re.I):
            return "growth cycle count must not use COUNT(DISTINCT user_id)"
        if not re.search(r"growth_cycle", sql_text, re.I):
            return "growth cycle question must reference growth_cycle column"

    if question_wants_breakdown(q) and not re.search(r"\bGROUP BY\b", sql_text, re.I):
        return "breakdown question requires GROUP BY"

    if re.search(r"^\s*(which|what)\b", q, re.I) and re.search(
        r"\b(activity|aspect|feature|page|category|program|improv)\b", q, re.I
    ):
        if re.search(r"SELECT\s+COUNT\s*\(\s*(?:DISTINCT\s+)?", sql_text, re.I):
            if not re.search(r"\bGROUP BY\b", sql_text, re.I):
                return "'which/what' question requires GROUP BY breakdown, not scalar COUNT"

    if re.search(r"COUNT\s*\(\s*DISTINCT\s+['\"]", sql_text, re.I):
        return "COUNT(DISTINCT 'literal') is invalid"

    for q_pat, sql_or_col_pat in _DIMENSION_IN_QUESTION:
        if not q_pat.search(q):
            continue
        if not sql_or_col_pat.search(sql_text):
            return "SQL missing dimension column referenced in the question"

    if re.search(r"\bnps\b", q, re.I) and not re.search(r"\baverage|avg\b", q, re.I):
        if re.search(r"\bAVG\s*\(", sql_text, re.I) and not re.search(
            r"COUNTIF|promoter|detractor|nps_score", sql_text, re.I
        ):
            return "NPS score questions need COUNTIF/promoter logic, not AVG alone"

    # NPS score / monthly NPS must not collapse to unique_responders COUNT.
    if re.search(r"\bnps\b", q, re.I) and re.search(
        r"\b(score|scores|rating|monthly|last\s+\w+\s+months?)\b", q, re.I
    ):
        if re.search(r"unique_responders|COUNT\s*\(\s*DISTINCT\s+[`\"]?user_id", sql_text, re.I):
            if not re.search(r"nps_score|COUNTIF|promoter|detractor", sql_text, re.I):
                return "NPS score question must compute nps_score, not unique_responders count"
        if re.search(r"\bmonth", q, re.I) and not re.search(r"\bGROUP BY\b", sql_text, re.I):
            return "monthly NPS scores require GROUP BY month"

    # Promoter / detractor / passive counts must filter the rating band.
    if re.search(r"\b(promoters?|detractors?|passives?)\b", q, re.I):
        if re.search(r"unique_responders", sql_text, re.I) and not re.search(
            r"promoter|detractor|passive|BETWEEN|>=\s*9|<=\s*6", sql_text, re.I
        ):
            return "promoter/detractor questions must filter rating_on_scale_of_0_to_10"
        if re.search(r"\bpromoter", q, re.I) and not re.search(
            r">=\s*9|BETWEEN\s*9\s*AND\s*10|promoter", sql_text, re.I
        ):
            return "promoter count requires rating 9–10 filter"
        if re.search(r"\bdetractor", q, re.I) and not re.search(
            r"<=\s*6|BETWEEN\s*0\s*AND\s*6|detractor", sql_text, re.I
        ):
            return "detractor count requires rating 0–6 filter"

    # "this year" / explicit year / period questions must include a date filter in SQL.
    if re.search(
        r"\b(this year|current year|ytd|this month|last month|yesterday|20\d{2})\b",
        q,
        re.I,
    ):
        if re.search(r"\b(placed|placement|got\s+jobs?|promoter|nps|attend)\b", q, re.I):
            if not re.search(
                r"date_of_placement|form_submission|slot_date|calendar_date|"
                r"BETWEEN\s+DATE|EXTRACT\s*\(\s*YEAR|DATE\s*\(",
                sql_text,
                re.I,
            ):
                return "period question requires a date filter in SQL"

    # Raw / CSV / field-wise must not collapse to aggregates.
    try:
        from agents.answer_shape import wants_raw_tabular_data

        if wants_raw_tabular_data(q):
            if re.search(
                r"SELECT\s+COUNT\s*\(|COUNT\s*\(\s*DISTINCT|unique_responders|response_count",
                sql_text,
                re.I,
            ) and not re.search(r"\bGROUP BY\b.{0,80}\buser_answer\b", sql_text, re.I | re.S):
                # Scalar or user-count aggregate is wrong for raw export.
                if re.search(
                    r"COUNT\s*\(\s*DISTINCT\s+[`\"]?user_id|SELECT\s+COUNT\s*\(\s*\*\s*\)",
                    sql_text,
                    re.I,
                ):
                    return "raw/CSV/field-wise request must return row-level columns, not aggregates"
            if re.search(r"SELECT\s+COUNT\s*\(\s*DISTINCT\s+[`\"]?user_id", sql_text, re.I):
                return "raw/CSV/field-wise request must return row-level columns, not aggregates"
    except Exception:
        pass

    return None


def sql_matches_question_intent(
    question: str,
    sql: str,
    *,
    schema_entities: list | None = None,
    query_plan: Any | None = None,
) -> bool:
    """True when SQL shape plausibly answers the question (pre- or post-execution)."""
    return (
        sql_intent_mismatch_reason(
            question,
            sql,
            schema_entities=schema_entities,
            query_plan=query_plan,
        )
        is None
    )


def stored_answer_matches_question(
    question: str,
    *,
    sql: str,
    columns: list[Any],
    rows: list[Any],
    schema_entities: list | None = None,
    summary: str = "",
) -> bool:
    """
    Reject stored answers whose SQL/result shape clearly cannot answer the question.
    Stale wrong answers (e.g. total COUNT for a breakdown question) are regenerated.
    """
    if _is_bad_stored_analysis(question, summary):
        return False
    from table_routing import validate_sql_table_choice

    ok, _ = validate_sql_table_choice(question, sql)
    if not ok:
        return False
    if not (rows or []) and not (sql or "").strip():
        return False
    from query_planner import is_nps_topic_feedback_question, nps_topic_sql_shape_ok

    if is_nps_topic_feedback_question(question) and not nps_topic_sql_shape_ok(
        sql or "", columns=columns
    ):
        return False
    if not sql_matches_question_intent(question, sql, schema_entities=schema_entities):
        return False

    if schema_entities:
        ok, _ = validate_result_for_question(
            question, sql, columns, rows, schema_entities
        )
        if not ok:
            return False

    q = (question or "").strip()
    cols = [str(c).lower() for c in (columns or [])]
    row_count = len(rows or [])

    if is_time_series_question(q):
        entry = {"columns": columns, "rows": rows}
        if not _cache_supports_time_series(entry):
            return False

    if question_wants_breakdown(q):
        # One row + one metric column = scalar aggregate, not a breakdown.
        if row_count <= 1 and len(cols) <= 2:
            return False
        if re.search(r"\bgender\b", q, re.I) and not any("gender" in c for c in cols):
            return False
        by = re.search(r"\bby\s+(\w+)", q, re.I)
        if by:
            dim = by.group(1).lower()
            if not any(dim in str(c).lower() for c in cols):
                return False
        for _q_pat, sql_or_col_pat in _DIMENSION_IN_QUESTION:
            if _q_pat.search(q) and not any(sql_or_col_pat.search(c) for c in cols):
                return False

    if re.search(r"COUNT\s*\(\s*DISTINCT\s+['\"]", sql or "", re.I):
        return False

    return True


def try_exact_memory_hit(
    question: str,
    entries: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    Return a complete ask result when this exact question was answered before
    in the Thread with usable query results. Most recent valid match wins.
    """
    key = normalize_question(question)
    if not key or not entries:
        return None

    thread = [e for e in entries if e.get("source") == "thread"]
    for entry in reversed(thread):
        if normalize_question(entry.get("question") or "") != key:
            continue
        if not _is_usable_cache_entry(entry):
            continue
        if not stored_answer_matches_question(
            question,
            sql=entry.get("sql") or "",
            columns=entry.get("columns") or [],
            rows=entry.get("rows") or [],
        ):
            continue
        chart_spec = entry.get("chart_spec") or {"chart": "none"}
        rows = entry.get("rows") or []
        analysis = (entry.get("summary") or entry.get("analysis") or "").strip()
        if _is_bad_stored_analysis(question, analysis):
            continue
        return {
            "question": question.strip(),
            "sql": entry.get("sql") or "",
            "columns": entry.get("columns") or [],
            "rows": rows,
            "viz_rows": rows,
            "chart_spec": chart_spec,
            "analysis": analysis,
            "bytes_estimate": int(entry.get("bytes_estimate") or 0),
            "from_cache": False,
            "skip_memory_save": True,
            "response_mode": "data",
            "suggestions": [],
        }
    return None
