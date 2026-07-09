"""QueryCriticAgent — unified SQL validation + self-correction loop."""
from __future__ import annotations

import re
from typing import Any

import bq
import config
import llm
from config_models import model_for_role, provider_for_model
from memory_lookup import sql_intent_mismatch_reason

_TIME_WORDS = re.compile(
    r"\b(yesterday|yestarday|today|now|this week|this month|currently|recent|"
    r"latest|last week|last month|last 7 days|last 30 days)\b",
    re.I,
)
_DATE_SQL = re.compile(
    r"\b(DATE|TIMESTAMP|BETWEEN|>=|<=|CURRENT_DATE|DATE_SUB|DATE_TRUNC)\b",
    re.I,
)
_LIST_START = re.compile(
    r"^\s*(which|who|list|show me|give me all|find all)\b",
    re.I,
)
_COUNT_ONLY = re.compile(r"SELECT\s+COUNT\b", re.I)
_BREAKDOWN = re.compile(
    r"\b(by state|by gender|by city|by region|by month|by week|by day|"
    r"breakdown|per state|per gender|distribution)\b",
    re.I,
)
_MAX_RETRIES = 3


class QueryCriticAgent:
    """Run ordered checks; self-correct via LLM when checks fail."""

    def validate(self, sql: str, question: str, *, schema_entities: list | None = None) -> list[str]:
        issues: list[str] = []
        sql_text = (sql or "").strip()
        q = (question or "").strip()

        if not sql_text.upper().lstrip().startswith("SELECT"):
            issues.append("SQL must start with SELECT — rewrite as a read-only SELECT query.")

        if _TIME_WORDS.search(q) and not _DATE_SQL.search(sql_text):
            issues.append(
                "Question implies a time window but SQL has no date filter. "
                "Add WHERE on the primary date column."
            )

        if _LIST_START.search(q) and _COUNT_ONLY.search(sql_text) and not re.search(
            r"\bGROUP BY\b", sql_text, re.I
        ):
            issues.append(
                "Question asks for a list of records but SQL returns a single count."
            )

        if _BREAKDOWN.search(q) and not re.search(r"\bGROUP BY\b", sql_text, re.I):
            issues.append("Breakdown question needs GROUP BY clause.")

        if re.search(r"SELECT\s+\*", sql_text, re.I):
            issues.append("Replace SELECT * with explicit column names.")

        intent_reason = sql_intent_mismatch_reason(
            q, sql_text, schema_entities=schema_entities
        )
        if intent_reason:
            issues.append(intent_reason)

        try:
            bq.dry_run_bytes(sql_text)
        except Exception as exc:
            issues.append(f"BigQuery dry-run failed: {bq.format_query_error(exc)}")

        return issues

    def validate_and_correct(
        self,
        sql: str,
        question: str,
        schema_context: str,
        *,
        fix_model: str | None = None,
        schema_entities: list | None = None,
    ) -> tuple[str, list[str]]:
        """Validate SQL; retry up to 3 times with LLM rewrite on failure."""
        all_issues: list[str] = []
        current = (sql or "").strip()
        fix_model = fix_model or model_for_role("complex")
        fix_provider = provider_for_model(fix_model)

        for attempt in range(1, _MAX_RETRIES + 1):
            issues = self.validate(
                current, question, schema_entities=schema_entities
            )
            if not issues:
                return current, all_issues
            all_issues.extend(issues)
            if attempt >= _MAX_RETRIES:
                break
            current = llm.rewrite_sql(
                question,
                current,
                schema_context,
                issues=issues,
                model=fix_model,
                provider=fix_provider,
            )
        return current, all_issues


_default_critic: QueryCriticAgent | None = None


def get_query_critic() -> QueryCriticAgent:
    global _default_critic
    if _default_critic is None:
        _default_critic = QueryCriticAgent()
    return _default_critic
