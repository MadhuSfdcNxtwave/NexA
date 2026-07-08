"""Deterministic SQL for feedback/survey tables — runs before LLM, fallback after failure."""
from __future__ import annotations

import re

from survey_sql import _escape_sql_string, _pick_feedback_table, is_survey_answer_question

_STOP = frozenset(
    "a an the and or of in on at to for from with by is are was were be been being "
    "this that these those it its i you we they he she what which who how when where "
    "why did do does can could would should will shall me my our your their them "
    "show give get list tell find most many much some any all".split()
)

_FEEDBACK_SIGNAL = re.compile(
    r"\b("
    r"user_answer|question_text|contextual feedback|feedback_details|"
    r"survey response|survey prompt|did you find|most valuable|"
    r"nps|rating|promoter|detractor|feedback form"
    r")\b",
    re.I,
)

# Legacy broad matcher — do NOT use for routing; kept for relaxed keyword scoring only.
_FEEDBACK_KEYWORD = re.compile(
    r"\b(feedback|response|responses|survey|nps|rating|valuable|user_answer|question_text)\b",
    re.I,
)


def is_feedback_table_question(question: str) -> bool:
    """True only when the question targets survey/feedback tables — not user/master analytics."""
    from question_intent import expand_question_abbreviations
    from query_planner import is_nps_topic_feedback_question

    q = expand_question_abbreviations((question or "").strip())
    if not q:
        return False
    if is_nps_topic_feedback_question(q):
        return False
    # Jobs / placements / salary analytics never belong on feedback tables.
    if re.search(r"\b(placed|placement|hired|lpa|ctc|salary|company|companies)\b", q, re.I):
        return False
    # NPS score / rating analytics — not contextual feedback surveys.
    if re.search(
        r"\b("
        r"nps|net promoter|rating_on_scale|promoter|detractor|"
        r"rating.{0,12}\(?0.{0,3}10\)?|scale of 0"
        r")\b",
        q,
        re.I,
    ) and re.search(
        r"\b(average|avg|count|by gender|by month|breakdown|score|monthly)\b",
        q,
        re.I,
    ):
        return False
    if is_survey_answer_question(q) or is_choice_survey_question(q):
        return True
    if _FEEDBACK_SIGNAL.search(q):
        return True
    # Platform / master-table analytics — never feedback path.
    if re.search(
        r"\b("
        r"growth cycle|onboarding|student|users?|coach|retention|"
        r"engage with|continue to engage|percentage of users|platform after"
        r")\b",
        q,
        re.I,
    ):
        return False
    return bool(_FEEDBACK_KEYWORD.search(q))

_GROUP_INTENT = re.compile(
    r"\b("
    r"count|how many|most|common|distribution|breakdown|group|share|percent|"
    r"which.*valuable|top|rank|frequent|often|picked|chose|selected"
    r")\b",
    re.I,
)

_CHOICE_INTENT = re.compile(
    r"\b("
    r"which of these|did you find most|most valuable|pick one|select all|"
    r"choose all|which update|which option|rank|ranking"
    r")\b",
    re.I,
)

_SELECT_TYPES = frozenset({"SINGLE_SELECT", "MULTI_SELECT"})


def _local_match_score(terms: list[str], question_text: str) -> int:
    qt = (question_text or "").lower()
    score = 0
    for term in terms:
        weight = _term_weight(term)
        for variant in _stem_variants(term):
            if variant in qt:
                score += weight
                break
    return score


def is_choice_survey_question(question: str) -> bool:
    return bool(_CHOICE_INTENT.search(question or "") or is_survey_answer_question(question))


def match_is_acceptable(
    user_question: str,
    matched_question_text: str,
    *,
    question_type: str | None = None,
    keyword_score: int | None = None,
) -> bool:
    """Reject proxy matches — e.g. TEXTUAL 'Course Library' for a 'most valuable update' pick-list."""
    mq = (matched_question_text or "").strip()
    if not mq:
        return False
    terms = _terms(user_question)
    if not terms:
        return True

    local = _local_match_score(terms, mq)
    high = [t for t in terms if _term_weight(t) >= 3]

    if is_choice_survey_question(user_question):
        if question_type and question_type.upper() == "TEXTUAL":
            return False
        if "valuable" in terms and "valuable" not in mq.lower():
            return False

    if len(high) >= 2:
        hits = sum(
            1
            for t in high
            if any(v in mq.lower() for v in _stem_variants(t))
        )
        uq = user_question.strip().rstrip("?").lower()
        mq_l = mq.lower()
        partial = len(uq) > 30 and (uq[:40] in mq_l or mq_l[:40] in uq)
        if hits < 2 and not partial:
            return False

    min_score = 6 if len(high) >= 2 else 4
    if keyword_score is not None and keyword_score < min_score:
        return False
    if local < min_score:
        return False
    return True


def _terms(question: str, min_len: int = 4) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (question or "").lower())
    out: list[str] = []
    seen: set[str] = set()
    for w in words:
        if w in _STOP or len(w) < min_len or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out[:8]


def _stem_variants(term: str) -> list[str]:
    """Light stemming so 'updates' also matches 'updated'."""
    t = term.lower()
    variants = {t}
    if t.endswith("s") and len(t) > 4:
        variants.add(t[:-1])
    if t.endswith("ed") and len(t) > 5:
        variants.add(t[:-2])
    if t.endswith("ing") and len(t) > 6:
        variants.add(t[:-3])
    return sorted(variants, key=len, reverse=True)


_WEIGHTED_TERMS: dict[str, int] = {
    "valuable": 4,
    "updates": 4,
    "update": 4,
    "recent": 3,
    "program": 3,
    "academy": 1,
}


def _term_weight(term: str) -> int:
    t = term.lower()
    if t in _WEIGHTED_TERMS:
        return _WEIGHTED_TERMS[t]
    for stem, w in _WEIGHTED_TERMS.items():
        if t.startswith(stem) or stem.startswith(t):
            return w
    return 2


def _score_expr(terms: list[str], col: str = "question_text") -> str:
    """BigQuery expression: weighted keyword hits in question_text."""
    if not terms:
        return "0"
    parts: list[str] = []
    for term in terms[:6]:
        weight = _term_weight(term)
        for variant in _stem_variants(term)[:2]:
            esc = _escape_sql_string(variant)
            parts.append(
                f"IF(LOWER(CAST({col} AS STRING)) LIKE '%{esc}%', {weight}, 0)"
            )
    return " + ".join(parts)


def _question_text_filter(terms: list[str], escaped_full: str) -> str:
    """OR-based keyword match — stored survey text rarely equals the UI label."""
    parts: list[str] = []
    if escaped_full and len(escaped_full) > 15:
        parts.append(f"TRIM(question_text) = '{escaped_full}'")
        if len(escaped_full) > 25:
            parts.append(
                f"LOWER(question_text) LIKE LOWER('%{_escape_sql_string(escaped_full[:50])}%')"
            )
    strong = [t for t in terms if len(t) >= 4][:6]
    if strong:
        likes = " OR ".join(
            f"LOWER(CAST(question_text AS STRING)) LIKE '%{_escape_sql_string(v)}%'"
            for t in strong
            for v in _stem_variants(t)[:2]
        )
        parts.append(f"({likes})")
    if not parts:
        return "1=1"
    return "(" + " OR ".join(parts) + ")"


def _text_columns(cols: set[str]) -> list[str]:
    out = ["question_text"]
    if "feedback_trigger" in cols:
        out.append("feedback_trigger")
    return out


def _build_group_sql(
    fq: str,
    cols: set[str],
    question: str,
    *,
    terms: list[str] | None = None,
    min_score: int = 1,
    exact_question_text: str | None = None,
) -> str:
    """Group user_answer for the best-matching survey question by keyword score."""
    terms = terms or _terms(question)
    qtext = question.strip().rstrip("?").strip()
    escaped = _escape_sql_string(qtext) if len(qtext) > 20 else ""
    score = _score_expr(terms)
    text_cols = _text_columns(cols)

    if exact_question_text:
        esc_q = _escape_sql_string(exact_question_text.strip())
        where_q = f"TRIM(question_text) = '{esc_q}'"
        min_score = 1
    else:
        score_cases = " OR ".join(
            f"LOWER(CAST({c} AS STRING)) LIKE '%{_escape_sql_string(v)}%'"
            for t in terms[:6]
            for c in text_cols
            for v in _stem_variants(t)[:2]
        )
        where_q = _question_text_filter(terms, escaped)
        if score_cases:
            where_q = f"({where_q} OR ({score_cases}))"
        if is_choice_survey_question(question) and "question_type" in cols:
            where_q = f"({where_q}) AND question_type IN ('SINGLE_SELECT', 'MULTI_SELECT')"

    scored_cols = ["question_text", "user_answer", "user_id"]
    if "question_type" in cols:
        scored_cols.insert(1, "question_type")

    select_cols = [
        "s.question_text",
        "s.user_answer",
        "COUNT(*) AS response_count",
        "COUNT(DISTINCT s.user_id) AS distinct_users",
    ]
    if "question_type" in cols:
        select_cols.insert(1, "s.question_type")
    group_cols = [c for c in ("s.question_text", "s.question_type", "s.user_answer") if c in select_cols]

    sql = f"""
WITH scored AS (
  SELECT
    {', '.join(scored_cols)},
    ({score}) AS match_score
  FROM `{fq}`
  WHERE {where_q}
    AND user_answer IS NOT NULL
    AND TRIM(CAST(user_answer AS STRING)) != ''
"""
    if "submitted_date" in cols:
        sql += "    AND submitted_date IS NOT NULL\n"
    sql += f"""),
best_question AS (
  SELECT question_text
  FROM scored
  WHERE match_score >= {min_score}
  GROUP BY question_text
  ORDER BY MAX(match_score) DESC, COUNT(*) DESC
  LIMIT 1
)
SELECT
  {', '.join(select_cols)}
FROM scored s
INNER JOIN best_question b ON s.question_text = b.question_text
GROUP BY {', '.join(group_cols)}
ORDER BY response_count DESC
LIMIT 50
"""
    return sql.strip()


def _build_discovery_sql(fq: str, cols: set[str], question: str, *, terms: list[str] | None = None) -> str:
    """List candidate question_text values when the primary match returns nothing."""
    terms = terms or _terms(question)
    score = _score_expr(terms)
    where_q = _question_text_filter(terms, "")
    if is_choice_survey_question(question) and "question_type" in cols:
        where_q = f"({where_q}) AND question_type IN ('SINGLE_SELECT', 'MULTI_SELECT')"
    sql = f"""
SELECT
  question_text,
  MAX(question_type) AS question_type,
  COUNT(*) AS response_count,
  COUNT(DISTINCT user_id) AS distinct_users,
  MAX({score}) AS keyword_score
FROM `{fq}`
WHERE {where_q}
"""
    if "submitted_date" in cols:
        sql += "  AND submitted_date IS NOT NULL\n"
    sql += """
GROUP BY question_text
HAVING keyword_score >= 1
ORDER BY keyword_score DESC, response_count DESC
LIMIT 15
"""
    return sql.strip()


def _build_clarification_discovery_sql(
    fq: str, cols: set[str], question: str, *, terms: list[str] | None = None
) -> str:
    """Broader discovery for clarification options (includes TEXTUAL prompts)."""
    terms = terms or _terms(question)
    score = _score_expr(terms)
    where_q = _question_text_filter(terms, "")
    sql = f"""
SELECT
  question_text,
  MAX(question_type) AS question_type,
  COUNT(*) AS response_count,
  COUNT(DISTINCT user_id) AS distinct_users,
  MAX({score}) AS keyword_score
FROM `{fq}`
WHERE {where_q}
"""
    if "submitted_date" in cols:
        sql += "  AND submitted_date IS NOT NULL\n"
    sql += """
GROUP BY question_text
HAVING keyword_score >= 1
ORDER BY keyword_score DESC, response_count DESC
LIMIT 8
"""
    return sql.strip()


def plan_feedback_query(
    question: str,
    tables: list,
    columns_by_table: dict[str, set[str]],
) -> dict | None:
    """Discovery-first plan: exact group SQL only when a candidate truly matches."""
    picked = _pick_feedback_table(tables, columns_by_table)
    if not picked:
        return None
    fq, cols = picked
    q = (question or "").strip()
    if not q:
        return None
    if not (is_feedback_table_question(q) or is_survey_answer_question(q)):
        return None

    disc_sql = _build_discovery_sql(fq, cols, q)
    return {
        "fq": fq,
        "cols": cols,
        "discovery_sql": disc_sql,
        "question": q,
        "terms": _terms(q),
    }


def build_group_sql_for_text(
    fq: str,
    cols: set[str],
    user_question: str,
    exact_question_text: str,
) -> str:
    return _build_group_sql(
        fq,
        cols,
        user_question,
        exact_question_text=exact_question_text,
    )


def try_build_feedback_sql(
    question: str,
    tables: list,
    columns_by_table: dict[str, set[str]],
    *,
    relaxed: bool = False,
    discovery: bool = False,
) -> str | None:
    """Simple single-table SQL for feedback questions - CTEs allowed after validation."""
    picked = _pick_feedback_table(tables, columns_by_table)
    if not picked:
        return None
    fq, cols = picked
    q = (question or "").strip()
    if not q:
        return None

    if discovery:
        return _build_discovery_sql(fq, cols, q)

    if not relaxed and not is_feedback_table_question(q) and not is_survey_answer_question(q):
        return None

    terms = _terms(q)
    if is_survey_answer_question(q) or _GROUP_INTENT.search(q) or is_feedback_table_question(q):
        return _build_group_sql(fq, cols, q, terms=terms)
    if relaxed and terms:
        return _build_group_sql(fq, cols, q, terms=terms, min_score=1)
    return None


def try_build_fallback_feedback_sql(
    question: str,
    tables: list,
    columns_by_table: dict[str, set[str]],
) -> str | None:
    """Last-resort: group-by on feedback table if present."""
    return try_build_feedback_sql(question, tables, columns_by_table, relaxed=True)


def try_build_feedback_discovery_sql(
    question: str,
    tables: list,
    columns_by_table: dict[str, set[str]],
) -> str | None:
    """Candidate survey prompts when answer distribution is empty."""
    return try_build_feedback_sql(
        question, tables, columns_by_table, relaxed=True, discovery=True
    )
