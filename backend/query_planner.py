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
_TREND_RE = re.compile(
    r"\btrend\b|\bover time\b|\bmonthly\b|\bweekly\b|\bdaily\b|"
    r"\bby month\b|\bby week\b|\bmom\b|\byoy\b|\beach month\b",
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
_PORTAL_ACTIVE = re.compile(
    r"\bactive\b.{0,40}\b(learning[\s_-]*portal|portal)\b|"
    r"\b(learning[\s_-]*portal|portal)\b.{0,40}\bactive\b",
    re.I,
)
_PORTAL_ACTIVITY = re.compile(
    r"\b(which|what)\s+(activity|activities|page|pages)\b|"
    r"\bin which activity\b|"
    r"\bactivity\b.{0,50}\b(?:learning[\s_-]*portal|learningportal|portal)\b|"
    r"\b(?:learning[\s_-]*portal|learningportal|portal)\b.{0,50}\b(activity|activit|page|events)\b|"
    r"\bactiv(?:e|ly|lly)\s+in\b.{0,40}\b(?:learning[\s_-]*portal|learningportal|portal)\b|"
    r"\bevents?\b.{0,40}\b(?:learning[\s_-]*portal|learningportal|portal)\b",
    re.I,
)


@dataclass
class QueryPlan:
    model_id: str
    intent: str
    topic: str | None = None
    topic_regex: str | None = None
    measure_id: str | None = None
    dimensions: list[str] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    viz_hint: str = "table"
    union_member_ids: list[str] = field(default_factory=list)
    entity: str = "general"
    domain_signals: list[str] = field(default_factory=list)
    reason: str = ""

    def to_trace_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "intent": self.intent,
            "measure_id": self.measure_id,
            "dimensions": self.dimensions[:6],
            "filters": self.filters[:6],
            "viz_hint": self.viz_hint,
            "topic": self.topic,
            "entity": self.entity,
            "domain_signals": self.domain_signals,
            "union_members": self.union_member_ids[:4],
            "reason": (self.reason or "")[:200],
        }


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


def infer_viz_hint(question: str, intent: str) -> str:
    """Chart/table hint for presentation: table | line | bar | scalar | none."""
    from question_intent import question_wants_breakdown

    q = (question or "").strip()
    if intent == "topic_search":
        return "table"
    if intent == "compound":
        return "scalar"
    if intent == "survey_distribution":
        return "bar"
    if _TREND_RE.search(q):
        return "line"
    if question_wants_breakdown(q) or (intent == "breakdown" and _BREAKDOWN_RE.search(q)):
        return "bar"
    if intent == "aggregate" and (_COUNT_RE.search(q) or _AVG_RE.search(q)):
        if not question_wants_breakdown(q) and not _BREAKDOWN_RE.search(q):
            return "scalar"
    return "table"


def classify_intent(question: str) -> str:
    """Return planner intent: topic_search | survey_distribution | aggregate | breakdown | compound."""
    from question_intent import expand_question_abbreviations
    from table_routing import is_compound_domain_question

    q = expand_question_abbreviations((question or "").strip())
    if not q:
        return "aggregate"

    if is_compound_domain_question(q):
        return "compound"

    if _PORTAL_ACTIVITY.search(q):
        return "breakdown"

    from question_intent import question_wants_breakdown

    if question_wants_breakdown(q) and _NPS.search(q):
        return "breakdown"

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


def nps_topic_sql_shape_ok(
    sql: str,
    *,
    columns: list[Any] | None = None,
) -> bool:
    """True when SQL matches Hex-style NPS topic search (union both tables + mentions)."""
    sql_text = (sql or "").strip()
    if not sql_text:
        return False
    sql_l = sql_text.lower()
    if "union all" not in sql_l:
        return False
    if "nps_form_responses_nov_and_dec_2025" not in sql_l:
        return False
    if "academy_nps_form_responses" not in sql_l:
        return False
    if "(?i)" in sql_text:
        return False
    if columns is not None:
        cols_l = [str(c).lower() for c in columns]
        if not any("mentions" in c for c in cols_l):
            return False
    return True


def active_portal_sql_shape_ok(sql: str, *, question: str = "") -> bool:
    """True when SQL matches canonical active portal definition (master or lp_status)."""
    sql_l = (sql or "").lower()
    q = (question or "").lower()
    if not sql_l:
        return False
    if re.search(r"\blp_status\b|\blp status\b", q):
        return "lp_status" in sql_l and "active" in sql_l
    # Canonical: master data — every row is an active portal user (no required WHERE)
    if "master_data" in sql_l:
        return bool(re.search(r"count\s*\(\s*distinct", sql_l)) and "user_id" in sql_l
    # Legacy shape still accepted
    if "pause_status" in sql_l and "is null" in sql_l:
        return True
    if "day_and_page_wise" in sql_l and "lp_status" in sql_l:
        return "active" in sql_l
    return False


def sql_plan_shape_mismatch_reason(
    question: str,
    sql: str,
    plan: QueryPlan | None,
) -> str | None:
    """Intent-specific SQL shape checks tied to QueryPlan."""
    if plan is None:
        return None
    q = (question or "").strip()
    sql_text = (sql or "").strip()
    if not sql_text:
        return "empty SQL"

    if plan.intent == "topic_search" and (plan.union_member_ids or plan.model_id == "nps_all_form_responses"):
        if is_nps_topic_feedback_question(q) and not nps_topic_sql_shape_ok(sql_text):
            return "NPS topic feedback requires UNION ALL across both NPS form tables"

    if plan.intent == "compound":
        from table_routing import validate_sql_table_choice

        ok, reason = validate_sql_table_choice(q, sql_text)
        if not ok:
            return reason

    if plan.intent == "breakdown" and not re.search(r"\bGROUP BY\b", sql_text, re.I):
        return "breakdown intent requires GROUP BY"

    if (
        plan.model_id in (
            "academy_users_day_and_page_wise_time_spent_details",
            "z_ccbp_academy_users_master_data",
        )
        or plan.measure_id == "active_learning_portal_users"
        or _PORTAL_ACTIVE.search(q)
    ):
        if _PORTAL_ACTIVE.search(q) and re.search(r"\bhow many\b|\bcount\b|\bnumber of\b", q, re.I):
            if not active_portal_sql_shape_ok(sql_text, question=q):
                return "active portal user count requires master-data or lp_status SQL"

    return None


def _routing_sql_filters(question: str, model_id: str) -> list[str]:
    from table_routing import match_routing_rule

    rule = match_routing_rule(question)
    if not rule or rule.table_short != model_id:
        return []
    return [f.to_sql() for f in rule.filters if f.to_sql()]


def _detect_entity(question: str) -> str:
    from ask_context import _detect_entity as detect

    return detect(question)


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

    if _PORTAL_ACTIVE.search(q):
        if "day_and_page_wise" in name:
            score += 500
        if "master_data" in name:
            score -= 300

    from table_routing import score_adjustment

    score += score_adjustment(question, semantic.short_name or semantic.model_id)
    if semantic.measures:
        score += 10
    return score


def _resolve_logical_union(question: str) -> TableSemantic | None:
    catalog = load_semantic_catalog()
    if not _NPS_FORM.search(question.lower()):
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

    if intent == "compound":
        from table_routing import detect_domain_signals

        signals = sorted(detect_domain_signals(q))
        return QueryPlan(
            model_id="compound",
            intent=intent,
            domain_signals=signals,
            reason=f"Compound domain join: {', '.join(signals)}",
        )

    semantic = _pick_model(q, selected_tables, catalog_tables)
    if not semantic:
        return None

    routing_filters = _routing_sql_filters(q, semantic.model_id)

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
            filters=routing_filters,
            reason=f"Topic search `{topic}` on model `{semantic.model_id}`",
        )

    if intent == "survey_distribution" and semantic.is_long_survey:
        return QueryPlan(
            model_id=semantic.model_id,
            intent=intent,
            filters=routing_filters,
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
        dims = list(mp.group_by)
        if intent == "breakdown" and not dims:
            m = _BREAKDOWN_RE.search(q)
            if m:
                token = (m.group(1) or "").strip().split()[0].lower()
                for d in semantic.dimensions:
                    if d.id.lower() == token or token in d.id.lower():
                        dims = [d.id]
                        break
        if intent == "breakdown" and not dims and _PORTAL_ACTIVITY.search(q):
            if semantic.model_id == "academy_users_day_and_page_wise_time_spent_details":
                dims = ["time_spent_page"]
        merged_filters = list(mp.filters)
        for f in routing_filters:
            if f not in merged_filters:
                merged_filters.append(f)
        return QueryPlan(
            model_id=semantic.model_id,
            intent=intent,
            measure_id=mp.measure.id,
            group_by=dims,
            dimensions=dims,
            filters=merged_filters,
            reason=mp.reason or f"Aggregate on `{semantic.model_id}`",
        )

    return None


def analyze_question(
    question: str,
    selected_tables: list[Any],
    *,
    catalog_tables: list[Any] | None = None,
    columns_by_table: dict[str, set[str]] | None = None,
) -> QueryPlan | None:
    """
    Unified intent analyzer — glossary resolver first, then semantic planner fallback.
    """
    import os

    q = (question or "").strip()
    if not q:
        return None

    use_resolver = os.environ.get("TERM_RESOLVER_ENABLED", "true").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if use_resolver:
        from term_resolver import resolve

        resolved = resolve(
            q,
            selected_tables,
            catalog_tables=catalog_tables,
            columns_by_table=columns_by_table,
        )
        if resolved:
            plan = resolved.to_query_plan()
            plan.entity = resolved.entity or plan.entity
            plan.viz_hint = resolved.viz_hint or plan.viz_hint
            plan.domain_signals = list(resolved.domain_signals)
            if resolved.glossary_terms:
                plan.reason = resolved.reason or plan.reason
            return plan

    plan = try_build_query_plan(
        q,
        selected_tables,
        catalog_tables=catalog_tables,
        columns_by_table=columns_by_table,
    )
    if not plan:
        return None

    plan.entity = _detect_entity(q)
    plan.viz_hint = infer_viz_hint(q, plan.intent)
    from table_routing import detect_domain_signals

    plan.domain_signals = sorted(detect_domain_signals(q))
    if plan.group_by and not plan.dimensions:
        plan.dimensions = list(plan.group_by)
    elif plan.dimensions and not plan.group_by:
        plan.group_by = list(plan.dimensions)

    if not plan.filters:
        plan.filters = _routing_sql_filters(q, plan.model_id)

    return plan
