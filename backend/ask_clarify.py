"""Detect ambiguous questions and offer clarification options before SQL generation."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import config
import join_graph as jg

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
        catalog.append(
            {
                "full_table_id": k.full_table_id,
                "short_name": k.short_name,
                "description": (k.table_description or "")[:400],
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
