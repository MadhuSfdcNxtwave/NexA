"""Deterministic SQL for placement / got-jobs questions."""
from __future__ import annotations

import re
from typing import Any

_PLACED = re.compile(
    r"\b(placed|placement|got\s+jobs?|got\s+placed|secured\s+jobs?|"
    r"students?\s+got\s+jobs?|how many.{0,40}\bjobs?\b)\b",
    re.I,
)
_JOBS_TABLE = re.compile(r"jobs_details", re.I)


def is_placement_count_question(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    if _JOBS_TABLE.search(q) and re.search(r"\bappli", q, re.I):
        return False
    return bool(_PLACED.search(q) and re.search(r"\b(how many|count|number of|got)\b", q, re.I))


def try_build_placement_sql(
    question: str,
    tables: list[Any],
    columns_by_table: dict[str, set[str]] | None = None,
) -> str | None:
    """COUNT DISTINCT placed users, with date_of_placement when period is asked."""
    if not is_placement_count_question(question):
        return None

    from question_dates import date_filter_sql, pick_date_column, resolve_relative_range

    table = None
    for t in tables or []:
        short = t.full_table_id.rsplit(".", 1)[-1].lower()
        if "placements_details" in short and "eligibility" not in short and "profile" not in short:
            table = t
            break
    if not table:
        return None

    fq = table.full_table_id
    cols = (columns_by_table or {}).get(fq) or set()
    date_col = "date_of_placement" if (not cols or "date_of_placement" in cols) else (
        pick_date_column(table) or "date_of_placement"
    )

    where: list[str] = []
    rel = resolve_relative_range(question)
    if rel:
        where.append(date_filter_sql(date_col, rel[0], rel[1]))

    where_sql = ("\nWHERE " + " AND ".join(where)) if where else ""
    return (
        f"SELECT COUNT(DISTINCT `user_id`) AS `unique_placed_users`\n"
        f"FROM `{fq}`{where_sql}"
    )
