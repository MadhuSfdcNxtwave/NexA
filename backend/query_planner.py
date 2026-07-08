"""Model-facing query planner — intent classification and QueryPlan from YAML metadata."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from semantic_layer import TableSemantic, load_semantic_catalog, semantic_by_model_id, semantic_for_table

_COUNT_RE = re.compile(r"\bhow many\b|\bcount\b|\bnumber of\b|\btotal\b", re.I)
_AVG_RE = re.compile(r"\baverage\b|\bavg\b|\bmean\b", re.I)
_BREAKDOWN_RE = re.compile(
    r"\bby\s+([a-z_][a-z0-9_\s]{1,40}?)(?:\s*[?.!,]|$|\s+and\s|\s+for\s)",
    re.I,
)
_NPS = re.compile(
    r"\bnps\b|net promoter|rating_on_scale|promoter|detractor|"
    r"rating.{0,12}\(0.{0,3}10\)|scale of 0",
    re.I,
)
_NPS_FORM = re.compile(
    r"\bnps\s+form|\bnps\b.{0,40}\bform\s+responses?|\bnet promoter\b.{0,40}\bform\b",
    re.I,
)
_TOPIC_PATTERNS = (
    r"\bon\s+(.+?)\s+feature\b",
    r"\bfeedback\s+(?:on|about)\s+(.+?)(?:\s+in\s+|\s+from\s+|\?|$)",
    r"\bmentions?\s+of\s+(.+?)(?:\s+in\s+|\?|$)",
    r"\bwhat\s+(?:did|do)\s+users?\s+say\s+about\s+(.+?)(?:\?|$)",
    r"\bcomments?\s+about\s+(.+?)(?:\?|$)",
)
_SURVEY_DIST = re.compile(
    r"\b(which of these|most valuable|did you find|distribution|"
    r"top\s+answers?|common\s+answers?|picked|chose|selected)\b",
    re.I,
)
_AGG_BLOCK = re.compile(
    r"\b(average|avg|count|how many|score|nps\s*score|promoter|detractor|"
    r"monthly nps|rating)\b",
    re.I,
)


@dataclass
class QueryPlan:
    model_id: str
    intent: str
    topic: str | None = None
    topic_regex: str | None = None
    measure_id: str | None = None
    group_by: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    union_member_ids: list[str] = field(default_factory=list)
    reason: str = ""


def extract_topic(question: str) -> str | None:
    q = (question or "").strip()
    if not q:
        return None
    for pat in _TOPIC_PATTERNS:
        m = re.search(pat, q, re.I)
        if not m:
            continue
        topic = (m.group(1) or "").strip().rstrip("?., ").strip()
        if len(topic) >= 3:
            return topic
    return None


def topic_to_regex(topic: str) -> str:
    words = re.findall(r"[a-z0-9]+", (topic or "").lower())
    if not words:
        return ""
    return r" ?".join(re.escape(w) for w in words)


def classify_intent(question: str) -> str:
    """Return planner intent: topic_search | survey_distribution | aggregate | breakdown."""
    from question_intent import expand_question_abbreviations

    q = expand_question_abbreviations((question or "").strip())
    if not q:
        return "aggregate"

    topic = extract_topic(q)
    if topic and not _AGG_BLOCK.search(q):
        return "topic_search"
    if topic and re.search(r"\bfeedback\b|\bmention|\bcomment|\bsaid\b|\breview\b", q, re.I):
        if not re.search(r"\bhow many\b|\bcount\b|\bnumber of\b", q, re.I):
            return "topic_search"

    from survey_sql import is_survey_answer_question
    from feedback_sql import is_choice_survey_question

    if is_survey_answer_question(q) or is_choice_survey_question(q) or _SURVEY_DIST.search(q):
        return "survey_distribution"

    if _BREAKDOWN_RE.search(q) and (_COUNT_RE.search(q) or _AVG_RE.search(q)):
        return "breakdown"
    if _AVG_RE.search(q) or _COUNT_RE.search(q):
        return "aggregate"

    if topic:
        return "topic_search"
    return "aggregate"


def is_nps_topic_feedback_question(question: str) -> bool:
    from question_intent import expand_question_abbreviations

    q = expand_question_abbreviations(question)
    if not q:
        return False
    if not _NPS.search(q) and not _NPS_FORM.search(q):
        return False
    return classify_intent(q) == "topic_search"


def _score_model(question: str, semantic: TableSemantic) -> int:
    q = question.lower()
    name = semantic.model_id.lower()
    short = semantic.short_name.lower()
    score = 0

    if _NPS_FORM.search(q):
        if "nps" in short or "nps" in name:
            score += 600
        if "contextual_feedback" in name:
            score -= 500

    if re.search(r"\bfeedback\b|\bemoji\b", q):
        if semantic.is_long_survey or "contextual_feedback" in name:
            score += 250
        if semantic.is_wide_survey and _NPS_FORM.search(q):
            score += 400

    if semantic.is_logical_union and _NPS_FORM.search(q):
        score += 700

    from table_routing import score_adjustment

    score += score_adjustment(question, semantic.short_name or semantic.model_id)
    if semantic.measures:
        score += 10
    return score


def _resolve_logical_union(question: str) -> TableSemantic | None:
    catalog = load_semantic_catalog()
    q = question.lower()
    if not _NPS_FORM.search(q):
        return None
    return catalog.get("nps_all_form_responses")


def _pick_model(
    question: str,
    selected_tables: list[Any],
    catalog_tables: list[Any] | None,
) -> TableSemantic | None:
    intent = classify_intent(question)

    if intent == "topic_search" and _NPS_FORM.search(question):
        union_sem = _resolve_logical_union(question)
        if union_sem:
            return union_sem

    pool = list(selected_tables) if len(selected_tables) == 1 else list(catalog_tables or selected_tables)
    ranked: list[tuple[int, TableSemantic]] = []
    seen: set[str] = set()

    for table in pool:
        sem = semantic_for_table(table)
        if not sem or sem.model_id in seen:
            continue
        seen.add(sem.model_id)
        ranked.append((_score_model(question, sem), sem))

    for sem in load_semantic_catalog().values():
        if not sem.is_logical_union or sem.model_id in seen:
            continue
        if intent == "topic_search" and _NPS_FORM.search(question):
            ranked.append((_score_model(question, sem), sem))
            seen.add(sem.model_id)

    if not ranked:
        return None
    ranked.sort(key=lambda x: (-x[0], x[1].model_id))
    return ranked[0][1]


def try_build_query_plan(
    question: str,
    selected_tables: list[Any],
    *,
    catalog_tables: list[Any] | None = None,
    columns_by_table: dict[str, set[str]] | None = None,
) -> QueryPlan | None:
    """Build a QueryPlan when the question maps to a known planner intent + model."""
    q = (question or "").strip()
    if not q:
        return None

    intent = classify_intent(q)
    semantic = _pick_model(q, selected_tables, catalog_tables)
    if not semantic:
        return None

    if intent == "topic_search":
        topic = extract_topic(q)
        if not topic:
            return None
        regex = topic_to_regex(topic)
        if not regex:
            return None
        members = list(semantic.union_members) if semantic.is_logical_union else []
        return QueryPlan(
            model_id=semantic.model_id,
            intent=intent,
            topic=topic,
            topic_regex=regex,
            union_member_ids=members,
            reason=f"Topic search `{topic}` on model `{semantic.model_id}`",
        )

    if intent == "survey_distribution" and semantic.is_long_survey:
        return QueryPlan(
            model_id=semantic.model_id,
            intent=intent,
            reason=f"Survey answer distribution on `{semantic.model_id}`",
        )

    if intent in ("aggregate", "breakdown"):
        from measure_router import try_build_measure_plan

        pool = list(selected_tables) if len(selected_tables) == 1 else list(catalog_tables or selected_tables)
        table_for_measure = next(
            (t for t in pool if semantic_for_table(t) and semantic_for_table(t).model_id == semantic.model_id),
            pool[0] if pool else None,
        )
        if not table_for_measure:
            return None
        mp = try_build_measure_plan(q, [table_for_measure], catalog_tables=pool)
        if not mp:
            return None
        return QueryPlan(
            model_id=semantic.model_id,
            intent=intent,
            measure_id=mp.measure.id,
            group_by=list(mp.group_by),
            filters=list(mp.filters),
            reason=mp.reason or f"Aggregate on `{semantic.model_id}`",
        )

    return None
