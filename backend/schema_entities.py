"""Discover countable dimensions from AI overviews and column names."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from question_intent import expand_question_abbreviations, question_wants_breakdown

_OVERVIEW_COL = re.compile(r"`([a-zA-Z_][a-zA-Z0-9_]*)`", re.I)
_SUFFIX_STRIP = re.compile(r"_(title|name|enum|id|email|status|type|code)$", re.I)
_COUNT_Q = re.compile(r"\b(how many|count|number of|total|distinct)\b", re.I)
_ID_COLS = frozenset({"user_id", "uid", "student_id", "learner_id", "id"})

# Known domain aliases → column name fragments (merged with dynamic discovery).
_STATIC_ALIASES: dict[str, list[str]] = {
    "growth cycle": ["growth_cycle", "gc", "gcs"],
    "growth cycles": ["growth_cycle", "gc", "gcs"],
    "coach": ["success_coach", "coach"],
    "coaches": ["success_coach", "coach"],
    "student": ["user_id", "student"],
    "students": ["user_id", "student"],
    "user": ["user_id"],
    "users": ["user_id"],
    "gender": ["gender"],
    "retention": ["retention_bucket", "retention"],
    "language": ["preferred_language", "language"],
    "nps": ["nps", "rating", "score"],
    "job": ["job", "applied", "placement"],
    "jobs": ["job", "applied", "placement"],
}


@dataclass
class SchemaEntity:
    """A business dimension discoverable from schema + overview."""

    label: str
    terms: list[str]
    columns: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)


def _humanize(name: str) -> str:
    base = _SUFFIX_STRIP.sub("", name)
    return re.sub(r"_+", " ", base).strip().lower()


def _terms_for_column(col: str) -> list[str]:
    terms = {_humanize(col), col.lower()}
    label = _humanize(col)
    if label:
        terms.add(label)
        if not label.endswith("s"):
            terms.add(label + "s")
    for alias_key, frags in _STATIC_ALIASES.items():
        if any(f in col.lower() for f in frags):
            terms.add(alias_key)
            terms.update(alias_key.split())
    return sorted(terms)


def _overview_columns(overview: str) -> list[str]:
    cols: list[str] = []
    for m in _OVERVIEW_COL.finditer(overview or ""):
        name = m.group(1)
        if name.lower() not in _ID_COLS and name not in cols:
            cols.append(name)
    return cols


def build_schema_entities(
    tables: list[Any],
    columns_by_table: dict[str, set[str]],
) -> list[SchemaEntity]:
    """Build dimension catalog from workspace tables, AI overviews, and BQ columns."""
    by_label: dict[str, SchemaEntity] = {}

    for table in tables:
        fq = table.full_table_id
        cols = columns_by_table.get(fq) or set()
        overview = (getattr(table, "ai_overview", "") or "").strip()
        candidate_cols = set(_overview_columns(overview)) | cols

        for col in sorted(candidate_cols):
            if col.lower() in _ID_COLS:
                continue
            if col not in cols and cols:
                continue
            label = _humanize(col)
            if not label or len(label) < 2:
                continue
            key = label
            if key not in by_label:
                by_label[key] = SchemaEntity(label=label, terms=_terms_for_column(col))
            ent = by_label[key]
            if col not in ent.columns:
                ent.columns.append(col)
            if fq not in ent.tables:
                ent.tables.append(fq)
            for t in _terms_for_column(col):
                if t not in ent.terms:
                    ent.terms.append(t)

    return sorted(by_label.values(), key=lambda e: (-len(e.columns), e.label))


def match_entities_in_question(
    question: str,
    entities: list[SchemaEntity],
) -> list[SchemaEntity]:
    """Entities whose terms appear in the question (longest / most specific first)."""
    q = expand_question_abbreviations(question).lower()
    q_words = set(re.findall(r"[a-z0-9]+", q))
    scored: list[tuple[int, SchemaEntity]] = []

    for ent in entities:
        score = 0
        for term in ent.terms:
            t = term.lower().strip()
            if len(t) < 2:
                continue
            if " " in t and t in q:
                score += len(t) * 4
            elif t in q_words:
                score += len(t) * 2
            elif len(t) >= 4 and t in q:
                score += len(t)
        if score > 0:
            scored.append((score, ent))

    scored.sort(key=lambda x: (-x[0], x[1].label))
    return [e for _, e in scored]


def primary_matched_entity(
    question: str,
    entities: list[SchemaEntity],
) -> SchemaEntity | None:
    """Best entity for a scalar «how many X» question."""
    q = expand_question_abbreviations(question)
    matched = match_entities_in_question(q, entities)
    if not matched:
        return None
    if question_wants_breakdown(q):
        return matched[0]
    if not _COUNT_Q.search(q):
        return None
    # Prefer non-user entities when question doesn't say user/student.
    if not re.search(r"\b(user|users|student|students|learner)\b", q, re.I):
        for ent in matched:
            if "user" not in ent.label and not any(c.lower() in _ID_COLS for c in ent.columns):
                return ent
    return matched[0]


def sql_hint_for_entities(
    question: str,
    entities: list[SchemaEntity],
    *,
    wants_breakdown: bool,
) -> str:
    matched = match_entities_in_question(question, entities)
    if not matched:
        return ""
    lines = ["Match the question topic to these schema dimensions:"]
    for ent in matched[:4]:
        cols = ", ".join(f"`{c}`" for c in ent.columns[:3])
        lines.append(f"- {ent.label}: use {cols}")
    primary = primary_matched_entity(question, entities)
    if primary and _COUNT_Q.search(question) and not wants_breakdown:
        col = primary.columns[0]
        lines.append(
            f"Scalar count question → COUNT(DISTINCT `{col}`) WHERE `{col}` IS NOT NULL. "
            "Do NOT substitute user_id unless the question asks for students/users."
        )
    elif wants_breakdown and matched:
        col = matched[0].columns[0]
        lines.append(f"Breakdown → GROUP BY `{col}` with COUNT(DISTINCT user_id) or COUNT(*).")
    return "\n".join(lines)


def _entity_in_sql(sql: str, ent: SchemaEntity) -> bool:
    sql_l = sql.lower()
    return any(re.search(re.escape(c.lower()), sql_l) for c in ent.columns)


def validate_sql_for_question(
    question: str,
    sql: str,
    entities: list[SchemaEntity],
) -> tuple[bool, str]:
    """Return (ok, reason) for SQL vs question + schema entities."""
    q = expand_question_abbreviations(question)
    sql_text = (sql or "").strip()
    if not sql_text:
        return False, "empty SQL"

    primary = primary_matched_entity(q, entities)
    if primary and _COUNT_Q.search(q) and not question_wants_breakdown(q):
        if re.search(r"\bCOUNT\s*\(\s*DISTINCT\s+`?user_id", sql_text, re.I):
            if not re.search(r"\b(user|users|student|students)\b", q, re.I):
                return False, f"question asks about {primary.label}, not user count"
        if not _entity_in_sql(sql_text, primary):
            return False, f"SQL must use column for {primary.label}"

    if question_wants_breakdown(q) and not re.search(r"\bGROUP BY\b", sql_text, re.I):
        return False, "breakdown question requires GROUP BY"

    matched = match_entities_in_question(q, entities)
    if question_wants_breakdown(q) and matched and not _entity_in_sql(sql_text, matched[0]):
        return False, f"breakdown should include {matched[0].label}"

    if re.search(r"pause_status\s+IS\s+NULL", sql_text, re.I):
        if not re.search(r"\b(active|not paused|live|unpaused)\b", q, re.I):
            return False, "remove pause_status filter — question did not ask for active students only"

    return True, ""


def validate_result_for_question(
    question: str,
    sql: str,
    columns: list[Any],
    rows: list[Any],
    entities: list[SchemaEntity],
) -> tuple[bool, str]:
    """Post-query check: result shape matches what was asked."""
    ok, reason = validate_sql_for_question(question, sql, entities)
    if not ok:
        return ok, reason

    from result_cache import _cache_supports_time_series, is_time_series_question

    q = expand_question_abbreviations(question)
    cols = [str(c).lower() for c in (columns or [])]
    row_count = len(rows or [])

    if is_time_series_question(q):
        if not _cache_supports_time_series({"columns": columns, "rows": rows}):
            return False, "time series question needs date/month columns in results"

    if question_wants_breakdown(q):
        if row_count <= 1 and len(cols) <= 1:
            return False, "breakdown question returned a single aggregate"
        by = re.search(r"\bby\s+([a-z_][a-z0-9_]*)\b", q, re.I)
        if by and any(by.group(1).lower() in c for c in cols):
            pass
        else:
            matched = match_entities_in_question(q, entities)
            if matched and not any(
                re.search(re.escape(c.lower()), " ".join(cols)) for c in matched[0].columns
            ):
                return False, f"results missing dimension for {matched[0].label}"

    primary = primary_matched_entity(q, entities)
    if primary and _COUNT_Q.search(q) and not question_wants_breakdown(q):
        if re.search(r"\b(user|users|student|students)\b", q, re.I) is None:
            if row_count == 1 and len(cols) == 1 and "user" in cols[0]:
                return False, "question asks about dimension, not user count"

    return True, ""


def try_build_dimension_count_sql(
    question: str,
    fq: str,
    cols: set[str],
    entities: list[SchemaEntity],
) -> str | None:
    """Generic COUNT(DISTINCT dimension) from matched schema entity."""
    if question_wants_breakdown(question):
        return None
    primary = primary_matched_entity(question, entities)
    if not primary:
        return None
    col = next((c for c in primary.columns if c in cols), None)
    if not col:
        return None
    if re.search(r"\b(user|users|student|students)\b", question, re.I) and col.lower() in _ID_COLS:
        return None
    alias = re.sub(r"[^a-z0-9_]", "_", f"{primary.label.replace(' ', '_')}_count")
    return (
        f"SELECT COUNT(DISTINCT `{col}`) AS {alias}\n"
        f"FROM `{fq}`\n"
        f"WHERE `{col}` IS NOT NULL"
    )


def try_build_breakdown_sql(
    question: str,
    fq: str,
    cols: set[str],
    entities: list[SchemaEntity],
    *,
    prior_sql: str = "",
) -> str | None:
    """GROUP BY dimension + COUNT(DISTINCT user_id) for breakdown questions."""
    if not question_wants_breakdown(question):
        return None

    dim_col: str | None = None
    by = re.search(r"\bby\s+([a-z_][a-z0-9_]*)\b", question, re.I)
    if by:
        term = by.group(1).lower()
        for ent in entities:
            if term in ent.terms or term == ent.label or term in ent.label.split():
                dim_col = next((c for c in ent.columns if c in cols), None)
                if dim_col:
                    break
        if not dim_col and term in {c.lower() for c in cols}:
            dim_col = next(c for c in cols if c.lower() == term)

    if not dim_col and re.search(r"\bgender\b", question, re.I):
        dim_col = "gender" if "gender" in cols else None

    if not dim_col:
        matched = match_entities_in_question(question, entities)
        if matched:
            dim_col = next(
                (c for c in matched[0].columns if c in cols and c.lower() not in _ID_COLS),
                None,
            )

    if not dim_col:
        return None

    id_col = next((c for c in _ID_COLS if c in cols), None)
    if not id_col:
        return None

    where = [f"`{dim_col}` IS NOT NULL"]
    prior_l = (prior_sql or "").lower()
    if "pause_status is null" in prior_l and "pause_status" in cols:
        where.append("pause_status IS NULL")

    return (
        f"SELECT `{dim_col}`, COUNT(DISTINCT `{id_col}`) AS student_count\n"
        f"FROM `{fq}`\n"
        f"WHERE {' AND '.join(where)}\n"
        f"GROUP BY `{dim_col}`\n"
        f"ORDER BY student_count DESC"
    )
