"""Per-table business rules stored on WorkspaceTable.business_rules.

Flow: after Ask selects table(s) → load each table's rules → inject as
mandatory SQL guidance (overrides default measures/filters when they conflict).
"""
from __future__ import annotations

import re
from typing import Any

# Phrases that mean: skip hardcoded pause/onboarding/measure WHERE filters.
_NO_DEFAULT_FILTERS = re.compile(
    r"(?:"
    r"no\s+(?:where|filter|filters|extra\s+filters)|"
    r"do\s+not\s+(?:add\s+)?(?:where|filter)|"
    r"don'?t\s+(?:add\s+)?(?:where|filter)|"
    r"without\s+(?:a\s+)?(?:where|filter)|"
    r"count\s+all\s+rows|"
    r"every\s+row\s+is|"
    r"all\s+rows\s+(?:are|mean)|"
    r"table\s+itself\s+is|"
    r"master\s+itself|"
    r"already\s+(?:an?\s+)?active|"
    r"no\s+pause_status|"
    r"skip\s+(?:pause|onboarding|default)\s*filters?|"
    r"filters?\s*:\s*(?:none|\[\]|empty)"
    r")",
    re.I,
)


def get_table_business_rules(table: Any) -> str:
    return (getattr(table, "business_rules", None) or "").strip()


def rules_skip_default_filters(rules: str) -> bool:
    """True when free-text rules say not to apply default measure WHERE clauses."""
    text = (rules or "").strip()
    if not text:
        return False
    return bool(_NO_DEFAULT_FILTERS.search(text))


def table_skips_default_filters(table: Any) -> bool:
    return rules_skip_default_filters(get_table_business_rules(table))


def format_table_rules_block(tables: list[Any]) -> str:
    """Prompt block: per-table rules that MUST override YAML defaults."""
    return build_mandatory_rules_preamble(tables, include_header=False)


def build_mandatory_rules_preamble(
    tables: list[Any],
    *,
    include_header: bool = True,
) -> str:
    """Build the post-selection rules block for SQL generation.

    Called after tables are chosen. Rules tell the model grain, filters,
    count semantics (COUNT(*) vs COUNT DISTINCT), and metric definitions.
    """
    lines: list[str] = []
    if include_header:
        lines.extend(
            [
                "# ============================================================",
                "# MANDATORY — SELECTED TABLE BUSINESS RULES",
                "# Tables are already chosen. Read these rules BEFORE writing SQL.",
                "# Rules override default measures, glossary filters, and habits.",
                "# If a rule conflicts with anything else in this prompt, FOLLOW THE RULE.",
                "# ============================================================",
                "# GLOBAL JOIN RULE (every table): user_id formats differ (with/without hyphens).",
                "# ALWAYS join with BOTH sides normalized:",
                "#   REPLACE(a.user_id, '-', '') = REPLACE(b.user_id, '-', '')",
                "# Never write bare a.user_id = b.user_id. Same for discussion_user_id / uid__c.",
            ]
        )

    any_rules = False
    for t in tables or []:
        rules = get_table_business_rules(t)
        short = getattr(t, "full_table_id", "").rsplit(".", 1)[-1] or "table"
        fq = getattr(t, "full_table_id", "") or short
        if not rules:
            if include_header:
                lines.append(f"# `{short}` — no custom business_rules; use description + measures carefully.")
            continue
        any_rules = True
        lines.append(f"# --- RULES for `{short}` (`{fq}`) ---")
        for ln in rules.splitlines():
            ln = ln.strip()
            if ln:
                lines.append(f"#   {ln[:400]}")
        if rules_skip_default_filters(rules):
            lines.append(
                f"#   → Do NOT add pause_status / onboarding-access / other default WHERE "
                f"filters for `{short}` unless the question explicitly asks for them."
            )

    if include_header and any_rules:
        lines.append(
            "# Apply the matching table's rules to the user question "
            "(grain, COUNT vs COUNT DISTINCT, rating bands, date columns, etc.)."
        )
    elif include_header and not any_rules and not any(
        get_table_business_rules(t) for t in (tables or [])
    ):
        # header-only with "no custom" lines already added
        pass

    return "\n".join(lines).strip()


def prepend_rules_to_schema(schema_text: str, tables: list[Any]) -> str:
    """Put mandatory rules at the top so truncation cannot drop them."""
    preamble = build_mandatory_rules_preamble(tables)
    body = (schema_text or "").strip()
    if not preamble:
        return body
    if not body:
        return preamble
    # Avoid duplicating if already prepended
    if "MANDATORY — SELECTED TABLE BUSINESS RULES" in body[:800]:
        return body
    return f"{preamble}\n\n{body}"
