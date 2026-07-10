"""Pre-SQL intent: date-wise, group-wise, and filter conditions for every question."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from question_intent import (
    expand_question_abbreviations,
    is_drill_down_data_request,
    question_wants_breakdown,
)


_FILTER_SIGNAL = re.compile(
    r"\b("
    r"where|only|excluding|except|without|with|for|among|"
    r"filter(?:ed)?|status|active|inactive|paused|"
    r"promoter|detractor|passive|placed|not\s+placed|"
    r"valid|invalid|completed|pending"
    r")\b",
    re.I,
)

_DATE_SIGNAL = re.compile(
    r"\b("
    r"today|yesterday|this\s+(?:week|month|year)|current\s+month|"
    r"last\s+\d+\s+days?|last\s+(?:week|month|year)|"
    r"past\s+\d+\s+(?:days?|months?)|mtd|ytd|"
    r"in\s+20\d{2}|for\s+20\d{2}|during\s+20\d{2}|"
    r"january|february|march|april|may|june|july|august|"
    r"september|october|november|december|"
    r"date[-\s]?wise|day[-\s]?wise|month[-\s]?wise|year[-\s]?wise|"
    r"by\s+date|by\s+month|by\s+week|by\s+day|over\s+time|trend"
    r")\b",
    re.I,
)

_COUNT_SIGNAL = re.compile(
    r"\b(how many|count|total|number of|unique|distinct)\b",
    re.I,
)

_LIST_SIGNAL = re.compile(
    r"\b("
    r"list|show|give|get|fetch|provide|return|display|"
    r"user[_\s]?ids?|userids?|uid|names?|emails?|details?|rows?"
    r")\b",
    re.I,
)

_GROUP_DIM = re.compile(
    r"\b(?:by|per|wise)\s+([a-z][a-z0-9_\s]{1,40}?)(?:\s|$|,|\?)",
    re.I,
)


@dataclass
class QueryShape:
    """What the SQL must encode before generation."""

    wants_date_filter: bool = False
    date_range: tuple[date, date] | None = None
    date_grain: str = ""  # day | week | month | year | ""
    wants_group_by: bool = False
    group_hints: list[str] = field(default_factory=list)
    wants_count: bool = False
    wants_list: bool = False
    is_followup_list: bool = False
    filter_hints: list[str] = field(default_factory=list)
    reason: str = ""

    def to_schema_checkpoint(self) -> str:
        lines = ["# QUERY SHAPE CHECKPOINT (apply before writing SQL):"]
        if self.wants_date_filter:
            if self.date_range:
                start, end = self.date_range
                lines.append(
                    f"# - DATE FILTER required: inclusive {start.isoformat()} .. {end.isoformat()}"
                    " on the table's primary date column."
                )
            else:
                lines.append(
                    "# - DATE / PERIOD filter required — use the table's primary date column."
                )
        if self.date_grain:
            lines.append(
                f"# - DATE-WISE breakdown: GROUP BY {self.date_grain} "
                f"(bucket the date column by {self.date_grain})."
            )
        if self.wants_group_by:
            dims = ", ".join(self.group_hints) if self.group_hints else "the requested dimension"
            lines.append(f"# - GROUP-WISE: GROUP BY {dims} (not a single total).")
        if self.wants_list or self.is_followup_list:
            lines.append(
                "# - LIST rows / ids — SELECT DISTINCT identifiers, NOT COUNT aggregates."
            )
            if self.is_followup_list:
                lines.append(
                    "# - FOLLOW-UP: reuse the SAME table and WHERE filters as the prior Thread SQL."
                )
        elif self.wants_count:
            lines.append(
                "# - COUNT / metric question — use COUNT or COUNT DISTINCT as appropriate."
            )
        if self.filter_hints:
            lines.append("# - FILTER conditions implied: " + "; ".join(self.filter_hints[:6]))
        if self.reason:
            lines.append(f"# - Reason: {self.reason}")
        return "\n".join(lines) if len(lines) > 1 else ""


def _date_grain(question: str) -> str:
    q = question.lower()
    if re.search(r"\b(day[-\s]?wise|by\s+day|daily)\b", q):
        return "day"
    if re.search(r"\b(week[-\s]?wise|by\s+week|weekly)\b", q):
        return "week"
    if re.search(r"\b(month[-\s]?wise|by\s+month|monthly)\b", q):
        return "month"
    if re.search(r"\b(year[-\s]?wise|by\s+year|yearly|annual)\b", q):
        return "year"
    if re.search(r"\bdate[-\s]?wise\b", q):
        return "day"
    return ""


def _group_hints(question: str) -> list[str]:
    hints: list[str] = []
    for m in _GROUP_DIM.finditer(question):
        dim = re.sub(r"\s+", " ", (m.group(1) or "").strip().lower())
        dim = re.sub(r"\b(please|thanks|data|results?)\b", "", dim).strip(" ,.?")
        if dim and dim not in hints and len(dim) < 40:
            hints.append(dim)
    for token in (
        "gender",
        "state",
        "city",
        "coach",
        "growth cycle",
        "program",
        "category",
        "status",
        "company",
    ):
        if re.search(rf"\b{re.escape(token)}\b", question, re.I) and token not in hints:
            if question_wants_breakdown(question) or re.search(
                rf"\b(?:by|per|wise)\b.{{0,20}}{re.escape(token)}", question, re.I
            ):
                hints.append(token)
    return hints[:5]


def _filter_hints(question: str) -> list[str]:
    q = expand_question_abbreviations(question)
    hints: list[str] = []
    patterns = [
        (r"\bpromoters?\b", "promoters (score ≥ 9)"),
        (r"\bdetractors?\b", "detractors (score ≤ 6)"),
        (r"\bpassives?\b", "passives (score 7–8)"),
        (r"\bpaused?\b", "paused status"),
        (r"\bactive\b", "active status"),
        (r"\bplaced\b", "placed / has placement"),
        (r"\bnot\s+placed\b", "not placed"),
        (r"\bvalid\b", "valid rows only"),
    ]
    for pat, label in patterns:
        if re.search(pat, q, re.I):
            hints.append(label)
    if _FILTER_SIGNAL.search(q) and not hints:
        hints.append("apply explicit filters mentioned in the question")
    return hints[:6]


def detect_query_shape(
    question: str,
    *,
    prior_sql: str = "",
    prior_question: str = "",
) -> QueryShape:
    """Detect date / group / filter / list intent before SQL is built."""
    q = expand_question_abbreviations((question or "").strip())
    if not q:
        return QueryShape(reason="empty question")

    date_range = None
    try:
        from question_dates import resolve_question_date_range

        date_range = resolve_question_date_range(q)
    except Exception:
        date_range = None

    grain = _date_grain(q)
    wants_date = bool(date_range) or bool(_DATE_SIGNAL.search(q)) or bool(grain)
    wants_group = question_wants_breakdown(q) or bool(grain)
    groups = _group_hints(q)
    if grain and grain not in groups:
        groups = [grain] + groups

    is_followup_list = bool(prior_sql) and is_drill_down_data_request(q)
    wants_list = is_followup_list or (
        bool(_LIST_SIGNAL.search(q))
        and not _COUNT_SIGNAL.search(q)
        and re.search(r"\b(user[_\s]?ids?|userids?|uid|names?|emails?|details?|rows?)\b", q, re.I)
    )
    wants_count = bool(_COUNT_SIGNAL.search(q)) and not wants_list

    reasons: list[str] = []
    if wants_date:
        reasons.append("date/period")
    if wants_group:
        reasons.append("group-wise")
    if wants_list:
        reasons.append("list/ids")
    elif wants_count:
        reasons.append("count")
    if is_followup_list:
        reasons.append("thread follow-up")

    return QueryShape(
        wants_date_filter=wants_date,
        date_range=date_range,
        date_grain=grain,
        wants_group_by=wants_group,
        group_hints=groups,
        wants_count=wants_count,
        wants_list=wants_list,
        is_followup_list=is_followup_list,
        filter_hints=_filter_hints(q),
        reason=", ".join(reasons) or "auto",
    )


def query_shape_status_message(shape: QueryShape) -> str:
    bits: list[str] = []
    if shape.wants_date_filter:
        if shape.date_range:
            a, b = shape.date_range
            bits.append(f"date filter {a.isoformat()}→{b.isoformat()}")
        else:
            bits.append("date filter")
    if shape.wants_group_by:
        bits.append("group-wise" + (f" ({', '.join(shape.group_hints[:2])})" if shape.group_hints else ""))
    if shape.is_followup_list:
        bits.append("continue prior query → list ids")
    elif shape.wants_list:
        bits.append("list rows/ids")
    elif shape.wants_count:
        bits.append("count/metric")
    if not bits:
        return ""
    return "Query shape: " + "; ".join(bits)
