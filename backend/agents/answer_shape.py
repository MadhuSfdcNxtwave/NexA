"""Detect what the user wants shown: raw/CSV rows vs aggregates."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from question_intent import expand_question_abbreviations


_RAW_EXPORT = re.compile(
    r"\b("
    r"raw\s+data|raw\s+rows?|field[-\s]?wise|field\s+level|"
    r"export\s*(?:as\s+)?csv|csv\s+export|download\s+(?:as\s+)?csv|"
    r"data\s+table|tabular|row[-\s]?level|all\s+columns?|"
    r"give\s+(?:me\s+)?(?:the\s+)?(?:raw|full|complete)\b|"
    r"just\s+(?:give|show|the)\s+(?:raw|data|rows?|table)|"
    r"details?\s+(?:for|to)\s+export|exportable"
    r")\b",
    re.I,
)

_DETAILS_LIST = re.compile(
    r"\b("
    r"(?:give|show|get|list|fetch|provide|return|pull)\s+"
    r"(?:me\s+)?(?:the\s+)?"
    r"(?:(?:current|this)\s+months?'?\s+)?"
    r"(?:contextual\s+)?feedback\s+details?"
    r"|feedback\s+details?"
    r"|detailed\s+(?:records?|rows?|data)"
    r"|(?:current|this)\s+months?'?\s+(?:contextual\s+)?feedback"
    r")\b",
    re.I,
)

_AGGREGATE = re.compile(
    r"\b("
    r"how many|count|total|average|avg|percentage|percent|"
    r"distribution|breakdown|most\s+common|top\s+\d+"
    r")\b",
    re.I,
)

_TOPIC_CHANGE = re.compile(
    r"\b("
    r"instead|different\s+table|switch\s+to|now\s+(?:about|for)|"
    r"nps\b|placement|attendance|live\s+class|portal\s+active"
    r")\b",
    re.I,
)

_CONTINUITY = re.compile(
    r"\b("
    r"raw|csv|export|field[-\s]?wise|data\s+table|tabular|"
    r"same\s+(?:data|table|query|ones?|users?|students?|rows?|results?)|"
    r"those\s+(?:rows?|users?|students?)|that\s+data|their\s+(?:ids?|user|"
    r"userids?|details?)|"
    r"just\s+give|only\s+(?:the\s+)?(?:raw|data|rows?|table)|"
    r"more\s+columns?|all\s+fields?|full\s+(?:data|rows?)"
    r")\b",
    re.I,
)


@dataclass
class AnswerShape:
    """Pre-SQL plan for what to return to the user."""

    mode: str = "auto"  # raw | aggregate | auto
    reason: str = ""
    columns: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    measure: str = ""
    rules_block: str = ""
    locked_table_ids: list[str] = field(default_factory=list)
    clarify: dict[str, Any] | None = None

    @property
    def wants_raw(self) -> bool:
        return self.mode == "raw"


def wants_raw_tabular_data(question: str) -> bool:
    """True when the user wants field-level rows / CSV-ready data, not aggregates."""
    q = expand_question_abbreviations((question or "").strip())
    if not q:
        return False
    if _RAW_EXPORT.search(q):
        return True
    if _DETAILS_LIST.search(q) and not _AGGREGATE.search(q):
        return True
    return False


def wants_aggregate_metric(question: str) -> bool:
    q = expand_question_abbreviations((question or "").strip())
    if not q:
        return False
    if wants_raw_tabular_data(q):
        return False
    return bool(_AGGREGATE.search(q))


def is_thread_continuity_followup(question: str, *, prior_sql: str = "") -> bool:
    """
    Follow-ups that must keep the prior table (raw/CSV/field-wise/same data).
    Broader than breakdown/drill-down — covers the contextual-feedback CSV thread.
    """
    if not (prior_sql or "").strip():
        return False
    q = expand_question_abbreviations((question or "").strip())
    if not q:
        return False
    from question_intent import is_drill_down_data_request, is_list_pagination_request

    if is_drill_down_data_request(q) or is_list_pagination_request(q):
        return True
    if _TOPIC_CHANGE.search(q) and not _CONTINUITY.search(q):
        # Explicit new domain — do not lock.
        if re.search(r"\b(nps|placement|attendance|portal)\b", q, re.I):
            return False
    if wants_raw_tabular_data(q):
        return True
    if _CONTINUITY.search(q):
        return True
    # Short follow-ups with no new domain nouns.
    words = re.findall(r"[a-z0-9_]+", q.lower())
    if len(words) <= 8 and _CONTINUITY.search(q):
        return True
    return False


def detect_answer_shape(question: str, *, prior_sql: str = "") -> AnswerShape:
    q = expand_question_abbreviations((question or "").strip())
    from question_intent import is_drill_down_data_request

    if prior_sql and is_drill_down_data_request(q):
        return AnswerShape(
            mode="auto",
            reason="Drill-down — list ids/rows using prior filters (not a new aggregate)",
        )
    if wants_raw_tabular_data(q):
        return AnswerShape(mode="raw", reason="User asked for raw/field-wise/CSV-ready rows")
    if wants_aggregate_metric(q):
        return AnswerShape(mode="aggregate", reason="User asked for a count/metric")
    if is_thread_continuity_followup(q, prior_sql=prior_sql) and wants_raw_tabular_data(
        # If prior was raw-ish continuity without explicit aggregate, prefer raw.
        q
    ):
        return AnswerShape(mode="raw", reason="Follow-up continues raw/export request")
    # Continuity follow-ups like "just give the raw data" already caught above.
    if is_thread_continuity_followup(q, prior_sql=prior_sql):
        return AnswerShape(mode="raw", reason="Thread continuity — keep row-level export shape")
    return AnswerShape(mode="auto", reason="No explicit raw/aggregate signal")
