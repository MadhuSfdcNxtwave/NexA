"""Per-thread knowledge base — rolling Q/A + SQL overview for follow-ups."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from db import Memory, Thread

_MAX_TURNS = 12
_MAX_CHARS = 6000


def _tables_from_sql(sql: str) -> list[str]:
    sql = (sql or "").strip()
    if not sql:
        return []
    try:
        import sql_parse as sp

        refs = sp.table_refs(sp.parse_bigquery(sql))
        return sorted({r.rsplit(".", 1)[-1] for r in refs})
    except Exception:
        return sorted(
            {
                m.group(1).rsplit(".", 1)[-1]
                for m in re.finditer(r"`([^`]+)`", sql)
            }
        )


def build_thread_kb_from_memories(memories: list[Memory]) -> str:
    """Structured thread KB from memory rows (no LLM)."""
    if not memories:
        return ""

    lines = [
        "# Thread knowledge base",
        "Use this for follow-ups: reuse the same tables, filters, and metrics unless the user asks for something new.",
    ]
    for i, m in enumerate(memories[-_MAX_TURNS:], 1):
        tables = _tables_from_sql(m.sql or "")
        answer = (m.summary or "").strip()
        sql = (m.sql or "").strip()
        lines.append(f"\n## Turn {i}")
        lines.append(f"**Question:** {m.question.strip()}")
        if tables:
            lines.append(f"**Tables used:** {', '.join(f'`{t}`' for t in tables)}")
        if answer:
            lines.append(f"**Answer:** {answer[:400]}")
        if sql:
            lines.append(f"**SQL:**\n```sql\n{sql[:1200]}\n```")

    text = "\n".join(lines)
    if len(text) > _MAX_CHARS:
        return text[: _MAX_CHARS - 1] + "…"
    return text


def refresh_thread_overview(db: Session, thread_id: int | None) -> str:
    """Rebuild and persist thread KB after a new memory is saved."""
    if not thread_id:
        return ""
    memories = db.scalars(
        select(Memory)
        .where(Memory.thread_id == thread_id)
        .order_by(Memory.created_at, Memory.id)
    ).all()
    kb = build_thread_kb_from_memories(memories)
    thread = db.get(Thread, thread_id)
    if thread is not None:
        thread.overview_kb = kb
    return kb


def get_thread_overview(db: Session, thread_id: int | None) -> str:
    if not thread_id:
        return ""
    thread = db.get(Thread, thread_id)
    if thread and (thread.overview_kb or "").strip():
        return thread.overview_kb.strip()
    return refresh_thread_overview(db, thread_id)
