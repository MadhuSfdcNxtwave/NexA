"""Bridge between agent modules and ask_pipeline — keeps pipeline edits small."""
from __future__ import annotations

import re
from typing import Any

import config
from agents.pattern_miner import PatternMatch, get_pattern_miner
from agents.query_critic import get_query_critic
from agents.schema_explorer import get_schema_explorer
from business_rules_loader import prompt_block_for_question
from config_models import model_for_role


_COMPLEX_SIGNALS = (
    re.compile(
        r"\b(and|who also|joined and|by gender|by state|cohort|retention|"
        r"compare|vs|versus|trend|percentage of|rate of)\b",
        re.I,
    ),
    re.compile(
        r"\b(among those who|of the ones|from users who|excluding|within)\b",
        re.I,
    ),
    re.compile(
        r"\b(last 30 days|last 90 days|month over month|week over week)\b",
        re.I,
    ),
    re.compile(r"\b(nps and|attended and|placed and|portal and)\b", re.I),
)


def agents_enabled() -> bool:
    return getattr(config, "AGENTS_PIPELINE_ENABLED", True)


def classify_complexity(question: str) -> str:
    """Pure-heuristic complexity routing — no LLM."""
    hits = sum(1 for pat in _COMPLEX_SIGNALS if pat.search(question or ""))
    return "complex" if hits >= 2 else "simple"


def sql_model_for_question(question: str) -> str:
    role = classify_complexity(question)
    return model_for_role(role)


def enrich_schema_context(table_ids: list[str], base_schema: str) -> str:
    """Append SchemaExplorer rich context (sample values, date ranges)."""
    if not agents_enabled() or not table_ids:
        return base_schema
    try:
        explorer = get_schema_explorer()
        rich = explorer.build_context(table_ids)
        if rich and rich not in base_schema:
            return f"{base_schema}\n\n# Rich table context (sample values + date ranges)\n{rich}"
    except Exception:
        pass
    return base_schema


def business_rules_block(question: str, tables: list | None = None) -> str:
    """Selected-table business_rules (mandatory) + YAML metric/date hints."""
    from table_business_rules import build_mandatory_rules_preamble

    parts: list[str] = []
    if tables:
        preamble = build_mandatory_rules_preamble(tables)
        if preamble:
            parts.append(preamble)
    if agents_enabled():
        yaml_block = prompt_block_for_question(question, tables=tables)
        if yaml_block:
            parts.append(yaml_block)
    return "\n\n".join(parts)


def try_learned_pattern(question: str) -> PatternMatch | None:
    if not agents_enabled():
        return None
    try:
        return get_pattern_miner().find_matching_pattern(question)
    except Exception:
        return None


def critic_validate_and_fix(
    sql: str,
    question: str,
    schema_context: str,
    *,
    schema_entities: list | None = None,
    skip_for_sources: set[str] | None = None,
    sql_source: str = "",
) -> tuple[str, list[str]]:
    """Run QueryCritic when enabled; skip deterministic template sources."""
    if not agents_enabled():
        return sql, []
    skip = skip_for_sources or {
        "template",
        "domain",
        "join_template",
        "user",
        "feedback_raw",
        "nps_template",
    }
    if sql_source in skip:
        return sql, []
    return get_query_critic().validate_and_correct(
        sql,
        question,
        schema_context,
        schema_entities=schema_entities,
    )
