"""Route natural-language questions: data query vs explain prior vs general assistant."""
from __future__ import annotations

import re

_EXPLAIN = re.compile(
    r"\b("
    r"why did you|why you|why was|why is|why are|what changed|"
    r"different (data|result|answer|number|value)|changed context|"
    r"wrong (data|answer|result|number)|explain (this|that|your|the)|"
    r"how did you (get|calculate|compute|find)|previous answer|last answer|"
    r"what do you mean|does that mean|interpret|you (said|showed|returned)|"
    r"not what i asked|that's not|that is not|confused about"
    r")\b",
    re.I,
)
_REF_PRIOR = re.compile(
    r"\b(this|that|it|them|these|those|above|previous|last|prior|earlier|your answer|the \d+)\b",
    re.I,
)
_DATA_SIGNAL = re.compile(
    r"\b("
    r"what is|how many|how much|show|list|count|total|average|sum|breakdown|break down|"
    r"compare|trend|monthly|weekly|daily|nps|rating|score|promoter|detractor|"
    r"filter|group by|top \d+|bottom \d+|percentage|percent|"
    r"give|get|fetch|pull|provide|display|return|uid|user_id|user id|ids?\b|who|which"
    r")\b",
    re.I,
)
# Drill-down: user wants row-level detail or new columns for a prior aggregate.
_DRILL_DOWN = re.compile(
    r"\b("
    r"(give|get|show|list|fetch|pull|provide|display|return)\s+.{0,40}\b("
    r"uid|user_id|user ids?|ids?|names?|emails?|records?|rows?|details?|breakdown"
    r")\b|"
    r"\b(uid|user_id|user ids?|ids?)\b.{0,30}\b(for|of|from)\b.{0,20}\b(those|these|them|that|the)\b"
    r")\b",
    re.I,
)
_GREETING = re.compile(
    r"^(hi|hello|hey|thanks|thank you|good (morning|afternoon|evening))\b",
    re.I,
)
_HELP = re.compile(
    r"\b(what can you do|how do i use|help me use|how does this work)\b",
    re.I,
)
_BREAKDOWN = re.compile(
    r"\b("
    r"by|per|wise|breakdown|break down|grouped|each|split|distribution|"
    r"cycle wise|growth cycle wise|by growth cycle|per growth cycle|"
    r"category|segment|gender|state|city|coach"
    r")\b",
    re.I,
)
_BREAK_DOWN_VERB = re.compile(r"\bbreak\s+(?:it|that|this|them)?\s*down\b", re.I)
_GROWTH_CYCLE = re.compile(
    r"\b(growth\s*cycles?|growth\s*cycle\s*count)\b",
    re.I,
)
_GC_ABBREV = re.compile(r"\bgcs?\b", re.I)


def normalize_question_markup(question: str) -> str:
    """Strip backticks from clarification/UI text so routing sees plain words."""
    text = (question or "").strip()
    return re.sub(r"`([^`]+)`", r"\1", text)


def expand_question_abbreviations(question: str) -> str:
    """Expand domain abbreviations so routing/SQL see full terms (e.g. Gc → growth cycle)."""
    text = normalize_question_markup(question)
    if not text:
        return text
    text = re.sub(r"\b[Gg][Cc]s\b", "growth cycles", text)
    text = re.sub(r"\b[Gg][Cc]\b", "growth cycles", text)
    text = re.sub(r"\bpotal\b", "portal", text, flags=re.I)
    return text


def question_wants_breakdown(question: str) -> bool:
    """True when the question needs GROUP BY / per-category results."""
    q = expand_question_abbreviations(question)
    if _BREAK_DOWN_VERB.search(q):
        return True
    if re.search(r"\bby\s+\w+", q, re.I) and _REF_PRIOR.search(q):
        return True
    return bool(_BREAKDOWN.search(q))


def question_is_breakdown_followup(
    question: str,
    *,
    prior_question: str = "",
    prior_sql: str = "",
) -> bool:
    """
    True only when the user explicitly continues a prior query (not fresh «by company»).
    Requires prior thread context plus a referential phrase or «break down» wording.
    """
    q = expand_question_abbreviations((question or "").strip())
    if not q:
        return False
    if not (prior_sql or "").strip() and not (prior_question or "").strip():
        return False
    if _BREAK_DOWN_VERB.search(q):
        return True
    if _REF_PRIOR.search(q) and re.search(r"\bby\s+\w+", q, re.I):
        return True
    if _REF_PRIOR.search(q) and is_drill_down_data_request(q):
        return True
    return False


def expand_breakdown_followup(
    question: str,
    prior_question: str = "",
    prior_sql: str = "",
) -> str:
    """
    Turn «break that down by gender» into an explicit SQL-ready question
    that reuses the prior query's filters.
    """
    q = expand_question_abbreviations((question or "").strip())
    if not question_wants_breakdown(q):
        return q
    if not _REF_PRIOR.search(q) and not _BREAK_DOWN_VERB.search(q):
        return q

    by = re.search(r"\bby\s+([a-z_][a-z0-9_\s]{0,24})\b", q, re.I)
    dim = (by.group(1).strip() if by else "").rstrip("?.!")
    if not dim and re.search(r"\bgender\b", q, re.I):
        dim = "gender"
    # Ignore bogus dimensions that repeat the metric/time scope, not a category.
    if dim and re.search(
        r"\b(yesterday|today|users?|active users?|platform|metric|count)\b",
        dim,
        re.I,
    ):
        dim = ""

    topic = prior_question.strip() or "the previous metric"
    if prior_sql.strip():
        try:
            from ask_plan import _tables_from_prior_sql

            # prior_sql may reference tables not in included; names still help the model.
            refs = {
                m.group(1).rsplit(".", 1)[-1]
                for m in re.finditer(r"`([^`]+)`", prior_sql)
            }
            if refs:
                parts_table = f"Use ONLY table(s): {', '.join(f'`{t}`' for t in sorted(refs))}."
            else:
                parts_table = ""
        except Exception:
            parts_table = ""
    else:
        parts_table = ""

    if dim:
        breakdown = f"by {dim}"
    else:
        breakdown = (
            "by the most relevant categorical dimension available in the same table "
            "(e.g. retention bucket, growth cycle, gender, state, product)"
        )
    parts = [
        f"Break down «{topic}» {breakdown}.",
    ]
    if parts_table:
        parts.append(parts_table)
    parts.extend([
        "Use the SAME table and WHERE filters as the prior query.",
        "Return GROUP BY with COUNT(DISTINCT user_id) per category.",
    ])
    if prior_sql.strip() and "pause_status" in prior_sql.lower():
        parts.append("Keep pause_status IS NULL if the prior query used active users.")
    return " ".join(parts)


def question_asks_growth_cycle_count(question: str) -> bool:
    """Scalar count of distinct growth cycles — not a per-cycle breakdown."""
    q = expand_question_abbreviations(question)
    if not q:
        return False
    if _GROWTH_CYCLE.search(q) or _GC_ABBREV.search(q.lower()):
        if question_wants_breakdown(q):
            return False
        if re.search(r"\b(how many|count|number of|total|distinct)\b", q, re.I):
            return True
        if re.search(r"\bhow many\b.+\b(there|exist|available)\b", q, re.I):
            return True
    return False


def is_drill_down_data_request(question: str) -> bool:
    """Follow-up that needs new SQL (e.g. 'give uid for those 6'), not cache/explain."""
    q = (question or "").strip()
    if not q:
        return False
    if _DRILL_DOWN.search(q):
        return True
    if _REF_PRIOR.search(q) and re.search(
        r"\b(uid|user_id|user ids?|ids?|names?|emails?|who|which|details?|rows?)\b",
        q,
        re.I,
    ):
        if re.search(r"\b(give|get|show|list|fetch|provide)\b", q, re.I):
            return True
    return False


def detect_intent(question: str, *, has_thread_history: bool) -> str:
    """
    Return one of: data_query | explain_prior | assistant
    """
    q = (question or "").strip()
    if not q:
        return "assistant"

    if _GREETING.search(q) or _HELP.search(q):
        return "assistant"

    # Breakdown / «break that down by X» always needs fresh GROUP BY SQL.
    if question_wants_breakdown(q):
        return "data_query"

    # Drill-downs always need fresh SQL, even when referencing prior results.
    if has_thread_history and is_drill_down_data_request(q):
        return "data_query"

    if has_thread_history and _EXPLAIN.search(q):
        return "explain_prior"

    if has_thread_history and _REF_PRIOR.search(q) and not _DATA_SIGNAL.search(q):
        return "explain_prior"

    return "data_query"
