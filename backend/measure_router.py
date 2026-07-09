"""Route a question to a semantic measure + dimensions (Hex-style)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from semantic_layer import MeasureDef, TableSemantic, semantic_for_table


@dataclass
class MeasurePlan:
    table_fq: str
    table_short: str
    measure: MeasureDef
    group_by: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    reason: str = ""


_COUNT_RE = re.compile(r"\bhow many\b|\bcount\b|\bnumber of\b|\btotal\b", re.I)
_AVG_RE = re.compile(r"\baverage\b|\bavg\b|\bmean\b", re.I)
_BREAKDOWN_RE = re.compile(
    r"\bby\s+([a-z_][a-z0-9_\s]{1,40}?)(?:\s*[?.!,]|$|\s+and\s|\s+for\s)",
    re.I,
)
_EMOJI_RE = re.compile(r"\bemoji\b|\brating\b|\bfeedback\b", re.I)


def _pick_count_measure(semantic: TableSemantic, question: str) -> MeasureDef | None:
    q = question.lower()
    if re.search(r"\bactive\b", q) and re.search(r"\blearning[\s_-]*portal|\bportal\b", q):
        for m in semantic.measures:
            if m.id == "active_learning_portal_users":
                return m
    prefer_user = re.search(r"\busers?\b|\bdistinct\b|\bunique\b", q)
    for m in semantic.measures:
        if prefer_user and m.func in ("count_distinct", "count distinct") and m.of_column:
            if "user" in m.of_column.lower() or m.id.startswith("unique"):
                return m
    for m in semantic.measures:
        if m.func in ("count_distinct", "count distinct") and m.of_column:
            return m
    for m in semantic.measures:
        if m.id == "count_of_records" or m.func == "count":
            return m
    return semantic.measures[0] if semantic.measures else None


def _pick_avg_measure(semantic: TableSemantic, question: str) -> MeasureDef | None:
    q = question.lower()
    for m in semantic.measures:
        if m.func != "avg":
            continue
        if m.of_column and m.of_column.lower() in q:
            return m
        if "ctc" in q and "ctc" in m.id.lower():
            return m
        if "nps" in q and "nps" in m.id.lower():
            return m
    for m in semantic.measures:
        if m.func == "avg":
            return m
    return None


def _match_dimension(semantic: TableSemantic, token: str) -> str | None:
    token = token.strip().lower().replace(" ", "_")
    if not token:
        return None
    for d in semantic.dimensions:
        did = d.id.lower()
        if did == token or token in did or did in token:
            return d.id
    aliases = {
        "gender": "gender",
        "company": "company_name",
        "companies": "company_name",
        "state": "state",
        "month": "month",
        "course": "course_id",
        "emoji": "user_answer",
        "rating": "user_answer",
    }
    mapped = aliases.get(token)
    if mapped:
        for d in semantic.dimensions:
            if d.id.lower() == mapped:
                return d.id
    return None


def _breakdown_dims(question: str, semantic: TableSemantic) -> list[str]:
    dims: list[str] = []
    for m in _BREAKDOWN_RE.finditer(question):
        raw = (m.group(1) or "").strip()
        raw = re.sub(r"\b(users?|active|platform|yesterday|today)\b", "", raw, flags=re.I).strip()
        if not raw:
            continue
        col = _match_dimension(semantic, raw)
        if col and col not in dims:
            dims.append(col)
    if re.search(r"\bby gender\b", question, re.I):
        col = _match_dimension(semantic, "gender")
        if col and col not in dims:
            dims.append(col)
    if re.search(r"\bby company\b", question, re.I):
        col = _match_dimension(semantic, "company")
        if col and col not in dims:
            dims.append(col)
    return dims[:3]


def _score_semantic_table(question: str, semantic: TableSemantic) -> int:
    q = question.lower()
    name = semantic.short_name.lower()
    score = 0
    wants_live_class = bool(re.search(r"\blive[\s_-]*class", q))
    if re.search(r"\battend", q) and "attendance" in name:
        score += 200
    if wants_live_class and "live_class" in name and "attendance" in name:
        score += 500
    if wants_live_class and ("cloudwatch" in name or "virtual_meet" in name):
        score -= 400
    if re.search(r"\battend", q) and "cloudwatch" in name:
        score -= 300
    if re.search(r"\bactive\b", q) and re.search(r"\blearning[\s_-]*portal|\bportal\b", q):
        if name == "academy_users_day_and_page_wise_time_spent_details":
            score += 650
        if name == "z_ccbp_academy_users_master_data":
            score += 200
        if "question_wise" in name or "question_set" in name:
            score -= 400
    if re.search(r"\bactive\b", q) and re.search(r"\bplatform\b", q):
        if "daily_engagement" in name or "time_spent" in name:
            score += 200
    if re.search(r"\bnps\b", q):
        if "nps" in name and "contextual_feedback" not in name:
            score += 400
        if "contextual_feedback" in name:
            score -= 400
    elif re.search(r"\bfeedback\b|\bemoji\b", q) and "contextual_feedback" in name:
        score += 200
    if re.search(r"\bplacement\b|\bctc\b", q) and "placement" in name:
        score += 150
    if re.search(r"\bjob\b|\bapplication\b", q) and "jobs" in name:
        score += 150
    if semantic.measures:
        score += 10
    from table_routing import score_adjustment

    score += score_adjustment(question, semantic.short_name)
    return score


def try_build_measure_plan(
    question: str,
    selected_tables: list[Any],
    *,
    catalog_tables: list[Any] | None = None,
) -> MeasurePlan | None:
    """Return a measure plan when the question maps to a defined semantic measure."""
    from query_planner import classify_intent

    intent = classify_intent(question)
    if intent in ("topic_search", "survey_distribution"):
        return None

    # Domain-pinned routing passes a single table — do not let catalog scan override it.
    if len(selected_tables) == 1:
        pool = list(selected_tables)
    else:
        pool = list(catalog_tables or selected_tables)
    if not pool:
        return None

    ranked: list[tuple[int, Any, TableSemantic]] = []
    for table in pool:
        semantic = semantic_for_table(table)
        if not semantic or not semantic.measures:
            continue
        ranked.append((_score_semantic_table(question, semantic), table, semantic))
    if not ranked:
        return None
    ranked.sort(key=lambda x: (-x[0], x[1].full_table_id))
    wants_avg = bool(_AVG_RE.search(question))

    if wants_avg:
        for _score, table, semantic in ranked:
            measure = _pick_avg_measure(semantic, question)
            if not measure:
                continue
            fq = table.full_table_id
            short = fq.rsplit(".", 1)[-1]
            group_by = _breakdown_dims(question, semantic)
            return MeasurePlan(
                table_fq=fq,
                table_short=short,
                measure=measure,
                group_by=group_by,
                reason=f"Semantic avg measure `{measure.id}` on `{short}`",
            )
        return None

    table = ranked[0][1]
    semantic = ranked[0][2]

    fq = table.full_table_id
    short = fq.rsplit(".", 1)[-1]
    group_by = _breakdown_dims(question, semantic)

    if _COUNT_RE.search(question) or not group_by:
        measure = _pick_count_measure(semantic, question)
        if measure:
            return MeasurePlan(
                table_fq=fq,
                table_short=short,
                measure=measure,
                group_by=group_by,
                reason=f"Semantic count measure `{measure.id}` on `{short}`",
            )

    if group_by:
        measure = _pick_count_measure(semantic, question)
        if measure:
            return MeasurePlan(
                table_fq=fq,
                table_short=short,
                measure=measure,
                group_by=group_by,
                reason=f"Semantic breakdown by {', '.join(group_by)} on `{short}`",
            )

    if _EMOJI_RE.search(question):
        measure = _pick_count_measure(semantic, question)
        if measure:
            return MeasurePlan(
                table_fq=fq,
                table_short=short,
                measure=measure,
                group_by=group_by or ["user_answer"],
                reason=f"Semantic feedback measure on `{short}`",
            )

    return None
