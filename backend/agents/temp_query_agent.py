"""
Temporary Query Agent — stopgap for complex NL → SQL until full agent stack matures.

Purpose:
  Plan the question (metric / date / breakdown / filters), then compose SQL from
  known templates. Prefer asking for clarification over returning a wrong aggregate.

This is intentionally a "dummy" agent module: rule-based, no LLM required for the
happy path. Swap for a real multi-agent planner later without changing ask_pipeline
beyond the bridge call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class AgentPlan:
    """Structured intent the temp agent extracts from a question."""

    metric: str = ""  # nps_score | attendance_count | placement_count | portal_activity | unknown
    date_hint: str = ""  # last_n_months | last_month | last_n_days | yesterday | none
    breakdown: str = ""  # month | page | aspect | none
    filters: list[str] = field(default_factory=list)
    needs_list: bool = False  # user_id list drill-down
    confidence: float = 0.0
    reason: str = ""
    clarify: dict[str, Any] | None = None


@dataclass
class AgentResult:
    sql: str | None = None
    reason: str = ""
    plan: AgentPlan | None = None
    clarify: dict[str, Any] | None = None
    source: str = "temp_agent"


_NPS = re.compile(r"\bnps\b|net promoter", re.I)
_SCORE = re.compile(r"\bscore|scores|rating\b", re.I)
_ATTEND = re.compile(r"\battend|live\s*class", re.I)
_PLACED = re.compile(r"\bplaced|placement\b", re.I)
_PORTAL = re.compile(r"\blearning\s*portal|portal\b", re.I)
_ACTIVITY = re.compile(r"\bactivity|page|pages\b", re.I)
_LPA = re.compile(r"\b(\d+(?:\.\d+)?)\s*lpa\b", re.I)
_ABOVE = re.compile(r"\b(above|over|greater than|>\s*)\s*(\d+(?:\.\d+)?)", re.I)
_MONTHS = re.compile(
    r"\b(?:last|past|previous)\s+"
    r"(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\d+)\s+months?\b",
    re.I,
)
_LAST_MONTH = re.compile(r"\b(?:last|past|previous)\s+month\b", re.I)
_LAST_DAYS = re.compile(r"\b(?:last|past|previous)\s+(\d+)\s+days?\b", re.I)


def plan_question(question: str) -> AgentPlan:
    """Extract a structured plan from the user question (no LLM)."""
    from question_intent import expand_question_abbreviations, is_drill_down_data_request

    q = expand_question_abbreviations((question or "").strip())
    plan = AgentPlan()

    if is_drill_down_data_request(q):
        plan.needs_list = True
        plan.metric = "user_list"
        plan.confidence = 0.9
        plan.reason = "Drill-down: list user_ids from prior filters"
        return plan

    if _NPS.search(q) and (_SCORE.search(q) or _MONTHS.search(q) or re.search(r"\bmonth", q, re.I)):
        plan.metric = "nps_score"
        plan.confidence = 0.92
        if _MONTHS.search(q) or re.search(r"\bmonth", q, re.I):
            plan.breakdown = "month"
            plan.date_hint = "last_n_months" if _MONTHS.search(q) else "by_month"
        elif _LAST_MONTH.search(q):
            plan.date_hint = "last_month"
        plan.reason = "NPS score (promoters - detractors) / total - not unique_responders"
        return plan

    if _ATTEND.search(q):
        plan.metric = "attendance_count"
        plan.confidence = 0.88
        if _LAST_DAYS.search(q):
            plan.date_hint = "last_n_days"
        elif _LAST_MONTH.search(q):
            plan.date_hint = "last_month"
        elif re.search(r"\byesterday\b", q, re.I):
            plan.date_hint = "yesterday"
        plan.reason = "Live class attendance (JOINED)"
        return plan

    if _PLACED.search(q):
        plan.metric = "placement_count"
        plan.confidence = 0.85
        if _LAST_MONTH.search(q):
            plan.date_hint = "last_month"
        elif _MONTHS.search(q):
            plan.date_hint = "last_n_months"
        m = _LPA.search(q) or _ABOVE.search(q)
        if m:
            # Prefer explicit LPA number
            lpa_m = _LPA.search(q)
            if lpa_m:
                plan.filters.append(f"ctc_in_lpa >= {lpa_m.group(1)}")
            else:
                plan.filters.append(f"ctc_in_lpa >= {m.group(2)}")
        plan.reason = "Placement count with optional CTC filter"
        return plan

    if _PORTAL.search(q) and _ACTIVITY.search(q):
        plan.metric = "portal_activity"
        plan.breakdown = "page"
        plan.confidence = 0.9
        plan.reason = "Portal page activity breakdown"
        return plan

    plan.metric = "unknown"
    plan.confidence = 0.2
    plan.reason = "No strong template match"
    return plan


def _find_table(tables: list[Any], needle: str) -> Any | None:
    n = needle.lower()
    for t in tables:
        short = t.full_table_id.rsplit(".", 1)[-1].lower()
        if n in short:
            return t
    return None


def _compose_nps_monthly(
    question: str,
    tables: list[Any],
    columns_by_table: dict[str, set[str]],
) -> str | None:
    from nps_sql import try_build_nps_sql

    return try_build_nps_sql(question, tables, columns_by_table)


def _compose_placement(
    question: str,
    plan: AgentPlan,
    tables: list[Any],
) -> str | None:
    from question_dates import date_filter_sql, pick_date_column, resolve_question_date_range

    table = _find_table(tables, "placements_details")
    if not table:
        return None
    fq = table.full_table_id
    where: list[str] = []
    rel = resolve_question_date_range(question)
    date_col = pick_date_column(table) or "date_of_placement"
    if rel:
        where.append(date_filter_sql(date_col, rel[0], rel[1]))
    for f in plan.filters:
        m = re.match(r"(ctc_in_lpa)\s*(>=|<=|>|<)\s*([\d.]+)", f)
        if m:
            where.append(f"`{m.group(1)}` {m.group(2)} {m.group(3)}")
    where_sql = ("\nWHERE " + " AND ".join(where)) if where else ""
    return (
        f"SELECT COUNT(DISTINCT `user_id`) AS `unique_placed_users`\n"
        f"FROM `{fq}`{where_sql}"
    )


def _compose_attendance(
    question: str,
    tables: list[Any],
) -> str | None:
    from question_dates import date_filter_sql, pick_date_column, resolve_relative_range

    table = _find_table(tables, "live_classes_attendance")
    if not table:
        return None
    fq = table.full_table_id
    where = ["`attendance_status` = 'JOINED'"]
    rel = resolve_relative_range(question)
    date_col = pick_date_column(table) or "slot_date"
    if rel:
        where.append(date_filter_sql(date_col, rel[0], rel[1]))
    return (
        f"SELECT COUNT(DISTINCT `user_id`) AS `unique_users`\n"
        f"FROM `{fq}`\n"
        f"WHERE {' AND '.join(where)}"
    )


def _compose_portal_activity(question: str, tables: list[Any]) -> str | None:
    from join_compose import compose_portal_activity_by_page_sql

    return compose_portal_activity_by_page_sql(question, tables)


def run_temp_query_agent(
    question: str,
    tables: list[Any],
    columns_by_table: dict[str, set[str]] | None = None,
    *,
    prior_sql: str = "",
) -> AgentResult:
    """
    Main entry: plan → compose → return SQL or clarification.
    Call this before planner/LLM fallback for complex or high-risk questions.
    """
    columns_by_table = columns_by_table or {}
    plan = plan_question(question)

    # Drill-down from prior aggregate (paginated)
    if plan.needs_list and prior_sql:
        from question_intent import parse_list_page_request, rewrite_aggregate_to_user_list_sql

        page_info = parse_list_page_request(question, prior_sql=prior_sql)
        sql = rewrite_aggregate_to_user_list_sql(
            prior_sql,
            page=page_info["page"],
            page_size=page_info["page_size"],
        )
        if sql:
            return AgentResult(sql=sql, reason=plan.reason, plan=plan, source="temp_agent_drill")

    if plan.metric == "nps_score":
        sql = _compose_nps_monthly(question, tables, columns_by_table)
        if sql:
            return AgentResult(sql=sql, reason=plan.reason, plan=plan)
        return AgentResult(
            reason="NPS score requested but could not compose SQL",
            plan=plan,
            clarify={
                "prompt": "I understood you want NPS scores. Confirm the time range:",
                "options": [
                    {
                        "id": "last3",
                        "label": "Last 3 full months (monthly NPS score)",
                        "refined_question": "What are the last three months NPS scores by month",
                    },
                    {
                        "id": "last1",
                        "label": "Last month only (June if today is July)",
                        "refined_question": "What is the NPS score for last month",
                    },
                ],
                "allow_custom": True,
                "confirm_mode": True,
            },
        )

    if plan.metric == "placement_count":
        sql = _compose_placement(question, plan, tables)
        if sql:
            return AgentResult(sql=sql, reason=plan.reason, plan=plan)

    if plan.metric == "attendance_count":
        sql = _compose_attendance(question, tables)
        if sql:
            return AgentResult(sql=sql, reason=plan.reason, plan=plan)

    if plan.metric == "portal_activity":
        sql = _compose_portal_activity(question, tables)
        if sql:
            return AgentResult(sql=sql, reason=plan.reason, plan=plan)
        return AgentResult(
            reason="Portal activity ambiguous",
            plan=plan,
            clarify={
                "prompt": "Are you asking about portal pages or events?",
                "options": [
                    {
                        "id": "pages",
                        "label": "Portal pages (time spent per page)",
                        "refined_question": (
                            "Which learning portal pages are students actively using"
                        ),
                    },
                    {
                        "id": "events",
                        "label": "Events / webinars",
                        "refined_question": (
                            "Which events are students engaging with in the learning portal"
                        ),
                    },
                ],
                "allow_custom": True,
                "confirm_mode": True,
            },
        )

    return AgentResult(reason=plan.reason or "no match", plan=plan)


def should_run_temp_agent(question: str) -> bool:
    """True when the temp agent should try before planner/LLM."""
    plan = plan_question(question)
    return plan.confidence >= 0.8 or plan.metric in (
        "nps_score",
        "portal_activity",
        "attendance_count",
        "placement_count",
        "user_list",
    )
