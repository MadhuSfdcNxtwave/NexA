"""Pre-flight data probes and empty-result retry helpers."""
from __future__ import annotations

import json
import re
from typing import Any

from question_dates import pick_date_column, resolve_relative_range


def build_preflight_sql(table: Any) -> str | None:
    """MIN/MAX date + row count probe for the primary date column."""
    fq = getattr(table, "full_table_id", "")
    if not fq:
        return None
    date_col = pick_date_column(table)
    if not date_col:
        return f"SELECT COUNT(*) AS total_rows FROM `{fq}`"
    return (
        f"SELECT\n"
        f"  MIN(DATE(`{date_col}`)) AS min_date,\n"
        f"  MAX(DATE(`{date_col}`)) AS max_date,\n"
        f"  COUNT(*) AS total_rows\n"
        f"FROM `{fq}`"
    )


def format_probe_stats(rows: list[dict]) -> str:
    if not rows:
        return ""
    row = rows[0]
    parts = []
    if row.get("min_date") is not None:
        parts.append(f"data from {row.get('min_date')} to {row.get('max_date')}")
    if row.get("total_rows") is not None:
        parts.append(f"{row.get('total_rows')} total rows")
    return ", ".join(parts)


def question_has_time_scope(question: str) -> bool:
    q = question.lower()
    if resolve_relative_range(question):
        return True
    return bool(
        re.search(
            r"\b(yesterday|today|last week|this month|last month|january|february|march|"
            r"april|may|june|july|august|september|october|november|december|20\d{2})\b",
            q,
        )
    )


def build_widen_date_sql(sql: str, table: Any) -> str | None:
    """Drop date WHERE clauses for a second attempt when the filtered query is empty."""
    if not sql or not table:
        return None
    fq = getattr(table, "full_table_id", "")
    short = fq.rsplit(".", 1)[-1]
    if short.lower() not in sql.lower():
        return None
    # Remove BETWEEN / DATE(...) = filters — keep the rest of the query.
    widened = re.sub(
        r"\n?\s*AND\s+DATE\(`[^`]+`\)\s+BETWEEN\s+DATE\s+'[^']+'\s+AND\s+DATE\s+'[^']+'",
        "",
        sql,
        flags=re.I,
    )
    widened = re.sub(
        r"\n?\s*WHERE\s+DATE\(`[^`]+`\)\s+BETWEEN\s+DATE\s+'[^']+'\s+AND\s+DATE\s+'[^']+'",
        "",
        widened,
        flags=re.I,
    )
    widened = re.sub(
        r"\n?\s*AND\s+DATE\(`[^`]+`\)\s*=\s*DATE\s+'[^']+'",
        "",
        widened,
        flags=re.I,
    )
    widened = re.sub(
        r"\n?\s*WHERE\s+DATE\(`[^`]+`\)\s*=\s*DATE\s+'[^']+'",
        "",
        widened,
        flags=re.I,
    )
    if widened.strip() == sql.strip():
        return None
    return widened.strip()


def profile_date_ranges(table: Any) -> dict[str, str]:
    try:
        profile = json.loads(getattr(table, "ai_profile_json", "") or "{}")
    except json.JSONDecodeError:
        return {}
    ranges = profile.get("date_ranges") or {}
    out: dict[str, str] = {}
    for col, bounds in ranges.items():
        mn = (bounds.get("min") or "")[:10]
        mx = (bounds.get("max") or "")[:10]
        if mn or mx:
            out[col] = f"{mn} to {mx}"
    return out
