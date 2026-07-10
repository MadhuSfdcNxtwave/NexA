"""Ask pipeline with progress events for streaming UI."""
from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import bq
import config
import join_graph as jg
import knowledge_base as kb
import llm
from ask_plan import build_ask_plan, domain_table_override
from sql_chain import (
    combine_sql,
    format_prior_steps,
    merge_rows_for_display,
    plan_steps,
)
from sql_guard import validate_sql
from sql_verify_log import SqlAuditContext, llm_review_sql, log_sql_verification


def _validation_label(question: str) -> str:
    q = (question or "").strip().replace("\n", " ")
    if len(q) > 56:
        return q[:53] + "…"
    return q


def _parse_column_descriptions(raw: str | None) -> dict[str, str]:
    try:
        data = json.loads(raw or "{}")
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items() if str(v).strip()}
    except (json.JSONDecodeError, TypeError):
        return {}


def _parse_column_hints(raw: str | None) -> dict[str, str]:
    try:
        data = json.loads(raw or "{}")
        if not isinstance(data, dict):
            return {}
        allowed = {"primary_field", "primary_key", "primary_date", "feedback_field", "deprecated_duplicate"}
        return {str(k): str(v) for k, v in data.items() if str(v) in allowed}
    except (json.JSONDecodeError, TypeError):
        return {}


def _table_notes(tables: list) -> dict[str, str]:
    out: dict[str, str] = {}
    for t in tables:
        parts: list[str] = []
        if (t.description or "").strip():
            parts.append(t.description.strip())
        overview = (getattr(t, "ai_overview", "") or "").strip()
        if overview:
            parts.append(f"[AI OVERVIEW] {overview[:1200]}")
        if getattr(t, "endorsed", False):
            parts.append("[ENDORSED — preferred table for AI queries across the workspace]")
        if parts:
            out[t.full_table_id] = " | ".join(parts)
    return out


def _column_notes(tables: list) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for t in tables:
        cols = _parse_column_descriptions(t.column_descriptions_json)
        if cols:
            out[t.full_table_id] = cols
    return out


def _column_hints_map(tables: list) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for t in tables:
        hints = _parse_column_hints(t.column_hints_json)
        if hints:
            out[t.full_table_id] = hints
    return out


def _workspace_columns_for_table(table: Any) -> set[str]:
    """Column names from workspace_models.yaml when DB/BQ metadata is missing."""
    from semantic_layer import semantic_for_table

    sem = semantic_for_table(table)
    if not sem:
        return set()
    cols: set[str] = set()
    for dim in sem.dimensions:
        cols.add(dim.id.lower())
    for measure in sem.measures:
        if measure.of_column:
            cols.add(measure.of_column.lower())
    return cols


def _infer_hints_for_tables(
    tables: list,
) -> tuple[dict[str, dict[str, str]], dict[str, set[str]]]:
    """Column names + hints per table. Prefer workspace YAML columns (no BQ round-trip)."""
    hints: dict[str, dict[str, str]] = {}
    columns_by_table: dict[str, set[str]] = {}
    for t in tables:
        fq = t.full_table_id
        ws_cols = _parse_column_descriptions(getattr(t, "column_descriptions_json", "{}"))
        if ws_cols:
            columns_by_table[fq] = {k.lower() for k in ws_cols}
        stored = _parse_column_hints(getattr(t, "column_hints_json", "{}"))
        if stored:
            hints[fq] = stored
        if columns_by_table.get(fq):
            continue
        try:
            meta = bq.table_metadata(fq)
            names = [c["name"] for c in meta.get("columns") or []]
            columns_by_table[fq] = {n.lower() for n in names}
            hints[fq] = bq.infer_column_hints(names, stored)
        except Exception:
            columns_by_table[fq] = set()
            hints.setdefault(fq, {})
    return hints, columns_by_table


def _ensure_columns_for_tables(
    tables: list,
    columns_by_table: dict[str, set[str]],
    *,
    hints_map: dict[str, dict[str, str]] | None = None,
    inferred: dict[str, dict[str, str]] | None = None,
) -> tuple[dict[str, set[str]], dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """Ensure every table in *tables* has BQ column metadata (fixes jobs routing misses)."""
    columns = dict(columns_by_table)
    hints = dict(hints_map or {})
    inf = dict(inferred or {})
    for t in tables:
        fq = t.full_table_id
        if columns.get(fq):
            continue
        try:
            meta = bq.table_metadata(fq)
            names = [c["name"] for c in meta.get("columns") or []]
            stored = _parse_column_hints(getattr(t, "column_hints_json", "{}"))
            columns[fq] = {n.lower() for n in names}
            inf[fq] = bq.infer_column_hints(names, stored)
            if fq not in hints:
                hints[fq] = stored
        except Exception:
            columns[fq] = set()
            inf.setdefault(fq, {})
    return columns, inf, hints


def _short(full_table_id: str) -> str:
    return full_table_id.rsplit(".", 1)[-1]


def _column_list_hint(columns_by_table: dict[str, set[str]], limit: int = 40) -> str:
    if not columns_by_table:
        return ""
    lines: list[str] = []
    for fq, cols in columns_by_table.items():
        short = _short(fq)
        sorted_cols = sorted(cols)[:limit]
        shown = ", ".join(f"`{c}`" for c in sorted_cols)
        extra = f" … ({len(cols)} total)" if len(cols) > limit else ""
        lines.append(f"  {short}: {shown}{extra}")
    return "\n\nValid columns per table:\n" + "\n".join(lines)


def _compact_schema_text(schema_text: str, *, num_tables: int = 1) -> str:
    """Hex-style: cap schema context size sent to SQL/presentation models."""
    base = config.SCHEMA_CONTEXT_MAX_CHARS
    if num_tables <= 1:
        limit = max(base, 12000)
    elif num_tables == 2:
        limit = max(base, 8000)
    else:
        limit = base
    text = (schema_text or "").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n… (schema truncated for compact context)"


def _attach_suggestions(
    result: dict[str, Any],
    *,
    schema_text: str = "",
    error_context: str = "",
) -> dict[str, Any]:
    if result.get("suggestions"):
        return result
    result["suggestions"] = llm.suggest_followups(
        result.get("question") or "",
        analysis=result.get("analysis") or "",
        columns=result.get("columns") or None,
        schema_excerpt=schema_text,
        error_context=error_context,
    )
    return result


def _routing_meta(
    *,
    plan=None,
    selected: list | None = None,
    routing_reason: str = "",
    probe_stats: str = "",
    sql_source: str = "",
    model_used: str = "",
) -> dict[str, Any]:
    tables = selected or []
    if plan is not None:
        routing_reason = routing_reason or getattr(plan, "routing_reason", "") or ""
        if not tables:
            sel_ids = set(getattr(plan, "selected_full_ids", []) or [])
            tables = [t for t in getattr(plan, "tables", []) if t.full_table_id in sel_ids]
    selected_tables = []
    for t in tables:
        if hasattr(t, "short_name"):
            selected_tables.append(t.short_name)
        elif hasattr(t, "full_table_id"):
            selected_tables.append(t.full_table_id.rsplit(".", 1)[-1])
        else:
            selected_tables.append(str(t))
    out: dict[str, Any] = {}
    if routing_reason:
        out["routing_reason"] = routing_reason
    if selected_tables:
        out["selected_tables"] = selected_tables
    if probe_stats:
        out["probe_stats"] = probe_stats
    if sql_source:
        out["sql_source"] = sql_source
    if model_used:
        out["model_used"] = model_used
    return out


def _assistant_complete(
    question: str,
    analysis: str,
    *,
    sql: str = "",
    suggestions: list[str] | None = None,
    response_mode: str = "assistant",
    from_cache: bool = False,
    schema_text: str = "",
    routing_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = {
        "question": question,
        "sql": sql,
        "columns": [],
        "rows": [],
        "chart_spec": {"chart": "none"},
        "analysis": analysis,
        "bytes_estimate": 0,
        "from_cache": from_cache,
        "response_mode": response_mode,
        "suggestions": suggestions or [],
    }
    if routing_meta:
        out.update(routing_meta)
    if not out["suggestions"]:
        _attach_suggestions(out, schema_text=schema_text)
    return out


def _is_feedback_question(question: str) -> bool:
    if not config.SQL_TEMPLATES_ENABLED:
        return False
    from feedback_sql import is_feedback_table_question
    from nps_sql import is_nps_analytics_question
    from query_planner import classify_intent

    q = (question or "").strip()
    if is_nps_analytics_question(q):
        return False
    if classify_intent(q) == "topic_search":
        return False
    return is_feedback_table_question(q)


def _try_nps_template_sql(
    question: str,
    selected_tables: list,
    hints_map: dict,
    inferred: dict,
    columns_by_table: dict[str, set[str]],
    *,
    included_tables: list | None = None,
    schema_entities: list | None = None,
) -> str | None:
    if not config.SQL_TEMPLATES_ENABLED:
        return None
    from memory_lookup import sql_matches_question_intent
    from nps_sql import try_build_nps_sql

    pool = list(selected_tables)
    if included_tables:
        seen = {t.full_table_id for t in pool}
        for t in included_tables:
            if t.full_table_id not in seen:
                pool.append(t)
                seen.add(t.full_table_id)
    raw = try_build_nps_sql(question, pool, columns_by_table)
    if not raw:
        return None
    try:
        sql = bq.validate_select_only(raw)
    except ValueError:
        return None
    if not sql_matches_question_intent(
        question, sql, schema_entities=schema_entities
    ):
        return None
    violations = validate_sql(
        sql, question, pool, hints_map, inferred, columns_by_table=columns_by_table
    )
    if violations:
        return None
    return sql


def _safe_template_run(
    question: str,
    sql: str,
    *,
    schema_entities: list | None = None,
    **run_kwargs,
) -> dict[str, Any] | None:
    """Run template SQL only when intent matches — avoids wrong recovery answers."""
    from memory_lookup import sql_matches_question_intent

    if not sql_matches_question_intent(question, sql, schema_entities=schema_entities):
        return None
    try:
        return _run_sql_and_build_complete(question, sql, **run_kwargs)
    except Exception:
        return None


def _try_overview_template_sql(
    question: str,
    selected_tables: list,
    hints_map: dict,
    inferred: dict,
    columns_by_table: dict[str, set[str]],
    *,
    relaxed: bool = False,
    schema_entities: list | None = None,
    prior_sql: str = "",
) -> str | None:
    if not config.SQL_TEMPLATES_ENABLED:
        return None
    from memory_lookup import sql_matches_question_intent
    from overview_sql import try_build_overview_sql

    raw = try_build_overview_sql(
        question,
        selected_tables,
        columns_by_table,
        relaxed=relaxed,
        prior_sql=prior_sql,
    )
    if not raw:
        return None
    try:
        sql = bq.validate_select_only(raw)
    except ValueError:
        return None
    if not sql_matches_question_intent(
        question, sql, schema_entities=schema_entities
    ):
        return None
    violations = validate_sql(
        sql, question, selected_tables, hints_map, inferred, columns_by_table=columns_by_table
    )
    if violations:
        return None
    return sql


def _resolve_feedback_sql(
    question: str,
    selected_tables: list,
    hints_map: dict,
    inferred: dict,
    columns_by_table: dict[str, set[str]],
    catalog: dict[str, str],
) -> tuple[str | None, dict[str, Any] | None]:
    """Discovery-first feedback SQL; return clarification if no trustworthy match."""
    from feedback_sql import (
        build_group_sql_for_text,
        match_is_acceptable,
        plan_feedback_query,
    )
    from survey_sql import is_survey_answer_question

    if not is_survey_answer_question(question) and not _is_feedback_question(question):
        raw = _try_feedback_template_sql(
            question, selected_tables, hints_map, inferred, columns_by_table
        )
        return raw, None

    plan = plan_feedback_query(question, selected_tables, columns_by_table)
    if not plan:
        return _try_feedback_template_sql(
            question, selected_tables, hints_map, inferred, columns_by_table
        ), None

    try:
        disc_df = bq.run_query(plan["discovery_sql"], table_catalog=catalog)
        discovery_rows = json.loads(disc_df.to_json(orient="records", date_format="iso"))
    except Exception:
        discovery_rows = []

    if len(discovery_rows) < 2:
        from feedback_sql import _build_clarification_discovery_sql

        try:
            broad_sql = _build_clarification_discovery_sql(
                plan["fq"], plan["cols"], question
            )
            broad_df = bq.run_query(broad_sql, table_catalog=catalog)
            discovery_rows = json.loads(
                broad_df.to_json(orient="records", date_format="iso")
            )
        except Exception:
            pass

    for row in discovery_rows:
        qt = str(row.get("question_text") or "")
        if not qt:
            continue
        if match_is_acceptable(
            question,
            qt,
            question_type=str(row.get("question_type") or ""),
            keyword_score=int(row.get("keyword_score") or 0),
        ):
            raw = build_group_sql_for_text(
                plan["fq"], plan["cols"], question, qt
            )
            try:
                sql = bq.validate_select_only(raw)
            except ValueError:
                continue
            violations = validate_sql(
                sql,
                question,
                selected_tables,
                hints_map,
                inferred,
                columns_by_table=columns_by_table,
            )
            if not violations:
                return sql, None

    from ask_clarify import clarification_from_discovery

    clar = clarification_from_discovery(question, discovery_rows)
    return None, clar


def _try_feedback_template_sql(
    question: str,
    selected_tables: list,
    hints_map: dict,
    inferred: dict,
    columns_by_table: dict[str, set[str]],
    *,
    relaxed: bool = False,
    discovery: bool = False,
) -> str | None:
    if not config.SQL_TEMPLATES_ENABLED:
        return None
    from feedback_sql import (
        try_build_feedback_discovery_sql,
        try_build_feedback_sql,
        try_build_fallback_feedback_sql,
    )

    if discovery:
        raw = try_build_feedback_discovery_sql(question, selected_tables, columns_by_table)
    elif relaxed:
        raw = try_build_fallback_feedback_sql(question, selected_tables, columns_by_table)
    else:
        raw = try_build_feedback_sql(question, selected_tables, columns_by_table)
    if not raw:
        return None
    try:
        sql = bq.validate_select_only(raw)
    except ValueError:
        return None
    violations = validate_sql(
        sql, question, selected_tables, hints_map, inferred, columns_by_table=columns_by_table
    )
    if violations:
        return None
    return sql


def _planner_tables_for_plan(
    plan,
    selected_tables: list,
    pool: list,
    columns_by_table: dict[str, set[str]],
) -> tuple[list, dict[str, set[str]]]:
    """Expand selected tables + column map for union member models (even if not in workspace)."""
    from types import SimpleNamespace

    from semantic_layer import semantic_by_model_id

    planner_tables = list(selected_tables)
    cols = dict(columns_by_table or {})
    pool_fqs = {getattr(t, "full_table_id", "") for t in pool}
    member_ids = list(getattr(plan, "union_member_ids", None) or [])
    for mid in member_ids:
        sem = semantic_by_model_id(mid)
        if not sem or not sem.full_table_id:
            continue
        fq = sem.full_table_id
        if fq not in cols:
            cols[fq] = {d.id for d in sem.dimensions}
        found = next((t for t in planner_tables if getattr(t, "full_table_id", "") == fq), None)
        if found:
            continue
        if fq in pool_fqs:
            planner_tables.append(next(t for t in pool if t.full_table_id == fq))
        else:
            planner_tables.append(
                SimpleNamespace(
                    full_table_id=fq,
                    column_hints_json="{}",
                    column_descriptions_json="{}",
                    ai_profile_json="{}",
                )
            )
    return planner_tables, cols


def _try_join_template_sql(
    question: str,
    selected_tables: list,
    hints_map: dict,
    inferred: dict,
    columns_by_table: dict[str, set[str]],
    *,
    catalog_tables: list | None = None,
    schema_entities: list | None = None,
) -> tuple[str | None, str, list | None]:
    """Deterministic JOIN SQL for cross-table breakdowns (state, gender, etc.)."""
    from domain_sql import _join_template_table
    from join_compose import _find_table, try_compose_join_sql
    from memory_lookup import sql_matches_question_intent

    pool = list(catalog_tables or selected_tables)
    raw = try_compose_join_sql(question, pool)
    if not raw:
        return None, "", None
    try:
        sql = bq.validate_select_only(raw)
    except ValueError:
        return None, "", None
    if not sql_matches_question_intent(question, sql, schema_entities=schema_entities):
        return None, "", None

    tables_for_val: list = []
    primary = _join_template_table(question, pool)
    master = _find_table(pool, "master_data")
    profile = _find_table(pool, "profile_basic_details")
    if primary:
        tables_for_val.append(primary)
    if profile and profile not in tables_for_val:
        tables_for_val.append(profile)
    elif master and master not in tables_for_val:
        tables_for_val.append(master)
    if not tables_for_val:
        tables_for_val = list(selected_tables)

    val_hints = _column_hints_map(tables_for_val)
    val_inferred, val_cols = _infer_hints_for_tables(tables_for_val)
    merged_cols = dict(columns_by_table or {})
    merged_cols.update(val_cols)
    for t in tables_for_val:
        fq = t.full_table_id
        ws_cols = _workspace_columns_for_table(t)
        if ws_cols:
            merged_cols.setdefault(fq, set()).update(ws_cols)
    violations = validate_sql(
        sql,
        question,
        tables_for_val,
        val_hints,
        val_inferred,
        columns_by_table=merged_cols,
    )
    if violations:
        return None, "", None
    reason = "Join template SQL"
    from debug_session import ask_trace

    ask_trace(
        "join_template_sql",
        question=question[:200],
        hit=True,
        sql_preview=(sql or "")[:300],
    )
    return sql, reason, tables_for_val


def _try_rag_compose_sql(
    question: str,
    selected_tables: list,
    hints_map: dict,
    inferred: dict,
    columns_by_table: dict[str, set[str]],
    *,
    catalog_tables: list | None = None,
    schema_entities: list | None = None,
    query_plan=None,
) -> tuple[str | None, str, list | None, Any | None]:
    """Universal RAG path: glossary resolve → compose SQL → validate (no LLM)."""
    from rag_pipeline import try_rag_compose_sql

    sql, reason, tables, plan, resolved = try_rag_compose_sql(
        question,
        selected_tables,
        hints_map,
        inferred,
        columns_by_table,
        catalog_tables=catalog_tables,
        schema_entities=schema_entities,
        query_plan=query_plan,
    )
    if not sql:
        return None, reason or "", None, plan
    from debug_session import ask_trace

    ask_trace(
        "rag_compose",
        question=question[:200],
        hit=True,
        reason=(reason or "")[:200],
        sql_preview=(sql or "")[:300],
        resolved=resolved.to_trace_dict() if resolved else {},
        plan=plan.to_trace_dict() if plan else {},
    )
    return sql, reason, tables, plan


def _try_planner_sql(
    question: str,
    selected_tables: list,
    hints_map: dict,
    inferred: dict,
    columns_by_table: dict[str, set[str]],
    *,
    catalog_tables: list | None = None,
    schema_entities: list | None = None,
    query_plan=None,
) -> tuple[str | None, str, list | None, Any | None]:
    """Model-facing query planner — topic search, survey distribution, aggregates."""
    if not config.HEX_STYLE_PIPELINE:
        return None, "", None, None
    from memory_lookup import sql_matches_question_intent
    from query_compose import compose_query_plan
    from query_planner import analyze_question

    pool = catalog_tables or selected_tables
    plan = query_plan or analyze_question(
        question,
        selected_tables,
        catalog_tables=pool,
        columns_by_table=columns_by_table,
    )
    if not plan:
        return None, "", None, None
    raw = compose_query_plan(
        plan,
        question,
        selected_tables,
        columns_by_table,
        catalog_tables=pool,
    )
    if not raw:
        return None, plan.reason, None, plan
    planner_tables, planner_cols = _planner_tables_for_plan(
        plan, selected_tables, pool, columns_by_table
    )
    try:
        sql = bq.validate_select_only(raw)
    except ValueError:
        return None, plan.reason, None, plan
    if not sql_matches_question_intent(
        question,
        sql,
        schema_entities=schema_entities,
        query_plan=plan,
    ):
        from debug_session import ask_trace

        ask_trace(
            "planner_sql",
            question=question[:200],
            hit=False,
            reason="intent_mismatch",
            sql_preview=(sql or "")[:300],
            plan=plan.to_trace_dict(),
        )
        return None, plan.reason, None, plan
    planner_hints = _column_hints_map(planner_tables)
    planner_inferred, _ = _infer_hints_for_tables(planner_tables)
    violations = validate_sql(
        sql,
        question,
        planner_tables,
        planner_hints,
        planner_inferred,
        columns_by_table=planner_cols,
    )
    if violations:
        from debug_session import ask_trace

        ask_trace(
            "planner_sql",
            question=question[:200],
            hit=False,
            reason="validation",
            issues=violations[:5],
            sql_preview=(sql or "")[:300],
            plan=plan.to_trace_dict(),
        )
        return None, plan.reason, None, plan
    from debug_session import ask_trace

    ask_trace(
        "planner_sql",
        question=question[:200],
        hit=True,
        reason=plan.reason[:200] if plan.reason else "",
        sql_preview=(sql or "")[:300],
        plan=plan.to_trace_dict(),
    )
    return sql, plan.reason, planner_tables or None, plan


def _try_semantic_template_sql(
    question: str,
    selected_tables: list,
    hints_map: dict,
    inferred: dict,
    columns_by_table: dict[str, set[str]],
    *,
    catalog_tables: list | None = None,
) -> tuple[str | None, str, list | None]:
    """Compose SQL from YAML measures/dimensions when the question maps cleanly."""
    if not config.HEX_STYLE_PIPELINE:
        return None, "", None
    from ask_plan import domain_table_override
    from measure_router import try_build_measure_plan
    from sql_composer import compose_sql

    pool = catalog_tables or selected_tables
    forced = domain_table_override(question, pool) if pool else []
    if forced:
        pinned = [t for t in pool if t.full_table_id in forced]
        if pinned:
            selected_tables = pinned
            pool = pinned
    measure_plan = try_build_measure_plan(question, selected_tables, catalog_tables=pool)
    if not measure_plan:
        return None, "", None
    semantic_table = next(
        (t for t in pool if t.full_table_id == measure_plan.table_fq),
        selected_tables[0] if selected_tables else None,
    )
    if not semantic_table:
        return None, measure_plan.reason, None
    raw = compose_sql(measure_plan, question, semantic_table)
    try:
        sql = bq.validate_select_only(raw)
    except ValueError:
        return None, measure_plan.reason, None
    sem_hints, sem_cols = _infer_hints_for_tables([semantic_table])
    violations = validate_sql(
        sql,
        question,
        [semantic_table],
        sem_hints,
        sem_hints,
        columns_by_table=sem_cols,
    )
    if violations:
        return None, measure_plan.reason, None
    return sql, measure_plan.reason, [semantic_table]


def _run_query_with_preflight(
    question: str,
    sql: str,
    *,
    catalog: dict[str, str],
    selected: list,
) -> tuple[Any, list[dict], list[str], int, str, str, bool]:
    """Run BigQuery with optional pre-flight probe and empty-result date widen retry."""
    from preflight_sql import (
        build_preflight_sql,
        build_widen_date_sql,
        format_probe_stats,
        question_has_time_scope,
    )

    probe_stats = ""
    primary = selected[0] if selected else None
    if primary and config.HEX_STYLE_PIPELINE:
        probe_sql = build_preflight_sql(primary)
        if probe_sql:
            try:
                probe_df = bq.run_query(probe_sql, table_catalog=catalog)
                probe_rows = json.loads(probe_df.to_json(orient="records", date_format="iso"))
                probe_stats = format_probe_stats(probe_rows)
            except Exception:
                pass

    bytes_estimate = bq.dry_run_bytes(sql, table_catalog=catalog)
    df = bq.run_query(sql, table_catalog=catalog)
    rows = json.loads(df.to_json(orient="records", date_format="iso"))
    columns = list(df.columns)
    sql_used = sql
    widened = False

    if _looks_empty_result(rows) and question_has_time_scope(question) and primary:
        alt = build_widen_date_sql(sql, primary)
        if alt and alt.strip() != sql.strip():
            try:
                bytes_estimate = bq.dry_run_bytes(alt, table_catalog=catalog)
                df2 = bq.run_query(alt, table_catalog=catalog)
                rows2 = json.loads(df2.to_json(orient="records", date_format="iso"))
                if not _looks_empty_result(rows2):
                    df = df2
                    rows = rows2
                    columns = list(df.columns)
                    sql_used = alt
                    widened = True
            except Exception:
                pass

    return df, rows, columns, bytes_estimate, probe_stats, sql_used, widened


def _run_sql_and_build_complete(
    question: str,
    sql: str,
    *,
    catalog: dict[str, str],
    schema_text: str,
    included_tables: list,
    entity_label: str = "",
    presentation_hints: list[str] | None = None,
    conversation_context: str = "",
) -> dict[str, Any]:
    bytes_estimate = bq.dry_run_bytes(sql, table_catalog=catalog)
    df = bq.run_query(sql, table_catalog=catalog)
    rows = json.loads(df.to_json(orient="records", date_format="iso"))
    columns = list(df.columns)
    sample = rows[:50]
    viz_rows, chart_spec, analysis = llm.build_presentation(
        question,
        columns,
        rows,
        sample=sample,
        sql=sql,
        entity_label=entity_label,
        presentation_hints=presentation_hints,
        conversation_context=conversation_context,
    )
    if _looks_empty_result(rows):
        from table_profile import coverage_note_for_tables

        queried = [t for t in included_tables if t.full_table_id.rsplit(".", 1)[-1] in (sql or "")]
        note = coverage_note_for_tables(queried or included_tables)
        if note:
            analysis = f"{analysis}\n\n{note}"
    return {
        "question": question,
        "sql": sql,
        "columns": columns,
        "rows": rows,
        "viz_rows": viz_rows,
        "chart_spec": chart_spec,
        "analysis": analysis,
        "bytes_estimate": bytes_estimate,
        "from_cache": False,
        "response_mode": "data",
    }


def _short_feedback_empty_analysis(question: str, discovery_rows: list[dict] | None = None) -> str:
    if discovery_rows:
        top = discovery_rows[0].get("question_text")
        hint = f" Closest stored prompt: “{str(top)[:120]}”." if top else ""
        return (
            "No rows matched that exact survey wording in BigQuery. "
            "The UI label is often different from stored `question_text`."
            f"{hint} See similar prompts in the table below."
        )
    return (
        "No feedback rows matched those keywords. "
        "Try a shorter phrase from the survey (e.g. “valuable” or “updated Course Library”)."
    )


def _run_feedback_with_discovery(
    question: str,
    sql: str,
    *,
    catalog: dict[str, str],
    schema_text: str,
    included_tables: list,
    selected_tables: list,
    hints_map: dict,
    inferred: dict,
    columns_by_table: dict[str, set[str]],
) -> dict[str, Any]:
    """Run feedback SQL; if empty, discover similar question_text and retry."""
    result = _run_sql_and_build_complete(
        question,
        sql,
        catalog=catalog,
        schema_text=schema_text,
        included_tables=included_tables,
    )
    if not _looks_empty_result(result.get("rows") or []):
        from feedback_sql import match_is_acceptable

        rows = result.get("rows") or []
        matched_q = str(rows[0].get("question_text") or "")
        matched_type = str(rows[0].get("question_type") or "")
        if matched_q and not match_is_acceptable(question, matched_q, question_type=matched_type):
            result["analysis"] = (
                "That survey prompt is not stored verbatim in BigQuery. "
                "Pick the closest question from the list below, or rephrase."
            )
            result["chart_spec"] = {"chart": "none"}
        return result

    disc_sql = _try_feedback_template_sql(
        question,
        selected_tables,
        hints_map,
        inferred,
        columns_by_table,
        discovery=True,
    )
    if not disc_sql:
        result["analysis"] = _short_feedback_empty_analysis(question)
        result["response_mode"] = "data"
        return result

    try:
        disc_df = bq.run_query(disc_sql, table_catalog=catalog)
        discovery_rows = json.loads(disc_df.to_json(orient="records", date_format="iso"))
    except Exception:
        discovery_rows = []

    if discovery_rows:
        retry_sql = _try_feedback_template_sql(
            question,
            selected_tables,
            hints_map,
            inferred,
            columns_by_table,
            relaxed=True,
        )
        if retry_sql and retry_sql != sql:
            retry = _run_sql_and_build_complete(
                question,
                retry_sql,
                catalog=catalog,
                schema_text=schema_text,
                included_tables=included_tables,
            )
            if not _looks_empty_result(retry.get("rows") or []):
                return retry

        result["columns"] = list(disc_df.columns)
        result["rows"] = discovery_rows
        result["viz_rows"] = discovery_rows[:50]
        result["sql"] = disc_sql
        result["analysis"] = _short_feedback_empty_analysis(question, discovery_rows)
        result["chart_spec"] = {"chart": "none"}
        result["response_mode"] = "data"
        return result

    result["analysis"] = _short_feedback_empty_analysis(question)
    result["response_mode"] = "data"
    return result


def _sql_failure_recovery(
    question: str,
    error_detail: str,
    *,
    project_context: str,
    schema_text: str,
    cache_entries: list[dict[str, Any]] | None,
    selected_tables: list | None = None,
    hints_map: dict | None = None,
    inferred: dict | None = None,
    columns_by_table: dict[str, set[str]] | None = None,
    catalog: dict[str, str] | None = None,
    included_tables: list | None = None,
) -> dict[str, Any]:
    """Last resort: run domain SQL if possible, otherwise explain the failure."""
    _ = (
        project_context,
        cache_entries,
        selected_tables,
        hints_map,
        inferred,
        columns_by_table,
    )
    if included_tables:
        from domain_sql import resolve_domain_sql
        from chart_prepare import prepare_chart
        from presentation import heuristic_analyze, infer_chart_spec

        domain = resolve_domain_sql(question, included_tables)
        if domain:
            sql, _domain_table, domain_reason = domain
            try:
                bytes_estimate = bq.dry_run_bytes(sql, table_catalog=catalog or {})
                df = bq.run_query(sql, table_catalog=catalog or {})
                rows = json.loads(df.to_json(orient="records", date_format="iso"))
                columns = list(df.columns)
                fallback_spec = infer_chart_spec(question, columns, rows)
                viz_rows, chart_spec = prepare_chart(rows, columns, fallback_spec, question)
                analysis = heuristic_analyze(question, columns, rows, len(rows))
                return {
                    "question": question,
                    "sql": sql,
                    "columns": columns,
                    "rows": rows,
                    "viz_rows": viz_rows,
                    "chart_spec": chart_spec,
                    "analysis": analysis,
                    "bytes_estimate": bytes_estimate,
                    "from_cache": False,
                    "response_mode": "data",
                    "routing_reason": domain_reason,
                    "sql_source": "domain",
                }
            except Exception:
                pass

    analysis = llm.sql_failure_reply(
        question,
        error_detail,
        schema_excerpt=schema_text[:4000],
    )
    return _assistant_complete(
        question,
        analysis
        or "Could not run a query for that question. Try naming a column or metric from the Data tab.",
        response_mode="data",
        schema_text=schema_text,
    )


def _plan_event(plan) -> dict:
    return {
        "type": "search_tables",
        "message": plan.status_message,
        "keywords": plan.keywords,
        "tables": [
            {
                "full_table_id": m.full_table_id,
                "short_name": m.short_name,
                "selected": m.selected,
                "score": m.score,
            }
            for m in plan.tables
        ],
    }


def _repair_unbalanced_parens(sql: str) -> str:
    """Close trailing unbalanced parens — common LLM slip in long CONCAT/REGEXP SQL."""
    text = (sql or "").strip().rstrip(";")
    depth = 0
    in_str: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == "\\" and in_str == "'":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in ("'", '"'):
            in_str = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        i += 1
    if in_str is None and 0 < depth <= 3:
        return text + ")" * depth
    return sql


def _iter_validated_sql(
    question: str,
    schema_text: str,
    project_context: str,
    selected_tables: list,
    hints_map: dict[str, dict[str, str]],
    inferred: dict[str, dict[str, str]],
    columns_by_table: dict[str, set[str]],
    *,
    chain_context: str = "",
    step_label: str = "",
    sql_entity_hint: str = "",
    schema_entities: list | None = None,
    prior_error: str = "",
    audit: SqlAuditContext | None = None,
    source: str = "llm",
    table_catalog: dict[str, str] | None = None,
    sql_model: str | None = None,
) -> Iterator[tuple[dict[str, Any] | None, str | None]]:
    """Yield (event|None, sql). Last yield has sql set.

    Agent loop: generate → schema check → intent check → LLM review →
    BigQuery dry-run → run. Every failed stage feeds its error back into
    the next generation attempt.
    """
    prior = prior_error or ""
    base_attempts = max(1, config.SQL_MAX_ATTEMPTS)
    hard_cap = max(base_attempts, config.SQL_MAX_ATTEMPTS_CAP)
    stale_limit = max(1, config.SQL_RETRY_STALE_LIMIT)
    gen_msg = f'Creating SQL for "{step_label}"…' if step_label else "Generating SQL…"

    attempt = 0
    prev_violation_score = 10_000
    stale_rounds = 0
    last_sql = ""

    while attempt < hard_cap:
        attempt += 1
        if attempt > 1:
            yield {
                "type": "generating_sql",
                "message": f"Refining SQL (attempt {attempt}/{hard_cap})…",
            }, None
        elif step_label:
            yield {"type": "generating_sql", "message": gen_msg}, None

        # Escalate temperature on later retries so the model explores new shapes
        # instead of regenerating the same failing query.
        from agents.pipeline_bridge import agents_enabled
        from config_models import provider_for_model

        gen_model = sql_model
        gen_provider = provider_for_model(gen_model) if gen_model else None
        raw_sql = llm.question_to_sql(
            question,
            schema_text,
            project_context,
            prior_error=prior,
            chain_context=chain_context,
            sql_entity_hint=sql_entity_hint,
            temperature=0.0 if attempt <= 2 else 0.2,
            model=gen_model,
            provider=gen_provider,
        )
        try:
            sql = bq.validate_select_only(_repair_unbalanced_parens(raw_sql))
        except ValueError as e:
            # Syntax errors (e.g. unclosed parenthesis) are retryable — feed the
            # parser message back to the model instead of aborting the pipeline.
            last_sql = raw_sql
            stale_rounds += 1
            prior = (
                f"Previous SQL had a syntax error — regenerate the FULL query:\n{e}\n"
                "Make sure every opening parenthesis is closed, especially in "
                "REGEXP_CONTAINS(CONCAT(...), r'...') expressions."
            )
            if attempt >= base_attempts and stale_rounds >= stale_limit:
                break
            continue
        last_sql = sql

        # Reject SQL that queries tables outside the routed selection.
        import sql_parse as sp

        allowed_fq = {t.full_table_id.lower() for t in selected_tables}
        allowed_short = {t.full_table_id.rsplit(".", 1)[-1].lower() for t in selected_tables}
        try:
            refs = sp.table_refs(sp.parse_bigquery(sql))
            extra = [
                r for r in refs
                if r not in allowed_fq and r.rsplit(".", 1)[-1] not in allowed_short
            ]
            if extra:
                stale_rounds += 1
                prior = (
                    "SQL uses table(s) not in the selected schema: "
                    + ", ".join(extra)
                    + ". Regenerate using ONLY: "
                    + ", ".join(f"`{s}`" for s in sorted(allowed_short))
                )
                if attempt >= base_attempts and stale_rounds >= stale_limit:
                    break
                continue
        except ValueError:
            pass

        yield {"type": "validating_sql", "message": "Validating SQL against schema…"}, None
        violations = validate_sql(
            sql, question, selected_tables, hints_map, inferred, columns_by_table=columns_by_table
        )
        if violations:
            score = len(violations) * 10 + sum(len(v) for v in violations)
            log_sql_verification(
                audit,
                question=question,
                sql=sql,
                attempt=attempt,
                phase="schema",
                passed=False,
                issues=violations,
                source=source,
            )
            if score < prev_violation_score:
                prev_violation_score = score
                stale_rounds = 0
            else:
                stale_rounds += 1

            prior = "Schema validation failed:\n- " + "\n- ".join(violations)
            if any("Unknown column" in v for v in violations):
                prior += _column_list_hint(columns_by_table)

            if attempt >= base_attempts and stale_rounds >= stale_limit:
                break
            continue

        from memory_lookup import sql_intent_mismatch_reason

        if not agents_enabled():
            intent_reason = sql_intent_mismatch_reason(
                question, sql, schema_entities=schema_entities
            )
            if intent_reason:
                stale_rounds += 1
                prior = f"SQL does not match the question intent: {intent_reason} Regenerate the full query."
                log_sql_verification(
                    audit,
                    question=question,
                    sql=sql,
                    attempt=attempt,
                    phase="intent",
                    passed=False,
                    issues=[prior],
                    source=source,
                )
                if attempt >= base_attempts and stale_rounds >= stale_limit:
                    break
                continue

            if config.SQL_VERIFY_WITH_LLM:
                vlabel = _validation_label(question)
                yield {
                    "type": "validating_sql",
                    "message": f"Validating «{vlabel}» SQL query…",
                    "label": vlabel,
                }, None
                review = llm_review_sql(
                    question,
                    sql,
                    schema_text,
                    project_context,
                    audit=audit,
                    attempt=attempt,
                    source=source,
                )
                if not review["pass"] and review["issues"]:
                    score = len(review["issues"]) * 10
                    if score < prev_violation_score:
                        prev_violation_score = score
                        stale_rounds = 0
                    else:
                        stale_rounds += 1
                    prior = "SQL review failed:\n- " + "\n- ".join(review["issues"])
                    if attempt >= base_attempts and stale_rounds >= stale_limit:
                        break
                    continue

        # Final gate: BigQuery itself validates the query (dry-run costs nothing).
        # Catches unknown columns, type mismatches, and function errors that
        # static checks miss — the error message feeds the next attempt.
        try:
            bq.dry_run_bytes(sql, table_catalog=table_catalog)
        except Exception as e:
            err = bq.format_query_error(e)
            stale_rounds += 1
            prior = (
                f"BigQuery rejected this SQL on dry-run — fix the exact error and "
                f"regenerate the FULL query:\n{err}"
            )
            log_sql_verification(
                audit,
                question=question,
                sql=sql,
                attempt=attempt,
                phase="dry_run",
                passed=False,
                issues=[err[:500]],
                source=source,
            )
            if attempt >= base_attempts and stale_rounds >= stale_limit:
                break
            continue

        log_sql_verification(
            audit,
            question=question,
            sql=sql,
            attempt=attempt,
            phase="approved",
            passed=True,
            issues=[],
            source=source,
        )
        yield {"type": "sql_verified", "message": "SQL passed validation.", "sql": sql}, sql
        return

    raise ValueError(
        f"Could not generate SQL that passes validation after {attempt} attempts.\n"
        f"Last feedback:\n{prior}"
        + (f"\n\nLast SQL tried:\n{last_sql[:1500]}" if last_sql else "")
    )


def _iter_chain_sql(
    question: str,
    chain_plan: list[dict[str, str]],
    schema_text: str,
    project_context: str,
    selected_tables: list,
    hints_map: dict[str, dict[str, str]],
    inferred: dict[str, dict[str, str]],
    columns_by_table: dict[str, set[str]],
    table_catalog: dict[str, str] | None = None,
    *,
    sql_entity_hint: str = "",
    schema_entities: list | None = None,
    audit: SqlAuditContext | None = None,
) -> Iterator[tuple[dict[str, Any] | None, list[dict[str, Any]] | None]]:
    """Yield progress events; final yield includes executed chain steps."""
    completed: list[dict[str, Any]] = []
    total = len(chain_plan)

    for idx, step in enumerate(chain_plan, 1):
        label = step["label"]
        step_q = step["question"]
        yield {
            "type": "chain_step",
            "step": idx,
            "total": total,
            "label": label,
            "message": f"Chain step {idx}/{total}: {label}",
        }, None

        chain_context = format_prior_steps(completed)

        sql = None
        from notebook_step_sql import compose_notebook_step_sql

        composed, compose_reason = compose_notebook_step_sql(
            step,
            question,
            selected_tables,
            hints_map,
            inferred,
            columns_by_table,
            catalog_tables=selected_tables,
            schema_entities=schema_entities,
        )
        if not composed:
            from chain_pipeline import try_compose_chain_step_sql

            composed, compose_reason = try_compose_chain_step_sql(
                step_q,
                selected_tables,
                hints_map,
                inferred,
                columns_by_table,
                catalog_tables=selected_tables,
                schema_entities=schema_entities,
            )
        if composed:
            sql = composed
            yield {
                "type": "status",
                "message": f"Chain step {idx}/{total}: composed SQL ({compose_reason[:60]})",
            }, None

        if not sql:
            for event, candidate in _iter_validated_sql(
                step_q,
                schema_text,
                project_context,
                selected_tables,
                hints_map,
                inferred,
                columns_by_table,
                chain_context=chain_context,
                step_label=label,
                sql_entity_hint=sql_entity_hint,
                schema_entities=schema_entities,
                audit=audit,
                source="chain",
                table_catalog=table_catalog,
            ):
                if event:
                    yield event, None
                if candidate:
                    sql = candidate
        if not sql:
            raise ValueError(f"SQL generation failed for chain step: {label}")

        yield {"type": "running_query", "message": f"Running step {idx}/{total} on BigQuery…"}, None
        bytes_estimate = bq.dry_run_bytes(sql, table_catalog=table_catalog)
        df = bq.run_query(sql, table_catalog=table_catalog)
        rows = json.loads(df.to_json(orient="records", date_format="iso"))
        columns = list(df.columns)

        completed.append(
            {
                "label": label,
                "question": step_q,
                "sql": sql,
                "columns": columns,
                "rows": rows,
                "bytes_estimate": bytes_estimate,
            }
        )

    yield None, completed


def iter_ask(
    question: str,
    project_context: str,
    *,
    included_tables: list,
    join_hints: str = "",
    preapproved_sql: str | None = None,
    cache_entries: list[dict[str, Any]] | None = None,
    reuse_cached: bool = True,
    force_fresh: bool = False,
    clarification_choice: str | None = None,
    clarification_text: str | None = None,
    refined_question: str | None = None,
    pinned_table_ids: list[str] | None = None,
    audit: SqlAuditContext | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield progress events, then a final complete event with the ask result."""
    from ask_clarify import apply_clarification, build_clarification, should_clarify_before_sql
    from debug_session import ask_trace, debug_log

    pipeline_started = time.monotonic()

    def _elapsed() -> float:
        return round(time.monotonic() - pipeline_started, 1)

    def _complete_payload(result: dict, **attach_kw) -> dict:
        out = _attach_suggestions(result, **attach_kw)
        out["worked_seconds"] = _elapsed()
        out["pipeline_mode"] = "hex" if config.HEX_STYLE_PIPELINE else "full"
        return out

    original_question = question.strip()
    if not preapproved_sql:
        from sql_parse import extract_sql_from_text

        embedded_sql = extract_sql_from_text(original_question)
        if embedded_sql:
            preapproved_sql = embedded_sql
    has_clarification = bool(
        (refined_question or "").strip()
        or clarification_choice
        or (clarification_text or "").strip()
    )
    if has_clarification:
        question = apply_clarification(
            original_question,
            refined_question=refined_question,
            clarification_choice=clarification_choice,
            clarification_text=clarification_text,
        )
        yield {
            "type": "status",
            "message": "Using your clarification — generating SQL…",
        }

    from ask_context import build_query_context

    prior_entry = None
    if cache_entries:
        from result_cache import _latest_thread_entry, _latest_thread_sql_entry

        prior_entry = _latest_thread_entry(cache_entries) or _latest_thread_sql_entry(cache_entries)

    prior_q = (prior_entry or {}).get("question") or ""
    prior_sql = (prior_entry or {}).get("sql") or ""
    from question_intent import expand_breakdown_followup, expand_drill_down_followup

    expanded_question = expand_breakdown_followup(
        original_question, prior_q, prior_sql
    )
    if expanded_question == original_question.strip():
        expanded_question = expand_drill_down_followup(
            original_question, prior_q, prior_sql
        )
    if expanded_question != original_question.strip():
        question = expanded_question

    query_ctx = build_query_context(
        question,
        original_question=original_question,
        has_thread_history=bool(cache_entries) or bool(project_context.strip()),
    )
    question = query_ctx.question
    yield {"type": "status", "message": query_ctx.understanding_message}

    resolved_pins = list(pinned_table_ids or [])
    pin_reason = ""
    if resolved_pins or "@" in question:
        from table_mentions import apply_table_pins

        cleaned, resolved_pins, pin_reason = apply_table_pins(
            question,
            included_tables,
            pinned_table_ids=resolved_pins,
        )
        if resolved_pins:
            if cleaned and cleaned != question:
                question = cleaned
                query_ctx.question = cleaned
            if pin_reason:
                yield {"type": "status", "message": pin_reason}

    from project_context import build_thread_conversation_context, merge_ask_context

    conversation_context = build_thread_conversation_context(
        cache_entries,
        exclude_question=original_question,
    )
    project_context = merge_ask_context(
        project_context, cache_entries, original_question
    )
    if conversation_context and cache_entries:
        yield {
            "type": "status",
            "message": f"Loaded {min(len([e for e in cache_entries if e.get('source') == 'thread']), config.THREAD_CONVERSATION_TURNS)} prior Thread turn(s) into context…",
        }

    debug_log(
        "ask_pipeline.py:iter_ask",
        "ask_start",
        {
            "question": question[:200],
            "force_fresh": force_fresh,
            "reuse_cached": reuse_cached,
            "cache_entry_count": len(cache_entries or []),
            "cache_sources": [e.get("source") for e in (cache_entries or [])[:8]],
        },
        hypothesis_id="H1",
    )
    if not included_tables:
        raise ValueError(
            "No tables in this project yet. Open the Data tab, add tables (or import YAML), "
            "then ask again."
        )

    # Same question in Thread memory → return stored result (no BigQuery, no new memory row).
    if (
        not force_fresh
        and not preapproved_sql
        and not has_clarification
        and cache_entries
    ):
        from memory_lookup import try_exact_memory_hit

        remembered = try_exact_memory_hit(question, cache_entries)
        if remembered:
            debug_log(
                "ask_pipeline.py:iter_ask",
                "memory_exact_hit",
                {"question": question[:200]},
                hypothesis_id="H1",
            )
            yield {
                "type": "complete",
                **_complete_payload(remembered),
            }
            return

    if (
        not force_fresh
        and config.CACHE_ANSWER_ENABLED
        and reuse_cached
        and cache_entries
        and not preapproved_sql
    ):
        from result_cache import (
            is_likely_followup,
            needs_fresh_query,
            try_answer_from_cache,
            try_explain_from_thread,
            try_revisualize_from_prior,
        )

        skip_cache = needs_fresh_query(question, cache_entries)

        if not skip_cache:
            yield {"type": "status", "message": "Checking prior results in memory…"}

            intent = query_ctx.intent
            if intent == "explain_prior" and not needs_fresh_query(question, cache_entries):
                explained = try_explain_from_thread(question, cache_entries)
                if explained:
                    yield {
                        "type": "status",
                        "message": "Explaining based on your conversation history…",
                    }
                    yield {
                        "type": "complete",
                        **_complete_payload(explained),
                    }
                    return

            reviz = try_revisualize_from_prior(question, cache_entries)
            if reviz:
                yield {
                    "type": "cache_hit",
                    "message": "Reusing your previous answer — building chart and report.",
                    "source": reviz.get("cache_source"),
                }
                yield {
                    "type": "complete",
                    **_complete_payload(reviz),
                }
                return

            cached = try_answer_from_cache(
                question,
                cache_entries,
                always_check=is_likely_followup(question, bool(cache_entries)),
            )
            if cached:
                debug_log(
                    "ask_pipeline.py:iter_ask",
                    "cache_hit",
                    {
                        "question": question[:200],
                        "cache_source": cached.get("cache_source"),
                        "row_count": len(cached.get("rows") or []),
                        "from_cache": True,
                    },
                    hypothesis_id="H1",
                )
                yield {
                    "type": "cache_hit",
                    "message": "Answered from cached data — no new BigQuery scan.",
                    "source": cached.get("cache_source"),
                }
                yield {
                    "type": "complete",
                    **_complete_payload(cached),
                }
                return
        elif cache_entries:
            yield {
                "type": "status",
                "message": "Running a new query for more detail…",
            }

    intent = query_ctx.intent
    if intent == "explain_prior":
        from result_cache import try_explain_from_thread

        if cache_entries:
            explained = try_explain_from_thread(question, cache_entries)
            if explained:
                yield {"type": "status", "message": "Reviewing your prior answers…"}
                yield {"type": "complete", **_complete_payload(explained)}
                return
        if project_context.strip():
            yield {"type": "status", "message": "Reviewing your conversation…"}
            analysis = llm.explain_from_thread(question, project_context)
            complete = _assistant_complete(
                question,
                analysis,
                response_mode="explain",
                from_cache=True,
            )
            yield {"type": "complete", **_complete_payload(complete)}
            return

    if intent == "knowledge_query":
        from metrics_registry import glossary_context_for_question

        yield {"type": "status", "message": "Explaining…"}
        glossary_ctx, _ = glossary_context_for_question(question)
        analysis = llm.knowledge_reply(question, glossary_ctx, project_context)
        complete = _assistant_complete(
            question,
            analysis,
            response_mode="knowledge",
            schema_text="",
        )
        yield {"type": "complete", **_complete_payload(complete)}
        return

    if intent == "assistant":
        yield {"type": "status", "message": "Thinking…"}
        analysis = llm.assistant_reply(question, project_context)
        complete = _assistant_complete(
            question,
            analysis,
            response_mode="assistant",
            schema_text="",
        )
        yield {"type": "complete", **_complete_payload(complete)}
        return

    plan = build_ask_plan(
        question,
        included_tables,
        join_hints,
        prior_sql=prior_sql,
        user_table_pins=resolved_pins or None,
    )
    ask_trace(
        "table_plan",
        question=question[:200],
        routing_reason=plan.routing_reason or plan.reasoning[:200],
        selected=[m.short_name for m in plan.tables if m.selected],
        top_scores=[(m.short_name, m.score) for m in plan.tables[:5]],
    )
    yield {"type": "status", "message": plan.status_message}
    yield _plan_event(plan)

    if plan.join_relations:
        yield {
            "type": "join_hints",
            "message": plan.join_reasoning or "Using project join hints…",
            "relations": [
                {
                    "source": r.source,
                    "target": r.target,
                    "rel_type": r.rel_type,
                    "join_sql": r.join_sql[:200],
                }
                for r in plan.join_relations
            ],
        }

    selected = [t for t in included_tables if t.full_table_id in plan.selected_full_ids]
    if not selected:
        forced = domain_table_override(question, included_tables)
        if forced:
            selected = [t for t in included_tables if t.full_table_id in forced]
    if not selected:
        # Never dump the entire catalog into the SQL context — with 40+ tables
        # that produces wrong answers. Keep the best-ranked few instead.
        ranked = [m.full_table_id for m in plan.tables[:3]]
        selected = [t for t in included_tables if t.full_table_id in ranked] or included_tables[:3]

    yield {
        "type": "view_tables",
        "count": len(selected),
        "tables": [
            {"full_table_id": t.full_table_id, "short_name": _short(t.full_table_id)}
            for t in selected
        ],
    }

    hints_map = _column_hints_map(selected)
    inferred, columns_by_table = _infer_hints_for_tables(selected)

    from ask_context import enrich_query_context

    query_ctx = enrich_query_context(query_ctx, selected, columns_by_table)

    query_plan = None
    if config.HEX_STYLE_PIPELINE:
        from query_planner import analyze_question

        query_plan = analyze_question(
            question,
            selected,
            catalog_tables=included_tables,
            columns_by_table=columns_by_table,
        )
        ask_trace(
            "intent_plan",
            question=question[:200],
            hit=bool(query_plan),
            plan=query_plan.to_trace_dict() if query_plan else {},
        )

    if not preapproved_sql and not has_clarification:
        pre_clar = should_clarify_before_sql(
            question,
            selected_table_shorts=[_short(t.full_table_id) for t in selected],
            has_clarification=has_clarification,
        )
        if pre_clar:
            yield {"type": "awaiting_clarification", **pre_clar}
            return

    knowledges = [kb.load_table_knowledge(t) for t in selected]
    column_matches: dict[str, list[kb.ColumnMatch]] = {}
    for k in knowledges:
        merged_hints = {**inferred.get(k.full_table_id, {}), **hints_map.get(k.full_table_id, {})}
        column_matches[k.full_table_id] = kb.match_columns(
            question, k, merged_hints
        )

    col_reasoning = kb.build_column_reasoning(column_matches)
    if plan.kb_columns:
        import kb_articles as kba

        kb_col_reason = kba.apply_kb_columns_to_matches(column_matches, plan.kb_columns)
        if kb_col_reason:
            col_reasoning = f"{kb_col_reason} {col_reasoning}".strip()
        try:
            from debug_session import ask_trace

            ask_trace(
                "column_plan",
                question=question[:200],
                measure=plan.kb_measure,
                filters=plan.kb_filters[:4],
                columns={k.rsplit(".", 1)[-1]: v for k, v in plan.kb_columns.items()},
            )
        except Exception:
            pass
    full_reasoning = plan.reasoning
    if col_reasoning:
        full_reasoning = f"{full_reasoning} {col_reasoning}".strip()
    yield {"type": "reasoning", "text": full_reasoning}

    if col_reasoning:
        yield {
            "type": "match_columns",
            "message": "Matched columns from descriptions…",
            "tables": [
                {
                    "full_table_id": k.full_table_id,
                    "short_name": k.short_name,
                    "columns": [
                        {
                            "name": c.name,
                            "description": (c.description or "")[:120],
                            "selected": c.selected,
                        }
                        for c in column_matches[k.full_table_id]
                        if c.selected
                    ],
                }
                for k in knowledges
            ],
        }

    highlight_columns = {
        fq: {c.name for c in cols if c.selected}
        for fq, cols in column_matches.items()
    }

    kb_filter_block = ""
    if plan.kb_filters:
        kb_filter_block = "# KB router filters (apply when valid for selected table)\n" + "\n".join(
            f"- {f}" for f in plan.kb_filters[:8]
        )
    if plan.kb_measure:
        kb_filter_block += f"\nPreferred measure: `{plan.kb_measure}`"
    try:
        from table_routing import compound_sql_hints

        compound_hint = compound_sql_hints(question)
        if compound_hint:
            kb_filter_block = f"{kb_filter_block}\n\n{compound_hint}".strip()
    except Exception:
        pass

    selected_short = {_short(t.full_table_id).lower() for t in selected}
    filtered_join_hints = jg.filter_join_hints_text(join_hints, selected_short)
    catalog = jg.catalog_short_names(included_tables)
    join_block = jg.build_join_knowledge_block(plan.join_relations, catalog)

    schema_text = kb.build_knowledge_header(question, knowledges, column_matches)
    if kb_filter_block:
        schema_text += "\n\n" + kb_filter_block.strip()
    from question_dates import build_date_hints

    date_hints = build_date_hints(question, selected)
    compact_context = ""
    rag_result = None
    if config.HEX_STYLE_PIPELINE and query_plan:
        if config.GLOSSARY_ENABLED:
            from rag_context import build_rag_context
            from retrieval_service import retrieve
            from term_resolver import resolve as resolve_terms

            resolved = resolve_terms(
                question,
                selected,
                catalog_tables=included_tables,
                columns_by_table=columns_by_table,
            )
            rag_result = retrieve(
                question,
                included_tables,
                resolved=resolved,
            )
            compact_context = build_rag_context(
                rag_result,
                selected_tables=selected,
                date_hints=date_hints,
                join_block=join_block,
                thread_memory=project_context[:1200] if project_context else "",
            )
            ask_trace(
                "rag_retrieval",
                question=question[:200],
                **rag_result.to_trace_dict(),
            )
        else:
            from context_builder import build_model_context

            compact_context = build_model_context(
                query_plan,
                selected_tables=selected,
                date_hints=date_hints,
                join_block=join_block,
                thread_memory=project_context[:1200] if project_context else "",
            )
    if date_hints:
        schema_text += "\n\n" + date_hints
    if join_block:
        schema_text += "\n\n" + join_block
    schema_text += "\n\n" + bq.schema_for_tables(
        [t.full_table_id for t in selected],
        filtered_join_hints or join_hints,
        table_notes=_table_notes(selected),
        column_notes=kb.merged_column_notes(knowledges),
        column_hints={fq: {**inferred.get(fq, {}), **hints_map.get(fq, {})} for fq in [t.full_table_id for t in selected]},
        highlight_columns=highlight_columns,
        max_columns_per_table=config.SCHEMA_MAX_COLUMNS_PER_TABLE,
    )
    if config.HEX_STYLE_PIPELINE and selected:
        from sql_composer import enrich_schema_with_measures

        schema_text = enrich_schema_with_measures(schema_text, selected[0])
    schema_text = _compact_schema_text(schema_text, num_tables=len(selected))

    from agents.pipeline_bridge import (
        agents_enabled,
        business_rules_block,
        critic_validate_and_fix,
        enrich_schema_context,
        sql_model_for_question,
        try_learned_pattern,
    )

    schema_text = enrich_schema_context([t.full_table_id for t in selected], schema_text)
    rules_block = business_rules_block(question)
    if rules_block:
        schema_text += "\n\n" + rules_block
    model_used = sql_model_for_question(question) if agents_enabled() else config.FETCH_MODEL

    routing_meta = _routing_meta(plan=plan, selected=selected)
    sql_source = ""
    probe_stats = ""
    ask_trace(
        "schema_ready",
        question=question[:200],
        tables=[_short(t.full_table_id) for t in selected],
        schema_chars=len(schema_text),
        date_hints=bool(date_hints),
    )

    if preapproved_sql:
        sql_source = "user"
        sql = bq.validate_select_only(preapproved_sql)
        violations = validate_sql(
            sql, question, selected, hints_map, inferred, columns_by_table=columns_by_table
        )
        if violations:
            raise ValueError("SQL failed validation:\n- " + "\n- ".join(violations))
        chain_steps = None
    else:
        domain_resolved = None
        chain_plan: list[dict[str, str]] = []
        from domain_sql import is_domain_question, resolve_domain_sql

        if not config.HEX_STYLE_PIPELINE:
            domain_resolved = resolve_domain_sql(question, included_tables)

        if config.HEX_STYLE_PIPELINE and not domain_resolved:
            from notebook_planner import plan_notebook_steps

            skip_chain = bool(
                query_plan
                and query_plan.intent in ("topic_search", "survey_distribution")
            )
            if (
                not skip_chain
                and config.SQL_CHAIN_ENABLED
                and not is_domain_question(question, included_tables)
            ):
                yield {
                    "type": "status",
                    "message": "Planning notebook SQL cells…",
                }
                chain_plan = plan_notebook_steps(
                    question,
                    schema_text,
                    selected_tables=selected,
                    query_plan=query_plan,
                    join_relations=plan.join_relations,
                    max_steps=config.SQL_CHAIN_MAX_STEPS,
                )

        if domain_resolved:
            template_sql, domain_table, domain_reason = domain_resolved
            selected = [domain_table]
            knowledges = [kb.load_table_knowledge(t) for t in selected]
            hints_map = _column_hints_map(selected)
            inferred, columns_by_table = _infer_hints_for_tables(selected)
            sql = template_sql
            sql_source = "domain"
            chain_steps = None
            routing_meta = {
                **routing_meta,
                "routing_reason": domain_reason,
                "sql_source": sql_source,
            }
            ask_trace(
                "domain_sql",
                question=question[:200],
                hit=True,
                reason=domain_reason,
                sql_preview=(sql or "")[:300],
            )
        else:
            try:
                if chain_plan:
                    yield {
                        "type": "chain_plan",
                        "steps": chain_plan,
                        "message": f"Generating {len(chain_plan)} notebook SQL cells…",
                    }
                    executed: list[dict[str, Any]] | None = None
                    for event, done in _iter_chain_sql(
                        question,
                        chain_plan,
                        schema_text,
                        project_context,
                        selected,
                        hints_map,
                        inferred,
                        columns_by_table,
                        table_catalog=catalog,
                        sql_entity_hint=query_ctx.sql_entity_hint,
                        schema_entities=query_ctx.schema_entities,
                        audit=audit,
                    ):
                        if event:
                            yield event
                        if done is not None:
                            executed = done
                    if not executed:
                        raise ValueError("SQL chain failed")

                    columns, rows = merge_rows_for_display(executed)
                    sql = combine_sql(executed)
                    bytes_estimate = sum(int(s.get("bytes_estimate") or 0) for s in executed)
                    chain_steps = executed
                    sample = rows[:50]

                    yield {"type": "analyzing", "message": "Building chart and analysis…"}
                    viz_rows, chart_spec, analysis = llm.build_presentation(
                        question,
                        columns,
                        rows,
                        sample=sample,
                        chain_steps=executed,
                        sql=sql,
                        entity_label=query_ctx.entity_label,
                        presentation_hints=query_ctx.presentation_hints,
                        conversation_context=conversation_context,
                    )

                    yield {
                        "type": "complete",
                        **_complete_payload(
                            {
                                "question": question,
                                "sql": sql,
                                "columns": columns,
                                "rows": rows,
                                "viz_rows": viz_rows,
                                "chart_spec": chart_spec,
                                "analysis": analysis,
                                "bytes_estimate": bytes_estimate,
                                "sql_steps": chain_steps,
                                "from_cache": False,
                                "response_mode": "data",
                            },
                            schema_text=schema_text,
                        ),
                    }
                    return

                yield {"type": "generating_sql", "message": plan.sql_label}

                clar_payload = None
                semantic_reason = ""
                planner_plan = query_plan
                template_sql = None
                semantic_tables = None

                # Temp query agent — plan complex / high-risk questions before planner.
                if not has_clarification:
                    from agents.temp_agent_bridge import try_temp_agent_sql

                    agent_sql, agent_reason, agent_clar = try_temp_agent_sql(
                        question,
                        list(included_tables or selected),
                        columns_by_table,
                        prior_sql=prior_sql,
                    )
                    if agent_clar:
                        yield {
                            "type": "awaiting_clarification",
                            "prompt": agent_clar.get("prompt") or "Which interpretation?",
                            "options": agent_clar.get("options") or [],
                            "allow_custom": agent_clar.get("allow_custom", True),
                            "confirm_mode": agent_clar.get("confirm_mode", True),
                            "question": original_question,
                            "reasons": [agent_reason] if agent_reason else ["temp_agent"],
                        }
                        return
                    if agent_sql:
                        try:
                            template_sql = bq.validate_select_only(agent_sql)
                            sql_source = "temp_agent"
                            semantic_reason = agent_reason or "Temp query agent"
                            ask_trace(
                                "temp_agent_sql",
                                question=question[:200],
                                hit=True,
                                reason=(semantic_reason or "")[:200],
                                sql_preview=(template_sql or "")[:300],
                            )
                        except ValueError:
                            template_sql = None

                # Drill-down: rewrite prior COUNT → SELECT DISTINCT user_id (same WHERE).
                if not template_sql and prior_sql and not has_clarification:
                    from question_intent import (
                        is_drill_down_data_request,
                        rewrite_aggregate_to_user_list_sql,
                    )

                    if is_drill_down_data_request(original_question) or is_drill_down_data_request(
                        question
                    ):
                        drill_sql = rewrite_aggregate_to_user_list_sql(prior_sql)
                        if drill_sql:
                            try:
                                template_sql = bq.validate_select_only(drill_sql)
                                sql_source = "drill_down"
                                semantic_reason = (
                                    "Drill-down — listing user_id with prior filters"
                                )
                                ask_trace(
                                    "drill_down_sql",
                                    question=original_question[:200],
                                    hit=True,
                                    sql_preview=(template_sql or "")[:300],
                                )
                            except ValueError:
                                template_sql = None

                if not template_sql and config.GLOSSARY_ENABLED and config.HEX_STYLE_PIPELINE:
                    template_sql, semantic_reason, semantic_tables, planner_plan = (
                        _try_rag_compose_sql(
                            question,
                            selected,
                            hints_map,
                            inferred,
                            columns_by_table,
                            catalog_tables=included_tables,
                            schema_entities=query_ctx.schema_entities,
                            query_plan=query_plan,
                        )
                    )
                    if template_sql:
                        sql_source = "rag"
                        query_plan = planner_plan or query_plan
                # NPS templates must run in hex mode too — planner often picks unique_responders.
                if not template_sql:
                    from nps_sql import is_nps_analytics_question

                    if is_nps_analytics_question(question) or re.search(
                        r"\bnps\b", question, re.I
                    ):
                        nps_sql = _try_nps_template_sql(
                            question,
                            selected,
                            hints_map,
                            inferred,
                            columns_by_table,
                            included_tables=included_tables,
                            schema_entities=query_ctx.schema_entities,
                        )
                        if nps_sql:
                            template_sql = nps_sql
                            semantic_reason = "NPS template SQL"
                            sql_source = "nps_template"
                pattern_match = try_learned_pattern(question)
                if not template_sql and pattern_match:
                    template_sql = pattern_match.sql
                    sql_source = "template"
                    semantic_reason = (
                        f"Learned pattern ({pattern_match.source}, score={pattern_match.score})"
                    )
                    if pattern_match.template_id and audit and audit.db:
                        from agents.pattern_miner import get_pattern_miner

                        get_pattern_miner().record_template_use(
                            audit.db, pattern_match.template_id
                        )
                if not template_sql and not (config.GLOSSARY_ENABLED and config.HEX_STYLE_PIPELINE):
                    template_sql, semantic_reason, semantic_tables = _try_join_template_sql(
                        question,
                        selected,
                        hints_map,
                        inferred,
                        columns_by_table,
                        catalog_tables=included_tables,
                        schema_entities=query_ctx.schema_entities,
                    )
                    if template_sql:
                        sql_source = "join_template"
                    if not template_sql:
                        nps_sql = _try_nps_template_sql(
                            question,
                            selected,
                            hints_map,
                            inferred,
                            columns_by_table,
                            included_tables=included_tables,
                            schema_entities=query_ctx.schema_entities,
                        )
                        if nps_sql:
                            template_sql = nps_sql
                            semantic_reason = "NPS template SQL"
                            sql_source = "nps_template"
                    if not template_sql:
                        template_sql, semantic_reason, semantic_tables, planner_plan = (
                            _try_planner_sql(
                                question,
                                selected,
                                hints_map,
                                inferred,
                                columns_by_table,
                                catalog_tables=included_tables,
                                schema_entities=query_ctx.schema_entities,
                                query_plan=query_plan,
                            )
                        )
                        if template_sql:
                            sql_source = "planner"
                    if planner_plan:
                        query_plan = planner_plan
                    if not template_sql:
                        from nps_sql import is_nps_analytics_question

                        if not is_nps_analytics_question(question):
                            template_sql, semantic_reason, semantic_tables = (
                                _try_semantic_template_sql(
                                    question,
                                    selected,
                                    hints_map,
                                    inferred,
                                    columns_by_table,
                                    catalog_tables=included_tables,
                                )
                            )
                            if template_sql:
                                sql_source = "semantic"
                    if not template_sql:
                        domain_resolved = resolve_domain_sql(question, included_tables)
                        if domain_resolved:
                            template_sql, domain_table, domain_reason = domain_resolved
                            semantic_tables = [domain_table]
                            semantic_reason = domain_reason
                            sql_source = "domain"
                            ask_trace(
                                "domain_sql",
                                question=question[:200],
                                hit=True,
                                reason=domain_reason,
                                sql_preview=(template_sql or "")[:300],
                                plan=query_plan.to_trace_dict() if query_plan else {},
                            )
                elif not template_sql and config.GLOSSARY_ENABLED and config.HEX_STYLE_PIPELINE:
                    # Hex path: still try planner/domain after NPS
                    template_sql, semantic_reason, semantic_tables, planner_plan = (
                        _try_planner_sql(
                            question,
                            selected,
                            hints_map,
                            inferred,
                            columns_by_table,
                            catalog_tables=included_tables,
                            schema_entities=query_ctx.schema_entities,
                            query_plan=query_plan,
                        )
                    )
                    if template_sql:
                        sql_source = "planner"
                    if planner_plan:
                        query_plan = planner_plan
                    if not template_sql:
                        domain_resolved = resolve_domain_sql(question, included_tables)
                        if domain_resolved:
                            template_sql, domain_table, domain_reason = domain_resolved
                            semantic_tables = [domain_table]
                            semantic_reason = domain_reason
                            sql_source = "domain"
                ask_trace(
                    "semantic_sql",
                    question=question[:200],
                    hit=bool(template_sql),
                    reason=semantic_reason[:200] if semantic_reason else "",
                    sql_preview=(template_sql or "")[:300],
                )
                if template_sql and semantic_tables:
                    if sql_source not in (
                        "planner",
                        "join_template",
                        "nps_template",
                        "template",
                        "rag",
                    ):
                        sql_source = "semantic"
                    selected = semantic_tables
                    knowledges = [kb.load_table_knowledge(t) for t in selected]
                    hints_map = _column_hints_map(selected)
                    inferred, columns_by_table = _infer_hints_for_tables(selected)
                if not template_sql and _is_feedback_question(question):
                    template_sql, clar_payload = _resolve_feedback_sql(
                        question,
                        selected,
                        hints_map,
                        inferred,
                        columns_by_table,
                        catalog,
                    )
                if not template_sql:
                    from nps_sql import is_nps_analytics_question

                    if not is_nps_analytics_question(question) and _is_feedback_question(question):
                        template_sql = _try_feedback_template_sql(
                            question,
                            selected,
                            hints_map,
                            inferred,
                            columns_by_table,
                        )
                if clar_payload:
                    yield {"type": "awaiting_clarification", **clar_payload}
                    return
                if not template_sql:
                    template_sql = _try_overview_template_sql(
                        question,
                        selected,
                        hints_map,
                        inferred,
                        columns_by_table,
                        schema_entities=query_ctx.schema_entities,
                        prior_sql=prior_sql,
                    )
                if template_sql:
                    from memory_lookup import sql_intent_mismatch_reason, sql_matches_question_intent

                    intent_reason = sql_intent_mismatch_reason(
                        question,
                        template_sql,
                        schema_entities=query_ctx.schema_entities,
                        query_plan=query_plan,
                    )
                    if intent_reason and not sql_matches_question_intent(
                        question,
                        template_sql,
                        schema_entities=query_ctx.schema_entities,
                        query_plan=query_plan,
                    ):
                        log_sql_verification(
                            audit,
                            question=question,
                            sql=template_sql,
                            attempt=1,
                            phase="intent",
                            passed=False,
                            issues=[intent_reason],
                            source=sql_source or "template",
                            plan=query_plan.to_trace_dict() if query_plan else None,
                        )
                        if not has_clarification and config.ASK_CLARIFICATION_ENABLED:
                            from ask_clarify import build_intent_clarification

                            clar = build_intent_clarification(
                                original_question,
                                reason=intent_reason,
                                sql=template_sql,
                                selected_table_shorts=[
                                    _short(t.full_table_id) for t in selected
                                ],
                            )
                            if clar:
                                yield {"type": "awaiting_clarification", **clar}
                                return
                        template_sql = None
                if template_sql and config.SQL_VERIFY_WITH_LLM and sql_source != "planner" and not agents_enabled():
                    vlabel = _validation_label(question)
                    yield {
                        "type": "validating_sql",
                        "message": f"Validating «{vlabel}» SQL query…",
                        "label": vlabel,
                    }
                    review = llm_review_sql(
                        question,
                        template_sql,
                        schema_text,
                        project_context,
                        audit=audit,
                        attempt=1,
                        source="template",
                    )
                    if not review["pass"] and review["issues"]:
                        template_sql = None
                if template_sql:
                    log_sql_verification(
                        audit,
                        question=question,
                        sql=template_sql,
                        attempt=1,
                        phase="approved",
                        passed=True,
                        issues=[],
                        source=sql_source or "template",
                        plan=query_plan.to_trace_dict() if query_plan else None,
                    )
                    yield {
                        "type": "sql_verified",
                        "message": (
                            "Semantic measure SQL — running on BigQuery…"
                            if sql_source == "semantic"
                            else "Query verified — running on BigQuery…"
                        ),
                        "sql": template_sql,
                    }
                    if semantic_reason:
                        routing_meta = {
                            **routing_meta,
                            "routing_reason": semantic_reason,
                        }
                    sql = template_sql
                    chain_steps = None
                else:
                    sql = None
                    llm_schema = (
                        f"{compact_context}\n\n---\n\n{schema_text}"
                        if compact_context
                        else schema_text
                    )
                    for event, candidate in _iter_validated_sql(
                        question,
                        llm_schema,
                        project_context,
                        selected,
                        hints_map,
                        inferred,
                        columns_by_table,
                        sql_entity_hint=query_ctx.sql_entity_hint,
                        schema_entities=query_ctx.schema_entities,
                        audit=audit,
                        table_catalog=catalog,
                        sql_model=model_used,
                    ):
                        if event:
                            yield event
                        if candidate:
                            sql = candidate
                            sql_source = "llm"
                if not sql:
                    raise ValueError("SQL generation failed")

                if agents_enabled():
                    sql, critic_issues = critic_validate_and_fix(
                        sql,
                        question,
                        schema_text,
                        schema_entities=query_ctx.schema_entities,
                        sql_source=sql_source or "",
                    )
                    if critic_issues:
                        ask_trace(
                            "query_critic",
                            question=question[:200],
                            issues=critic_issues[:6],
                            sql_preview=(sql or "")[:300],
                        )

                ask_trace(
                    "sql_ready",
                    question=question[:200],
                    sql_source=sql_source or ("template" if template_sql else "llm"),
                    sql_preview=(sql or "")[:400],
                    model=model_used,
                )
                yield {
                    "type": "sql_ready",
                    "sql": sql,
                    "source": sql_source or "llm",
                    "model": model_used,
                }

                if config.REQUIRE_SQL_APPROVAL:
                    yield {
                        "type": "awaiting_approval",
                        "question": question,
                        "sql": sql,
                        "message": "SQL passed validation. Confirm to run on BigQuery.",
                    }
                    return
                chain_steps = None
            except ValueError as e:
                debug_log(
                    "ask_pipeline.py:iter_ask",
                    "sql_recovery",
                    {"question": question[:200], "error": str(e)[:500]},
                    hypothesis_id="H2",
                )
                from domain_sql import resolve_domain_sql

                domain_retry = resolve_domain_sql(question, included_tables)
                if domain_retry:
                    sql, domain_table, domain_reason = domain_retry
                    selected = [domain_table]
                    hints_map = _column_hints_map(selected)
                    inferred, columns_by_table = _infer_hints_for_tables(selected)
                    sql_source = "domain"
                    chain_steps = None
                    routing_meta = {
                        **routing_meta,
                        "routing_reason": domain_reason,
                        "sql_source": sql_source,
                    }
                else:
                    recovered_sql = None
                    if not has_clarification and config.ASK_CLARIFICATION_ENABLED:
                        if config.GLOSSARY_ENABLED and config.HEX_STYLE_PIPELINE:
                            from rag_pipeline import try_rag_compose_sql

                            recovered_sql, _, _, _, _ = try_rag_compose_sql(
                                original_question,
                                selected,
                                hints_map,
                                inferred,
                                columns_by_table,
                                catalog_tables=included_tables,
                                schema_entities=query_ctx.schema_entities,
                                query_plan=query_plan,
                            )
                        if not recovered_sql and not resolve_domain_sql(
                            question, included_tables
                        ):
                            all_knowledges = [
                                kb.load_table_knowledge(t) for t in included_tables
                            ]
                            clar = build_clarification(
                                original_question,
                                matches=plan.tables,
                                selected_ids=[t.full_table_id for t in selected],
                                knowledges=all_knowledges,
                                join_relations=plan.join_relations,
                                schema_excerpt=schema_text,
                                error_detail=str(e),
                                force=True,
                                matched_entity_label=query_ctx.matched_entity_label,
                                wants_breakdown=query_ctx.wants_breakdown,
                            )
                            if clar:
                                yield {"type": "awaiting_clarification", **clar}
                                return

                    if recovered_sql:
                        sql = recovered_sql
                        chain_steps = None
                        yield {
                            "type": "sql_verified",
                            "message": "Running NPS query…",
                            "sql": sql,
                        }
                    else:
                        yield {"type": "status", "message": "Trying a simpler query…"}
                        recovery = _sql_failure_recovery(
                            question,
                            str(e),
                            project_context=project_context,
                            schema_text=schema_text,
                            cache_entries=cache_entries,
                            selected_tables=selected,
                            hints_map=hints_map,
                            inferred=inferred,
                            columns_by_table=columns_by_table,
                            catalog=catalog,
                            included_tables=included_tables,
                        )
                        yield {
                            "type": "complete",
                            **_complete_payload(recovery, schema_text=schema_text),
                        }
                        return

    display_question = original_question

    yield {"type": "running_query", "message": "Running query on BigQuery…"}
    try:
        from nps_sql import is_nps_analytics_question

        if _is_feedback_question(question) and not is_nps_analytics_question(question):
            complete = _run_feedback_with_discovery(
                question,
                sql,
                catalog=catalog,
                schema_text=schema_text,
                included_tables=included_tables,
                selected_tables=selected,
                hints_map=hints_map,
                inferred=inferred,
                columns_by_table=columns_by_table,
            )
            complete.update(_routing_meta(plan=plan, selected=selected, probe_stats=probe_stats, sql_source=sql_source))
            yield {"type": "analyzing", "message": "Building chart and analysis…"}
            yield {"type": "complete", **_complete_payload(complete, schema_text=schema_text)}
            return

        widened = False
        if config.HEX_STYLE_PIPELINE:
            _df, rows, columns, bytes_estimate, probe_stats, sql, widened = _run_query_with_preflight(
                question,
                sql,
                catalog=catalog,
                selected=selected,
            )
            ask_trace(
                "query_run",
                question=question[:200],
                probe_stats=probe_stats,
                widened=widened,
                row_count=len(rows),
                bytes_estimate=bytes_estimate,
                sql_preview=(sql or "")[:400],
            )
        else:
            bytes_estimate = bq.dry_run_bytes(sql, table_catalog=catalog)
            _df = bq.run_query(sql, table_catalog=catalog)
            rows = json.loads(_df.to_json(orient="records", date_format="iso"))
            columns = list(_df.columns)
    except Exception as e:
        debug_log(
            "ask_pipeline.py:iter_ask",
            "bq_run_failed",
            {"question": question[:200], "sql": (sql or "")[:400], "error": str(e)[:500]},
            hypothesis_id="H4",
        )
        yield {"type": "status", "message": "Trying a simpler query…"}
        recovery = _sql_failure_recovery(
            question,
            bq.format_query_error(e),
            project_context=project_context,
            schema_text=schema_text,
            cache_entries=cache_entries,
            selected_tables=selected,
            hints_map=hints_map,
            inferred=inferred,
            columns_by_table=columns_by_table,
            catalog=catalog,
            included_tables=included_tables,
        )
        recovery["sql"] = sql or recovery.get("sql") or ""
        yield {"type": "complete", **_complete_payload(recovery, schema_text=schema_text)}
        return

    sample = rows[:50]
    debug_log(
        "ask_pipeline.py:iter_ask",
        "bq_run_ok",
        {
            "question": question[:200],
            "row_count": len(rows),
            "columns": columns,
            "bytes_estimate": bytes_estimate,
            "sql": (sql or "")[:400],
        },
        hypothesis_id="H3",
    )

    from memory_lookup import sql_matches_question_intent
    from schema_entities import validate_result_for_question

    result_ok = True
    mismatch = ""
    retry_fixed = False
    if sql_source == "domain":
        result_ok = True
    elif query_ctx.schema_entities:
        result_ok, mismatch = validate_result_for_question(
            question,
            sql or "",
            columns,
            rows,
            query_ctx.schema_entities,
        )
    elif not sql_matches_question_intent(question, sql or "", schema_entities=None):
        result_ok = False
        mismatch = "SQL does not match question intent"

    if not result_ok:
        yield {
            "type": "status",
            "message": "Refining query to match your question…",
        }
        retry_sql = None
        retry_prior = (
            f"Previous SQL result does NOT answer the question: {mismatch}. "
            f"{query_ctx.sql_entity_hint}\nRegenerate the complete SELECT query."
        )
        for event, candidate in _iter_validated_sql(
            question,
            schema_text,
            project_context,
            selected,
            hints_map,
            inferred,
            columns_by_table,
            sql_entity_hint=query_ctx.sql_entity_hint,
            schema_entities=query_ctx.schema_entities,
            prior_error=retry_prior,
            audit=audit,
            source="post_run_retry",
            table_catalog=catalog,
        ):
            if event:
                yield event
            if candidate:
                retry_sql = candidate
        if retry_sql and retry_sql.strip() != (sql or "").strip():
            try:
                yield {"type": "running_query", "message": "Running refined query on BigQuery…"}
                sql = retry_sql
                bytes_estimate = bq.dry_run_bytes(sql, table_catalog=catalog)
                df = bq.run_query(sql, table_catalog=catalog)
                rows = json.loads(df.to_json(orient="records", date_format="iso"))
                columns = list(df.columns)
                sample = rows[:50]
                retry_fixed = True
                if query_ctx.schema_entities:
                    result_ok, mismatch = validate_result_for_question(
                        question,
                        sql or "",
                        columns,
                        rows,
                        query_ctx.schema_entities,
                    )
                else:
                    result_ok = sql_matches_question_intent(
                        question, sql or "", schema_entities=None
                    )
            except Exception:
                pass

        if not result_ok and not has_clarification and config.ASK_CLARIFICATION_ENABLED:
            from ask_clarify import build_intent_clarification

            clar = build_intent_clarification(
                original_question,
                reason=mismatch or "result_does_not_answer_question",
                sql=sql or "",
                selected_table_shorts=[_short(t.full_table_id) for t in selected],
                force=True,
            )
            if clar:
                yield {"type": "awaiting_clarification", **clar}
                return

        if not result_ok and not retry_fixed:
            analysis = (
                "I couldn't confidently answer your question with the available data. "
                "The query I tried would not give you the breakdown or metric you asked for. "
                "Please pick one of the clarifications below, or rephrase your question "
                "with more detail (e.g. which dimension, date range, or activity type)."
            )
            yield {
                "type": "complete",
                **_complete_payload(
                    {
                        "question": display_question,
                        "sql": sql or "",
                        "columns": columns,
                        "rows": [],
                        "viz_rows": [],
                        "chart_spec": {"chart": "none"},
                        "analysis": analysis,
                        "bytes_estimate": bytes_estimate,
                        "from_cache": False,
                        "response_mode": "clarify_needed",
                        "needs_clarification": True,
                    },
                    schema_text=schema_text,
                ),
            }
            return

    yield {"type": "analyzing", "message": "Preparing your results…"}
    from metrics_registry import glossary_context_for_question

    glossary_ctx, _ = glossary_context_for_question(question)
    presentation_hints = list(query_ctx.presentation_hints)
    if query_ctx.wants_breakdown or len(rows) > 1:
        if not any("every part" in h.lower() for h in presentation_hints):
            presentation_hints.append(
                "Cover every part of the question: what happened, why it matters, "
                "how to interpret the numbers, and any caveats."
            )
    query_reason = (query_plan.reason if query_plan else "") or ""
    if query_plan and query_plan.viz_hint:
        presentation_hints.append(f"viz_hint:{query_plan.viz_hint}")
    table_kb_context = kb.build_answer_kb_context(knowledges)
    try:
        viz_rows, chart_spec, analysis = llm.build_presentation(
            question,
            columns,
            rows,
            sample=sample,
            sql=sql or "",
            entity_label=query_ctx.entity_label,
            presentation_hints=presentation_hints,
            conversation_context=conversation_context,
            glossary_context=glossary_ctx,
            query_reason=query_reason,
            table_kb_context=table_kb_context,
        )
    except Exception as pres_err:
        from presentation import heuristic_analyze, infer_chart_spec
        from chart_prepare import prepare_chart

        ask_trace("presentation_fallback", error=str(pres_err)[:200])
        fallback_spec = infer_chart_spec(question, columns, rows)
        viz_rows, chart_spec = prepare_chart(rows, columns, fallback_spec, question)
        analysis = heuristic_analyze(question, columns, rows, len(rows))

    # Empty / zero results: tell the user what date ranges actually have data.
    if _looks_empty_result(rows):
        from table_profile import coverage_note_for_tables

        queried = [t for t in included_tables if t.full_table_id.rsplit(".", 1)[-1] in (sql or "")]
        note = coverage_note_for_tables(queried or included_tables)
        if note:
            analysis = f"{analysis}\n\n{note}"
        if widened:
            analysis = (
                f"{analysis}\n\nNote: The original date filter returned no rows; "
                "results below use a widened date range."
            )
        elif probe_stats:
            analysis = f"{analysis}\n\nTable coverage: {probe_stats}."

    final_meta = _routing_meta(
        plan=plan,
        selected=selected,
        probe_stats=probe_stats,
        sql_source=sql_source,
        model_used=model_used if agents_enabled() else "",
    )
    log_sql_verification(
        audit,
        question=question,
        sql=sql or "",
        attempt=1,
        phase="execute",
        passed=True,
        issues=[],
        source=sql_source or "llm",
        result_row_count=len(rows),
        model_used=model_used if agents_enabled() else config.FETCH_MODEL,
    )
    ask_trace(
        "ask_complete",
        question=display_question[:200],
        **final_meta,
        row_count=len(rows),
        columns=columns[:8],
        worked_seconds=_elapsed(),
    )

    yield {"type": "insight", "data": analysis}
    yield {
        "type": "complete",
        **_complete_payload(
            {
                "question": display_question,
                "sql": sql,
                "columns": columns,
                "rows": rows,
                "viz_rows": viz_rows,
                "chart_spec": chart_spec,
                "analysis": analysis,
                "bytes_estimate": bytes_estimate,
                "from_cache": False,
                "response_mode": "data",
                **final_meta,
            },
            schema_text=schema_text,
        ),
    }


def _looks_empty_result(rows: list[dict]) -> bool:
    """True for no rows, or a single all-zero aggregate row (e.g. COUNT(*) = 0)."""
    if not rows:
        return True
    if len(rows) == 1:
        vals = list(rows[0].values())
        try:
            return all(v is None or float(v) == 0 for v in vals)
        except (TypeError, ValueError):
            return False
    return False


def run_ask_stream(
    question: str,
    project_context: str,
    included_tables: list,
    join_hints: str = "",
) -> Iterator[str]:
    """Yield Server-Sent Events for the ask pipeline."""
    try:
        for event in iter_ask(
            question,
            project_context,
            included_tables=included_tables,
            join_hints=join_hints,
        ):
            yield f"data: {json.dumps(event, default=str)}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
