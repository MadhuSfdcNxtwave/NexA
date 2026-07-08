"""Deterministic SQL for NPS / rating questions — including joins to master for demographics."""
from __future__ import annotations

import re
from typing import Any

_AVG = re.compile(r"\baverage|avg\b", re.I)
_NPS = re.compile(
    r"\bnps\b|net promoter|rating_on_scale|promoter|detractor|"
    r"rating.{0,12}\(0.{0,3}10\)|scale of 0",
    re.I,
)
_NPS_SCORE = re.compile(r"\bnps\s*(score)?\b|net promoter", re.I)
_NPS_THRESHOLD = re.compile(r"\b(above|below|over|under|greater|less|>=|<=|>|<)\s*(\d+)", re.I)
_BY = re.compile(r"\bby\s+(\w+)", re.I)
_LEGACY = re.compile(r"\b(nov|dec).{0,12}2025|snapshot|legacy\b", re.I)
_PROMOTER = re.compile(r"\bpromoter", re.I)
_DETRACTOR = re.compile(r"\bdetractor", re.I)

_MASTER_DIMS: dict[str, str] = {
    "gender": "gender",
    "retention": "latest_retention_bucket",
    "coach": "success_coach_email",
    "institute": "bachelors_institute_name",
}


def _pick_nps_table(
    question: str,
    tables: list[Any],
    columns_by_table: dict[str, set[str]],
) -> tuple[str, set[str]] | None:
    best: tuple[int, str, set[str]] | None = None
    prefer_legacy = bool(_LEGACY.search(question or ""))
    for t in tables:
        fq = t.full_table_id
        cols = columns_by_table.get(fq) or set()
        short = fq.rsplit(".", 1)[-1].lower()
        if "nps" not in short and "rating_on_scale_of_0_to_10" not in cols:
            continue
        score = 0
        if short == "academy_nps_form_responses":
            score += 10
        if "form_submission_month" in cols:
            score += 4
        if "rating_on_scale_of_0_to_10" in cols:
            score += 6
        if "nov_and_dec" in short or short.startswith("nps_form_responses_nov"):
            score += 8 if prefer_legacy else -6
        if score <= 0:
            continue
        if best is None or score > best[0]:
            best = (score, fq, cols)
    return (best[1], best[2]) if best else None


def _pick_score_col(cols: set[str]) -> str | None:
    for name in (
        "rating_on_scale_of_0_to_10",
        "on_a_scale_of_0_10_how_likely_are_you_to_recommend_nxtwaves_academy_program_to_a_friend_or_peer",
    ):
        if name in cols:
            return name
    return None


def _pick_master_table(
    tables: list[Any],
    columns_by_table: dict[str, set[str]],
    dim_col: str,
) -> tuple[str, set[str]] | None:
    for t in tables:
        fq = t.full_table_id
        cols = columns_by_table.get(fq) or set()
        short = fq.rsplit(".", 1)[-1].lower()
        if "master" not in short:
            continue
        if dim_col in cols:
            return fq, cols
    return None


def _master_join_on(nps_fq: str, master_fq: str) -> str:
    """Standard join: NPS user_id has hyphens, master often does not."""
    return f"REPLACE(n.`user_id`, '-', '') = m.`user_id`"


def _breakdown_dim(question: str, cols: set[str]) -> str | None:
    q = (question or "").lower()
    m = _BY.search(q)
    if m:
        term = m.group(1).lower()
        if term in _MASTER_DIMS:
            return _MASTER_DIMS[term]
        if term in {c.lower() for c in cols}:
            return next(c for c in cols if c.lower() == term)
    for key, col in _MASTER_DIMS.items():
        if re.search(rf"\b{key}\b", q):
            return col
    if "form_submission_month" in cols and re.search(r"\bmonth", q):
        return "form_submission_month"
    return None


def _month_date_filter(
    question: str,
    nps_fq: str,
    nps_cols: set[str],
    tables: list[Any],
) -> str:
    """WHERE clause for month/year mentioned in the question."""
    from datetime import date

    from question_dates import (
        _month_from_question,
        _pick_year_for_month,
        _profile_ranges,
        _year_from_question,
    )

    q = question or ""
    month = _month_from_question(q)
    year = _year_from_question(q)
    ql = q.lower()
    if re.search(r"\b(this month|current month|mtd)\b", ql):
        today = date.today()
        month, year = today.month, today.year
    elif re.search(r"\blast month\b", ql):
        today = date.today()
        month = today.month - 1 or 12
        year = today.year if today.month > 1 else today.year - 1
    if not month:
        return ""

    table_obj = next((t for t in tables if t.full_table_id == nps_fq), None)
    ranges = _profile_ranges(table_obj) if table_obj else {}
    if not year and ranges:
        year = _pick_year_for_month(month, ranges)
    if not year:
        year = date.today().year

    for c in nps_cols:
        if "form_submission_month" in c.lower():
            return f"`{c}` = DATE '{year}-{month:02d}-01'"
    return ""


def _threshold_filter(score_col: str, question: str) -> str | None:
    m = _NPS_THRESHOLD.search(question or "")
    if not m:
        m = re.search(r"\b(above|below)\s+(\d+)\b", question or "", re.I)
    if not m:
        return None
    op_word, val = m.group(1).lower(), int(m.group(2))
    op = ">=" if op_word in ("above", "over", "greater", ">=") else "<="
    if op_word in ("below", "under", "less", "<="):
        op = "<="
    return f"`{score_col}` {op} {val}"


def disambiguate_nps_table_ids(
    question: str,
    selected_ids: list[str],
) -> list[str]:
    """Keep one NPS table — ongoing monthly vs legacy snapshot."""
    nps_ids = [
        fq
        for fq in selected_ids
        if "nps" in fq.rsplit(".", 1)[-1].lower()
    ]
    if len(nps_ids) <= 1:
        return selected_ids
    prefer_legacy = bool(_LEGACY.search(question or ""))
    ongoing = next(
        (fq for fq in nps_ids if fq.rsplit(".", 1)[-1] == "academy_nps_form_responses"),
        None,
    )
    legacy = next(
        (fq for fq in nps_ids if "nov_and_dec" in fq.rsplit(".", 1)[-1]),
        None,
    )
    drop: set[str] = set()
    if prefer_legacy and legacy:
        drop = {fq for fq in nps_ids if fq != legacy}
    elif ongoing:
        drop = {fq for fq in nps_ids if fq != ongoing}
    else:
        drop = set(nps_ids[1:])
    if not drop:
        return selected_ids
    return [fq for fq in selected_ids if fq not in drop]


def is_nps_analytics_question(question: str) -> bool:
    """True for NPS score/rating aggregates — not contextual feedback surveys."""
    from question_intent import expand_question_abbreviations

    q = expand_question_abbreviations(question)
    if not q or not _NPS.search(q):
        return False
    return bool(
        _AVG.search(q)
        or _BY.search(q)
        or _PROMOTER.search(q)
        or _DETRACTOR.search(q)
        or _NPS_SCORE.search(q)
        or _NPS_THRESHOLD.search(q)
        or re.search(r"\b(count|how many|score|monthly nps|rating)\b", q, re.I)
    )


def try_build_nps_sql(
    question: str,
    tables: list[Any],
    columns_by_table: dict[str, set[str]],
) -> str | None:
    """Build NPS SQL; joins master automatically for demographic breakdowns."""
    q = (question or "").strip()
    if not q or not _NPS.search(q):
        return None

    picked = _pick_nps_table(q, tables, columns_by_table)
    if not picked:
        return None
    nps_fq, nps_cols = picked
    score_col = _pick_score_col(nps_cols)
    if not score_col:
        return None

    date_f = _month_date_filter(q, nps_fq, nps_cols, tables)
    thresh_f = _threshold_filter(score_col, q)
    base_filters = [f for f in (date_f, thresh_f) if f]

    # NPS count above/below threshold (e.g. "NPS count above 8").
    if thresh_f and re.search(r"\b(count|how many)\b", q, re.I):
        filt = base_filters + [f"`{score_col}` IS NOT NULL"]
        return (
            f"SELECT COUNT(*) AS response_count\n"
            f"FROM `{nps_fq}`\n"
            f"WHERE {' AND '.join(filt)}"
        )

    dim = _breakdown_dim(q, nps_cols)
    if dim and dim not in nps_cols:
        master = _pick_master_table(tables, columns_by_table, dim)
        if master:
            master_fq, _ = master
            if _AVG.search(q):
                return f"""SELECT m.`{dim}`, AVG(n.`{score_col}`) AS avg_nps
FROM `{nps_fq}` n
JOIN `{master_fq}` m ON {_master_join_on(nps_fq, master_fq)}
WHERE n.`{score_col}` IS NOT NULL AND m.`{dim}` IS NOT NULL
GROUP BY m.`{dim}`
ORDER BY avg_nps DESC"""
            if _PROMOTER.search(q):
                return f"""SELECT m.`{dim}`, COUNT(*) AS promoter_count
FROM `{nps_fq}` n
JOIN `{master_fq}` m ON {_master_join_on(nps_fq, master_fq)}
WHERE n.`{score_col}` >= 9 AND m.`{dim}` IS NOT NULL
GROUP BY m.`{dim}`
ORDER BY promoter_count DESC"""
            if _DETRACTOR.search(q):
                return f"""SELECT m.`{dim}`, COUNT(*) AS detractor_count
FROM `{nps_fq}` n
JOIN `{master_fq}` m ON {_master_join_on(nps_fq, master_fq)}
WHERE n.`{score_col}` <= 6 AND m.`{dim}` IS NOT NULL
GROUP BY m.`{dim}`
ORDER BY detractor_count DESC"""

    if dim and dim in nps_cols and _AVG.search(q):
        return f"""SELECT `{dim}`, AVG(`{score_col}`) AS avg_nps
FROM `{nps_fq}`
WHERE `{score_col}` IS NOT NULL AND `{dim}` IS NOT NULL
GROUP BY `{dim}`
ORDER BY avg_nps DESC"""

    if _AVG.search(q) and not dim:
        filt = base_filters + [f"`{score_col}` IS NOT NULL"]
        return (
            f"SELECT AVG(`{score_col}`) AS avg_nps\n"
            f"FROM `{nps_fq}`\n"
            f"WHERE {' AND '.join(filt)}"
        )

    # Canonical NPS score — (promoters − detractors) / total, not AVG.
    if _NPS_SCORE.search(q) and not _AVG.search(q) and not dim:
        filt = base_filters + [f"`{score_col}` IS NOT NULL"]
        return (
            f"SELECT ROUND(100.0 * (COUNTIF(`{score_col}` >= 9) - COUNTIF(`{score_col}` <= 6)) "
            f"/ NULLIF(COUNT(`{score_col}`), 0), 2) AS nps_score\n"
            f"FROM `{nps_fq}`\n"
            f"WHERE {' AND '.join(filt)}"
        )

    if _PROMOTER.search(q) and dim and dim in nps_cols:
        return f"""SELECT `{dim}`, COUNT(*) AS promoter_count
FROM `{nps_fq}`
WHERE `{score_col}` >= 9 AND `{dim}` IS NOT NULL
GROUP BY `{dim}`
ORDER BY promoter_count DESC"""

    if _DETRACTOR.search(q) and dim and dim in nps_cols:
        return f"""SELECT `{dim}`, COUNT(*) AS detractor_count
FROM `{nps_fq}`
WHERE `{score_col}` <= 6 AND `{dim}` IS NOT NULL
GROUP BY `{dim}`
ORDER BY detractor_count DESC"""

    return None
