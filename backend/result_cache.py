"""Answer follow-up questions from cached thread/notebook data without BigQuery."""
from __future__ import annotations

import json
import re
from typing import Any

import llm

_FOLLOWUP = re.compile(
    r"\b(same|again|previous|last|before|earlier|follow.?up|what about|how about|"
    r"break down|breakdown|more detail|explain|why|compare|difference|changed?|"
    r"new rows?|since we|from that|those results?|that data|above)\b",
    re.I,
)
_DELTA = re.compile(r"\b(new|delta|changed?|since|compared to|vs\.?|versus)\b", re.I)
_TRIVIAL_SQL = re.compile(
    r"^\s*SELECT\s+1\b",
    re.I,
)
_REF_PRIOR = re.compile(
    r"\b(this|that|it|them|these|those|above|previous|last answer|same data|prior)\b",
    re.I,
)
_VIZ_FOLLOWUP = re.compile(
    r"\b(graph|chart|plot|visuali[sz]e|visualization|detailed report|"
    r"in a (graph|chart)|as a (graph|chart|table)|show (this|that|it)|"
    r"display (this|that)|report on)\b",
    re.I,
)
# Vague scope expansions need a fresh warehouse query, not cached rows.
_VAGUE_NEW_SCOPE = re.compile(
    r"\b(other aspects?|other metrics?|other factors?|everything else|all other|"
    r"rest of the|different (aspects?|metrics?|dimensions?|tables?))\b",
    re.I,
)
# Calendar-time questions — not references to the prior Thread answer.
_TEMPORAL_SCOPE = re.compile(
    r"\b("
    r"trend|trends|over time|month over month|mom|yoy|year over year|"
    r"previous months?|past months?|last \d+ months?|"
    r"monthly trend|monthly breakdown|monthly submissions?|"
    r"by month|by week|by day|per month|each month|"
    r"submission trends?|how many .{0,40}(each|per) month"
    r")\b",
    re.I,
)
_TEMPORAL_PREVIOUS = re.compile(
    r"\b(previous|past|last)\s+(month|months|week|weeks|quarter|quarters|year|years)\b",
    re.I,
)
# Explicit reference to a prior answer/result — not calendar "previous months".
_PRIOR_ANSWER_REF = re.compile(
    r"\b("
    r"that|those|this|it|from that|from the (previous|last|prior)|"
    r"previous (answer|result|query|response)|last (answer|result|query|response)|"
    r"same (data|result|answer)|above|cached"
    r")\b",
    re.I,
)
_DATE_COLUMN = re.compile(
    r"(month|date|week|period|day|submission|applied_datetime|form_submission_month)",
    re.I,
)


def _is_usable_cache_entry(entry: dict[str, Any]) -> bool:
    """Skip placeholder SQL and empty results — they must not satisfy real questions."""
    sql = (entry.get("sql") or "").strip()
    if not sql or _TRIVIAL_SQL.search(sql):
        return False
    if "`" not in sql and not re.search(r"\bFROM\s+[\w`.]+", sql, re.I):
        return False
    cols = entry.get("columns") or []
    if not cols:
        return False
    rows = entry.get("rows") or []
    if not rows:
        return False
    return True


def _usable_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in entries if _is_usable_cache_entry(e)]


def _entry_blob(entry: dict[str, Any]) -> str:
    rows = entry.get("rows") or []
    return json.dumps(rows[:100], default=str)


def build_cache_entries(
    memories: list[Any],
    cell_runs: list[Any],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    thread_sql_norm: set[str] = set()

    for m in memories:
        try:
            rows = json.loads(m.rows_json or "[]")
        except json.JSONDecodeError:
            rows = []
        try:
            columns = json.loads(m.columns_json or "[]")
        except json.JSONDecodeError:
            columns = []
        sql = m.sql or ""
        if sql.strip():
            thread_sql_norm.add(
                re.sub(r"\s+", " ", sql.strip().rstrip(";")).strip().lower()
            )
        try:
            chart_spec = json.loads(m.chart_spec_json or "{}")
        except json.JSONDecodeError:
            chart_spec = {}
        entries.append({
            "source": "thread",
            "id": f"memory:{m.id}",
            "question": m.question,
            "sql": sql,
            "summary": m.summary,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "chart_spec": chart_spec,
            "bytes_estimate": m.bytes_estimate,
        })

    for r in cell_runs:
        sql = r.sql or ""
        norm = (
            re.sub(r"\s+", " ", sql.strip().rstrip(";")).strip().lower()
            if sql.strip()
            else ""
        )
        if norm and norm in thread_sql_norm:
            continue
        try:
            rows = json.loads(r.rows_json or "[]")
        except json.JSONDecodeError:
            rows = []
        try:
            columns = json.loads(r.columns_json or "[]")
        except json.JSONDecodeError:
            columns = []
        entries.append({
            "source": "notebook",
            "id": f"cell:{r.cell_id}",
            "question": f"Notebook cell: {r.cell_name}",
            "sql": sql,
            "summary": r.summary or "",
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
        })
    return entries


def is_visualization_followup(question: str) -> bool:
    """Chart/report requests that should reuse the prior answer's rows, not new SQL."""
    q = question.lower().strip()
    if not _VIZ_FOLLOWUP.search(q) and not (
        len(q.split()) <= 12 and re.search(r"\b(graph|chart|report)\b", q)
    ):
        return False
    return bool(_REF_PRIOR.search(q)) or len(q.split()) <= 10


def _latest_thread_entry(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    usable = _usable_entries(entries)
    if not usable:
        return None
    thread = [e for e in usable if e.get("source") == "thread"]
    return thread[-1] if thread else usable[-1]


def try_revisualize_from_prior(
    question: str,
    entries: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    Re-chart / re-report on the most recent Thread answer without a new BigQuery query.
    Used for: "show this in a graph", "detailed report on that", etc.
    """
    if not entries or not is_visualization_followup(question):
        return None
    entry = _latest_thread_entry(entries)
    if not entry:
        return None

    prior_q = entry.get("question") or "prior answer"
    viz_prompt = (
        f"{question}\n\n"
        f"(Visualize and write a detailed report using ONLY the data from the previous "
        f"answer to: «{prior_q}». Do not switch topics or tables.)"
    )
    sample = entry["rows"][:50]
    viz_rows, chart_spec, analysis = llm.build_presentation(
        viz_prompt, entry["columns"], entry["rows"], sample=sample
    )
    note = " _(Chart and report from your previous answer — no new BigQuery scan.)_"
    return {
        "question": question,
        "sql": entry["sql"],
        "columns": entry["columns"],
        "rows": entry["rows"],
        "viz_rows": viz_rows,
        "chart_spec": chart_spec,
        "analysis": analysis + note,
        "bytes_estimate": 0,
        "from_cache": True,
        "cache_source": entry["id"],
    }


def is_time_series_question(question: str) -> bool:
    """Trend / multi-period questions need a fresh warehouse query."""
    q = (question or "").strip()
    if not q:
        return False
    if _TEMPORAL_SCOPE.search(q) or _TEMPORAL_PREVIOUS.search(q):
        return True
    return bool(re.search(r"\bwhat were\b.+\b(trend|trends|submissions?)\b", q, re.I))


def _cache_supports_time_series(entry: dict[str, Any]) -> bool:
    """Cached rows must already be broken down by time to answer trend questions."""
    cols = [str(c).lower() for c in (entry.get("columns") or [])]
    if not any(_DATE_COLUMN.search(c) for c in cols):
        return False
    rows = entry.get("rows") or []
    return len(rows) >= 2


def needs_fresh_query(question: str, entries: list[dict[str, Any]]) -> bool:
    """
    True when the question needs new BigQuery SQL, not cache or explain.
    E.g. prior answer was COUNT(*) and user asks 'give uid for those 6'.
    """
    from question_intent import is_drill_down_data_request, question_wants_breakdown

    if question_wants_breakdown(question):
        return True

    if is_drill_down_data_request(question):
        return True

    if is_time_series_question(question):
        return True

    q = question.lower()
    if not _REF_PRIOR.search(q):
        return False

    wants_ids = bool(
        re.search(r"\b(uid|user_id|user ids?|ids?|names?|emails?|who|which)\b", q)
    )
    wants_list = bool(re.search(r"\b(give|get|show|list|fetch|provide)\b", q))
    if wants_ids or (wants_list and re.search(r"\b(details?|rows?|records?)\b", q)):
        entry = _latest_thread_entry(entries) if entries else None
        if not entry:
            return True
        cols = {str(c).lower() for c in (entry.get("columns") or [])}
        if wants_ids and not any(
            x in cols for x in ("uid", "user_id", "id", "userid", "user_uid")
        ):
            return True
        rows = entry.get("rows") or []
        if len(rows) <= 1 and wants_list:
            return True
    return False


def is_likely_followup(question: str, has_cache: bool) -> bool:
    if not has_cache:
        return False
    from question_intent import question_wants_breakdown

    if question_wants_breakdown(question):
        return False
    if needs_fresh_query(question, []):
        return False
    if is_time_series_question(question):
        return False
    if is_visualization_followup(question):
        return True
    q = question.lower().strip()
    # New standalone questions (even short) should hit BigQuery, not cache.
    if re.search(
        r"\b(give|show|get|compute|calculate|what is|what were|how many|list|monthly|count of|total)\b",
        q,
    ):
        if not _PRIOR_ANSWER_REF.search(q):
            return False
    if _VAGUE_NEW_SCOPE.search(q):
        return False
    # "previous months" is calendar scope, not a follow-up to the last answer.
    if _TEMPORAL_PREVIOUS.search(q) and not _PRIOR_ANSWER_REF.search(q):
        return False
    if len(q) < 120 and _FOLLOWUP.search(q):
        if _TEMPORAL_SCOPE.search(q) and not _PRIOR_ANSWER_REF.search(q):
            return False
        return True
    # Short drill-down only when referencing prior context
    if has_cache and len(q.split()) <= 12 and _PRIOR_ANSWER_REF.search(q):
        if re.search(r"\b(promoter|detractor|passive|breakdown|total|count|score|average|sum)\b", q):
            return True
    return False


def _thread_block_for_explain(entries: list[dict[str, Any]]) -> str:
    usable = _usable_entries(entries)
    thread = [e for e in usable if e.get("source") == "thread"]
    if not thread:
        thread = usable[-3:]
    lines: list[str] = []
    for e in thread[-4:]:
        lines.append(
            f"Q: {e.get('question', '')}\n"
            f"SQL: {(e.get('sql') or '')[:800]}\n"
            f"Columns: {e.get('columns', [])}\n"
            f"Summary: {e.get('summary', '')}\n"
            f"Sample rows: {_entry_blob(e)[:2000]}\n"
        )
    return "\n---\n".join(lines)


def try_explain_from_thread(
    question: str,
    entries: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Answer meta / why questions from Thread history without new SQL."""
    if not entries or needs_fresh_query(question, entries):
        return None
    block = _thread_block_for_explain(entries)
    if not block.strip():
        return None
    analysis = llm.explain_from_thread(question, block)
    suggestions = llm.suggest_followups(
        question,
        analysis=analysis,
        columns=(entries[-1].get("columns") if entries else None),
    )
    prior_sql = ""
    for e in reversed(_usable_entries(entries)):
        if e.get("source") == "thread" and e.get("sql"):
            prior_sql = e["sql"]
            break
    return {
        "question": question,
        "sql": prior_sql,
        "columns": [],
        "rows": [],
        "chart_spec": {"chart": "none"},
        "analysis": analysis,
        "bytes_estimate": 0,
        "from_cache": True,
        "response_mode": "explain",
        "suggestions": suggestions,
    }


def try_answer_from_cache(
    question: str,
    entries: list[dict[str, Any]],
    *,
    always_check: bool = False,
) -> dict[str, Any] | None:
    """
    If the question can be answered from cached rows, return a complete ask result dict.
    Otherwise return None (caller should run BigQuery).

    When always_check=True (project reuse_cached_results), every question is evaluated
    against prior Thread/notebook data before hitting BigQuery.
    """
    if not entries:
        return None

    from question_intent import question_wants_breakdown

    if question_wants_breakdown(question):
        return None

    if needs_fresh_query(question, entries):
        return None

    usable = _usable_entries(entries)
    if not usable:
        return None

    if _VAGUE_NEW_SCOPE.search(question):
        return None

    if is_time_series_question(question):
        return None

    # Only reuse cached rows when the user explicitly references a prior answer,
    # or asks to re-chart prior results — never for standalone new questions.
    if not is_visualization_followup(question) and not _PRIOR_ANSWER_REF.search(question):
        return None

    if not always_check and not is_likely_followup(question, True):
        if not _PRIOR_ANSWER_REF.search(question):
            return None

    cache_block = []
    for e in usable[-5:]:
        cache_block.append(
            f"[{e['id']}] {e['question']}\n"
            f"SQL: {e['sql'][:500]}\n"
            f"Columns: {e['columns']}\n"
            f"Rows ({e['row_count']}): {_entry_blob(e)[:6000]}\n"
            f"Summary: {e.get('summary', '')}\n"
        )

    import config
    from memory_lookup import stored_answer_matches_question

    entry: dict[str, Any] | None = None
    if config.CACHE_ROUTER_MODE == "rules":
        for e in reversed(usable):
            if stored_answer_matches_question(
                question,
                sql=e.get("sql") or "",
                columns=e.get("columns") or [],
                rows=e.get("rows") or [],
            ):
                entry = e
                break
    else:
        decision = llm.cache_decision(question, "".join(cache_block))
        if not decision.get("use_cache"):
            return None
        source_id = str(decision.get("source_id") or "")
        entry = next((e for e in usable if e["id"] == source_id), None)

    if not entry or not _is_usable_cache_entry(entry):
        return None

    if is_time_series_question(question) and not _cache_supports_time_series(entry):
        return None

    if not stored_answer_matches_question(
        question,
        sql=entry.get("sql") or "",
        columns=entry.get("columns") or [],
        rows=entry.get("rows") or [],
    ):
        return None

    viz_rows, chart_spec, analysis = llm.build_presentation(
        question, entry["columns"], entry["rows"], sample=entry["rows"][:50]
    )
    from_cache_note = (
        " _(Answered from cached data — no new BigQuery scan.)_"
        if not analysis.endswith(".")
        else " _(Answered from cached data — no new BigQuery scan.)_"
    )

    return {
        "question": question,
        "sql": entry["sql"],
        "columns": entry["columns"],
        "rows": entry["rows"],
        "viz_rows": viz_rows,
        "chart_spec": chart_spec,
        "analysis": analysis + from_cache_note,
        "bytes_estimate": 0,
        "from_cache": True,
        "cache_source": entry["id"],
        "response_mode": "data",
        "suggestions": llm.suggest_followups(
            question,
            analysis=analysis,
            columns=entry["columns"],
        ),
    }
