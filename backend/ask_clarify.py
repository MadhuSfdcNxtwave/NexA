"""Detect ambiguous questions and offer clarification options before SQL generation."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import config
import join_graph as jg
import knowledge_base as kb

_AMBIGUOUS = re.compile(
    r"\b("
    r"active|engaged|engagement|valuable|recent|update|conversion|retention|"
    r"churn|qualified|eligible|completed|success|performance|usage|popular"
    r")\b",
    re.I,
)
_JOIN_INTENT = re.compile(
    r"\b(join|combined|across|with their|along with|together with|per user|by user)\b",
    re.I,
)
_COUNT = re.compile(r"\b(how many|count|number of|total)\b", re.I)
# Clear domain questions — schema + overview already define the dimension.
_CLEAR_DATA_QUESTION = re.compile(
    r"("
    r"growth\s*cycle.{0,30}\b(student|user|learner)s?\b|"
    r"\b(student|user|learner)s?.{0,30}(growth\s*cycle|gc\b)|"
    r"\b(gender|coach|retention|language).*(wise|by|per|breakdown|count)|"
    r"(wise|by|per|breakdown).*\b(gender|coach|retention|language)\b|"
    r"\bnps\b.{0,50}\bby\b|\baverage.{0,30}\bnps\b.{0,40}\bby\b|"
    r"\baverage.{0,25}\brating\b.{0,40}\bby\b|"
    r"\bmonthly nps\b|\bnps survey\b|\bnps rating\b|\bnps response"
    r")",
    re.I,
)
# Raw table / schema identifiers — never show these in user-facing options.
_RAW_TABLE_TOKEN = re.compile(
    r"\b("
    r"academy_nps|nps_form|z_ccbp|z_users|form_responses|"
    r"use\s+[a-z0-9_]{6,}|from\s+table|database\s+table|"
    r"_[a-z0-9_]{10,}"
    r")\b",
    re.I,
)
_TABLE_PICK_REASON = re.compile(r"^multiple_tables_compete", re.I)
_PORTAL_ACTIVITY = re.compile(
    r"\b(?:learning[\s_-]*portal|learningportal|portal)\b.{0,60}\b(activity|activit|page|events?|which)\b|"
    r"\b(which|what)\b.{0,40}\b(activity|page)\b|"
    r"\bin which activity\b|"
    r"\bactiv(?:e|ly|lly)\s+in\b.{0,40}\b(?:learning[\s_-]*portal|learningportal|portal)\b|"
    r"\bevents?\b.{0,40}\b(?:learning[\s_-]*portal|learningportal|portal)\b",
    re.I,
)
_EVENT_ENGAGEMENT = re.compile(r"event_engagement|nw_events", re.I)
_PORTAL_PAGE = re.compile(r"day_and_page|time_spent_page", re.I)


def should_skip_clarification(
    question: str,
    *,
    matched_entity_label: str = "",
    wants_breakdown: bool = False,
) -> bool:
    """Skip clarification when the question already names a clear dimension + metric."""
    from question_intent import expand_question_abbreviations, question_wants_breakdown

    q = expand_question_abbreviations(question)
    wb = wants_breakdown or question_wants_breakdown(q)
    # Canonical domain pins — never ask the user to pick among wrong tables.
    if re.search(r"\battend", q, re.I) and re.search(r"\blive[\s_-]*class", q, re.I):
        return True
    if re.search(r"\bactive\b", q, re.I) and re.search(r"\bplatform\b", q, re.I):
        return True
    if re.search(r"\bactive\b", q, re.I) and re.search(
        r"\blearning[\s_-]*portal|\bportal\b", q, re.I
    ):
        # "Which activity on portal" needs disambiguation — don't auto-skip.
        from question_intent import question_wants_breakdown

        if not (_PORTAL_ACTIVITY.search(q) or question_wants_breakdown(q)):
            return True
    if re.search(r"\bfeedback\b|\bemoji\b", q, re.I):
        return True
    if _CLEAR_DATA_QUESTION.search(q):
        return True
    if matched_entity_label and (wb or _COUNT.search(q)):
        return True
    if re.search(r"\bgrowth\s*cycle\b", q, re.I) and re.search(
        r"\b(student|user|learner|count)\b", q, re.I
    ):
        return True
    return False


def _is_nps_or_clear_question(question: str) -> bool:
    from nps_sql import is_nps_analytics_question
    from question_intent import expand_question_abbreviations

    q = expand_question_abbreviations(question)
    return is_nps_analytics_question(question) or bool(_CLEAR_DATA_QUESTION.search(q))


def _reasons_need_user_input(reasons: list[str]) -> bool:
    """Table competition alone is resolved automatically — never ask the user."""
    substantive = [r for r in reasons if not _TABLE_PICK_REASON.match(r)]
    return bool(substantive)


def _option_mentions_table_name(text: str, *, table_names: set[str] | None = None) -> bool:
    blob = (text or "").strip()
    if not blob:
        return False
    if _RAW_TABLE_TOKEN.search(blob):
        return True
    if table_names:
        low = blob.lower()
        hits = sum(1 for t in table_names if t in low)
        if hits >= 1 and re.search(r"\buse\b|\btable\b|\bfrom\b", low):
            return True
    return False


def _sanitize_clarification_options(
    options: list[dict],
    *,
    table_names: set[str] | None = None,
) -> list[dict]:
    """Drop options that ask users to pick raw tables."""
    clean: list[dict] = []
    for opt in options:
        label = str(opt.get("label") or "").strip()
        refined = str(opt.get("refined_question") or "").strip()
        if _option_mentions_table_name(label, table_names=table_names):
            continue
        if _option_mentions_table_name(refined, table_names=table_names):
            continue
        if label and refined:
            clean.append(opt)
    return clean


def _prompt_mentions_tables(prompt: str) -> bool:
    return bool(_RAW_TABLE_TOKEN.search(prompt or ""))


def _clarification_id(question: str, options: list[dict]) -> str:
    blob = question.strip().lower() + "|" + "|".join(o.get("id", "") for o in options)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _close_table_alternatives(matches: list[Any], *, ratio: float = 0.65) -> list[Any]:
    if len(matches) < 2:
        return []
    top = matches[0].score
    if top <= 0:
        return []
    alts = [m for m in matches[1:4] if m.score >= top * ratio and m.score >= 6]
    return alts if alts else []


def _heuristic_reasons(
    question: str,
    matches: list[Any],
    selected_ids: list[str],
    join_relations: list,
    *,
    error_detail: str = "",
) -> list[str]:
    reasons: list[str] = []
    q = question.strip()
    if _AMBIGUOUS.search(q):
        reasons.append("ambiguous_metric_or_definition")
    alts = _close_table_alternatives(matches)
    if alts and not _is_nps_or_clear_question(q):
        names = ", ".join(f"`{a.short_name}`" for a in alts[:2])
        reasons.append(f"multiple_tables_compete:{names}")
    if len(selected_ids) > 1 and not join_relations:
        if _JOIN_INTENT.search(q):
            reasons.append("multi_table_without_join_hints")
    if _JOIN_INTENT.search(q) and len(selected_ids) < 2:
        reasons.append("join_requested_but_single_table")
    if error_detail and ("validation" in error_detail.lower() or "could not generate" in error_detail.lower()):
        reasons.append("sql_generation_failed")
    return reasons


def apply_clarification(
    question: str,
    *,
    refined_question: str | None = None,
    clarification_choice: str | None = None,
    clarification_text: str | None = None,
    options: list[dict] | None = None,
) -> str:
    """Merge user clarification into the effective question for SQL generation."""
    if refined_question and refined_question.strip():
        return refined_question.strip()
    if clarification_choice and options:
        for opt in options:
            if opt.get("id") == clarification_choice and opt.get("refined_question"):
                base = opt["refined_question"].strip()
                if clarification_text and clarification_text.strip():
                    return f"{base} ({clarification_text.strip()})"
                return base
    if clarification_text and clarification_text.strip():
        return f"{question.strip()} — User clarification: {clarification_text.strip()}"
    return question.strip()


def _portal_activity_options(question: str) -> list[dict[str, str]]:
    return [
        {
            "id": "portal_pages",
            "label": "Portal pages students use (time spent per page)",
            "refined_question": (
                "Which learning portal pages are students actively using — "
                "break down by page with active user counts"
            ),
        },
        {
            "id": "portal_events",
            "label": "Events / webinars in the learning portal",
            "refined_question": (
                "Which events or webinars are students engaging with in the learning portal"
            ),
        },
        {
            "id": "portal_attendance",
            "label": "Portal activity + live class attendance %",
            "refined_question": (
                "Learning portal page activity and live class attendance percentage by page"
            ),
        },
    ]


def _which_dimension_options(question: str, *, context: str = "") -> list[dict[str, str]]:
    opts: list[dict[str, str]] = []
    q = (question or "").lower()
    if re.search(r"\bnps\b|score|rating", q):
        opts.append(
            {
                "id": "nps_aspect",
                "label": "Program aspects that drove higher NPS scores",
                "refined_question": (
                    "Which program aspects have the highest average NPS rating "
                    "from form responses"
                ),
            }
        )
    if re.search(r"\bactivity|page|portal", q):
        opts.extend(_portal_activity_options(question)[:2])
    if not opts:
        opts = [
            {
                "id": "breakdown",
                "label": "Break down by category (GROUP BY)",
                "refined_question": f"{question.strip()} — show breakdown by category, not total count",
            },
            {
                "id": "total",
                "label": "Total count only",
                "refined_question": f"How many total records match: {question.strip()}",
            },
        ]
    return opts[:4]


def build_intent_clarification(
    question: str,
    *,
    reason: str = "",
    sql: str = "",
    selected_table_shorts: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any] | None:
    """
    Ask the user to confirm intent before returning wrong data.
    Returns clarification payload or None when intent is clear enough.
    """
    if not config.ASK_CLARIFICATION_ENABLED:
        return None

    from question_intent import expand_question_abbreviations, question_wants_breakdown

    q = expand_question_abbreviations((question or "").strip())
    if not q:
        return None

    shorts = [s.lower() for s in (selected_table_shorts or [])]
    options: list[dict[str, str]] = []
    prompt = ""

    # Portal activity routed to event engagement — wrong table for page breakdown.
    if _PORTAL_ACTIVITY.search(q):
        on_events = any(_EVENT_ENGAGEMENT.search(s) for s in shorts)
        on_pages = any(_PORTAL_PAGE.search(s) for s in shorts)
        wants_breakdown = question_wants_breakdown(q)
        if wants_breakdown and on_events and not on_pages:
            options = _portal_activity_options(q)
            prompt = (
                "Your question asks which activity on the learning portal. "
                "That can mean portal pages students use, or events/webinars they attended. "
                "Which did you mean?"
            )
        elif wants_breakdown and not on_pages and not on_events:
            options = _portal_activity_options(q)
            prompt = (
                "I want to make sure I answer the right question. "
                "Are you asking about portal pages, events, or both with attendance?"
            )

    # SQL is a scalar count but question asks which/what dimension.
    if not options and reason:
        low = reason.lower()
        if "group by" in low or "which" in low or "breakdown" in low:
            options = _which_dimension_options(q, context=reason)
            prompt = (
                "Your question asks for a breakdown, but I only have a total count. "
                "Which interpretation should I use?"
            )

    if not options and force and reason:
        options = _which_dimension_options(q, context=reason)
        prompt = (
            "I'm not confident I understood your question correctly. "
            "Which of these matches what you're looking for?"
        )

    if len(options) < 2:
        return None

    cid = _clarification_id(question, options)
    return {
        "clarification_id": cid,
        "prompt": prompt or "Which interpretation should I use?",
        "question": question,
        "options": options[:4],
        "allow_custom": True,
        "reasons": [reason] if reason else ["intent_ambiguous"],
        "confirm_mode": True,
    }


def should_clarify_before_sql(
    question: str,
    *,
    selected_table_shorts: list[str] | None = None,
    has_clarification: bool = False,
) -> dict[str, Any] | None:
    """Pre-SQL gate — clarify ambiguous intent instead of guessing."""
    if has_clarification:
        return None
    from question_intent import expand_question_abbreviations, question_wants_breakdown

    q = expand_question_abbreviations((question or "").strip())
    if not q:
        return None

    if _PORTAL_ACTIVITY.search(q) and question_wants_breakdown(q):
        shorts = [s.lower() for s in (selected_table_shorts or [])]
        on_events = any(_EVENT_ENGAGEMENT.search(s) for s in shorts)
        on_pages = any(_PORTAL_PAGE.search(s) for s in shorts)
        # Wrong table selected, or no page table available — ask before running SQL.
        if on_events and not on_pages:
            return build_intent_clarification(
                question,
                reason="portal_activity_wrong_table",
                selected_table_shorts=selected_table_shorts,
            )
        if not on_pages:
            return build_intent_clarification(
                question,
                reason="portal_activity_ambiguous",
                selected_table_shorts=selected_table_shorts,
            )
    return None


def clarification_from_discovery(
    question: str,
    discovery_rows: list[dict],
) -> dict[str, Any] | None:
    """Build clarification options from real survey prompts in BigQuery."""
    if not discovery_rows:
        return None
    options: list[dict[str, str]] = []
    for i, row in enumerate(discovery_rows[:4]):
        qt = str(row.get("question_text") or "").strip()
        if not qt:
            continue
        n = int(row.get("response_count") or row.get("n") or 0)
        qtype = str(row.get("question_type") or "").strip()
        label = qt if len(qt) <= 90 else qt[:87] + "…"
        if n:
            label = f"{label} ({n} responses)"
        if qtype:
            label = f"[{qtype}] {label}"
        options.append(
            {
                "id": chr(97 + i),
                "label": label,
                "refined_question": (
                    f"For survey question: {qt} — show each user_answer and count of responses, "
                    "ordered by most common."
                ),
            }
        )
    if len(options) < 2:
        return None
    cid = _clarification_id(question, options)
    return {
        "clarification_id": cid,
        "prompt": (
            "That exact survey wording is not stored in BigQuery. "
            "Which question did you mean?"
        ),
        "question": question,
        "options": options,
        "allow_custom": True,
        "reasons": ["survey_prompt_not_found"],
    }


def build_clarification(
    question: str,
    *,
    matches: list[Any],
    selected_ids: list[str],
    knowledges: list,
    join_relations: list,
    schema_excerpt: str = "",
    error_detail: str = "",
    force: bool = False,
    matched_entity_label: str = "",
    wants_breakdown: bool = False,
) -> dict[str, Any] | None:
    """Return clarification payload or None when confident enough to proceed."""
    if not config.ASK_CLARIFICATION_ENABLED:
        return None

    # Clear NPS / breakdown questions never need table-picking — auto-resolve instead.
    if should_skip_clarification(
        question,
        matched_entity_label=matched_entity_label,
        wants_breakdown=wants_breakdown,
    ):
        return None

    if _is_nps_or_clear_question(question):
        return None

    reasons = _heuristic_reasons(
        question,
        matches,
        selected_ids,
        join_relations,
        error_detail=error_detail,
    )
    if not _reasons_need_user_input(reasons):
        return None
    if not reasons and not force:
        return None

    import llm

    catalog = []
    table_names: set[str] = set()
    for k in knowledges:
        table_names.add(k.short_name.lower())
        summary, guidance = kb.split_table_description(k.table_description or "")
        catalog.append(
            {
                "full_table_id": k.full_table_id,
                "short_name": k.short_name,
                "description": (summary or k.table_description or "")[:600],
                "guidance": (guidance or k.operational_guidance or "")[:1500],
                "profile": (k.ai_overview or "")[:1200],
                "selected": k.full_table_id in selected_ids,
            }
        )
    alts = _close_table_alternatives(matches)
    join_block = jg.build_join_knowledge_block(join_relations, {}) if join_relations else ""

    result = llm.build_clarification_options(
        question,
        catalog=catalog,
        schema_excerpt=schema_excerpt,
        reasons=reasons,
        join_block=join_block,
        error_detail=error_detail[:1500],
        alternative_tables=[a.short_name for a in alts],
    )
    if not result.get("needs_clarification"):
        return None

    options = _sanitize_clarification_options(
        result.get("options") or [],
        table_names=table_names,
    )
    if len(options) < 2:
        return None

    prompt = str(result.get("prompt") or "Which interpretation should I use?").strip()
    if _prompt_mentions_tables(prompt):
        prompt = "Which interpretation should I use?"
    cid = _clarification_id(question, options)
    return {
        "clarification_id": cid,
        "prompt": prompt,
        "question": question,
        "options": options[:4],
        "allow_custom": bool(result.get("allow_custom", True)),
        "reasons": reasons,
    }
