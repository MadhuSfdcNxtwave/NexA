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
    # Common typo: «give there names» → «give their names»
    text = re.sub(
        r"\bthere\s+(names?|user\s*ids?|uids?|ids?)\b",
        r"their \1",
        text,
        flags=re.I,
    )
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
    # «give their names and uid» / «names of those who attended»
    if question_wants_user_names(q) and re.search(
        r"\b(uid|user[_\s]?ids?|userids?|ids?|attended|students?|users?|who)\b",
        q,
        re.I,
    ):
        if re.search(r"\b(give|get|show|list|fetch|provide|return)\b", q, re.I) or _REF_PRIOR.search(
            q
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


def question_wants_user_names(question: str) -> bool:
    """True when the user asked for person names along with ids."""
    q = expand_question_abbreviations((question or "").strip())
    if not q:
        return False
    return bool(
        re.search(
            r"\b("
            r"names?|first[_\s]?names?|last[_\s]?names?|full[_\s]?names?|"
            r"student[_\s]?names?|user[_\s]?names?|display[_\s]?names?"
            r")\b",
            q,
            re.I,
        )
    )


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
    if re.search(r"SELECT\s+DISTINCT\s+[`\"]?user_id", text, re.I):
        return True
    # Name-enriched list: SELECT DISTINCT a.user_id, … user_name …
    if re.search(r"SELECT\s+DISTINCT\b", text, re.I) and re.search(
        r"[`\"]?user_id[`\"]?", text, re.I
    ):
        if re.search(r"[`\"]?(user_name|first_name|last_name|full_name)[`\"]?", text, re.I):
            return True
    return False


def _pick_name_columns(cols: set[str] | list[str]) -> list[str]:
    lower_map = {str(c).lower(): str(c) for c in (cols or [])}
    if "first_name" in lower_map and "last_name" in lower_map:
        return [lower_map["first_name"], lower_map["last_name"]]
    for key in (
        "full_name",
        "user_name",
        "student_name",
        "display_name",
        "name",
        "student_name__c",
    ):
        if key in lower_map:
            return [lower_map[key]]
    return []


def resolve_user_name_source(
    fact_fq: str,
    columns_by_table: dict[str, set[str]] | None,
    included_tables: list | None = None,
) -> tuple[str | None, list[str], str]:
    """
    Find where to read person names.
    Returns (join_table_fq or None, name_columns, join_on_sql).
    join_table_fq is None when names exist on the fact table itself.
    """
    columns_by_table = columns_by_table or {}
    fact_cols = columns_by_table.get(fact_fq) or set()
    local = _pick_name_columns(fact_cols)
    if local:
        return None, local, ""

    # Prefer profile basic details (has first_name / last_name), then master, then any.
    preferred = (
        "academy_user_profile_basic_details",
        "z_ccbp_academy_users_master_data",
    )
    candidates: list[tuple[str, set[str], str]] = []
    for t in included_tables or []:
        fq = getattr(t, "full_table_id", "") or ""
        if not fq or fq == fact_fq:
            continue
        cols = columns_by_table.get(fq) or set()
        names = _pick_name_columns(cols)
        if not names:
            continue
        short = fq.rsplit(".", 1)[-1].lower()
        # Master user_id is typically unhyphenated; profile/attendance use UUID hyphens.
        if "master_data" in short:
            on_sql = "REPLACE(s.`user_id`, '-', '') = p.`user_id`"
        else:
            on_sql = "s.`user_id` = p.`user_id`"
        candidates.append((fq, set(names), on_sql))

    def _rank(item: tuple[str, set[str], str]) -> tuple[int, str]:
        short = item[0].rsplit(".", 1)[-1].lower()
        for i, pref in enumerate(preferred):
            if pref in short:
                return (i, short)
        return (len(preferred), short)

    if not candidates:
        # Fall back to known profile FQ even if columns map is incomplete.
        for t in included_tables or []:
            fq = getattr(t, "full_table_id", "") or ""
            short = fq.rsplit(".", 1)[-1].lower()
            if "academy_user_profile_basic_details" in short:
                return fq, ["first_name", "last_name"], "s.`user_id` = p.`user_id`"
            if "master_data" in short:
                return (
                    fq,
                    ["first_name", "last_name"],
                    "REPLACE(s.`user_id`, '-', '') = p.`user_id`",
                )
        return None, [], ""

    candidates.sort(key=_rank)
    fq, names, on_sql = candidates[0]
    return fq, sorted(names), on_sql


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
    # Prefer ordering by aliased user_id when present
    order_col = "`user_id`"
    if re.search(r"\bs\.`user_id`|\ba\.`user_id`", body, re.I):
        order_col = "s.`user_id`" if "s.`user_id`" in body or "s.user_id" in body.lower() else order_col
    if re.search(r"\bAS\s+s\b|\bFROM\s*\(", body, re.I) and re.search(
        r"s\.`user_id`|s\.user_id", body, re.I
    ):
        order_col = "s.`user_id`"
    return f"{body}\nORDER BY {order_col}\nLIMIT {size} OFFSET {offset}"


def _wrap_user_list_with_names(
    user_list_sql: str,
    *,
    name_table_fq: str,
    name_columns: list[str],
    join_on: str,
    page: int = 1,
    page_size: int | None = None,
) -> str:
    """Wrap a DISTINCT user_id SELECT with a LEFT JOIN to the name table."""
    inner = (user_list_sql or "").strip().rstrip(";")
    # Ensure inner is only user_id list (strip pagination; outer will paginate)
    inner = re.sub(r"\bORDER\s+BY\b[\s\S]*$", "", inner, flags=re.I).strip()
    inner = re.sub(r"\bLIMIT\s+\d+(?:\s+OFFSET\s+\d+)?\s*$", "", inner, flags=re.I).strip()
    size = _drill_page_size(page_size)
    page = max(1, int(page or 1))
    offset = (page - 1) * size

    select_bits = ["s.`user_id`"]
    cols_l = [c.lower() for c in name_columns]
    if "first_name" in cols_l and "last_name" in cols_l:
        fn = next(c for c in name_columns if c.lower() == "first_name")
        ln = next(c for c in name_columns if c.lower() == "last_name")
        select_bits.append(
            f"TRIM(CONCAT(IFNULL(p.`{fn}`, ''), ' ', IFNULL(p.`{ln}`, ''))) AS `user_name`"
        )
        select_bits.append(f"p.`{fn}` AS `first_name`")
        select_bits.append(f"p.`{ln}` AS `last_name`")
    else:
        for c in name_columns:
            alias = "user_name" if c.lower() in ("name", "full_name", "display_name") else c
            select_bits.append(f"p.`{c}` AS `{alias}`")

    select_list = ",\n  ".join(select_bits)
    return (
        f"SELECT DISTINCT\n  {select_list}\n"
        f"FROM (\n{inner}\n) AS s\n"
        f"LEFT JOIN `{name_table_fq}` AS p\n"
        f"  ON {join_on}\n"
        f"ORDER BY s.`user_id`\n"
        f"LIMIT {size} OFFSET {offset}"
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
    wants_names = question_wants_user_names(q)
    parts = [
        f"List distinct user_id{' and names' if wants_names else ''} for: {topic}.",
        "Reuse the SAME table and WHERE filters as the prior query.",
        (
            "SELECT DISTINCT user_id plus names via JOIN to academy_user_profile_basic_details "
            "(first_name, last_name) when the fact table has no name columns."
            if wants_names
            else "SELECT DISTINCT user_id (not COUNT)."
        ),
        f"Paginate with LIMIT {page_size} OFFSET {(page - 1) * page_size} (page {page}).",
    ]
    if prior_sql.strip():
        try:
            # Prefer fully-qualified FROM table refs (project.dataset.table).
            refs = {
                m.group(1).rsplit(".", 1)[-1]
                for m in re.finditer(
                    r"FROM\s+`([^`]+)`",
                    prior_sql,
                    flags=re.I,
                )
            }
            if not refs:
                refs = {
                    m.group(1).rsplit(".", 1)[-1]
                    for m in re.finditer(r"`([^`]+)`", prior_sql)
                    if "." in m.group(1)
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
    question: str = "",
    included_tables: list | None = None,
    columns_by_table: dict[str, set[str]] | None = None,
) -> str | None:
    """
    Convert a prior COUNT(DISTINCT user_id) query into SELECT DISTINCT user_id
    keeping the same FROM/WHERE. Supports LIMIT/OFFSET pagination.
    When the question asks for names and the fact table has none, JOIN
    academy_user_profile_basic_details (or master) for first_name/last_name.
    """
    sql = (prior_sql or "").strip().rstrip(";")
    if not sql:
        return None

    size = _drill_page_size(page_size)
    page = max(1, int(page or 1))
    wants_names = question_wants_user_names(question)

    # Already a list query → re-page; optionally add names if missing
    if is_user_id_list_sql(sql):
        base = apply_list_pagination(sql, page=page, page_size=size)
        if not wants_names:
            return base
        if re.search(r"[`\"]?(user_name|first_name|last_name)[`\"]?", base, re.I):
            return base
        # Strip pagination, wrap with names, re-apply page
        bare = re.sub(r"\bORDER\s+BY\b[\s\S]*$", "", base, flags=re.I).strip()
        bare = re.sub(r"\bLIMIT\s+\d+(?:\s+OFFSET\s+\d+)?\s*$", "", bare, flags=re.I).strip()
        # Ensure bare is a simple user_id select for wrapping
        if not re.search(r"SELECT\s+DISTINCT\s+[`\"]?user_id[`\"]?\s*$", bare.split("\n")[0], re.I):
            # Extract fact table from prior for name source
            pass
        fact_fq = ""
        m_fq = re.search(r"FROM\s+`([^`]+)`", bare, re.I)
        if m_fq:
            fact_fq = m_fq.group(1)
        name_fq, name_cols, join_on = resolve_user_name_source(
            fact_fq, columns_by_table, included_tables
        )
        if name_fq and name_cols:
            # Reduce to user_id-only subquery if the list already has only user_id
            if re.search(r"SELECT\s+DISTINCT\s+[`\"]?user_id[`\"]?", bare, re.I) and not re.search(
                r"SELECT\s+DISTINCT\s+[`\"]?user_id[`\"]?\s*,", bare, re.I
            ):
                return _wrap_user_list_with_names(
                    bare,
                    name_table_fq=name_fq,
                    name_columns=name_cols,
                    join_on=join_on,
                    page=page,
                    page_size=size,
                )
        return base

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
    base = (
        f"SELECT DISTINCT `user_id`\n{tail}\n"
        f"ORDER BY `user_id`\nLIMIT {size} OFFSET {offset}"
    )

    if not wants_names:
        return base

    fact_fq = ""
    m_fq = re.search(r"FROM\s+`([^`]+)`", tail, re.I)
    if m_fq:
        fact_fq = m_fq.group(1)
    name_fq, name_cols, join_on = resolve_user_name_source(
        fact_fq, columns_by_table, included_tables
    )
    # Names already on fact table
    if not name_fq and name_cols:
        bits = ["`user_id`"] + [f"`{c}`" for c in name_cols]
        return (
            f"SELECT DISTINCT {', '.join(bits)}\n{tail}\n"
            f"ORDER BY `user_id`\nLIMIT {size} OFFSET {offset}"
        )
    if name_fq and name_cols:
        inner = f"SELECT DISTINCT `user_id`\n{tail}"
        return _wrap_user_list_with_names(
            inner,
            name_table_fq=name_fq,
            name_columns=name_cols,
            join_on=join_on,
            page=page,
            page_size=size,
        )
    return base


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
