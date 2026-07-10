"""Plan which project tables match a question (shown in ask progress UI).

Routing order (Hex-style — metadata + curation first, LLM last):
  0. User @mention / pinned_table_ids (must use)
  1. Breakdown follow-up / domain table pins (deterministic safety net)
  2. Fused retrieval → auto-pick when score gap is clear
  3. Metadata keyword backup when fusion is ambiguous
  4. LLM disambiguation on top-K only when still ambiguous
  5. Metadata keyword fallback (again if LLM confidence is low)
  6. Keyword/profile scoring fallback
  7. Legacy LLM table router as last resort
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
    answer_mode: str = "auto"  # raw | aggregate | auto
    rules_block: str = ""
    clarify: dict | None = None


def extract_keywords(question: str) -> list[str]:
    return kb.extract_keywords(question)


def _llm_route_tables(question: str, project_tables: list[Any]) -> tuple[list[str], str]:
    """LLM reads names, descriptions, and AI profiles — works for any table set."""
    import llm

    catalog = []
    for t in project_tables:
        overview = (getattr(t, "ai_overview", "") or "").strip()
        desc = (getattr(t, "description", "") or "").strip()
        summary, guidance = kb.split_table_description(desc)
        catalog.append(
            {
                "full_table_id": t.full_table_id,
                "short_name": t.full_table_id.rsplit(".", 1)[-1],
                "description": (summary or desc)[:800],
                "guidance": guidance[:2000],
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


def _metadata_auto_pick(
    question: str,
    fused: list[Any],
    matches: list[TableMatch],
    knowledges: list[kb.TableKnowledge],
) -> tuple[list[str], str] | None:
    """Skip LLM when fused metadata search has a clear winner."""
    import config

    if not fused or not config.ROUTING_AUTO_PICK_ENABLED:
        return None

    top = fused[0]
    second_score = fused[1].fused_score if len(fused) > 1 else 0.0
    gap = top.fused_score - second_score
    knowledge = next((k for k in knowledges if k.full_table_id == top.full_table_id), None)
    match = next((m for m in matches if m.full_table_id == top.full_table_id), None)

    if gap >= config.ROUTING_AUTO_PICK_GAP and top.fused_score >= 0.12:
        return [top.full_table_id], f"Metadata search: `{top.short_name}` (clear match)"

    if knowledge and knowledge.endorsed and match and match.score >= 18:
        return [top.full_table_id], f"Endorsed table `{top.short_name}` (curated metadata match)"

    if match and match.score >= 700:
        return [top.full_table_id], f"Semantic layer + metadata: `{top.short_name}`"

    return None


def _metadata_confident_pick(
    question: str,
    matches: list[TableMatch],
) -> tuple[list[str], str] | None:
    """Keyword/metadata pick when fusion or LLM are not confident."""
    import config

    if not config.ROUTING_METADATA_BACKUP_ENABLED or not matches:
        return None

    top = matches[0]
    if top.score <= 0:
        return None

    second_score = matches[1].score if len(matches) > 1 else 0
    gap = top.score - second_score

    if top.score >= 700:
        return [top.full_table_id], f"Metadata: `{top.short_name}` (semantic layer match)"

    if top.score >= config.ROUTING_METADATA_MIN_SCORE and gap >= config.ROUTING_METADATA_SCORE_GAP:
        return [top.full_table_id], f"Metadata: `{top.short_name}` (clear keyword gap)"

    picked, reason = _keyword_route_tables(question, matches)
    if picked:
        return picked, reason
    return None


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

    auto = _metadata_auto_pick(question, fused, matches, knowledges)
    if auto:
        picked, route_label = auto
        kb_columns, kb_filters, kb_measure = _plan_columns_for_selection(
            question, picked, knowledges, project_tables, ""
        )
        return picked, route_label, kb_columns, kb_filters, kb_measure

    meta = _metadata_confident_pick(question, matches)
    if meta:
        picked, route_label = meta
        kb_columns, kb_filters, kb_measure = _plan_columns_for_selection(
            question, picked, knowledges, project_tables, ""
        )
        return picked, f"Metadata backup (fused ambiguous): {route_label}", kb_columns, kb_filters, kb_measure

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
    confidence = str(result.get("confidence") or "medium").strip().lower()
    if table_fq in valid_ids and confidence != "low":
        picked = [table_fq]
        reason = result.get("reason") or "LLM disambiguation from fused top tables"
        kb_measure = result.get("measure") or ""
        route_label = f"Fused routing + LLM: {reason}"
    elif table_fq in valid_ids and confidence == "low":
        meta = _metadata_confident_pick(question, matches)
        if meta:
            picked, route_label = meta
            route_label = f"Metadata backup (LLM low confidence): {route_label}"
        else:
            picked = [table_fq]
            reason = result.get("reason") or "LLM disambiguation (low confidence)"
            kb_measure = result.get("measure") or ""
            route_label = f"Fused routing + LLM: {reason}"
    else:
        meta = _metadata_confident_pick(question, matches)
        if meta:
            picked, route_label = meta
            route_label = f"Metadata backup (LLM unavailable): {route_label}"
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
            metadata_backup=route_label.startswith("Metadata backup"),
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


def _apply_intent_rerank(matches: list[TableMatch], question: str) -> None:
    """Boost table scores using planner intent before final routing selection."""
    try:
        from query_planner import classify_intent

        intent = classify_intent(question)
    except Exception:
        return

    q = question.lower()
    for m in matches:
        short = m.short_name.lower()
        if intent == "topic_search":
            if "nps" in short and "contextual" not in short:
                m.score += 220
            if "contextual_feedback" in short and ("nps" in q or "form responses" in q):
                m.score -= 350
            if short == "nps_all_form_responses" or "nps_all" in short:
                m.score += 300
        elif intent in ("aggregate", "breakdown"):
            if re.search(
                r"\b(learning[\s_-]*portal|portal)\b.{0,50}\b(activity|activit|page|events?)\b|"
                r"\bin which activity\b",
                q,
            ):
                if "day_and_page_wise" in short:
                    m.score += 500
                if "event_engagement" in short:
                    m.score -= 450
                if "master_data" in short and "which" in q:
                    m.score -= 200
            if re.search(
                r"\bactive\b.{0,40}\b(learning[\s_-]*portal|portal)\b|"
                r"\b(learning[\s_-]*portal|portal)\b.{0,40}\bactive\b",
                q,
            ):
                if "day_and_page_wise" in short:
                    m.score += 450
                if "master_data" in short:
                    m.score -= 250
        elif intent == "survey_distribution":
            if "contextual_feedback" in short or "survey" in short:
                m.score += 180
        elif intent == "compound":
            if any(k in short for k in ("attendance", "master_data", "engagement")):
                m.score += 200


def _apply_glossary_rerank(matches: list[TableMatch], question: str) -> None:
    """Boost tables whose model matches a glossary term hit."""
    try:
        from metrics_registry import match_glossary_terms
        from semantic_layer import semantic_by_model_id

        hits = match_glossary_terms(question)
        if not hits:
            return
        model_ids = {term.model_id for term, _ in hits}
        for m in matches:
            short = m.short_name.lower()
            for mid in model_ids:
                if short == mid.lower() or mid.lower() in short:
                    m.score += 700
                sem = semantic_by_model_id(mid)
                if sem and sem.full_table_id:
                    if sem.full_table_id.rsplit(".", 1)[-1].lower() == short:
                        m.score += 700
    except Exception:
        pass


def build_ask_plan(
    question: str,
    project_tables: list[Any],
    join_hints: str = "",
    *,
    prior_sql: str = "",
    user_table_pins: list[str] | None = None,
) -> AskPlan:
    """Select tables: user pins → metadata fusion → keyword → LLM fallback."""
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

    _apply_intent_rerank(matches, question)
    if config.GLOSSARY_ENABLED:
        _apply_glossary_rerank(matches, question)
    matches.sort(key=lambda m: (-m.score, m.short_name.lower()))

    prior_table_ids = _tables_from_prior_sql(prior_sql, included)
    from question_intent import is_drill_down_data_request
    from agents.answer_shape import is_thread_continuity_followup, detect_answer_shape

    is_followup = (
        question_is_breakdown_followup(question, prior_sql=prior_sql)
        or (bool(prior_sql) and question_wants_breakdown(question))
        or (bool(prior_sql) and is_drill_down_data_request(question))
        or is_thread_continuity_followup(question, prior_sql=prior_sql)
    )
    answer_shape = detect_answer_shape(question, prior_sql=prior_sql)
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
    user_pinned = bool(user_table_pins)
    rules_block = ""
    clarify_payload = None

    # 0. Explicit user control — @mention or pinned_table_ids from API.
    if user_table_pins:
        allowed = {t.full_table_id for t in included}
        selected_ids = [fq for fq in user_table_pins if fq in allowed][:_MAX_SELECTED]
        if selected_ids:
            short = ", ".join(f"`{fq.rsplit('.', 1)[-1]}`" for fq in selected_ids)
            route_reason = f"User table pin: {short}"

    # Breakdown / drill-down / raw-CSV continuity follow-ups stay on prior table(s).
    if not selected_ids and prior_table_ids and is_followup:
        selected_ids = prior_table_ids[:_MAX_SELECTED]
        short = ", ".join(f"`{fq.rsplit('.', 1)[-1]}`" for fq in selected_ids)
        if is_thread_continuity_followup(question, prior_sql=prior_sql):
            kind = "Thread continuity"
        elif is_drill_down_data_request(question):
            kind = "Drill-down"
        else:
            kind = "Breakdown"
        route_reason = f"{kind} follow-up — reusing table(s) from prior query: {short}"

    # Staged selection agent: keyword shortlist → description confirm → columns → rules.
    if not selected_ids and getattr(config, "SELECTION_AGENT_ENABLED", True):
        try:
            from agents.selection_agent import run_selection_agent

            sel = run_selection_agent(
                question,
                included,
                matches,
                knowledges,
                prior_sql=prior_sql,
                user_table_pins=user_table_pins,
            )
            if sel and sel.clarify:
                clarify_payload = sel.clarify
            if sel and sel.answer_shape:
                answer_shape = sel.answer_shape
            if sel and sel.selected_full_ids:
                selected_ids = sel.selected_full_ids[:_MAX_SELECTED]
                route_reason = sel.route_reason or route_reason
                if sel.kb_columns:
                    kb_columns = sel.kb_columns
                if sel.kb_filters:
                    kb_filters = sel.kb_filters
                if sel.kb_measure:
                    kb_measure = sel.kb_measure
                if sel.rules_block:
                    rules_block = sel.rules_block
        except Exception as exc:
            print(f"[ask_plan] selection agent skipped: {exc}")

    # Compound multi-domain → both tables + join (skip single-table domain pin).
    if not selected_ids and compound:
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
    if (pinned or user_pinned) and not compound:
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
    elif not compound and not user_pinned:
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

    if not rules_block and selected_ids:
        try:
            from table_business_rules import build_mandatory_rules_preamble

            sel_tables = [t for t in included if t.full_table_id in selected_ids]
            rules_block = build_mandatory_rules_preamble(sel_tables)
        except Exception:
            rules_block = ""

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
        answer_mode=getattr(answer_shape, "mode", "auto") or "auto",
        rules_block=rules_block,
        clarify=clarify_payload,
    )
