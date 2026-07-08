"""Plan which project tables match a question (shown in ask progress UI).

Routing order:
  1. Breakdown follow-up / domain table pins (deterministic safety net)
  2. Fused retrieval over all tables (vector + keyword) → LLM disambiguation on top 8
  3. Keyword/profile scoring fallback
  4. Legacy LLM table router as last resort
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import join_graph as jg
import knowledge_base as kb

_MONTHS = kb._MONTHS  # noqa: SLF001 — shared month list

_MAX_SELECTED = 3
_PRIOR_TABLE_BOOST = 500


def _tables_from_prior_sql(
    prior_sql: str,
    project_tables: list[Any],
) -> list[str]:
    """Resolve table short names in prior SQL to catalog full_table_id values."""
    sql = (prior_sql or "").strip()
    if not sql:
        return []
    try:
        from sql_parse import parse_bigquery, table_refs

        refs = {r.lower() for r in table_refs(parse_bigquery(sql))}
    except Exception:
        refs = {
            m.group(1).lower()
            for m in re.finditer(r"`([^`]+)`", sql)
        }
        refs.update(m.group(0).lower() for m in re.finditer(r"\b[\w-]+\.[\w-]+\.(\w+)\b", sql))
    if not refs:
        return []

    picked: list[str] = []
    for t in project_tables:
        fq = t.full_table_id
        short = fq.rsplit(".", 1)[-1].lower()
        if fq.lower() in refs or short in refs:
            picked.append(fq)
    return picked


@dataclass
class TableMatch:
    full_table_id: str
    short_name: str
    score: int
    selected: bool = False
    table_description: str = ""


@dataclass
class AskPlan:
    keywords: list[str]
    status_message: str
    tables: list[TableMatch] = field(default_factory=list)
    selected_full_ids: list[str] = field(default_factory=list)
    reasoning: str = ""
    routing_reason: str = ""
    sql_label: str = ""
    join_relations: list[jg.JoinRelation] = field(default_factory=list)
    join_reasoning: str = ""
    kb_columns: dict[str, list[str]] = field(default_factory=dict)
    kb_filters: list[str] = field(default_factory=list)
    kb_measure: str = ""


def extract_keywords(question: str) -> list[str]:
    return kb.extract_keywords(question)


def _llm_route_tables(question: str, project_tables: list[Any]) -> tuple[list[str], str]:
    """LLM reads names, descriptions, and AI profiles — works for any table set."""
    import llm

    catalog = []
    for t in project_tables:
        overview = (getattr(t, "ai_overview", "") or "").strip()
        catalog.append(
            {
                "full_table_id": t.full_table_id,
                "short_name": t.full_table_id.rsplit(".", 1)[-1],
                "description": (getattr(t, "description", "") or "").strip(),
                "profile": overview[:1200],
            }
        )
    try:
        result = llm.select_tables(question, catalog)
    except Exception:
        return [], ""
    valid_ids = {t.full_table_id for t in project_tables}
    picked = [fq for fq in result["tables"] if fq in valid_ids][:_MAX_SELECTED]
    return picked, result["reason"]


def _keyword_route_tables(
    question: str,
    matches: list[TableMatch],
) -> tuple[list[str], str]:
    """Metadata + keyword scoring — no API call. Generic across any catalog."""
    if not matches:
        return [], ""

    keywords = kb.extract_keywords(question)
    top_score = matches[0].score
    if top_score <= 0:
        return [], ""

    q_lower = question.lower()
    if re.search(r"\battend", q_lower):
        attend_matches = [
            m for m in matches if "attendance" in m.short_name.lower()
        ]
        if attend_matches:
            best = attend_matches[0]
            return [best.full_table_id], f"Keyword match on table metadata: `{best.short_name}`"

    if re.search(r"\bactive\b", q_lower) and re.search(r"\bplatform\b", q_lower):
        engagement_matches = [
            m
            for m in matches
            if "daily_engagement" in m.short_name.lower()
            or "time_spent" in m.short_name.lower()
        ]
        if engagement_matches:
            best = engagement_matches[0]
            return [best.full_table_id], f"Keyword match on table metadata: `{best.short_name}`"

    if re.search(r"\blearning[\s_-]*portal|\bportal\b", q_lower) and re.search(
        r"\bactive\b|\baccess\b|\bnow\b|\bcurrent\b", q_lower
    ):
        master = [
            m
            for m in matches
            if m.short_name.lower() == "z_ccbp_academy_users_master_data"
        ]
        if master:
            return [master[0].full_table_id], "Keyword match: user master data (learning portal access)"

    top_domain = kb._domain_name_hits(keywords, matches[0].short_name.lower())
    if top_domain >= 2:
        strong = [
            m
            for m in matches
            if kb._domain_name_hits(keywords, m.short_name.lower()) >= 2
            and m.score >= max(12, int(top_score * 0.65))
        ]
        if len(strong) == 1:
            names = f"`{strong[0].short_name}`"
            return [strong[0].full_table_id], f"Keyword match on table metadata: {names}"

    # Require a clear winner — avoid guessing on weak matches.
    threshold = max(12, int(top_score * 0.55))
    picked: list[str] = []
    for m in matches:
        if m.score >= threshold and len(picked) < _MAX_SELECTED:
            picked.append(m.full_table_id)
    if not picked and top_score >= 8:
        picked = [matches[0].full_table_id]
    if not picked:
        return [], ""

    names = ", ".join(f"`{m.short_name}`" for m in matches if m.full_table_id in picked)
    return picked, f"Keyword match on table metadata: {names}"


def domain_table_override(question: str, included: list[Any]) -> list[str]:
    """Hard-pin canonical tables for well-known question shapes (beats noisy scores)."""
    from table_routing import pin_table

    return pin_table(question, included)


def _vector_route_tables(
    question: str,
    project_tables: list[Any],
    knowledges: list[kb.TableKnowledge],
    matches: list[TableMatch],
) -> tuple[list[str], str]:
    """Semantic search over pre-indexed table metadata vectors (Hex-style)."""
    try:
        import vector_index

        vector_matches, reason = vector_index.route_tables(question, project_tables, knowledges)
    except Exception as e:
        print(f"[vector-index] route failed: {e}")
        return [], ""
    if not vector_matches:
        return [], ""

    selected = [m.full_table_id for m in vector_matches][:_MAX_SELECTED]
    score_by_id = {m.full_table_id: int(round(m.score * 100)) for m in vector_matches}
    for match in matches:
        if match.full_table_id in score_by_id:
            match.score = max(match.score, score_by_id[match.full_table_id])
    return selected, reason


def _fusion_route_tables(
    question: str,
    matches: list[TableMatch],
    knowledges: list[kb.TableKnowledge],
    project_tables: list[Any],
) -> tuple[list[str], str, dict[str, list[str]], list[str], str]:
    """Search all tables via fused scores, then LLM disambiguation on top-K cards."""
    import config
    import kb_articles as kba
    import llm
    import vector_index

    keyword_scores = {m.full_table_id: m.score for m in matches}
    fused = vector_index.rank_all_tables(
        question, project_tables, knowledges, keyword_scores
    )
    if not fused:
        return [], "", {}, [], ""

    catalog = kba.build_card_catalog(fused, knowledges, project_tables)
    if not catalog:
        return [], "", {}, [], ""

    valid_ids = {t.full_table_id for t in project_tables}
    picked: list[str] = []
    reason = ""
    kb_measure = ""

    try:
        result = llm.disambiguate_tables(question, catalog)
    except Exception as exc:
        print(f"[ask-plan] disambiguate failed: {exc}")
        result = {}

    table_fq = result.get("table") or ""
    if table_fq in valid_ids:
        picked = [table_fq]
        reason = result.get("reason") or "LLM disambiguation from fused top tables"
        kb_measure = result.get("measure") or ""
        route_label = f"Fused routing + LLM: {reason}"
    else:
        # Fallback to highest fused score when LLM fails or returns invalid id
        picked = [fused[0].full_table_id]
        reason = f"Top fused match `{fused[0].short_name}` (LLM unavailable)"
        route_label = reason

    try:
        from debug_session import ask_trace

        ask_trace(
            "table_disambiguation",
            question=question[:200],
            picked=[fq.rsplit(".", 1)[-1] for fq in picked],
            llm_table=table_fq.rsplit(".", 1)[-1] if table_fq else "",
            confidence=result.get("confidence", ""),
        )
    except Exception:
        pass

    kb_columns: dict[str, list[str]] = {}
    kb_filters: list[str] = []
    if picked:
        table = next((t for t in project_tables if t.full_table_id == picked[0]), None)
        knowledge = next((k for k in knowledges if k.full_table_id == picked[0]), None)
        if table and knowledge:
            kb_columns, kb_filters, plan_measure = kba.plan_columns_for_table(
                question, table, knowledge
            )
            if plan_measure and not kb_measure:
                kb_measure = plan_measure

    return picked, route_label, kb_columns, kb_filters, kb_measure


def _plan_columns_for_selection(
    question: str,
    selected_ids: list[str],
    knowledges: list[kb.TableKnowledge],
    project_tables: list[Any],
    kb_measure: str = "",
) -> tuple[dict[str, list[str]], list[str], str]:
    """Column + filter plan for already-selected table(s)."""
    import kb_articles as kba

    kb_columns: dict[str, list[str]] = {}
    kb_filters: list[str] = []
    measure = kb_measure
    for fq in selected_ids[:1]:
        table = next((t for t in project_tables if t.full_table_id == fq), None)
        knowledge = next((k for k in knowledges if k.full_table_id == fq), None)
        if not table or not knowledge:
            continue
        cols, filters, m = kba.plan_columns_for_table(question, table, knowledge)
        kb_columns.update(cols)
        for f in filters:
            if f not in kb_filters:
                kb_filters.append(f)
        if m and not measure:
            measure = m
    return kb_columns, kb_filters, measure


def build_ask_plan(
    question: str,
    project_tables: list[Any],
    join_hints: str = "",
    *,
    prior_sql: str = "",
) -> AskPlan:
    """Select tables: fused retrieval → keyword scoring → LLM router fallback."""
    import config
    from question_intent import question_is_breakdown_followup, question_wants_breakdown
    from table_routing import (
        compound_domain_table_ids,
        is_compound_domain_question,
    )

    keywords = kb.extract_keywords(question)
    matches: list[TableMatch] = []
    knowledges: list[kb.TableKnowledge] = []

    included = [t for t in project_tables if getattr(t, "included_for_ai", True)]
    for t in included:
        knowledge = kb.load_table_knowledge(t)
        knowledges.append(knowledge)
        score = kb.score_table_knowledge(question, knowledge, keywords)
        matches.append(
            TableMatch(
                full_table_id=knowledge.full_table_id,
                short_name=knowledge.short_name,
                score=score,
                table_description=knowledge.table_description,
            )
        )

    matches.sort(key=lambda m: (-m.score, m.short_name.lower()))

    prior_table_ids = _tables_from_prior_sql(prior_sql, included)
    is_followup = question_is_breakdown_followup(
        question, prior_sql=prior_sql
    )
    if prior_table_ids and is_followup:
        prior_set = set(prior_table_ids)
        for m in matches:
            if m.full_table_id in prior_set:
                m.score += _PRIOR_TABLE_BOOST
        matches.sort(key=lambda m: (-m.score, m.short_name.lower()))

    compound = is_compound_domain_question(question)
    mode = config.TABLE_ROUTER_MODE
    selected_ids: list[str] = []
    route_reason = ""
    kb_columns: dict[str, list[str]] = {}
    kb_filters: list[str] = []
    kb_measure = ""
    pinned: list[str] = []

    # Breakdown follow-ups must stay on the prior query's table(s).
    if prior_table_ids and is_followup:
        selected_ids = prior_table_ids[:_MAX_SELECTED]
        short = ", ".join(f"`{fq.rsplit('.', 1)[-1]}`" for fq in selected_ids)
        route_reason = f"Breakdown follow-up — reusing table(s) from prior query: {short}"

    # Compound multi-domain → both tables + join (skip single-table domain pin).
    elif compound:
        compound_ids = compound_domain_table_ids(question, included)
        if compound_ids:
            selected_ids = compound_ids[: max(_MAX_SELECTED, len(compound_ids))]
            short = ", ".join(f"`{fq.rsplit('.', 1)[-1]}`" for fq in selected_ids)
            route_reason = f"Compound domain (join): {short}"

    if not compound and not selected_ids:
        pinned = domain_table_override(question, included)
        if pinned:
            selected_ids = pinned[:_MAX_SELECTED]
            short = ", ".join(f"`{fq.rsplit('.', 1)[-1]}`" for fq in selected_ids)
            route_reason = f"Domain table pin: {short}"

    # 1. Fused retrieval over all tables → LLM disambiguation on top-K compact cards.
    if not selected_ids and config.ROUTING_FUSION_ENABLED and mode != "llm":
        fusion_ids, fusion_reason, kb_columns, kb_filters, kb_measure = _fusion_route_tables(
            question, matches, knowledges, included
        )
        if fusion_ids:
            selected_ids = fusion_ids[:_MAX_SELECTED]
            route_reason = fusion_reason

    # 2. Vector-only fallback when fusion disabled but embeddings enabled.
    if not selected_ids and config.EMBEDDING_RETRIEVAL_ENABLED and mode != "llm":
        selected_ids, route_reason = _vector_route_tables(
            question, included, knowledges, matches
        )

    # 3. Keyword/profile scoring fallback.
    if not selected_ids and mode != "llm":
        selected_ids, route_reason = _keyword_route_tables(question, matches)

    # 4. Legacy LLM router — last resort only when nothing else matched.
    if not selected_ids and mode in ("llm", "hybrid"):
        selected_ids, route_reason = _llm_route_tables(question, included)

    # 5. Absolute fallback: best keyword matches, never the full catalog.
    if not selected_ids and matches:
        selected_ids = [m.full_table_id for m in matches[:_MAX_SELECTED]]
        route_reason = "Best-effort match on table names and profiles."

    # Column/measure plan for pinned or fallback-selected tables.
    if selected_ids and not kb_columns:
        kb_columns, kb_filters, kb_measure = _plan_columns_for_selection(
            question, selected_ids, knowledges, included, kb_measure
        )
    elif selected_ids and pinned:
        pin_cols, pin_filters, pin_measure = _plan_columns_for_selection(
            question, selected_ids, knowledges, included, kb_measure
        )
        if pin_cols:
            kb_columns = pin_cols
        if pin_filters:
            kb_filters = pin_filters
        if pin_measure and not kb_measure:
            kb_measure = pin_measure

    for m in matches:
        m.selected = m.full_table_id in selected_ids

    catalog = jg.catalog_short_names(included)
    if pinned and not compound:
        join_relations = []
        join_reasoning = ""
    else:
        selected_ids, join_relations, join_reasoning = jg.expand_selection_with_joins(
            question,
            selected_ids,
            matches,
            knowledges,
            join_hints,
            catalog,
            keywords,
        )
    for m in matches:
        m.selected = m.full_table_id in selected_ids

    # Re-apply compound tables after join expansion (never collapse to single-table pin).
    if compound:
        compound_ids = compound_domain_table_ids(question, included)
        if compound_ids:
            selected_ids = compound_ids[: max(_MAX_SELECTED, len(compound_ids))]
            short = ", ".join(f"`{fq.rsplit('.', 1)[-1]}`" for fq in selected_ids)
            route_reason = f"Compound domain (join): {short}"
            if join_reasoning:
                route_reason = f"{route_reason}. {join_reasoning}"
    elif not compound:
        forced = domain_table_override(question, included)
        if forced:
            selected_ids = forced[:_MAX_SELECTED]
            short = ", ".join(f"`{fq.rsplit('.', 1)[-1]}`" for fq in selected_ids)
            route_reason = f"Domain table pin: {short}"
            join_relations = []
            join_reasoning = ""

    status = "Searching table metadata…"
    if keywords:
        focus = ", ".join(keywords[:3])
        status = f"Semantic table search for: {focus}…"

    selected_knowledge = [k for k in knowledges if k.full_table_id in selected_ids]
    if route_reason:
        reasoning = f"Table routing: {route_reason}"
    else:
        reasoning = kb.build_table_reasoning(question, selected_knowledge, selected_ids)
    if join_reasoning:
        reasoning = f"{reasoning} {join_reasoning}"

    compact = re.sub(r"\s+", " ", question.strip())
    if len(compact) > 42:
        compact = compact[:42].rstrip() + "…"
    sql_label = f'Creating "{compact}" sql cell…'

    return AskPlan(
        keywords=keywords,
        status_message=status,
        tables=matches,
        selected_full_ids=selected_ids,
        reasoning=reasoning,
        routing_reason=route_reason,
        sql_label=sql_label,
        join_relations=join_relations,
        join_reasoning=join_reasoning,
        kb_columns=kb_columns,
        kb_filters=kb_filters,
        kb_measure=kb_measure,
    )
