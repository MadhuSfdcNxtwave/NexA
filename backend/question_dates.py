"""Resolve date filters from question + AI table profiles (no year guessing)."""
from __future__ import annotations

import calendar
import json
import re
from datetime import date, timedelta
from typing import Any

_MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)
_MONTH_SHORT = {m[:3]: i for i, m in enumerate(_MONTHS, 1)}
_YEAR_RE = re.compile(r"\b(20\d{2})\b")

_DATE_COLUMN_CANDIDATES = (
    "submitted_date",
    "calendar_date",
    "slot_date",
    "form_submission_datetime",
    "created_date",
    "date_of_placement",
    "applied_datetime",
    "month",
)


def resolve_relative_range(question: str, *, today: date | None = None) -> tuple[date, date] | None:
    """Map relative phrases to inclusive (start, end) dates."""
    q = question.lower()
    ref = today or date.today()

    if re.search(r"\byesterday\b", q):
        d = ref - timedelta(days=1)
        return d, d
    if re.search(r"\btoday\b", q):
        return ref, ref
    if re.search(r"\blast\s+7\s+days\b|\bpast\s+7\s+days\b", q):
        return ref - timedelta(days=6), ref
    if re.search(r"\blast\s+week\b|\bpast\s+week\b", q):
        end = ref - timedelta(days=1)
        return end - timedelta(days=6), end
    if re.search(r"\blast\s+30\s+days\b|\bpast\s+30\s+days\b|\blast\s+month\b|\bpast\s+month\b", q):
        return ref - timedelta(days=29), ref
    if re.search(r"\bthis\s+week\b", q):
        start = ref - timedelta(days=ref.weekday())
        return start, ref
    if re.search(r"\bthis\s+month\b|\bcurrent\s+month\b|\bmtd\b", q):
        return ref.replace(day=1), ref
    return None


def _month_from_question(question: str) -> int | None:
    q = question.lower()
    for i, name in enumerate(_MONTHS, 1):
        if re.search(rf"\b{name}\b", q) or re.search(rf"\b{name[:3]}\b", q):
            return i
    return None


def _year_from_question(question: str) -> int | None:
    m = _YEAR_RE.search(question)
    return int(m.group(1)) if m else None


def _profile_ranges(table: Any) -> dict[str, dict[str, str]]:
    try:
        profile = json.loads(getattr(table, "ai_profile_json", "") or "{}")
    except json.JSONDecodeError:
        return {}
    return profile.get("date_ranges") or {}


def _pick_year_for_month(month: int, ranges: dict[str, dict[str, str]]) -> int | None:
    candidates: set[int] = set()
    for bounds in ranges.values():
        for key in ("min", "max"):
            raw = (bounds.get(key) or "")[:10]
            if len(raw) >= 7:
                try:
                    y, m = int(raw[:4]), int(raw[5:7])
                    candidates.add(y)
                    if m == month:
                        candidates.add(y)
                except ValueError:
                    continue
        mn, mx = (bounds.get("min") or "")[:10], (bounds.get("max") or "")[:10]
        if len(mn) >= 7 and len(mx) >= 7:
            try:
                y1, m1 = int(mn[:4]), int(mn[5:7])
                y2, m2 = int(mx[:4]), int(mx[5:7])
                for y in range(y1, y2 + 1):
                    if (y == y1 and month >= m1) or (y == y2 and month <= m2) or (y1 < y < y2):
                        candidates.add(y)
            except ValueError:
                pass
    if not candidates:
        return None
    return max(candidates)


def pick_date_column(table: Any, ranges: dict[str, dict[str, str]] | None = None) -> str | None:
    """Best date column for filters on this table."""
    ranges = ranges if ranges is not None else _profile_ranges(table)
    if ranges:
        for col in _DATE_COLUMN_CANDIDATES:
            if col in ranges:
                return col
        return next(iter(ranges.keys()), None)
    try:
        cols = json.loads(getattr(table, "column_descriptions_json", "") or "{}")
    except json.JSONDecodeError:
        cols = {}
    for col in _DATE_COLUMN_CANDIDATES:
        if col in cols:
            return col
    for col in cols:
        if "date" in col.lower() or col.lower() == "month":
            return col
    return None


def date_filter_sql(date_col: str, start: date, end: date, *, month_bucket: bool = False) -> str:
    if month_bucket and start.day == 1 and end.day >= 28 and start.month == end.month:
        return f"`{date_col}` = DATE '{start.year}-{start.month:02d}-01'"
    if start == end:
        return f"DATE(`{date_col}`) = DATE '{start.isoformat()}'"
    return (
        f"DATE(`{date_col}`) BETWEEN DATE '{start.isoformat()}' "
        f"AND DATE '{end.isoformat()}'"
    )


def build_date_hints(question: str, tables: list[Any]) -> str:
    """Schema header block: exact date filter SQL from profiles + question."""
    rel = resolve_relative_range(question)
    if rel:
        start, end = rel
        lines = [
            "# DATE FILTER (relative period from question — use exactly)",
            f"# Period: {start.isoformat()} to {end.isoformat()}",
        ]
        for t in tables:
            short = t.full_table_id.rsplit(".", 1)[-1]
            ranges = _profile_ranges(t)
            date_col = pick_date_column(t, ranges)
            if not date_col:
                continue
            month_bucket = "month" in date_col.lower() and start != end
            filt = date_filter_sql(date_col, start, end, month_bucket=month_bucket)
            lines.append(f"#   `{short}`: WHERE {filt}")
        return "\n".join(lines) if len(lines) > 2 else ""

    month = _month_from_question(question)
    if re.search(r"\b(this month|current month|mtd)\b", question.lower()):
        today = date.today()
        month = today.month
        year = today.year
    else:
        year = _year_from_question(question)
    if not month:
        return ""
    month_name = _MONTHS[month - 1].capitalize()
    lines = ["# DATE FILTER (from AI table profiles — use these, do NOT guess 2025)"]

    for t in tables:
        ranges = _profile_ranges(t)
        if not ranges:
            continue
        short = t.full_table_id.rsplit(".", 1)[-1]
        yr = year
        if yr is None:
            yr = _pick_year_for_month(month, ranges)
        if not yr:
            spans = ", ".join(
                f"{c}: {r.get('min', '')[:10]} to {r.get('max', '')[:10]}"
                for c, r in ranges.items()
            )
            lines.append(f"#   `{short}` coverage: {spans}")
            continue

        month_col = next(
            (c for c in ranges if "month" in c.lower()),
            next(iter(ranges)),
        )
        date_col = next(
            (c for c in ranges if c.lower() in ("submitted_date", "form_submission_datetime")),
            month_col,
        )
        if "month" in month_col.lower():
            filter_sql = f"`{month_col}` = DATE '{yr}-{month:02d}-01'"
        else:
            last = calendar.monthrange(yr, month)[1]
            filter_sql = (
                f"`{date_col}` BETWEEN DATE '{yr}-{month:02d}-01' "
                f"AND DATE '{yr}-{month:02d}-{last}'"
            )
        lines.append(
            f"#   `{short}` — {month_name} {yr}: {filter_sql} "
            f"(profile range: {ranges.get(month_col, {}).get('min', '')[:10]} "
            f"to {ranges.get(month_col, {}).get('max', '')[:10]})"
        )
    return "\n".join(lines) if len(lines) > 1 else ""
