"""Per-table business rules stored on WorkspaceTable.business_rules."""
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
    lines: list[str] = []
    for t in tables or []:
        rules = get_table_business_rules(t)
        if not rules:
            continue
        short = getattr(t, "full_table_id", "").rsplit(".", 1)[-1] or "table"
        lines.append(f"# TABLE BUSINESS RULES for `{short}` (MUST FOLLOW — override default filters):")
        for ln in rules.splitlines():
            ln = ln.strip()
            if ln:
                lines.append(f"#   {ln[:300]}")
        if rules_skip_default_filters(rules):
            lines.append(
                f"#   → Do NOT add pause_status / onboarding-access / other default WHERE filters "
                f"for `{short}`."
            )
    return "\n".join(lines)
