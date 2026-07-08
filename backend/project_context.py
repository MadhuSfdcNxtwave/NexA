"""Build SQL generation context from saved project knowledge (Thread + Notebook)."""
from __future__ import annotations

from typing import Any


def _field(obj: Any, name: str, default: str = "") -> str:
    """Read a field from a SQLAlchemy model or dict."""
    if isinstance(obj, dict):
        return str(obj.get(name) or default)
    return str(getattr(obj, name, None) or default)


def build_sql_context(
    *,
    thread_memories: list[Any] | None = None,
    notebook_runs: list[Any] | None = None,
    join_hints: str = "",
    memory_summary: str = "",
    compact: bool = False,
    max_sql_chars: int = 12000,
) -> str:
    """
    Prior successful queries teach the model how this project uses its tables.
    When memory_summary is set, use compact mode to save tokens on follow-ups.
    """
    lines = [
        "# Project context",
        "Reuse table names, column names, filters, and aggregates from below. "
        "Adapt when the question asks for something new.",
    ]

    if (join_hints or "").strip():
        lines.append(f"\n## Join hints\n{join_hints.strip()}")

    summary = (memory_summary or "").strip()
    if summary and summary not in ("(No queries yet.)", "(No queries yet)"):
        lines.append(
            "\n## Thread knowledge base (use for follow-ups — tables, SQL, answers)\n"
            + summary
        )

    thread_memories = thread_memories or []
    notebook_runs = notebook_runs or []

    if compact and summary:
        recent = thread_memories[-2:] if thread_memories else []
        if recent:
            lines.append("\n## Recent exchanges (detail for follow-up)")
            for m in recent:
                lines.append(
                    f"\nQ: {_field(m, 'question')}\n"
                    f"SQL:\n{_clip(_field(m, 'sql'), 1200)}\n"
                    f"Finding: {_clip(_field(m, 'summary'), 400)}"
                )
        if recent:
            last = recent[-1]
            lines.append(
                "\n## Most recent answer\n"
                f"Last question: {_field(last, 'question')}\n"
                "Reuse SAME table/WHERE for drill-downs; SELECT new columns when asked.\n"
                f"SQL:\n{_clip(_field(last, 'sql'), 1000)}"
            )
    elif thread_memories:
        cap = 3 if summary else 8
        shown = thread_memories[-cap:]
        lines.append("\n## Thread — questions already answered in this project")
        for m in shown:
            lines.append(
                f"\nQ: {_field(m, 'question')}\n"
                f"SQL:\n{_clip(_field(m, 'sql'), 1500 if not summary else 800)}\n"
                f"Finding: {_clip(_field(m, 'summary'), 300)}"
            )
        last = thread_memories[-1]
        lines.append(
            "\n## Most recent Thread answer (for follow-ups)\n"
            f"Last question: {_field(last, 'question')}\n"
            "Reuse the SAME table and WHERE filters for drill-downs but write NEW SQL "
            "selecting the requested columns.\n"
            f"SQL:\n{_clip(_field(last, 'sql'), 1000)}"
        )

    if notebook_runs:
        cap = 2 if compact else 3
        lines.append("\n## Notebook — SQL cells that ran successfully")
        for r in notebook_runs[-cap:]:
            name = _field(r, "cell_name", "cell")
            sql = _field(r, "sql")
            note = _field(r, "summary")
            lines.append(f"\nCell `{name}`:\n{_clip(sql, 800 if not compact else 500)}")
            if note.strip():
                lines.append(f"Note: {_clip(note.strip(), 200)}")

    if not thread_memories and not notebook_runs and not summary:
        lines.append(
            "\n(No prior queries in this project yet. Use schema annotations — "
            "[PRIMARY FIELD], [PRIMARY DATE], [FEEDBACK FIELD], [DO NOT USE].)"
        )

    text = "\n".join(lines)
    if len(text) > max_sql_chars:
        return text[:max_sql_chars] + "\n…(truncated)"
    return text


def _clip(s: str, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def build_thread_conversation_context(
    cache_entries: list[Any] | None,
    *,
    max_turns: int | None = None,
    exclude_question: str | None = None,
) -> str:
    """
    Cursor-style: prior Thread Q&A loaded BEFORE the current question.
    Gives SQL + analysis the same conversational continuity as a chat IDE.
    """
    import config
    from memory_lookup import normalize_question

    entries = [e for e in (cache_entries or []) if e.get("source") == "thread"]
    if exclude_question:
        key = normalize_question(exclude_question)
        entries = [
            e for e in entries
            if normalize_question(e.get("question") or "") != key
        ]
    if not entries:
        return ""

    cap = max_turns if max_turns is not None else config.THREAD_CONVERSATION_TURNS
    shown = entries[-cap:]
    lines = [
        "# Conversation so far (Thread memory — read before answering)",
        "These are prior questions and answers in this project. "
        "Use them for follow-ups, drill-downs, and consistent filters. "
        "When the new question builds on a prior turn, reuse the same tables and logic.",
    ]
    for i, entry in enumerate(shown, 1):
        q = (entry.get("question") or "").strip()
        summary = _clip((entry.get("summary") or ""), 600)
        sql = _clip((entry.get("sql") or ""), 800)
        cols = entry.get("columns") or []
        row_count = entry.get("row_count") or len(entry.get("rows") or [])
        lines.append(f"\n## Turn {i}\n**User:** {q}")
        if summary:
            lines.append(f"**Assistant:** {summary}")
        if cols:
            lines.append(f"*(Result: {row_count} rows, columns: {', '.join(str(c) for c in cols[:6])})*")
        if sql:
            lines.append(f"SQL used:\n```sql\n{sql}\n```")
    return "\n".join(lines)


def merge_ask_context(
    project_context: str,
    cache_entries: list[Any] | None,
    current_question: str,
) -> str:
    """Full context for SQL generation: conversation history + project memory."""
    conv = build_thread_conversation_context(
        cache_entries,
        exclude_question=current_question,
    )
    parts = [p.strip() for p in (conv, project_context or "") if p and p.strip()]
    return "\n\n".join(parts)
