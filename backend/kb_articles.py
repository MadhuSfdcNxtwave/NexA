"""Per-table KB articles and AI-driven table + column routing on every Ask."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import knowledge_base as kb
from semantic_layer import semantic_for_table, TableSemantic


@dataclass
class KbRouteResult:
    """LLM routing decision from KB articles."""

    tables: list[str]
    columns: dict[str, list[str]] = field(default_factory=dict)
    filters: list[str] = field(default_factory=list)
    measure: str = ""
    reason: str = ""


def _infer_grain(short_name: str, semantic: TableSemantic | None, desc: str) -> str:
    name = short_name.lower()
    blob = f"{name} {desc}".lower()
    if "question_wise" in name or "question_set" in name:
        return "one row per user per question attempt"
    if "day_and_page" in name or "page_wise" in name:
        return "one row per user per day per page"
    if "daily_engagement" in name or "time_spent" in name:
        return "one row per user per calendar day"
    if "attendance" in name and "live_class" in name:
        return "one row per user per live class slot"
    if "master_data" in name or name.endswith("_master_data"):
        return "one row per user (master profile)"
    if "nps" in name or "feedback" in name:
        return "one row per feedback / survey response"
    if "placement" in name or "jobs" in name:
        return "one row per placement or job application event"
    if semantic and semantic.dimensions:
        for d in semantic.dimensions:
            if d.unique and "user" in d.id.lower():
                return "one row per user"
    if " per " in blob:
        m = re.search(r"one row per [^.]+", blob, re.I)
        if m:
            return m.group(0).strip()[:80]
    return "see table description for grain"


def _infer_domain(short_name: str) -> str:
    name = short_name.lower()
    if "attendance" in name and "live_class" in name:
        return "live class attendance"
    if "master_data" in name:
        return "user master / portal access / demographics"
    if "daily_engagement" in name or "time_spent" in name:
        return "platform engagement / time spent"
    if "nps" in name:
        return "NPS surveys"
    if "contextual_feedback" in name or "feedback" in name:
        return "feedback / surveys"
    if "placement" in name:
        return "placements / offers"
    if "jobs" in name:
        return "job applications"
    if "cloudwatch" in name or "nav_bar" in name:
        return "navigation / UI telemetry"
    if "question_wise" in name:
        return "question-level learning attempts"
    if "coach" in name or "call" in name:
        return "success coach activity"
    if "course" in name and "completion" in name:
        return "course completion progress"
    return "academy analytics"


def _key_columns(knowledge: kb.TableKnowledge, semantic: TableSemantic | None) -> list[str]:
    cols: list[str] = []
    desc_map = knowledge.column_descriptions

    def add(name: str) -> None:
        if name in desc_map and name not in cols:
            cols.append(name)

    for hint in ("user_id", "slot_date", "calendar_date", "attendance_status", "pause_status",
                 "learning_portal_onboarding_access_given_datetime", "form_submission_datetime"):
        add(hint)

    if semantic:
        for d in semantic.dimensions:
            if d.id in desc_map and d.id not in cols:
                if any(k in d.id.lower() for k in ("user", "date", "status", "month")):
                    cols.append(d.id)
            if len(cols) >= 8:
                break
        for m in semantic.measures[:4]:
            if m.of_column:
                add(m.of_column)

    return cols[:8]


def _routing_hints(short_name: str) -> tuple[list[str], list[str]]:
    """Use / avoid hints from domain routing rules and table name patterns."""
    from table_routing import ROUTING_RULES

    use: list[str] = []
    avoid: list[str] = []
    name = short_name.lower()

    for rule in ROUTING_RULES:
        if rule.table_short.lower() == name:
            use.append(rule.reason)
            if rule.filters:
                parts = [f.to_sql() for f in rule.filters if f.to_sql()]
                if parts:
                    use.append("Typical filters: " + " AND ".join(parts))
        for bad in rule.score_penalty_shorts:
            if bad in name:
                avoid.append(
                    f"Not the canonical table for {rule.id.replace('_', ' ')} questions "
                    f"(prefer `{rule.table_short}`)"
                )

    if "question_wise" in name or "question_set" in name:
        avoid.append("Question-level attempt/responses — not user master or portal access counts")
    if "cloudwatch" in name or "nav_bar" in name:
        avoid.append("Navigation/event telemetry — not attendance or portal active user counts")
    if "virtual_meet" in name and "attendance" not in name:
        avoid.append("Virtual meeting metadata — not live class attendance counts")

    return use, avoid


def _semantic_section(semantic: TableSemantic | None) -> str:
    if not semantic:
        return ""
    lines: list[str] = []
    if semantic.description and semantic.description not in ("", semantic.model_id):
        pass  # often duplicated in table_description
    if semantic.measures:
        lines.append("## Semantic measures (prefer these)")
        for m in semantic.measures[:12]:
            parts = [f"- {m.id}: {m.func}"]
            if m.of_column:
                parts.append(f"of `{m.of_column}`")
            if m.description:
                parts.append(f"— {m.description[:160]}")
            lines.append(" ".join(parts))
    key_dims = [d for d in semantic.dimensions if d.description][:16]
    if key_dims:
        lines.append("## Key dimensions")
        for d in key_dims:
            desc = (d.description or d.id)[:120]
            lines.append(f"- {d.id}: {desc}")
    return "\n".join(lines)


def build_table_card(
    knowledge: kb.TableKnowledge,
    *,
    table_obj: Any | None = None,
) -> str:
    """Compact table card (~200-400 tokens) for embedding index and LLM disambiguation."""
    sem = semantic_for_table(table_obj) if table_obj is not None else None
    if sem is None:
        sem = semantic_for_table(
            type("_T", (), {"full_table_id": knowledge.full_table_id})()
        )

    use_hints, avoid_hints = _routing_hints(knowledge.short_name)
    grain = _infer_grain(knowledge.short_name, sem, knowledge.table_description or "")
    domain = _infer_domain(knowledge.short_name)
    desc = (knowledge.table_description or knowledge.ai_overview or "").strip()

    lines = [
        f"Table: {knowledge.short_name}",
        f"full_table_id: {knowledge.full_table_id}",
        f"Domain: {domain}",
        f"Grain: {grain}",
    ]
    if knowledge.endorsed:
        lines.append("Endorsed: yes")

    if desc:
        lines.append(f"Summary: {desc[:400]}")

    if use_hints:
        lines.append("Use when: " + "; ".join(use_hints[:3]))
    if avoid_hints:
        lines.append("Do NOT use for: " + "; ".join(avoid_hints[:2]))

    key_cols = _key_columns(knowledge, sem)
    if key_cols:
        lines.append("Key columns: " + ", ".join(f"`{c}`" for c in key_cols))

    if sem and sem.measures:
        meas_parts = []
        for m in sem.measures[:6]:
            part = f"{m.id} ({m.func}"
            if m.of_column:
                part += f" `{m.of_column}`"
            part += ")"
            meas_parts.append(part)
        lines.append("Measures: " + "; ".join(meas_parts))

    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()[:3500]


def build_table_kb_article(
    knowledge: kb.TableKnowledge,
    *,
    table_obj: Any | None = None,
    max_columns: int = 24,
) -> str:
    """Structured KB article: what the table contains and when to use it."""
    sem = semantic_for_table(table_obj) if table_obj is not None else None
    if sem is None:
        sem = semantic_for_table(
            type("_T", (), {"full_table_id": knowledge.full_table_id})()
        )

    use_hints, avoid_hints = _routing_hints(knowledge.short_name)
    lines = [
        f"# {knowledge.short_name}",
        f"full_table_id: {knowledge.full_table_id}",
    ]
    if knowledge.endorsed:
        lines.append("endorsed: yes (curated analytics table)")

    desc = (knowledge.table_description or "").strip()
    if desc:
        lines.append("")
        lines.append("## What this table contains")
        lines.append(desc[:2000])

    overview = (knowledge.ai_overview or "").strip()
    if overview and overview not in desc:
        lines.append("")
        lines.append("## AI data profile")
        lines.append(overview[:1800])

    if use_hints:
        lines.append("")
        lines.append("## Use this table when")
        for h in use_hints[:6]:
            lines.append(f"- {h}")

    if avoid_hints:
        lines.append("")
        lines.append("## Do NOT use for")
        for h in avoid_hints[:6]:
            lines.append(f"- {h}")

    if knowledge.column_descriptions:
        lines.append("")
        lines.append("## Columns")
        shown = 0
        for name in sorted(knowledge.column_descriptions):
            if shown >= max_columns:
                lines.append(f"- … ({len(knowledge.column_descriptions) - shown} more columns)")
                break
            desc_c = (knowledge.column_descriptions.get(name) or "").strip()
            col_type = (knowledge.column_types.get(name) or "").strip()
            type_part = f" ({col_type})" if col_type else ""
            if desc_c:
                lines.append(f"- `{name}`{type_part}: {desc_c[:140]}")
            else:
                lines.append(f"- `{name}`{type_part}")
            shown += 1

    sem_block = _semantic_section(sem)
    if sem_block:
        lines.append("")
        lines.append(sem_block)

    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def build_card_catalog(
    candidates: list[Any],
    knowledges: list[kb.TableKnowledge],
    project_tables: list[Any],
) -> list[dict[str, str]]:
    """Attach compact table cards for LLM disambiguation."""
    by_id = {k.full_table_id: k for k in knowledges}
    table_by_id = {t.full_table_id: t for t in project_tables}
    catalog: list[dict[str, str]] = []
    for c in candidates:
        fq = c.full_table_id if hasattr(c, "full_table_id") else str(c)
        knowledge = by_id.get(fq)
        if not knowledge:
            continue
        table_obj = table_by_id.get(fq)
        catalog.append(
            {
                "full_table_id": fq,
                "short_name": knowledge.short_name,
                "card": build_table_card(knowledge, table_obj=table_obj),
                "fused_score": getattr(c, "fused_score", getattr(c, "score", 0)),
            }
        )
    return catalog


def plan_columns_for_table(
    question: str,
    table: Any,
    knowledge: kb.TableKnowledge,
) -> tuple[dict[str, list[str]], list[str], str]:
    """Column + filter + measure plan for a single locked table."""
    from measure_router import try_build_measure_plan
    from table_routing import sql_filters_for_table

    fq = table.full_table_id
    kb_columns: dict[str, list[str]] = {}
    kb_filters: list[str] = []
    kb_measure = ""

    measure_plan = try_build_measure_plan(question, [table], catalog_tables=[table])
    if measure_plan:
        kb_measure = measure_plan.measure.id
        cols: list[str] = []
        if measure_plan.measure.of_column:
            cols.append(measure_plan.measure.of_column)
        for dim in measure_plan.group_by:
            if dim not in cols:
                cols.append(dim)
        if cols:
            kb_columns[fq] = cols
        kb_filters.extend(measure_plan.filters)

    domain_filters = sql_filters_for_table(question, table)
    for f in domain_filters:
        if f not in kb_filters:
            kb_filters.append(f)

    if fq not in kb_columns:
        sem = semantic_for_table(table)
        cols = _key_columns(knowledge, sem)
        if cols:
            kb_columns[fq] = cols

    return kb_columns, kb_filters, kb_measure


def build_kb_catalog(
    question: str,
    matches: list[Any],
    knowledges: list[kb.TableKnowledge],
    project_tables: list[Any],
    *,
    top_n: int = 12,
) -> list[dict[str, str]]:
    """Pre-rank tables and attach full KB articles for the LLM router."""
    by_id = {k.full_table_id: k for k in knowledges}
    table_by_id = {t.full_table_id: t for t in project_tables}
    ranked_ids = [m.full_table_id for m in matches[:top_n]]

    catalog: list[dict[str, str]] = []
    for fq in ranked_ids:
        knowledge = by_id.get(fq)
        if not knowledge:
            continue
        table_obj = table_by_id.get(fq)
        catalog.append(
            {
                "full_table_id": fq,
                "short_name": knowledge.short_name,
                "article": build_table_kb_article(knowledge, table_obj=table_obj),
                "score": next((m.score for m in matches if m.full_table_id == fq), 0),
            }
        )
    return catalog


def _valid_columns(result: KbRouteResult, knowledges: list[kb.TableKnowledge]) -> KbRouteResult:
    by_id = {k.full_table_id: k for k in knowledges}
    cleaned: dict[str, list[str]] = {}
    for fq, cols in result.columns.items():
        knowledge = by_id.get(fq)
        if not knowledge:
            continue
        valid_names = {c.lower(): c for c in knowledge.column_descriptions}
        picked: list[str] = []
        for c in cols:
            key = (c or "").strip().lower()
            if key in valid_names:
                picked.append(valid_names[key])
        if picked:
            cleaned[fq] = picked
    result.columns = cleaned
    return result


def route_question(
    question: str,
    matches: list[Any],
    knowledges: list[kb.TableKnowledge],
    project_tables: list[Any],
    *,
    restrict_to: list[str] | None = None,
) -> KbRouteResult | None:
    """AI route: pick table(s), columns, filters, and optional measure from KB articles."""
    import config
    import llm

    if not config.KB_AI_ROUTING:
        return None

    top_n = max(3, config.KB_AI_TOP_CANDIDATES)
    catalog = build_kb_catalog(question, matches, knowledges, project_tables, top_n=top_n)
    if restrict_to:
        allow = set(restrict_to)
        catalog = [c for c in catalog if c["full_table_id"] in allow]
    if not catalog:
        return None

    try:
        raw = llm.route_with_kb(question, catalog)
    except Exception as exc:
        print(f"[kb-articles] route failed: {exc}")
        return None

    valid_ids = {t.full_table_id for t in project_tables}
    tables = [fq for fq in raw.get("tables") or [] if fq in valid_ids][:3]
    if not tables:
        return None

    result = KbRouteResult(
        tables=tables,
        columns=dict(raw.get("columns") or {}),
        filters=[str(f).strip() for f in (raw.get("filters") or []) if str(f).strip()],
        measure=str(raw.get("measure") or "").strip(),
        reason=str(raw.get("reason") or "").strip(),
    )
    result = _valid_columns(result, knowledges)

    try:
        from debug_session import ask_trace

        ask_trace(
            "kb_route",
            question=question[:200],
            tables=[t.rsplit(".", 1)[-1] for t in tables],
            columns={k.rsplit(".", 1)[-1]: v for k, v in result.columns.items()},
            filters=result.filters[:4],
            measure=result.measure,
        )
    except Exception:
        pass

    return result


def apply_kb_columns_to_matches(
    column_matches: dict[str, list[kb.ColumnMatch]],
    kb_columns: dict[str, list[str]],
) -> str:
    """Mark KB-picked columns as selected; return reasoning snippet."""
    if not kb_columns:
        return ""
    lines: list[str] = []
    for fq, cols in kb_columns.items():
        if fq not in column_matches or not cols:
            continue
        want = {c.lower() for c in cols}
        for m in column_matches[fq]:
            if m.name.lower() in want:
                m.selected = True
        short = fq.rsplit(".", 1)[-1]
        names = ", ".join(f"`{c}`" for c in cols[:8])
        lines.append(f"KB router columns for `{short}`: {names}")
    return " ".join(lines)
