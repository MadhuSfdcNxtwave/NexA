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
    r"\b("
    r"this|that|it|them|their|these|those|above|previous|last|prior|earlier|"
    r"your answer|the \d+|same\s+(?:ones?|users?|students?|rows?|results?)"
    r")\b",
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
    r"uid|user_?ids?|userids?|ids?|names?|emails?|records?|rows?|details?|breakdown"
    r")\b|"
    r"\b(uid|user_?ids?|userids?|ids?)\b.{0,30}\b(for|of|from)\b.{0,20}"
    r"\b(those|these|them|their|that|the)\b"
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
_KNOWLEDGE = re.compile(
    r"^\s*(what is|what's|what does|what do|define|meaning of|stands for|"
    r"abbreviation for|acronym for|tell me about)\b",
    re.I,
)
_KNOWLEDGE_EXPLAIN = re.compile(
    r"^\s*explain\b(?!\s+(this|that|your|the|why|how did))\b",
    re.I,
)
_METRIC_IN_QUESTION = re.compile(
    r"\b(how many|count|total|average|avg|by month|by gender|trend|show me|"
    r"list|score for|in \d{4}|for \w+ \d{4}|last \d+ days?|this month)\b",
    re.I,
)
_WHICH_DIM = re.compile(
    r"\b(which|what)\s+(activity|activities|page|pages|aspect|aspects|"
    r"feature|features|program|category|categories)\b",
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
    text = re.sub(r"\blearningportal\b", "learning portal", text, flags=re.I)
    text = re.sub(r"\bactivlly\b", "actively", text, flags=re.I)
    text = re.sub(r"\bactivly\b", "actively", text, flags=re.I)
    text = re.sub(r"\battendence\b", "attendance", text, flags=re.I)
    text = re.sub(r"\bhappend\b", "happened", text, flags=re.I)
    return text


def question_wants_breakdown(question: str) -> bool:
    """True when the question needs GROUP BY / per-category results."""
    q = expand_question_abbreviations(question)
    if _BREAK_DOWN_VERB.search(q):
        return True
    if _WHICH_DIM.search(q):
        return True
    if re.search(r"\bwhich\b.{0,40}\b(improv|drove|boosted|helped|highest|lowest|top)\b", q, re.I):
        return True
    if re.search(r"\bby\s+\w+", q, re.I) and _REF_PRIOR.search(q):
        return True
    return bool(_BREAKDOWN.search(q))


def is_knowledge_question(question: str) -> bool:
    """Definition / abbreviation questions — answer from glossary, not SQL."""
    q = expand_question_abbreviations((question or "").strip())
    if not q:
        return False
    if not (_KNOWLEDGE.search(q) or _KNOWLEDGE_EXPLAIN.search(q)):
        return False
    if _METRIC_IN_QUESTION.search(q):
        return False
    if re.search(r"\b(how many|count|total|breakdown|by \w+)\b", q, re.I):
        return False
    return True


def question_needs_deep_analysis(question: str) -> bool:
    """True when the user expects a detailed what/why/how narrative."""
    q = expand_question_abbreviations((question or "").strip())
    if not q:
        return False
    if question_wants_breakdown(q):
        return True
    return bool(
        re.search(
            r"\b(why|how|what happened|explain|improv|compare|drove|caused|"
            r"reason|insight|analyze|analysis)\b",
            q,
            re.I,
        )
    )


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
    q = expand_question_abbreviations((question or "").strip())
    if not q:
        return False
    if _DRILL_DOWN.search(q):
        return True
    # Short follow-ups like «give with user id» / «show their userid»
    if re.search(
        r"\b(give|get|show|list|fetch|provide|return)\b.{0,40}\b"
        r"(uid|user[_\s]?ids?|userids?|ids?)\b",
        q,
        re.I,
    ):
        return True
    if _REF_PRIOR.search(q) and re.search(
        r"\b(uid|user_?ids?|userids?|ids?|names?|emails?|who|which|details?|rows?)\b",
        q,
        re.I,
    ):
        if re.search(r"\b(give|get|show|list|fetch|provide|return)\b", q, re.I):
            return True
    # «their user ids» / «those students' ids» without an explicit verb
    if re.search(
        r"\b(their|those|these|them)\b.{0,20}\b(uid|user_?ids?|userids?)\b",
        q,
        re.I,
    ):
        return True
    return False


_PAGE_NEXT = re.compile(
    r"\b("
    r"next\s+page|show\s+more|more\s+(?:ids?|rows?|results?|users?)|"
    r"load\s+more|continue|another\s+page|following\s+page"
    r")\b",
    re.I,
)
_PAGE_NUM = re.compile(
    r"\b(?:page\s*[=:]?\s*|p(?:g)?\s*)(\d+)\b|"
    r"\b(?:offset\s*[=:]?\s*)(\d+)\b|"
    r"\bnext\s+(\d+)\b",
    re.I,
)


def _drill_page_size(requested: int | None = None) -> int:
    try:
        import config

        default = int(getattr(config, "DRILL_DOWN_PAGE_SIZE", 500) or 500)
        cap = int(getattr(config, "DRILL_DOWN_MAX_PAGE_SIZE", 2000) or 2000)
    except Exception:
        default, cap = 500, 2000
    size = int(requested) if requested and requested > 0 else default
    return max(1, min(size, cap))


def is_list_pagination_request(question: str) -> bool:
    """True for «next page» / «show more» / «page 2» on a prior id list."""
    q = expand_question_abbreviations((question or "").strip())
    if not q:
        return False
    if _PAGE_NEXT.search(q):
        return True
    if _PAGE_NUM.search(q) and (
        re.search(r"\b(page|offset|more|next)\b", q, re.I)
        or len(q.split()) <= 6
    ):
        return True
    return False


def parse_list_page_request(question: str, *, prior_sql: str = "") -> dict:
    """
    Resolve which page of a user-id list to fetch.
    Returns {page, page_size, next_page} (page is 1-based).
    """
    q = expand_question_abbreviations((question or "").strip())
    page_size = _drill_page_size()
    # «next 1000» / explicit size
    m_size = re.search(r"\b(?:next|show|get|fetch)\s+(\d{2,4})\b", q, re.I)
    if m_size:
        page_size = _drill_page_size(int(m_size.group(1)))

    prior_limit, prior_offset = extract_sql_limit_offset(prior_sql)
    if prior_limit:
        page_size = _drill_page_size(prior_limit)

    if _PAGE_NEXT.search(q):
        cur_page = (prior_offset // page_size) + 1 if page_size else 1
        return {"page": cur_page + 1, "page_size": page_size, "next_page": True}

    m = _PAGE_NUM.search(q)
    if m:
        if m.group(1):
            return {"page": max(1, int(m.group(1))), "page_size": page_size, "next_page": False}
        if m.group(2):
            offset = max(0, int(m.group(2)))
            page = (offset // page_size) + 1 if page_size else 1
            return {"page": page, "page_size": page_size, "next_page": False}
        if m.group(3):
            page_size = _drill_page_size(int(m.group(3)))
            cur_page = (prior_offset // page_size) + 1 if page_size else 1
            return {"page": cur_page + 1, "page_size": page_size, "next_page": True}

    return {"page": 1, "page_size": page_size, "next_page": False}


def extract_sql_limit_offset(sql: str) -> tuple[int | None, int]:
    """Return (limit, offset) from a SELECT; offset defaults to 0."""
    text = (sql or "").strip().rstrip(";")
    if not text:
        return None, 0
    # LIMIT n OFFSET m  OR  LIMIT m, n (MySQL-style — rare in BQ)
    m = re.search(
        r"\bLIMIT\s+(\d+)\s+OFFSET\s+(\d+)\s*$",
        text,
        re.I,
    )
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"\bLIMIT\s+(\d+)\s*,\s*(\d+)\s*$", text, re.I)
    if m:
        return int(m.group(2)), int(m.group(1))
    m = re.search(r"\bLIMIT\s+(\d+)\s*$", text, re.I)
    if m:
        return int(m.group(1)), 0
    m_off = re.search(r"\bOFFSET\s+(\d+)\s*$", text, re.I)
    if m_off:
        return None, int(m_off.group(1))
    return None, 0


def is_user_id_list_sql(sql: str) -> bool:
    text = (sql or "").strip()
    if not text:
        return False
    if re.search(r"\bCOUNT\s*\(", text, re.I):
        return False
    return bool(re.search(r"SELECT\s+DISTINCT\s+[`\"]?user_id", text, re.I))


def apply_list_pagination(sql: str, *, page: int = 1, page_size: int | None = None) -> str:
    """Replace/add ORDER BY + LIMIT/OFFSET on a list SELECT."""
    text = (sql or "").strip().rstrip(";")
    if not text:
        return text
    size = _drill_page_size(page_size)
    page = max(1, int(page or 1))
    offset = (page - 1) * size
    # Strip existing ORDER BY / LIMIT / OFFSET tail
    body = re.sub(r"\bORDER\s+BY\b[\s\S]*$", "", text, flags=re.I).strip()
    body = re.sub(r"\bLIMIT\s+\d+(?:\s*,\s*\d+)?(?:\s+OFFSET\s+\d+)?\s*$", "", body, flags=re.I).strip()
    body = re.sub(r"\bOFFSET\s+\d+\s*$", "", body, flags=re.I).strip()
    return (
        f"{body}\nORDER BY `user_id`\nLIMIT {size} OFFSET {offset}"
    )


def expand_drill_down_followup(
    question: str,
    prior_question: str = "",
    prior_sql: str = "",
) -> str:
    """
    Turn «give with user id» into an explicit list request that reuses prior filters.
    """
    q = expand_question_abbreviations((question or "").strip())
    if not is_drill_down_data_request(q) and not is_list_pagination_request(q):
        return q
    if not (prior_sql or "").strip() and not (prior_question or "").strip():
        return q

    page_info = parse_list_page_request(q, prior_sql=prior_sql)
    page = page_info["page"]
    page_size = page_info["page_size"]
    topic = (prior_question or "").strip() or "the previous result"
    parts = [
        f"List distinct user_id for: {topic}.",
        "Reuse the SAME table and WHERE filters as the prior query.",
        "SELECT DISTINCT user_id (not COUNT).",
        f"Paginate with LIMIT {page_size} OFFSET {(page - 1) * page_size} (page {page}).",
    ]
    if prior_sql.strip():
        try:
            refs = {
                m.group(1).rsplit(".", 1)[-1]
                for m in re.finditer(r"`([^`]+)`", prior_sql)
            }
            if refs:
                parts.insert(1, f"Use ONLY table(s): {', '.join(f'`{t}`' for t in sorted(refs))}.")
        except Exception:
            pass
    return " ".join(parts)


def rewrite_aggregate_to_user_list_sql(
    prior_sql: str,
    *,
    page: int = 1,
    page_size: int | None = None,
) -> str | None:
    """
    Convert a prior COUNT(DISTINCT user_id) query into SELECT DISTINCT user_id
    keeping the same FROM/WHERE. Supports LIMIT/OFFSET pagination.
    If prior_sql is already a user_id list, re-page it instead.
    """
    sql = (prior_sql or "").strip().rstrip(";")
    if not sql:
        return None

    size = _drill_page_size(page_size)
    page = max(1, int(page or 1))

    # Already a list query → just change page (next page / page N)
    if is_user_id_list_sql(sql):
        return apply_list_pagination(sql, page=page, page_size=size)

    # Must be a count aggregate (user_id / * / 1) — not already a list
    is_count = bool(
        re.search(
            r"\bCOUNT\s*\(\s*(?:DISTINCT\s+)?[`\"]?user_id[`\"]?\s*\)",
            sql,
            re.I,
        )
        or re.search(r"\bCOUNT\s*\(\s*(?:\*|1)\s*\)", sql, re.I)
        or re.search(r"\bCOUNT\s*\(\s*DISTINCT\s+[^)]+\)", sql, re.I)
    )
    if not is_count:
        return None
    # Multi-dimension GROUP BY aggregates are not safe to rewrite blindly
    if re.search(r"\bGROUP\s+BY\b", sql, re.I):
        return None

    from_m = re.search(r"\bFROM\b", sql, re.I)
    if not from_m:
        return None
    tail = sql[from_m.start() :]
    # Drop trailing ORDER BY / LIMIT from aggregate (we'll add our own LIMIT)
    tail = re.sub(r"\bORDER\s+BY\b[\s\S]*$", "", tail, flags=re.I).strip()
    tail = re.sub(r"\bLIMIT\s+\d+(?:\s+OFFSET\s+\d+)?\s*$", "", tail, flags=re.I).strip()
    offset = (page - 1) * size
    return (
        f"SELECT DISTINCT `user_id`\n{tail}\n"
        f"ORDER BY `user_id`\nLIMIT {size} OFFSET {offset}"
    )


def detect_intent(question: str, *, has_thread_history: bool) -> str:
    """
    Return one of: data_query | explain_prior | assistant | knowledge_query
    """
    q = (question or "").strip()
    if not q:
        return "assistant"

    if _GREETING.search(q) or _HELP.search(q):
        return "assistant"

    expanded = expand_question_abbreviations(q)
    if is_knowledge_question(expanded):
        return "knowledge_query"

    # Breakdown / «break that down by X» always needs fresh GROUP BY SQL.
    if question_wants_breakdown(expanded):
        return "data_query"

    # Drill-downs / list pagination always need fresh SQL, even when referencing prior results.
    if has_thread_history and (
        is_drill_down_data_request(q) or is_list_pagination_request(q)
    ):
        return "data_query"

    if has_thread_history and _EXPLAIN.search(q):
        return "explain_prior"

    if has_thread_history and _REF_PRIOR.search(q) and not _DATA_SIGNAL.search(q):
        return "explain_prior"

    return "data_query"
