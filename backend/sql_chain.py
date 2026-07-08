"""Multi-step SQL chain planning and result formatting."""
from __future__ import annotations

import json
import re
from typing import Any

import llm

_MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)

_CHAIN_HINT = re.compile(
    r"\b(compare|comparison|versus|vs\.?|difference between|month over month|"
    r"year over year|each month|both\b.+\band\b|promoters?.+detractors?)\b",
    re.I,
)


def chain_candidate(question: str) -> bool:
    """Fast rule check — should we consider a multi-query chain?"""
    q = question.lower()
    if _CHAIN_HINT.search(q):
        return True
    months = [m for m in _MONTHS if re.search(rf"\b{re.escape(m)}\b", q)]
    if len(set(months)) >= 2:
        return True
    if re.search(r"\band\b", q) and re.search(r"\b(nps|count|rating|score|average|total)\b", q):
        if len(months) >= 1 and re.search(r"\b(also|as well|plus|versus|vs)\b", q):
            return True
    return False


def _rule_plan_steps(question: str, *, max_steps: int = 3) -> list[dict[str, str]]:
    """Hex-style chain planner — no LLM; only obvious month/compare splits."""
    q = question.strip()
    q_lower = q.lower()
    months_found: list[str] = []
    for m in _MONTHS:
        if re.search(rf"\b{re.escape(m)}\b", q_lower):
            months_found.append(m)
    unique_months = list(dict.fromkeys(months_found))
    if len(unique_months) >= 2:
        steps: list[dict[str, str]] = []
        for m in unique_months[:max_steps]:
            label = m.title()
            sub_q = re.sub(
                r"\b(" + "|".join(re.escape(x) for x in unique_months) + r")\b",
                m,
                q,
                count=1,
                flags=re.I,
            )
            if sub_q.strip().lower() == q_lower:
                sub_q = f"{q} (for {label})"
            steps.append({"label": label, "question": sub_q})
        if len(steps) >= 2:
            return steps

    if re.search(r"\b(promoters?|detractors?)\b", q_lower) and re.search(
        r"\b(and|versus|vs\.?|compare)\b", q_lower
    ):
        steps = []
        for label, term in (("Promoters", "promoters"), ("Detractors", "detractors")):
            if term in q_lower or term.rstrip("s") in q_lower:
                steps.append(
                    {
                        "label": label,
                        "question": re.sub(
                            r"\b(promoters?|detractors?)\b",
                            term,
                            q,
                            flags=re.I,
                        ),
                    }
                )
        if len(steps) >= 2:
            return steps[:max_steps]
    return []


def plan_steps(question: str, schema_text: str, *, max_steps: int = 3) -> list[dict[str, str]]:
    """
    Return chain steps as [{label, question}, ...].
    Empty list means use a single SQL query.
    """
    if not chain_candidate(question):
        return []

    import config

    if config.SQL_CHAIN_PLANNER == "rules":
        return _rule_plan_steps(question, max_steps=max_steps)

    plan = llm.plan_sql_chain(question, schema_text, max_steps=max_steps)
    if plan.get("mode") != "chain":
        return []

    steps: list[dict[str, str]] = []
    for raw in plan.get("steps") or []:
        label = str(raw.get("label") or "").strip()
        sub_q = str(raw.get("question") or "").strip()
        if label and sub_q:
            steps.append({"label": label, "question": sub_q})
        if len(steps) >= max_steps:
            break
    return steps if len(steps) >= 2 else []


def format_prior_steps(completed: list[dict[str, Any]]) -> str:
    """Context block for the next SQL generation step."""
    if not completed:
        return ""
    blocks: list[str] = ["# Prior chain steps (already executed on BigQuery)"]
    for i, step in enumerate(completed, 1):
        sample = step.get("rows") or []
        blocks.append(
            f"\n## Step {i}: {step.get('label', '')}\n"
            f"Question: {step.get('question', '')}\n"
            f"SQL:\n{step.get('sql', '')}\n"
            f"Columns: {step.get('columns', [])}\n"
            f"Row count: {len(sample)}\n"
            f"Sample rows: {json.dumps(sample[:15], default=str)}"
        )
    blocks.append(
        "\nWrite the next standalone BigQuery SELECT for the current step. "
        "Do not reference temp tables from prior steps — each query runs independently."
    )
    return "\n".join(blocks)


def combine_sql(steps: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for i, step in enumerate(steps, 1):
        label = step.get("label") or f"Step {i}"
        parts.append(f"-- Step {i}: {label}\n{step.get('sql', '').strip()}")
    return "\n\n".join(parts)


def merge_rows_for_display(steps: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    """Flatten multi-step results for table/chart display."""
    if len(steps) == 1:
        s = steps[0]
        return list(s.get("columns") or []), list(s.get("rows") or [])

    merged: list[dict[str, Any]] = []
    col_set: list[str] = ["chain_step"]
    seen_cols: set[str] = {"chain_step"}

    for step in steps:
        label = step.get("label") or "step"
        for row in step.get("rows") or []:
            out = {"chain_step": label}
            for k, v in row.items():
                out[k] = v
                if k not in seen_cols:
                    seen_cols.add(k)
                    col_set.append(k)
            merged.append(out)

    return col_set, merged
