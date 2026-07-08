"""Org Schema — a saved, admin-visible snapshot of the full workspace catalog.

Builds one schema "memory" document from every workspace table (descriptions,
column metadata, join hints) and persists it on WorkspaceSettings so admins can
review exactly what the AI sees when routing questions.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db import WorkspaceTable, get_workspace_join_hints, get_workspace_settings


def _build_relations(join_hints: str) -> list[dict[str, str]]:
    """Parse workspace join hints into deduplicated relation records."""
    import join_graph as jg

    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, str]] = []
    for rel in jg.parse_join_hints(join_hints):
        key = (rel.source.lower(), rel.target.lower(), rel.rel_type.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "source": rel.source,
                "target": rel.target,
                "rel_type": rel.rel_type,
                "join_sql": rel.join_sql,
            }
        )
    out.sort(key=lambda r: (r["source"].lower(), r["target"].lower()))
    return out


def build_org_schema(db: Session) -> dict[str, Any]:
    """Assemble the full org schema document from the workspace catalog."""
    tables = db.scalars(
        select(WorkspaceTable).order_by(WorkspaceTable.full_table_id)
    ).all()

    table_docs: list[dict[str, Any]] = []
    for t in tables:
        try:
            col_desc = json.loads(t.column_descriptions_json or "{}")
        except json.JSONDecodeError:
            col_desc = {}
        if not isinstance(col_desc, dict):
            col_desc = {}
        try:
            col_hints = json.loads(t.column_hints_json or "{}")
        except json.JSONDecodeError:
            col_hints = {}
        if not isinstance(col_hints, dict):
            col_hints = {}

        columns = [
            {
                "name": name,
                "description": (desc or "").strip(),
                "hint": (col_hints.get(name) or "").strip(),
            }
            for name, desc in col_desc.items()
        ]
        table_docs.append(
            {
                "short_name": t.full_table_id.rsplit(".", 1)[-1],
                "full_table_id": t.full_table_id,
                "description": (t.description or "").strip(),
                "included_for_ai": bool(t.included_for_ai),
                "endorsed": bool(t.endorsed),
                "has_ai_overview": bool((t.ai_overview or "").strip()),
                "column_count": len(columns),
                "columns": columns,
            }
        )

    join_hints = get_workspace_join_hints(db)
    relations = _build_relations(join_hints)

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "table_count": len(table_docs),
        "tables": table_docs,
        "join_hints": join_hints,
        "relations": relations,
        "relation_count": len(relations),
    }


def schema_markdown(doc: dict[str, Any]) -> str:
    """Render the schema document as readable markdown (for export/copy)."""
    lines = [
        "# Org Schema",
        f"Generated: {doc.get('generated_at', '')}",
        f"Tables: {doc.get('table_count', 0)}",
        "",
    ]
    for t in doc.get("tables", []):
        flags = []
        if not t.get("included_for_ai"):
            flags.append("excluded from AI")
        if t.get("endorsed"):
            flags.append("endorsed")
        suffix = f" ({', '.join(flags)})" if flags else ""
        lines.append(f"## {t['short_name']}{suffix}")
        lines.append(f"`{t['full_table_id']}`")
        if t.get("description"):
            lines.append("")
            lines.append(t["description"])
        cols = t.get("columns") or []
        if cols:
            lines.append("")
            lines.append("| Column | Description |")
            lines.append("|---|---|")
            for c in cols:
                desc = (c.get("description") or "").replace("\n", " ").replace("|", "\\|")
                lines.append(f"| `{c['name']}` | {desc} |")
        lines.append("")
    hints = (doc.get("join_hints") or "").strip()
    if hints:
        lines.append("## Workspace join hints")
        lines.append("")
        lines.append("```")
        lines.append(hints)
        lines.append("```")
    return "\n".join(lines)


def save_org_schema(db: Session, doc: dict[str, Any]) -> None:
    row = get_workspace_settings(db)
    row.org_schema_json = json.dumps(doc, default=str)
    row.org_schema_updated_at = dt.datetime.now(dt.timezone.utc)


def load_org_schema(db: Session) -> dict[str, Any] | None:
    row = get_workspace_settings(db)
    raw = (getattr(row, "org_schema_json", "") or "").strip()
    if not raw or raw == "{}":
        return None
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return doc if isinstance(doc, dict) and doc.get("tables") else None


def get_or_build_org_schema(db: Session, *, rebuild: bool = False) -> dict[str, Any]:
    """Return the saved schema, rebuilding (and saving) when missing or forced."""
    if not rebuild:
        doc = load_org_schema(db)
        if doc:
            if "relations" not in doc:
                doc = build_org_schema(db)
                save_org_schema(db, doc)
                db.commit()
            return doc
    doc = build_org_schema(db)
    save_org_schema(db, doc)
    db.commit()
    return doc
