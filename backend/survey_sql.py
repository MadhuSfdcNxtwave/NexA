"""Detect survey-prompt questions and build answer-distribution SQL."""
from __future__ import annotations

import re

_SURVEY_PROMPT = re.compile(
    r"\b("
    r"which of these|find most valuable|did you find|rate your|how satisfied|"
    r"how likely|recommend|select all|choose all|pick one|user_answer|question_text"
    r")\b",
    re.I,
)
_FEEDBACK_TABLE = re.compile(r"contextual_feedback|feedback_details|nps_form|survey", re.I)


def is_survey_answer_question(question: str) -> bool:
    """User pasted or references a survey prompt — count answers, not keyword search."""
    q = (question or "").strip()
    if not q:
        return False
    if _SURVEY_PROMPT.search(q):
        return True
    # Long questions ending in ? are often analytics — only treat as survey if survey-like.
    if len(q) > 50 and q.rstrip().endswith("?"):
        if re.search(
            r"\b(survey|feedback|user_answer|question_text|did you find|most valuable|"
            r"rate your|how likely|recommend|select all|choose all)\b",
            q,
            re.I,
        ):
            return True
        return False
    return False


def _pick_feedback_table(tables: list, columns_by_table: dict[str, set[str]]) -> tuple[str, set[str]] | None:
    for t in tables:
        fq = t.full_table_id
        short = fq.rsplit(".", 1)[-1].lower()
        if not _FEEDBACK_TABLE.search(short):
            continue
        cols = columns_by_table.get(fq) or set()
        if "question_text" in cols and "user_answer" in cols:
            return fq, cols
    return None


def _escape_sql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def try_build_survey_sql(
    question: str,
    tables: list,
    columns_by_table: dict[str, set[str]],
) -> str | None:
    """Return GROUP BY user_answer SQL when question is a survey prompt."""
    if not is_survey_answer_question(question):
        return None
    picked = _pick_feedback_table(tables, columns_by_table)
    if not picked:
        return None
    fq, cols = picked
    qtext = question.strip().rstrip("?").strip()
    escaped = _escape_sql_string(qtext)
    date_col = "submitted_date" if "submitted_date" in cols else None

    where_parts = [
        f"TRIM(question_text) = '{escaped}'",
        f"LOWER(question_text) LIKE LOWER('%{escaped[:60]}%')" if len(escaped) > 20 else None,
    ]
    where_parts = [p for p in where_parts if p]
    where_clause = " OR ".join(f"({p})" for p in where_parts)

    select_cols = ["user_answer", "COUNT(*) AS response_count"]
    if "question_type" in cols:
        select_cols.insert(0, "question_type")
    group_cols = [c for c in ("question_type", "user_answer") if c in cols or c == "user_answer"]

    sql = (
        f"SELECT\n  {', '.join(select_cols)}\n"
        f"FROM `{fq}`\n"
        f"WHERE ({where_clause})\n"
        f"  AND user_answer IS NOT NULL AND TRIM(CAST(user_answer AS STRING)) != ''\n"
    )
    if date_col:
        sql += f"  AND {date_col} IS NOT NULL\n"
    sql += f"GROUP BY {', '.join(group_cols)}\n"
    sql += "ORDER BY response_count DESC\n"
    sql += "LIMIT 50"
    return sql
