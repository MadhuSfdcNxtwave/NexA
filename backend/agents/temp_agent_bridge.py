"""Bridge helpers for the temporary query agent."""
from __future__ import annotations

from typing import Any


def try_temp_agent_sql(
    question: str,
    tables: list[Any],
    columns_by_table: dict[str, set[str]] | None = None,
    *,
    prior_sql: str = "",
) -> tuple[str | None, str, dict | None]:
    """
    Returns (sql, reason, clarify_payload).
    clarify_payload is set when the agent refuses to guess.
    """
    from agents.temp_query_agent import run_temp_query_agent, should_run_temp_agent

    if not should_run_temp_agent(question):
        return None, "", None

    result = run_temp_query_agent(
        question,
        tables,
        columns_by_table,
        prior_sql=prior_sql,
    )
    if result.clarify and not result.sql:
        return None, result.reason, result.clarify
    if result.sql:
        return result.sql, result.reason or "Temp query agent", None
    return None, result.reason or "", None
