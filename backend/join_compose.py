"""Deterministic JOIN SQL for common cross-table breakdown questions."""
from __future__ import annotations

import re
from typing import Any

from question_dates import date_filter_sql, pick_date_column, resolve_relative_range

_CROSS_TABLE_BREAKDOWN = re.compile(
    r"\bby\s+(state|gender|growth\s*cycle|city|region)\b",
    re.I,
)
_PORTAL_ACTIVITY = re.compile(
    r"\b(?:learning[\s_-]*portal|learningportal|portal)\b.{0,60}\b(activity|activit|page|events?|which)\b|"
    r"\b(which|what)\b.{0,40}\b(activity|page)\b|"
    r"\bin which activity\b|"
    r"\bactiv(?:e|ly|lly)\s+in\b.{0,40}\b(?:learning[\s_-]*portal|learningportal|portal)\b|"
    r"\bevents?\b.{0,40}\b(?:learning[\s_-]*portal|learningportal|portal)\b",
    re.I,
)
_PORTAL_ATTEND_PCT = re.compile(
    r"\b(?:learning[\s_-]*portal|learningportal|portal)\b.{0,80}\b(attendance|attend)\b.{0,40}\b(percent|percentage|%)|"
    r"\battendance\b.{0,40}\b(percent|percentage|%)\b.{0,40}\b(?:learning[\s_-]*portal|learningportal|portal)\b|"
    r"\bevents?\b.{0,60}\b(?:learning[\s_-]*portal|learningportal|portal)\b.{0,60}\b(attendance|attend)\b",
    re.I,
)
_PLACED = re.compile(r"\bplaced\b|\bplacement\b", re.I)
_NPS = re.compile(r"\bnps\b|net promoter", re.I)
_ATTEND_LIST = re.compile(
    r"\b(which|what)\b.{0,40}\b(live\s*class|class|cohort|session)\b",
    re.I,
)


def _fq(table: Any) -> str:
    return table.full_table_id


def _q(question: str) -> str:
    from question_intent import expand_question_abbreviations

    return expand_question_abbreviations(question or "")


def _find_table(tables: list[Any], needle: str) -> Any | None:
    n = needle.lower()
    for t in tables:
        short = t.full_table_id.rsplit(".", 1)[-1].lower()
        if n in short or short == n:
            return t
    return None


def _profile_fq(tables: list[Any], anchor: Any | None = None) -> str:
    """Profile table FQ — Hex uses academy_user_profile_basic_details for state/gender."""
    profile = _find_table(tables, "profile_basic_details")
    if profile:
        return _fq(profile)
    if anchor:
        prefix = _fq(anchor).rsplit(".", 1)[0]
        return f"{prefix}.academy_user_profile_basic_details"
    return ""


def _date_clause(question: str, table: Any, *, alias: str = "", default_col: str = "") -> str:
    rel = resolve_relative_range(question)
    if not rel:
        return ""
    col = pick_date_column(table) or default_col
    if not col:
        return ""
    filt = date_filter_sql(col, rel[0], rel[1])
    if alias:
        filt = filt.replace(f"`{col}`", f"{alias}.`{col}`")
    return filt


def compose_attendance_list_sql(question: str, tables: list[Any]) -> str | None:
    """List/detail SQL for 'which live class yesterday' style questions."""
    if not _ATTEND_LIST.search(question):
        return None
    if re.search(r"\bhow many\b|\bcount\b|\bnumber of\b", question, re.I):
        return None
    attend = _find_table(tables, "live_classes_attendance")
    if not attend:
        return None
    fq = _fq(attend)
    where = ["`attendance_status` = 'JOINED'"]
    date_part = _date_clause(question, attend, default_col="slot_date")
    if date_part:
        where.append(date_part)
    where_sql = "\n  AND ".join(where)
    return f"""SELECT
  `cohort_name`,
  `course_title`,
  `slot_date`,
  `slot_id`,
  COUNT(DISTINCT `user_id`) AS `attendees`
FROM `{fq}`
WHERE {where_sql}
GROUP BY `cohort_name`, `course_title`, `slot_date`, `slot_id`
ORDER BY `slot_date` DESC, `attendees` DESC
LIMIT 200"""


def compose_placed_by_state_sql(question: str, tables: list[Any]) -> str | None:
    if not _PLACED.search(question) or not re.search(r"\bby\s+state\b", question, re.I):
        return None
    placements = _find_table(tables, "placements_details")
    if not placements:
        return None
    pf = _fq(placements)
    bf = _profile_fq(tables, placements)
    if not bf:
        return None
    state_expr = (
        "COALESCE(NULLIF(TRIM(UPPER(b.`current_address_state`)), ''), 'UNKNOWN')"
    )
    return f"""SELECT
  {state_expr} AS `state`,
  COUNT(DISTINCT p.`user_id`) AS `placed_users`
FROM `{pf}` AS p
LEFT JOIN `{bf}` AS b
  ON REPLACE(b.`user_id`, '-', '') = REPLACE(p.`user_id`, '-', '')
GROUP BY `state`
ORDER BY `placed_users` DESC"""


def compose_nps_by_gender_sql(question: str, tables: list[Any]) -> str | None:
    if not _NPS.search(question) or not re.search(r"\bby\s+gender\b", question, re.I):
        return None
    nps = _find_table(tables, "nps_form_responses") or _find_table(tables, "academy_nps")
    master = _find_table(tables, "master_data")
    if not nps or not master:
        return None
    nf, mf = _fq(nps), _fq(master)
    rating_col = "rating_on_scale_of_0_to_10"
    return f"""SELECT
  m.`gender` AS `gender`,
  AVG(CAST(n.`{rating_col}` AS FLOAT64)) AS `avg_nps_rating`,
  COUNT(DISTINCT n.`user_id`) AS `respondents`
FROM `{nf}` n
INNER JOIN `{mf}` m
  ON REPLACE(n.`user_id`, '-', '') = REPLACE(m.`user_id`, '-', '')
WHERE m.`gender` IS NOT NULL
  AND n.`{rating_col}` IS NOT NULL
GROUP BY m.`gender`
ORDER BY `avg_nps_rating` DESC"""


def compose_jobs_by_gender_sql(question: str, tables: list[Any]) -> str | None:
    if not re.search(r"\bjob\b|\bappli", question, re.I) or not re.search(r"\bby\s+gender\b", question, re.I):
        return None
    jobs = _find_table(tables, "jobs_details")
    master = _find_table(tables, "master_data")
    if not jobs or not master:
        return None
    jf, mf = _fq(jobs), _fq(master)
    return f"""SELECT
  m.`gender` AS `gender`,
  COUNT(DISTINCT j.`user_id`) AS `unique_applicants`
FROM `{jf}` j
INNER JOIN `{mf}` m
  ON REPLACE(j.`user_id`, '-', '') = REPLACE(m.`user_id`, '-', '')
WHERE m.`gender` IS NOT NULL
GROUP BY m.`gender`
ORDER BY `unique_applicants` DESC"""


def compose_portal_activity_by_page_sql(question: str, tables: list[Any]) -> str | None:
    """Which portal page/activity active students use — NOT event_engagement."""
    q = _q(question)
    if not _PORTAL_ACTIVITY.search(q):
        return None
    if _PORTAL_ATTEND_PCT.search(q):
        return None
    portal = _find_table(tables, "day_and_page_wise_time_spent")
    if not portal:
        return None
    fq = _fq(portal)
    where = [
        "`lp_status` = 'ACTIVE'",
        "`time_spent_page` IS NOT NULL",
        "`time_spent_date` IS NOT NULL",
    ]
    date_part = _date_clause(q, portal, default_col="time_spent_date")
    if date_part:
        where.append(date_part)
    where_sql = "\n  AND ".join(where)
    return f"""SELECT
  `time_spent_page` AS `portal_activity`,
  COUNT(DISTINCT `user_id`) AS `active_users`,
  SUM(`time_spent_in_mins`) AS `total_mins`,
  ROUND(AVG(`time_spent_in_mins`), 2) AS `avg_mins_per_visit`
FROM `{fq}`
WHERE {where_sql}
GROUP BY `time_spent_page`
ORDER BY `active_users` DESC
LIMIT 50"""


def compose_portal_activity_attendance_pct_sql(question: str, tables: list[Any]) -> str | None:
    """Portal page activity + live class attendance % per page (notebook final cell)."""
    q = _q(question)
    if not _PORTAL_ATTEND_PCT.search(q) and not (
        _PORTAL_ACTIVITY.search(q)
        and re.search(r"\battendance\b|\battend\b", q, re.I)
    ):
        return None
    portal = _find_table(tables, "day_and_page_wise_time_spent")
    attend = _find_table(tables, "live_classes_attendance")
    if not portal or not attend:
        return None
    pf, af = _fq(portal), _fq(attend)
    where = [
        "p.`lp_status` = 'ACTIVE'",
        "p.`time_spent_page` IS NOT NULL",
        "p.`time_spent_date` IS NOT NULL",
    ]
    date_part = _date_clause(q, portal, alias="p", default_col="time_spent_date")
    if date_part:
        where.append(date_part)
    where_sql = "\n  AND ".join(where)
    return f"""SELECT
  p.`time_spent_page` AS `portal_activity`,
  COUNT(DISTINCT p.`user_id`) AS `active_portal_users`,
  COUNT(DISTINCT CASE WHEN a.`attendance_status` = 'JOINED' THEN a.`user_id` END) AS `attended_live_class_users`,
  ROUND(
    100.0 * COUNT(DISTINCT CASE WHEN a.`attendance_status` = 'JOINED' THEN a.`user_id` END)
    / NULLIF(COUNT(DISTINCT p.`user_id`), 0),
    2
  ) AS `live_class_attendance_pct`
FROM `{pf}` AS p
LEFT JOIN `{af}` AS a
  ON REPLACE(p.`user_id`, '-', '') = REPLACE(a.`user_id`, '-', '')
WHERE {where_sql}
GROUP BY p.`time_spent_page`
ORDER BY `active_portal_users` DESC
LIMIT 50"""


def try_compose_join_sql(question: str, tables: list[Any]) -> str | None:
    """Try known JOIN templates before LLM fallback."""
    for fn in (
        compose_portal_activity_attendance_pct_sql,
        compose_portal_activity_by_page_sql,
        compose_attendance_list_sql,
        compose_placed_by_state_sql,
        compose_nps_by_gender_sql,
        compose_jobs_by_gender_sql,
    ):
        sql = fn(question, tables)
        if sql:
            return sql
    return None
