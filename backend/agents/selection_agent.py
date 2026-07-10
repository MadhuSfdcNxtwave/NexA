"""Staged table selection: keyword shortlist → description confirm → columns → rules → shape.

Keywords only shortlist. Final table comes from description match (and domain pins).
Business rules from the Table tab become planner checkpoints for SQL.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from agents.answer_shape import AnswerShape, detect_answer_shape, wants_raw_tabular_data

if TYPE_CHECKING:
    import knowledge_base as kb


# Feedback-like tables that must not be confused with contextual feedback.
_FEEDBACK_CONFUSABLES = (
    "live_classes_user_feedback",
    "feedback_successcoach",
    "batch_registration_form",
    "event_engagement",
    "nps_form",
    "nw_events",
)

_CONTEXTUAL_PHRASE = re.compile(
    r"\bcontextual\s+feedback\b|\bfeedback_details\b|\bemotion\b|\bemoji\s+feedback\b",
    re.I,
)


@dataclass
class SelectionResult:
    selected_full_ids: list[str] = field(default_factory=list)
    route_reason: str = ""
    confidence: str = "medium"  # high | medium | low
    answer_shape: AnswerShape = field(default_factory=AnswerShape)
    kb_columns: dict[str, list[str]] = field(default_factory=dict)
    kb_filters: list[str] = field(default_factory=list)
    kb_measure: str = ""
    rules_block: str = ""
    clarify: dict[str, Any] | None = None


def _short(fq: str) -> str:
    return (fq or "").rsplit(".", 1)[-1]


def _norm_name(name: str) -> str:
    n = (name or "").lower()
    if n.startswith("z_"):
        n = n[2:]
    return n


def _table_by_short(included: list[Any], want: str) -> Any | None:
    want_n = _norm_name(want)
    for t in included:
        short = _short(t.full_table_id)
        if _norm_name(short) == want_n or short.lower() == want.lower():
            return t
    return None


def _description_confirms_contextual(knowledge: Any) -> bool:
    blob = " ".join(
        [
            getattr(knowledge, "short_name", "") or "",
            getattr(knowledge, "table_description", "") or "",
            getattr(knowledge, "ai_overview", "") or "",
            " ".join((getattr(knowledge, "column_descriptions", None) or {}).values()),
        ]
    ).lower()
    short = (getattr(knowledge, "short_name", "") or "").lower()
    return (
        "contextual" in blob
        or "feedback_trigger" in blob
        or "in-app" in blob
        or "emoji" in blob
        or short.endswith("contextual_feedback_details")
        or "users_contextual_feedback" in short
    )


def _pick_columns_for_table(
    knowledge: Any,
    *,
    wants_raw: bool,
) -> list[str]:
    """Prefer described columns; always include useful undescribed text/date cols for raw export."""
    desc = getattr(knowledge, "column_descriptions", None) or {}
    names = list(desc.keys()) if desc else []
    # Prefer semantic / known survey columns even without descriptions.
    preferred_raw = [
        "user_id",
        "feedback_id",
        "feedback_trigger",
        "question_id",
        "question_order",
        "question_type",
        "question_text",
        "user_answer",
        "emoji_rating",
        "submitted_date",
        "enroll_plans_str",
        "is_valid_question",
        "is_valid_trigger",
    ]
    if wants_raw:
        picked: list[str] = []
        name_set = {n.lower() for n in names} | {c.lower() for c in desc}
        for col in preferred_raw:
            if col.lower() in name_set or col in desc:
                picked.append(col)
        if not picked:
            # Descriptions missing — still recommend core survey fields for the SQL planner.
            picked = list(preferred_raw[:12])
        for col, d in desc.items():
            if col in picked:
                continue
            blob = f"{col} {d}".lower()
            if any(k in blob for k in ("text", "answer", "question", "trigger", "comment", "note")):
                picked.append(col)
        return picked[:20]

    # Aggregate path: keyword-matched columns from descriptions.
    scored: list[tuple[int, str]] = []
    for col, d in desc.items():
        blob = f"{col} {d}".lower()
        score = 0
        for token in ("answer", "question", "rating", "user", "date", "trigger"):
            if token in blob:
                score += 2
        if score:
            scored.append((score, col))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [c for _, c in scored[:12]]


def _load_rules(tables: list[Any]) -> str:
    try:
        from table_business_rules import build_mandatory_rules_preamble

        return build_mandatory_rules_preamble(tables)
    except Exception:
        return ""


def _clarify_feedback_tables(
    question: str,
    candidates: list[tuple[str, str]],
) -> dict[str, Any]:
    """Ask which feedback dataset when several look plausible."""
    options = []
    for i, (fq, label) in enumerate(candidates[:4]):
        options.append(
            {
                "id": f"table_{i}",
                "label": label,
                "refined_question": (
                    f"{question.strip()} — Use ONLY the table `{_short(fq)}`."
                ),
            }
        )
    return {
        "clarification_id": "select_feedback_table",
        "prompt": (
            "I found several feedback-related datasets. Which one do you want?"
        ),
        "question": question,
        "options": options,
        "allow_custom": True,
        "reasons": ["Ambiguous feedback table — need user confirmation"],
    }


def run_selection_agent(
    question: str,
    included: list[Any],
    matches: list[Any],
    knowledges: list[Any],
    *,
    prior_sql: str = "",
    user_table_pins: list[str] | None = None,
) -> SelectionResult | None:
    """
    Staged selection. Returns None when the caller should keep existing routing.
    Always returns a result when it can improve contextual-feedback / raw-export paths.
    """
    import knowledge_base as kb
    from question_intent import expand_question_abbreviations
    from table_routing import match_routing_rule, pin_table

    q = expand_question_abbreviations((question or "").strip())
    if not q or not included:
        return None

    shape = detect_answer_shape(q, prior_sql=prior_sql)
    knowledge_by_id = {k.full_table_id: k for k in knowledges}

    # --- Stage 0: user pins ---
    if user_table_pins:
        allowed = {t.full_table_id for t in included}
        ids = [fq for fq in user_table_pins if fq in allowed][:3]
        if ids:
            tables = [t for t in included if t.full_table_id in ids]
            rules = _load_rules(tables)
            cols = {
                ids[0]: _pick_columns_for_table(
                    knowledge_by_id.get(ids[0]) or kb.load_table_knowledge(tables[0]),
                    wants_raw=shape.wants_raw or shape.mode == "auto",
                )
            }
            shape.rules_block = rules
            shape.columns = cols.get(ids[0], [])
            shape.locked_table_ids = ids
            return SelectionResult(
                selected_full_ids=ids,
                route_reason=f"User table pin: `{_short(ids[0])}`",
                confidence="high",
                answer_shape=shape,
                kb_columns=cols,
                rules_block=rules,
            )

    # --- Stage 1: keyword shortlist (top matches already scored by caller) ---
    shortlist = list(matches[:8]) if matches else []

    # --- Stage 2: description confirm for contextual feedback ---
    if _CONTEXTUAL_PHRASE.search(q) or (
        re.search(r"\bfeedback\b", q, re.I)
        and not re.search(r"\bnps\b|live\s+class|success\s*coach", q, re.I)
    ):
        # Prefer canonical contextual table via description, not keyword alone.
        ctx = _table_by_short(included, "users_contextual_feedback_details")
        if ctx is None:
            ctx = _table_by_short(included, "z_users_contextual_feedback_details")
        if ctx is not None:
            k = knowledge_by_id.get(ctx.full_table_id) or kb.load_table_knowledge(ctx)
            if _description_confirms_contextual(k) or _CONTEXTUAL_PHRASE.search(q):
                # If shortlist top is a confusable and question says contextual — override.
                top = shortlist[0] if shortlist else None
                top_short = (getattr(top, "short_name", "") or "").lower() if top else ""
                if any(bad in top_short for bad in _FEEDBACK_CONFUSABLES) or _CONTEXTUAL_PHRASE.search(
                    q
                ):
                    wants_raw = shape.wants_raw or wants_raw_tabular_data(q) or (
                        "details" in q.lower() and "how many" not in q.lower()
                    )
                    if wants_raw:
                        shape.mode = "raw"
                        shape.reason = shape.reason or "Contextual feedback details → raw rows"
                    rules = _load_rules([ctx])
                    cols = {
                        ctx.full_table_id: _pick_columns_for_table(k, wants_raw=wants_raw)
                    }
                    shape.rules_block = rules
                    shape.columns = cols[ctx.full_table_id]
                    shape.locked_table_ids = [ctx.full_table_id]
                    # Ambiguity: only clarify when question is bare "feedback" with no contextual cue
                    # and top confusable is close in score — skip clarify for explicit contextual.
                    return SelectionResult(
                        selected_full_ids=[ctx.full_table_id],
                        route_reason=(
                            "Description confirm: contextual feedback → "
                            f"`{_short(ctx.full_table_id)}` (Business rules as checkpoints)"
                        ),
                        confidence="high",
                        answer_shape=shape,
                        kb_columns=cols,
                        kb_filters=["current month on submitted_date"]
                        if re.search(r"\b(this|current)\s+month\b", q, re.I)
                        else [],
                        rules_block=rules,
                    )

    # Domain pin with alias-aware resolve (also done in table_routing; reinforce here).
    pinned = pin_table(q, included)
    if pinned:
        tables = [t for t in included if t.full_table_id in pinned]
        rules = _load_rules(tables)
        k = knowledge_by_id.get(pinned[0])
        if k is None and tables:
            k = kb.load_table_knowledge(tables[0])
        wants_raw = shape.wants_raw or wants_raw_tabular_data(q)
        if wants_raw:
            shape.mode = "raw"
        cols = {
            pinned[0]: _pick_columns_for_table(k, wants_raw=wants_raw)
            if k
            else []
        }
        shape.rules_block = rules
        shape.columns = cols.get(pinned[0], [])
        shape.locked_table_ids = pinned
        rule = match_routing_rule(q)
        reason = (
            f"Domain pin + description: `{_short(pinned[0])}`"
            + (f" — {rule.reason}" if rule else "")
        )
        return SelectionResult(
            selected_full_ids=pinned,
            route_reason=reason,
            confidence="high",
            answer_shape=shape,
            kb_columns=cols,
            rules_block=rules,
        )

    # Low-confidence feedback ambiguity → clarify
    feedbackish = [
        m
        for m in shortlist[:5]
        if any(
            x in (getattr(m, "short_name", "") or "").lower()
            for x in ("feedback", "nps", "survey", "emoji")
        )
    ]
    if len(feedbackish) >= 2 and re.search(r"\bfeedback\b", q, re.I) and not _CONTEXTUAL_PHRASE.search(
        q
    ):
        cands = [
            (
                m.full_table_id,
                f"{m.short_name}: {(getattr(m, 'table_description', '') or '')[:80]}",
            )
            for m in feedbackish
        ]
        return SelectionResult(
            selected_full_ids=[],
            route_reason="Ambiguous feedback tables — asking user",
            confidence="low",
            answer_shape=shape,
            clarify=_clarify_feedback_tables(q, cands),
        )

    # Enrich shape only (no table override)
    shape.rules_block = ""
    return SelectionResult(
        selected_full_ids=[],
        route_reason="",
        confidence="medium",
        answer_shape=shape,
    )


def get_selection_agent():
    """Singleton-style accessor for pipeline bridge."""
    return run_selection_agent
