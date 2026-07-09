"""Single resolver — question → ResolvedQuery from glossary + semantic layer."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from metrics_registry import GlossaryTerm, match_glossary_terms, resolve_measure
from query_planner import (
    QueryPlan,
    _BREAKDOWN_RE,
    classify_intent,
    extract_topic,
    infer_viz_hint,
    topic_to_regex,
)
from semantic_layer import semantic_by_model_id, semantic_for_table

_COUNT_RE = re.compile(r"\bhow many\b|\bcount\b|\bnumber of\b|\btotal\b", re.I)
_NPS_FORM = re.compile(
    r"\bnps\s+form|\bnps\b.{0,40}\bform\s+responses?|\bnet promoter\b.{0,40}\bform\b",
    re.I,
)


@dataclass
class ResolvedQuery:
    """Unified resolution output — feeds planner, RAG, context, and SQL compose."""

    intent: str
    model_id: str
    measure_id: str | None = None
    dimensions: list[str] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    topic: str | None = None
    topic_regex: str | None = None
    union_member_ids: list[str] = field(default_factory=list)
    viz_hint: str = "table"
    entity: str = "general"
    domain_signals: list[str] = field(default_factory=list)
    glossary_terms: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""
    trace: list[str] = field(default_factory=list)

    def to_query_plan(self) -> QueryPlan:
        return QueryPlan(
            model_id=self.model_id,
            intent=self.intent,
            topic=self.topic,
            topic_regex=self.topic_regex,
            measure_id=self.measure_id,
            dimensions=list(self.dimensions),
            group_by=list(self.group_by),
            filters=list(self.filters),
            viz_hint=self.viz_hint,
            union_member_ids=list(self.union_member_ids),
            entity=self.entity,
            domain_signals=list(self.domain_signals),
            reason=self.reason,
        )

    def to_trace_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "model_id": self.model_id,
            "measure_id": self.measure_id,
            "glossary_terms": self.glossary_terms,
            "confidence": round(self.confidence, 3),
            "reason": (self.reason or "")[:200],
            "trace": self.trace[:8],
        }


_BREAKDOWN_DIM_ALIASES: dict[str, str] = {
    "gender": "gender",
    "growth cycle": "growth_cycle_title",
    "growth cycles": "growth_cycle_title",
    "retention": "latest_retention_bucket",
    "coach": "growth_cycle_title",
    "month": "form_submission_month",
    "state": "current_address_state",
    "activity": "time_spent_page",
    "activities": "time_spent_page",
    "page": "time_spent_page",
    "pages": "time_spent_page",
}


def extract_breakdown_dimensions(question: str, sem=None) -> list[str]:
    """Parse 'by X' tokens — works even when X is not on the base model."""
    q = (question or "").strip()
    dims: list[str] = []
    for pattern, dim_id in (
        (r"\bby\s+gender\b", "gender"),
        (r"\bby\s+state\b", "current_address_state"),
        (r"\bby\s+month\b", "form_submission_month"),
        (r"\bby\s+growth\s+cycle\b", "growth_cycle_title"),
    ):
        if re.search(pattern, q, re.I) and dim_id not in dims:
            dims.append(dim_id)
    for m in _BREAKDOWN_RE.finditer(q):
        raw = (m.group(1) or "").strip()
        raw = re.sub(
            r"\b(users?|active|platform|yesterday|today|for)\b",
            "",
            raw,
            flags=re.I,
        ).strip().lower()
        if not raw:
            continue
        mapped = _BREAKDOWN_DIM_ALIASES.get(raw)
        if mapped and mapped not in dims:
            dims.append(mapped)
            continue
        if sem:
            from measure_router import _match_dimension

            col = _match_dimension(sem, raw)
            if col and col not in dims:
                dims.append(col)
    return dims


def _filters_from_term(term: GlossaryTerm, sem) -> list[str]:
    if not term.filters:
        return []
    dim_by_id = {d.id: d for d in (sem.dimensions if sem else [])}
    out: list[str] = []
    for fid in term.filters:
        dim = dim_by_id.get(fid)
        if dim and dim.expr_sql:
            out.append(dim.expr_sql)
        elif dim:
            out.append(f"`{dim.id}`")
        else:
            out.append(fid)
    return out


def _resolve_from_glossary(
    question: str,
    matches: list[tuple[GlossaryTerm, str]],
) -> ResolvedQuery | None:
    if not matches:
        return None

    q = question.strip()
    intent = classify_intent(q)
    from table_routing import is_compound_domain_question

    if is_compound_domain_question(q):
        return None

    best_term, best_syn = matches[0]
    trace = [f"glossary:{best_term.id} via '{best_syn}'"]

    if best_term.intent == "compound" or best_term.model_id == "compound":
        from table_routing import detect_domain_signals

        return ResolvedQuery(
            intent="compound",
            model_id="compound",
            glossary_terms=[t.id for t, _ in matches],
            confidence=0.92,
            reason=f"Glossary compound `{best_term.label}`",
            trace=trace,
            domain_signals=sorted(detect_domain_signals(q)),
        )

    # Prefer topic_search when question has a topic + NPS form context
    if intent == "topic_search" or (
        extract_topic(q) and best_term.id == "nps_topic_feedback"
    ):
        topic = extract_topic(q)
        if topic and best_term.model_id == "nps_all_form_responses":
            sem = semantic_by_model_id("nps_all_form_responses")
            return ResolvedQuery(
                intent="topic_search",
                model_id="nps_all_form_responses",
                topic=topic,
                topic_regex=topic_to_regex(topic),
                union_member_ids=list(sem.union_members) if sem else [],
                glossary_terms=[t.id for t, _ in matches],
                confidence=0.92,
                reason=f"Glossary topic search `{topic}` on nps_all_form_responses",
                trace=trace,
            )

    sem = semantic_by_model_id(best_term.model_id)
    if not sem:
        return None

    resolved_intent = best_term.intent or intent
    if intent == "breakdown" and _BREAKDOWN_RE.search(q):
        resolved_intent = "breakdown"
    elif intent == "topic_search" and extract_topic(q):
        resolved_intent = "topic_search"
    measure_id = best_term.measure_id or None
    filters = _filters_from_term(best_term, sem)

    # Breakdown: extract dimension from "by X" if glossary term is generic
    group_by: list[str] = []
    if resolved_intent == "breakdown":
        from measure_router import _breakdown_dims

        group_by = _breakdown_dims(q, sem)
        if not group_by:
            group_by = extract_breakdown_dimensions(q, sem)
        if not group_by and best_term.id == "learning_portal_activity_by_page":
            group_by = ["time_spent_page"]
        if not group_by and re.search(
            r"\b(which|what)\s+(activity|page)\b|\bin which activity\b",
            q,
            re.I,
        ) and sem and sem.model_id == "academy_users_day_and_page_wise_time_spent_details":
            group_by = ["time_spent_page"]

    confidence = 0.85 + 0.05 * min(len(matches), 2)
    reason = f"Glossary `{best_term.label}` → model `{best_term.model_id}`"
    if measure_id:
        reason += f", measure `{measure_id}`"

    return ResolvedQuery(
        intent=resolved_intent,
        model_id=best_term.model_id,
        measure_id=measure_id,
        dimensions=list(group_by),
        group_by=list(group_by),
        filters=filters,
        glossary_terms=[t.id for t, _ in matches],
        confidence=confidence,
        reason=reason,
        trace=trace,
    )


def _resolve_from_planner(
    question: str,
    selected_tables: list[Any],
    *,
    catalog_tables: list[Any] | None = None,
    columns_by_table: dict[str, set[str]] | None = None,
) -> ResolvedQuery | None:
    from query_planner import try_build_query_plan

    plan = try_build_query_plan(
        question,
        selected_tables,
        catalog_tables=catalog_tables,
        columns_by_table=columns_by_table,
    )
    if not plan:
        return None
    return ResolvedQuery(
        intent=plan.intent,
        model_id=plan.model_id,
        measure_id=plan.measure_id,
        dimensions=list(plan.dimensions),
        group_by=list(plan.group_by),
        filters=list(plan.filters),
        topic=plan.topic,
        topic_regex=plan.topic_regex,
        union_member_ids=list(plan.union_member_ids),
        glossary_terms=[],
        confidence=0.6,
        reason=plan.reason,
        trace=["planner:fallback"],
    )


def resolve(
    question: str,
    selected_tables: list[Any],
    *,
    catalog_tables: list[Any] | None = None,
    columns_by_table: dict[str, set[str]] | None = None,
) -> ResolvedQuery | None:
    """
    Single entry point: glossary match first, then planner fallback.
    Enriches with entity, viz_hint, domain_signals.
    """
    q = (question or "").strip()
    if not q:
        return None

    glossary_matches = match_glossary_terms(q)
    resolved = _resolve_from_glossary(q, glossary_matches)
    if not resolved:
        resolved = _resolve_from_planner(
            q,
            selected_tables,
            catalog_tables=catalog_tables,
            columns_by_table=columns_by_table,
        )
    if not resolved:
        return None

    from ask_context import _detect_entity
    from table_routing import detect_domain_signals, match_routing_rule

    resolved.entity = _detect_entity(q)
    resolved.viz_hint = infer_viz_hint(q, resolved.intent)
    resolved.domain_signals = sorted(detect_domain_signals(q))

    if not resolved.filters:
        rule = match_routing_rule(q)
        if rule and rule.table_short == resolved.model_id:
            resolved.filters = [f.to_sql() for f in rule.filters if f.to_sql()]
            resolved.trace.append(f"routing:{rule.id}")

    if resolved.measure_id:
        entry = resolve_measure(resolved.model_id, resolved.measure_id)
        if entry and entry.description and not resolved.reason:
            resolved.reason = entry.description[:200]

    if (
        resolved.intent == "breakdown"
        and not resolved.group_by
        and resolved.model_id == "academy_users_day_and_page_wise_time_spent_details"
    ):
        from query_planner import _PORTAL_ACTIVITY

        if _PORTAL_ACTIVITY.search(q):
            resolved.group_by = ["time_spent_page"]
            resolved.dimensions = ["time_spent_page"]

    return resolved
