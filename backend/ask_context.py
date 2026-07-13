"""Unified question understanding — step 1 of every Ask (context before SQL)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from question_intent import (
    detect_intent,
    expand_question_abbreviations,
    question_asks_growth_cycle_count,
    question_wants_breakdown,
)
from schema_entities import (
    build_schema_entities,
    match_entities_in_question,
    primary_matched_entity,
    sql_hint_for_entities,
)

_STUDENT = re.compile(r"\b(student|students|user|users|learner|learners)\b", re.I)
_COACH = re.compile(r"\b(coach|coaches|success coach)\b", re.I)
_NPS = re.compile(r"\bnps\b|net promoter|promoter|detractor", re.I)
_JOB = re.compile(r"\b(job|jobs|placement|hiring|applied)\b", re.I)
_FEEDBACK = re.compile(r"\b(feedback|survey|response|valuable|rating)\b", re.I)
_TIME = re.compile(
    r"\b(trend|monthly|weekly|daily|over time|by month|by week|mom|yoy)\b",
    re.I,
)

_ENTITY_LABELS = {
    "growth_cycle": "growth cycles",
    "users": "students and users",
    "coaches": "success coaches",
    "nps": "NPS and ratings",
    "jobs": "jobs and placements",
    "feedback": "feedback and surveys",
    "time_series": "trends over time",
    "general": "your data",
}


@dataclass
class QueryContext:
    """Normalized understanding of what the user is asking — used across SQL + presentation."""

    original_question: str
    question: str
    intent: str
    wants_breakdown: bool
    entity: str
    entity_label: str
    sql_entity_hint: str
    understanding_message: str
    presentation_hints: list[str] = field(default_factory=list)
    schema_entities: list[Any] = field(default_factory=list)
    matched_entity_label: str = ""
    pasted_user_ids: list[str] = field(default_factory=list)


def _detect_entity(question: str) -> str:
    q = question or ""
    if question_asks_growth_cycle_count(q) or (
        re.search(r"\bgrowth\s*cycle", q, re.I) and question_wants_breakdown(q)
    ):
        return "growth_cycle"
    if _NPS.search(q):
        return "nps"
    if _JOB.search(q):
        return "jobs"
    if _FEEDBACK.search(q):
        return "feedback"
    if _COACH.search(q):
        return "coaches"
    if _STUDENT.search(q):
        return "users"
    if _TIME.search(q):
        return "time_series"
    return "general"


def _static_sql_hint(entity: str, wants_breakdown: bool) -> str:
    """Generic, schema-agnostic guidance — table/column specifics come from
    the selected tables' schema and AI profiles (enrich_query_context)."""
    hints = {
        "users": "Count DISTINCT on the user identifier column for headcount questions.",
        "nps": (
            "NPS = (promoters − detractors) / total responses × 100 using the 0–10 score column: "
            "promoters ≥ 9, detractors ≤ 6. Do not answer NPS with a plain AVG unless asked for average."
        ),
        "time_series": "Break down by month/week using the table's primary date column.",
    }
    base = hints.get(entity, "")
    if wants_breakdown:
        base += " Use GROUP BY on the breakdown dimension."
    return base.strip()


def _presentation_hints(
    entity: str,
    wants_breakdown: bool,
    matched_label: str = "",
    *,
    question: str = "",
    row_count: int = 0,
) -> list[str]:
    from question_intent import expand_question_abbreviations, question_needs_deep_analysis

    q = expand_question_abbreviations(question or "")
    out = [
        "Lead with the direct answer in plain English — no SQL or table names.",
        "Only use numbers that appear in the query result; do not invent trends.",
    ]
    topic = matched_label or _ENTITY_LABELS.get(entity, "the metric")
    out.append(f"The question is about {topic} — answer that topic directly.")
    if question_needs_deep_analysis(q) or wants_breakdown or row_count > 1:
        out.append(
            "Cover every part of the question: what happened, why it matters, "
            "how to interpret the numbers, and any caveats."
        )
        if wants_breakdown or row_count > 1:
            out.append("Name the top categories with their values; keep it scannable.")
    else:
        out.append("Give a clear answer with context — not just a lone number.")
    return out


def build_query_context(
    question: str,
    *,
    original_question: str = "",
    has_thread_history: bool = False,
    prior_question: str = "",
    prior_sql: str = "",
) -> QueryContext:
    """Step 1a: question-only context (before table routing)."""
    orig = (original_question or question or "").strip()
    expanded = expand_question_abbreviations((question or "").strip())
    intent = detect_intent(expanded, has_thread_history=has_thread_history)
    wants_bd = question_wants_breakdown(expanded)
    entity = _detect_entity(expanded)
    label = _ENTITY_LABELS.get(entity, _ENTITY_LABELS["general"])

    from user_id_filter import resolve_user_ids

    pasted_ids = resolve_user_ids(
        orig or expanded,
        prior_question=prior_question,
        prior_sql=prior_sql,
    )
    if not pasted_ids:
        pasted_ids = resolve_user_ids(
            expanded,
            prior_question=prior_question,
            prior_sql=prior_sql,
        )

    if intent == "explain_prior":
        msg = "Reviewing your previous answers…"
    elif intent == "assistant":
        msg = "Understanding your message…"
    elif intent == "knowledge_query":
        msg = "Looking up what that means…"
    elif pasted_ids:
        n = len(pasted_ids)
        msg = f"Understanding your question about {n} pasted user id{'s' if n != 1 else ''}…"
    elif wants_bd:
        msg = f"Understanding your question — breakdown by {label}…"
    else:
        msg = f"Understanding your question about {label}…"

    return QueryContext(
        original_question=orig,
        question=expanded,
        intent=intent,
        wants_breakdown=wants_bd,
        entity=entity,
        entity_label=label,
        sql_entity_hint=_static_sql_hint(entity, wants_bd),
        understanding_message=msg,
        presentation_hints=_presentation_hints(entity, wants_bd, question=expanded),
        pasted_user_ids=pasted_ids,
    )


def enrich_query_context(
    ctx: QueryContext,
    tables: list[Any],
    columns_by_table: dict[str, set[str]],
) -> QueryContext:
    """Step 1b: merge AI overview + column discovery after tables are selected."""
    entities = build_schema_entities(tables, columns_by_table)
    dynamic_hint = sql_hint_for_entities(
        ctx.question,
        entities,
        wants_breakdown=ctx.wants_breakdown,
    )
    hints = [h for h in (ctx.sql_entity_hint, dynamic_hint) if h.strip()]
    merged_hint = "\n\n".join(hints)

    primary = primary_matched_entity(ctx.question, entities)
    matched_label = primary.label if primary else ""
    if matched_label:
        entity_label = matched_label
        if ctx.wants_breakdown:
            msg = f"Understanding your question — breakdown by {matched_label}…"
        else:
            msg = f"Understanding your question about {matched_label}…"
    else:
        entity_label = ctx.entity_label
        msg = ctx.understanding_message

    matched = match_entities_in_question(ctx.question, entities)
    entity_key = ctx.entity
    if primary and primary.label:
        entity_key = primary.label.replace(" ", "_")[:40]

    return QueryContext(
        original_question=ctx.original_question,
        question=ctx.question,
        intent=ctx.intent,
        wants_breakdown=ctx.wants_breakdown,
        entity=entity_key,
        entity_label=entity_label,
        sql_entity_hint=merged_hint,
        understanding_message=msg,
        presentation_hints=_presentation_hints(
            ctx.entity, ctx.wants_breakdown, matched_label, question=ctx.question
        ),
        schema_entities=entities,
        matched_entity_label=matched_label,
        pasted_user_ids=list(ctx.pasted_user_ids or []),
    )
