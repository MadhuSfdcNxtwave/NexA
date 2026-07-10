"""Centralized table routing rules and SQL column filters for domain questions."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SqlFilter:
    """WHERE clause fragment when the column exists on the selected table."""

    column: str
    op: str  # =, IS NULL, IS NOT NULL
    value: str | None = None

    def to_sql(self) -> str:
        col = f"`{self.column}`"
        if self.op.upper() == "IS NULL":
            return f"{col} IS NULL"
        if self.op.upper() == "IS NOT NULL":
            return f"{col} IS NOT NULL"
        if self.value is not None:
            return f"{col} {self.op} '{self.value}'"
        return ""


@dataclass(frozen=True)
class RoutingRule:
    id: str
    table_short: str
    question_re: re.Pattern[str]
    reason: str
    filters: tuple[SqlFilter, ...] = field(default_factory=tuple)
    score_boost: int = 500
    score_penalty_shorts: tuple[str, ...] = field(default_factory=tuple)


_PORTAL_ACTIVE_COUNT = re.compile(
    r"\bactive\b.{0,40}\b(learning[\s_-]*portal|portal)\b|"
    r"\b(learning[\s_-]*portal|portal)\b.{0,40}\bactive\b",
    re.I,
)
_LP_STATUS = re.compile(r"\blp_status\b|\blp status\b", re.I)

_ATTEND = re.compile(r"\battend(?:ance|ed|ing)?\b", re.I)
_LIVE_CLASS = re.compile(r"\blive[\s_-]*class", re.I)
_PORTAL_MENTION = re.compile(r"\blearning[\s_-]*portal|\bportal\b", re.I)
_PORTAL_ACCESS = re.compile(
    r"\bportal\s+access\b|\blearning\s+portal\s+access\b|"
    r"\baccess\s+to\s+(?:the\s+)?(?:learning\s*)?portal\b|"
    r"\bhave\s+.{0,30}\bportal\b",
    re.I,
)
_PLATFORM_ACTIVE = re.compile(
    r"\bactive\b.{0,30}\bplatform\b|\bplatform\b.{0,30}\bactive\b",
    re.I,
)

# Signal pairs → canonical table short names (join required).
_COMPOUND_TABLES: dict[frozenset[str], tuple[str, ...]] = {
    frozenset({"live_class_attendance", "learning_portal"}): (
        "z_academy_users_live_classes_attendance_and_time_spent_details",
        "z_ccbp_academy_users_master_data",
    ),
    frozenset({"live_class_attendance", "platform_active"}): (
        "z_academy_users_live_classes_attendance_and_time_spent_details",
        "y_academy_user_daily_engagement_time_spent",
    ),
    frozenset({"platform_active", "learning_portal"}): (
        "y_academy_user_daily_engagement_time_spent",
        "z_ccbp_academy_users_master_data",
    ),
    frozenset({"portal_page_activity", "live_class_attendance"}): (
        "academy_users_day_and_page_wise_time_spent_details",
        "z_academy_users_live_classes_attendance_and_time_spent_details",
    ),
}

ROUTING_RULES: tuple[RoutingRule, ...] = (
    RoutingRule(
        id="live_class_attendance",
        table_short="z_academy_users_live_classes_attendance_and_time_spent_details",
        question_re=re.compile(r"\battend", re.I),
        reason="Live class attendance records",
        filters=(SqlFilter("attendance_status", "=", "JOINED"),),
        score_penalty_shorts=("cloudwatch", "question_wise", "virtual_meet"),
    ),
    RoutingRule(
        id="learning_portal_active_users",
        table_short="z_ccbp_academy_users_master_data",
        question_re=_PORTAL_ACTIVE_COUNT,
        reason="Active learning portal users (master profile — every row is active)",
        filters=(),
        score_penalty_shorts=("question_wise", "question_set", "day_and_page_wise"),
    ),
    RoutingRule(
        id="learning_portal_lp_status_active",
        table_short="academy_users_day_and_page_wise_time_spent_details",
        question_re=_LP_STATUS,
        reason="Portal lp_status enum (engagement table — use only when lp_status is explicit)",
        filters=(SqlFilter("lp_status", "=", "ACTIVE"),),
        score_penalty_shorts=("question_wise", "question_set", "master_data", "cloudwatch"),
    ),
    RoutingRule(
        id="learning_portal_access_granted",
        table_short="z_ccbp_academy_users_master_data",
        question_re=_PORTAL_ACCESS,
        reason="Users granted learning portal access (master profile)",
        filters=(
            SqlFilter("pause_status", "IS NULL"),
            SqlFilter("learning_portal_onboarding_access_given_datetime", "IS NOT NULL"),
        ),
        score_penalty_shorts=("question_wise", "question_set", "day_and_page_wise"),
    ),
    RoutingRule(
        id="platform_active",
        table_short="y_academy_user_daily_engagement_time_spent",
        question_re=re.compile(r"\bactive\b.{0,30}\bplatform\b|\bplatform\b.{0,30}\bactive\b", re.I),
        reason="Daily platform engagement",
        score_penalty_shorts=("cloudwatch", "event_engagement"),
    ),
    RoutingRule(
        id="nps_form_responses",
        table_short="academy_nps_form_responses",
        question_re=re.compile(
            r"\bnps\s+form|\bnps\b.{0,40}\bform\s+responses?|"
            r"\bnet promoter\b.{0,40}\bfeedback\b|"
            r"\bnps\b.{0,40}\bfeedback\b|\bfeedback\b.{0,40}\bnps\b",
            re.I,
        ),
        reason="NPS form free-text responses",
        score_penalty_shorts=("contextual_feedback", "cloudwatch"),
    ),
    RoutingRule(
        id="contextual_feedback",
        table_short="users_contextual_feedback_details",
        question_re=re.compile(r"\b(feedback|emoji)\b", re.I),
        reason="Contextual feedback responses",
        score_penalty_shorts=("nps_form", "cloudwatch"),
    ),
    RoutingRule(
        id="learning_portal_page_activity",
        table_short="academy_users_day_and_page_wise_time_spent_details",
        question_re=re.compile(
            r"\b(?:learning[\s_-]*portal|learningportal|portal)\b.{0,60}\b(activity|activit|page|events?|which)\b|"
            r"\b(which|what)\b.{0,40}\b(activity|page)\b.{0,40}\b(?:learning[\s_-]*portal|learningportal|portal)\b|"
            r"\bactiv(?:e|ly|lly)\s+in\b.{0,40}\b(?:learning[\s_-]*portal|learningportal|portal)\b|"
            r"\bevents?\b.{0,40}\b(?:learning[\s_-]*portal|learningportal|portal)\b",
            re.I,
        ),
        reason="Learning portal page/activity breakdown (time_spent_page)",
        score_penalty_shorts=("event_engagement", "cloudwatch", "nw_events"),
    ),
)


def normalize_question(question: str) -> str:
    from question_intent import expand_question_abbreviations

    return expand_question_abbreviations(question or "").strip()


def detect_domain_signals(question: str) -> set[str]:
    """Detect which analytics domains a question spans (may imply a JOIN)."""
    q = normalize_question(question)
    if not q:
        return set()
    signals: set[str] = set()
    if _ATTEND.search(q) or (_LIVE_CLASS.search(q) and re.search(r"\bclass", q, re.I)):
        signals.add("live_class_attendance")
    if _PORTAL_ACTIVE_COUNT.search(q):
        signals.add("learning_portal")
    elif _PORTAL_MENTION.search(q) and (
        _PORTAL_ACCESS.search(q) or re.search(r"\band\b", q, re.I)
    ):
        signals.add("learning_portal")
    if _PORTAL_MENTION.search(q) and re.search(
        r"\bactivity|activit|which\s+page|page\b|events?\b|actively\b",
        q,
        re.I,
    ):
        signals.add("portal_page_activity")
    if re.search(r"\battendance\b|\battend(?:ed|ance)?\b", q, re.I) and re.search(
        r"\bpercent|percentage|%\b",
        q,
        re.I,
    ):
        signals.add("live_class_attendance")
    if _PLATFORM_ACTIVE.search(q):
        signals.add("platform_active")
    if re.search(
        r"\bnps\s+form|\bnps_form_responses|net promoter\b.{0,40}\bform\b|"
        r"\bnps\b.{0,40}\bfeedback\b|\bfeedback\b.{0,40}\bnps\b",
        q,
        re.I,
    ):
        signals.add("nps_form_responses")
    elif re.search(r"\bfeedback\b|\bemoji\b", q, re.I):
        signals.add("contextual_feedback")
    return signals


def is_compound_domain_question(question: str) -> bool:
    """True when the question spans two+ domains that need different tables."""
    signals = detect_domain_signals(question)
    if len(signals) < 2:
        return False
    key = frozenset(signals)
    if key in _COMPOUND_TABLES:
        return True
    if frozenset({"portal_page_activity", "live_class_attendance"}).issubset(signals):
        return True
    # Any attend + portal-like combo is compound even if wording varies.
    return "live_class_attendance" in signals and "learning_portal" in signals


def compound_domain_table_shorts(question: str) -> tuple[str, ...]:
    """Canonical table short names for a compound-domain question."""
    signals = detect_domain_signals(question)
    key = frozenset(signals)
    if key in _COMPOUND_TABLES:
        return _COMPOUND_TABLES[key]
    if frozenset({"portal_page_activity", "live_class_attendance"}).issubset(signals):
        return _COMPOUND_TABLES[frozenset({"portal_page_activity", "live_class_attendance"})]
    if "live_class_attendance" in signals and "learning_portal" in signals:
        return _COMPOUND_TABLES[frozenset({"live_class_attendance", "learning_portal"})]
    return tuple()


def compound_domain_table_ids(question: str, included: list[Any]) -> list[str]:
    """Resolve compound-domain table shorts to full_table_id values."""
    shorts = {s.lower() for s in compound_domain_table_shorts(question)}
    if not shorts:
        return []
    picked: list[str] = []
    for t in included:
        short = t.full_table_id.rsplit(".", 1)[-1].lower()
        if short in shorts:
            picked.append(t.full_table_id)
    # Preserve canonical order from compound map
    order = [s.lower() for s in compound_domain_table_shorts(question)]
    picked.sort(key=lambda fq: order.index(fq.rsplit(".", 1)[-1].lower()) if fq.rsplit(".", 1)[-1].lower() in order else 99)
    return picked


def match_routing_rule(question: str) -> RoutingRule | None:
    q = normalize_question(question)
    if not q:
        return None
    for rule in ROUTING_RULES:
        if not rule.question_re.search(q):
            continue
        if rule.id == "contextual_feedback" and re.search(r"\bnps\b", q, re.I):
            continue
        return rule
    return None


def pin_table(question: str, included: list[Any]) -> list[str]:
    """Return pinned full_table_id list or []. Skips pin for compound join questions."""
    if is_compound_domain_question(question):
        return []
    rule = match_routing_rule(question)
    if not rule:
        return []
    for t in included:
        short = t.full_table_id.rsplit(".", 1)[-1]
        if short == rule.table_short:
            return [t.full_table_id]
    return []


def routing_reason(question: str, table_short: str) -> str:
    rule = match_routing_rule(question)
    if rule and rule.table_short == table_short:
        return f"Domain table pin: `{table_short}` — {rule.reason}"
    return f"Domain table pin: `{table_short}`"


def score_adjustment(question: str, short_name: str) -> int:
    """Boost/penalty for semantic and keyword scoring."""
    rule = match_routing_rule(question)
    if not rule:
        return 0
    name = short_name.lower()
    delta = 0
    if name == rule.table_short.lower():
        delta += rule.score_boost
    for bad in rule.score_penalty_shorts:
        if bad in name:
            delta -= 400
    return delta


def validate_sql_table_choice(question: str, sql: str) -> tuple[bool, str]:
    """True when SQL uses the canonical table(s) for a domain-routed question."""
    if is_compound_domain_question(question):
        sql_l = (sql or "").lower()
        if not sql_l.strip():
            return False, "empty SQL"
        if not re.search(r"\bJOIN\b", sql_l, re.I):
            return False, "compound domain question requires JOIN SQL"
        for short in compound_domain_table_shorts(question):
            if short.lower() not in sql_l:
                return False, f"expected table `{short}` in join SQL"
        return True, ""

    rule = match_routing_rule(question)
    if not rule:
        return True, ""
    sql_l = (sql or "").lower()
    if not sql_l.strip():
        return False, "empty SQL"
    # NPS topic search unions ongoing + legacy snapshot — not a single-table pin violation.
    if rule.id == "nps_form_responses" and " union all " in sql_l:
        if rule.table_short.lower() in sql_l or "nps_form_responses" in sql_l:
            for bad in rule.score_penalty_shorts:
                if bad in sql_l:
                    return False, f"wrong table fragment `{bad}`"
            return True, ""
    if rule.table_short.lower() not in sql_l:
        return False, f"expected table `{rule.table_short}`"
    for bad in rule.score_penalty_shorts:
        if bad in sql_l:
            return False, f"wrong table fragment `{bad}`"
    return True, ""


def sql_filters_for_table(question: str, table: object) -> list[str]:
    """Apply rule filters when columns exist on the table."""
    from table_business_rules import table_skips_default_filters

    if table_skips_default_filters(table):
        return []

    rule = match_routing_rule(question)
    if not rule:
        return []
    short = getattr(table, "full_table_id", "").rsplit(".", 1)[-1]
    if short != rule.table_short:
        return []

    from semantic_layer import semantic_for_table

    sem = semantic_for_table(table)
    dim_ids = {d.id for d in (sem.dimensions if sem else [])}
    if not dim_ids:
        import json

        try:
            cols = json.loads(getattr(table, "column_descriptions_json", "") or "{}")
            dim_ids = set(cols.keys())
        except (json.JSONDecodeError, TypeError):
            dim_ids = set()

    parts: list[str] = []
    for f in rule.filters:
        if f.column not in dim_ids:
            continue
        sql = f.to_sql()
        if sql:
            parts.append(sql)
    return parts


def compound_sql_hints(question: str) -> str:
    """Schema hints for AI-generated JOIN SQL on compound domain questions."""
    if not is_compound_domain_question(question):
        return ""
    signals = detect_domain_signals(question)
    lines = [
        "# Compound domain question — requires JOIN across canonical tables",
        f"# Detected domains: {', '.join(sorted(signals))}",
        "# Use COUNT(DISTINCT user_id) from the fact/event table unless breakdown requested.",
    ]
    if "live_class_attendance" in signals and "learning_portal" in signals:
        lines.extend(
            [
                "# JOIN pattern:",
                "#   FROM `...z_academy_users_live_classes_attendance_and_time_spent_details` a",
                "#   JOIN `...z_ccbp_academy_users_master_data` m",
                "#     ON REPLACE(a.user_id, '-', '') = m.user_id",
                "# WHERE DATE(a.slot_date) = <asked date>",
                "#   AND a.attendance_status = 'JOINED'",
                "#   AND m.pause_status IS NULL",
                "#   AND m.learning_portal_onboarding_access_given_datetime IS NOT NULL",
            ]
        )
    return "\n".join(lines)
