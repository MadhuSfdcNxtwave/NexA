"""Deterministic SQL from AI table overviews — count/active/date patterns before LLM."""
from __future__ import annotations

import calendar
import json
import re
from datetime import date
from typing import Any

from question_dates import _month_from_question, _pick_year_for_month, _year_from_question

from question_intent import (
    expand_question_abbreviations,
    question_asks_growth_cycle_count,
    question_wants_breakdown,
)

_COUNT = re.compile(r"\b(how many|count|number of|total)\b", re.I)
_ACTIVE = re.compile(r"\b(active|not paused|live users?)\b", re.I)
_THIS_MONTH = re.compile(r"\b(this month|current month|mtd)\b", re.I)
_STUDENT = re.compile(r"\b(student|students|user|users|learner|learners)\b", re.I)
_PORTAL = re.compile(r"\b(learning portal|portal|onboarding)\b", re.I)
_INTERACTION = re.compile(r"\b(interaction|nav|cloudwatch|login|visited|usage)\b", re.I)
_GROWTH_CYCLE = re.compile(r"\bgrowth\s*cycle", re.I)

_DATE_TYPES = {"DATE", "DATETIME", "TIMESTAMP"}
_ID_COLS = ("user_id", "uid", "student_id", "learner_id")
_GROWTH_CYCLE_COLS = ("growth_cycle_title", "growth_cycle_name_enum")


def _profile(table: Any) -> dict:
    try:
        return json.loads(getattr(table, "ai_profile_json", "") or "{}")
    except json.JSONDecodeError:
        return {}


def _overview(table: Any) -> str:
    return (getattr(table, "ai_overview", "") or "").strip()


def _pick_growth_cycle_col(cols: set[str]) -> str | None:
    for name in _GROWTH_CYCLE_COLS:
        if name in cols:
            return name
    return None


def _pick_table(
    question: str,
    tables: list,
    columns_by_table: dict[str, set[str]],
) -> tuple[str, set[str], Any] | None:
    q = question.lower()
    wants_interaction = bool(_INTERACTION.search(q))
    wants_gc = bool(_GROWTH_CYCLE.search(q) or question_asks_growth_cycle_count(question))
    best: tuple[int, str, set[str], Any] | None = None

    for t in tables:
        fq = t.full_table_id
        cols = columns_by_table.get(fq) or set()
        short = fq.rsplit(".", 1)[-1].lower()
        ov = _overview(t).lower()
        score = 0
        if wants_gc:
            if _pick_growth_cycle_col(cols):
                score += 8
            if "growth_cycle" in ov:
                score += 4
            if "master" in short and "academy" in short:
                score += 3
        if _STUDENT.search(q) or _PORTAL.search(q):
            if "master" in short or "user" in short:
                score += 5
            if "cloudwatch" in short or "interaction" in short:
                score += 3 if wants_interaction else 1
        if _ACTIVE.search(q) and "pause_status" in cols:
            score += 4
        if _PORTAL.search(q) and "learning_portal" in ov:
            score += 3
        if wants_interaction and ("interaction" in short or "cloudwatch" in short):
            score += 6
        if _COUNT.search(q):
            score += 1
        if score <= 0:
            continue
        if best is None or score > best[0]:
            best = (score, fq, cols, t)
    if best:
        return best[1], best[2], best[3]
    if len(tables) == 1:
        t = tables[0]
        return t.full_table_id, columns_by_table.get(t.full_table_id) or set(), t
    return None


def _pick_id_col(cols: set[str]) -> str | None:
    for c in _ID_COLS:
        if c in cols:
            return c
    return None


def _pick_date_col(cols: set[str], profile: dict, overview: str) -> tuple[str, str] | None:
    ranges = profile.get("date_ranges") or {}
    for name in ranges:
        if name.lower() in cols:
            col_type = next(
                (c.get("type", "DATETIME").upper() for c in profile.get("columns") or [] if c.get("name") == name),
                "DATETIME",
            )
            return name, col_type
    for c in sorted(cols):
        if any(c.endswith(s) for s in ("_datetime", "_date", "_time")):
            col_type = next(
                (x.get("type", "DATETIME").upper() for x in profile.get("columns") or [] if x.get("name") == c),
                "DATETIME",
            )
            return c, col_type
    m = re.search(r"`(\w+)` \(DATETIME\): primary date", overview, re.I)
    if m and m.group(1).lower() in cols:
        return m.group(1), "DATETIME"
    return None


def _resolve_month_year(question: str, profile: dict) -> tuple[int, int] | None:
    month = _month_from_question(question)
    if _THIS_MONTH.search(question):
        today = date.today()
        return today.month, today.year
    if not month:
        return None
    year = _year_from_question(question)
    if not year:
        year = _pick_year_for_month(month, profile.get("date_ranges") or {})
    if not year:
        return None
    return month, year


def _month_filter(col: str, col_type: str, month: int, year: int) -> str:
    last = calendar.monthrange(year, month)[1]
    if col_type == "DATE":
        return (
            f"`{col}` BETWEEN DATE '{year}-{month:02d}-01' "
            f"AND DATE '{year}-{month:02d}-{last}'"
        )
    return (
        f"`{col}` >= DATETIME '{year}-{month:02d}-01 00:00:00' "
        f"AND `{col}` < DATETIME '{year}-{month:02d}-{last} 23:59:59'"
    )


def _active_clause(cols: set[str], overview: str, *, portal: bool = False) -> str | None:
    parts: list[str] = []
    if "pause_status" in cols:
        parts.append("pause_status IS NULL")
    elif re.search(r"pause_status IS NULL", overview, re.I):
        parts.append("pause_status IS NULL")
    if portal and "learning_portal_onboarding_access_given_datetime" in cols:
        parts.append("learning_portal_onboarding_access_given_datetime IS NOT NULL")
    if not parts:
        return None
    return " AND ".join(parts)


def try_build_overview_sql(
    question: str,
    tables: list,
    columns_by_table: dict[str, set[str]],
    *,
    relaxed: bool = False,
    prior_sql: str = "",
) -> str | None:
    """Build COUNT SQL using AI overview + profile — no LLM, no CTEs."""
    q = expand_question_abbreviations((question or "").strip())
    if not q:
        return None

    if question_wants_breakdown(q):
        from schema_entities import build_schema_entities, try_build_breakdown_sql

        picked = _pick_table(q, tables, columns_by_table)
        if not picked:
            return None
        fq, cols, _table = picked
        entities = build_schema_entities(tables, columns_by_table)
        return try_build_breakdown_sql(
            q, fq, cols, entities, prior_sql=prior_sql
        )

    if not relaxed and not _COUNT.search(q):
        return None

    picked = _pick_table(q, tables, columns_by_table)
    if not picked:
        return None
    fq, cols, table = picked
    profile = _profile(table)
    overview = _overview(table)

    where: list[str] = []
    label_bits: list[str] = []

    gc_col = _pick_growth_cycle_col(cols)
    if question_asks_growth_cycle_count(q) and gc_col:
        where.append(f"`{gc_col}` IS NOT NULL")
        sql = (
            f"SELECT COUNT(DISTINCT `{gc_col}`) AS growth_cycle_count\n"
            f"FROM `{fq}`"
        )
        if where:
            sql += "\nWHERE " + "\n  AND ".join(where)
        return sql

    from schema_entities import build_schema_entities, try_build_dimension_count_sql

    entities = build_schema_entities(tables, columns_by_table)
    dim_sql = try_build_dimension_count_sql(q, fq, cols, entities)
    if dim_sql:
        return dim_sql

    id_col = _pick_id_col(cols)
    if not id_col:
        return None

    active = _active_clause(cols, overview, portal=bool(_PORTAL.search(q)))
    if active and (_ACTIVE.search(q) or _PORTAL.search(q)):
        where.append(active)
        label_bits.append("active")

    period = _resolve_month_year(q, profile)
    date_info = _pick_date_col(cols, profile, overview)
    if period and date_info:
        col, col_type = date_info
        where.append(_month_filter(col, col_type, period[0], period[1]))
        label_bits.append(f"{period[0]:02d}/{period[1]}")

    metric = "students" if _STUDENT.search(q) else "rows"
    alias = "_".join(label_bits + [metric, "count"]).strip("_") or "row_count"
    alias = re.sub(r"[^a-z0-9_]", "_", alias.lower())

    sql = f"SELECT COUNT(DISTINCT `{id_col}`) AS {alias}\nFROM `{fq}`"
    if where:
        sql += "\nWHERE " + "\n  AND ".join(where)
    return sql
