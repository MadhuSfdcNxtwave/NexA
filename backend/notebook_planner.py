"""Hex-style notebook step planning for complex questions and table joins."""
from __future__ import annotations

import re
from typing import Any

from sql_chain import _plan_and_split, _rule_plan_steps, chain_candidate, plan_steps

_BREAKDOWN = re.compile(r"\bby\s+([a-z][a-z0-9_\s]{1,40})", re.I)
_JOIN_HINT = re.compile(
    r"\b(join|with\b.+\bby\b|across|combined|intersection|both\b.+\band\b)\b",
    re.I,
)


def _short(fq: str) -> str:
    return fq.rsplit(".", 1)[-1]


def _breakdown_dim(question: str) -> str | None:
    m = _BREAKDOWN.search(question or "")
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1).strip().lower())


def _plan_compound_join_steps(question: str) -> list[dict[str, str]]:
    """Split compound JOIN questions into explore → explore → final join cells."""
    from table_routing import compound_domain_table_ids, is_compound_domain_question

    if not is_compound_domain_question(question):
        return []

    q = question.strip()
    from question_intent import expand_question_abbreviations

    q_lower = expand_question_abbreviations(q).lower()

    if re.search(r"\battend", q_lower) and re.search(r"\bportal\b", q_lower) and re.search(
        r"\bpercent|percentage|%\b|activity|activit",
        q_lower,
    ):
        return [
            {
                "label": "1. Portal page activity",
                "question": "Which activity are students actively using on learning portal",
                "kind": "explore",
            },
            {
                "label": "2. Live class attendance rate",
                "question": "Live class attendance percentage for active portal users",
                "kind": "explore",
            },
            {
                "label": "3. Activity + attendance % (JOIN)",
                "question": q,
                "kind": "final",
            },
        ]

    if re.search(r"\battend", q_lower) and re.search(r"\bportal\b|\baccess\b", q_lower):
        return [
            {
                "label": "1. Attendance cohort",
                "question": "How many users attended live classes yesterday",
                "kind": "explore",
            },
            {
                "label": "2. Portal access cohort",
                "question": "How many users have learning portal access",
                "kind": "explore",
            },
            {
                "label": "3. Combined (JOIN)",
                "question": q,
                "kind": "final",
            },
        ]

    if re.search(r"\bplaced\b|\bplacement\b", q_lower) and re.search(r"\bprofile\b|\bstate\b|\bgender\b", q_lower):
        dim = _breakdown_dim(q) or "state"
        return [
            {
                "label": "1. Placements base",
                "question": "How many placed users",
                "kind": "explore",
            },
            {
                "label": "2. Profile dimension",
                "question": f"User count by {dim} from profile",
                "kind": "explore",
            },
            {
                "label": f"3. Placements by {dim} (JOIN)",
                "question": q,
                "kind": "final",
            },
        ]

    return [
        {
            "label": "1. First metric",
            "question": q,
            "kind": "explore",
        },
        {
            "label": "2. Combined result (JOIN)",
            "question": q,
            "kind": "final",
        },
    ]


def _plan_join_breakdown_steps(
    question: str,
    selected_tables: list[Any],
    join_relations: list[Any] | None,
) -> list[dict[str, str]]:
    """NPS/jobs/placed by dimension → explore base → dimension → final JOIN."""
    dim = _breakdown_dim(question)
    if not dim:
        return []

    has_join = bool(join_relations) or len(selected_tables) >= 2
    if not has_join and not _JOIN_HINT.search(question):
        return []

    base_short = _short(selected_tables[0].full_table_id) if selected_tables else "base"
    target_short = ""
    if join_relations:
        target_short = join_relations[0].target
    elif len(selected_tables) >= 2:
        target_short = _short(selected_tables[1].full_table_id)

    dim_label = dim.replace("_", " ").title()
    steps: list[dict[str, str]] = [
        {
            "label": f"1. Base: {base_short}",
            "question": _base_explore_question(question, selected_tables),
            "kind": "explore",
        },
    ]
    if target_short:
        steps.append(
            {
                "label": f"2. Dimension: {target_short}",
                "question": f"User count by {dim} from {target_short}",
                "kind": "explore",
            }
        )
    steps.append(
        {
            "label": f"3. Result by {dim_label} (JOIN)",
            "question": question.strip(),
            "kind": "final",
        }
    )
    return steps


def _base_explore_question(question: str, selected_tables: list[Any]) -> str:
    q = (question or "").strip()
    q_lower = q.lower()
    base = _short(selected_tables[0].full_table_id) if selected_tables else "table"
    if re.search(r"\bnps\b", q_lower):
        return "How many NPS responses and average NPS rating"
    if re.search(r"\bplaced\b|\bplacement\b", q_lower):
        return "How many placed users"
    if re.search(r"\bjob\b|\bappli", q_lower):
        return "How many job applications"
    if re.search(r"\battend", q_lower):
        return "How many users attended live classes yesterday"
    return f"Row count and key metrics from {base}"


def needs_notebook_steps(
    question: str,
    *,
    query_plan: Any | None = None,
    selected_tables: list[Any] | None = None,
    join_relations: list[Any] | None = None,
) -> bool:
    """True when Hex-style multi-cell generation should run."""
    if chain_candidate(question) or re.search(r"\band\b", question, re.I):
        return True
    if query_plan and getattr(query_plan, "intent", None) in ("compound", "breakdown"):
        if len(selected_tables or []) >= 2 or join_relations:
            return True
    if _breakdown_dim(question) and (len(selected_tables or []) >= 2 or join_relations):
        return True
    from table_routing import is_compound_domain_question

    if is_compound_domain_question(question):
        return True
    return False


def plan_notebook_steps(
    question: str,
    schema_text: str,
    *,
    selected_tables: list[Any] | None = None,
    query_plan: Any | None = None,
    join_relations: list[Any] | None = None,
    max_steps: int = 5,
) -> list[dict[str, str]]:
    """
    Hex-style notebook cells: independent metrics, join breakdowns, compound joins.
    Returns [{label, question, kind?}, ...] — empty means single-query mode.
    """
    q = (question or "").strip()
    if not q:
        return []

    tables = list(selected_tables or [])

    # 1. Standard chain (compare, month split, A and B independent metrics)
    chain = plan_steps(q, schema_text, max_steps=max_steps)
    if chain:
        return [{**s, "kind": s.get("kind") or "metric"} for s in chain]

    # 2. Compound JOIN (attendance + portal, etc.)
    from table_routing import is_compound_domain_question

    if is_compound_domain_question(q):
        compound = _plan_compound_join_steps(q)
        if compound:
            return compound[:max_steps]

    # 3. Breakdown across joined tables (NPS by gender, placed by state)
    if _breakdown_dim(q) and (len(tables) >= 2 or join_relations):
        join_steps = _plan_join_breakdown_steps(q, tables, join_relations)
        if join_steps:
            return join_steps[:max_steps]

    # 4. Explicit join intent with multiple tables selected
    if len(tables) >= 2 and _JOIN_HINT.search(q):
        dim = _breakdown_dim(q) or "dimension"
        return [
            {
                "label": f"1. Base: {_short(tables[0].full_table_id)}",
                "question": _base_explore_question(q, tables),
                "kind": "explore",
            },
            {
                "label": f"2. Join: {_short(tables[1].full_table_id)}",
                "question": f"Preview join keys for {dim}",
                "kind": "explore",
            },
            {
                "label": "3. Final JOIN query",
                "question": q,
                "kind": "final",
            },
        ][:max_steps]

    # 5. LLM planner fallback for complex multi-step (when enabled)
    import config

    if config.SQL_CHAIN_PLANNER == "llm" and needs_notebook_steps(
        q,
        query_plan=query_plan,
        selected_tables=tables,
        join_relations=join_relations,
    ):
        and_steps = _plan_and_split(q, max_steps=max_steps)
        if and_steps:
            return [{**s, "kind": "metric"} for s in and_steps]
        rule_steps = _rule_plan_steps(q, max_steps=max_steps)
        if rule_steps:
            return [{**s, "kind": "metric"} for s in rule_steps]

    return []
