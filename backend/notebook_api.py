"""Notebook API helpers — cell CRUD, run, seed template."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

import config
import llm
from db import NotebookCell, NotebookCellRun, Project, WorkspaceTable
from notebook_engine import build_logic_graph, nps_starter_cells, run_notebook


def _parse_config(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _norm_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", (sql or "").strip().rstrip(";")).lower()


def _cell_dict(c: NotebookCell, last_run: NotebookCellRun | None = None) -> dict[str, Any]:
    from debug_session import debug_log

    cfg = _parse_config(c.config_json)
    if last_run and c.cell_type == "sql" and cfg.get("source") != "thread":
        stored = (last_run.sql or "").strip()
        current = (c.content or "").strip()
        if stored and current and _norm_sql(stored) != _norm_sql(current):
            debug_log(
                "notebook_api.py:_cell_dict",
                "last_run_cleared",
                {
                    "cell_id": c.id,
                    "name": c.name,
                    "source": cfg.get("source"),
                    "role": cfg.get("role"),
                    "stored_len": len(stored),
                    "current_len": len(current),
                },
                hypothesis_id="H6",
            )
            last_run = None
    out = {
        "id": c.id,
        "cell_type": c.cell_type,
        "name": c.name,
        "content": c.content,
        "config": _parse_config(c.config_json),
        "sort_order": c.sort_order,
    }
    if last_run:
        try:
            rows = json.loads(last_run.rows_json or "[]")
        except json.JSONDecodeError:
            rows = []
        try:
            columns = json.loads(last_run.columns_json or "[]")
        except json.JSONDecodeError:
            columns = []
        out["last_run"] = {
            "sql": last_run.sql,
            "columns": columns,
            "rows": rows[:50],
            "row_count": len(rows),
            "bytes_estimate": last_run.bytes_estimate,
            "summary": last_run.summary,
            "ran_at": last_run.ran_at.isoformat() if last_run.ran_at else None,
        }
    else:
        out["last_run"] = None
    return out


def _is_thread_import(cfg: dict[str, Any]) -> bool:
    return cfg.get("source") == "thread"


def list_cells(db: Session, project_id: int, *, include_thread_imports: bool = False) -> list[dict[str, Any]]:
    cells = db.scalars(
        select(NotebookCell)
        .where(NotebookCell.project_id == project_id)
        .order_by(NotebookCell.sort_order, NotebookCell.id)
    ).all()
    if not include_thread_imports:
        cells = [c for c in cells if not _is_thread_import(_parse_config(c.config_json))]
    out: list[dict[str, Any]] = []
    for c in cells:
        lr = db.scalar(
            select(NotebookCellRun)
            .where(NotebookCellRun.cell_id == c.id)
            .order_by(NotebookCellRun.ran_at.desc())
            .limit(1)
        )
        out.append(_cell_dict(c, lr))
    return out


def _primary_nps_table(db: Session, project_id: int) -> str | None:
    """First NPS-like table in the workspace (any project)."""
    _ = project_id
    tables = db.scalars(select(WorkspaceTable)).all()
    for t in tables:
        if "nps" in t.full_table_id.lower():
            return t.full_table_id
    return tables[0].full_table_id if tables else None


def seed_notebook_if_empty(db: Session, project: Project) -> list[dict[str, Any]]:
    existing = db.scalar(
        select(NotebookCell).where(NotebookCell.project_id == project.id).limit(1)
    )
    if existing:
        return list_cells(db, project.id)

    fq = _primary_nps_table(db, project.id)
    if not fq:
        return []

    for i, spec in enumerate(nps_starter_cells(fq)):
        db.add(
            NotebookCell(
                project_id=project.id,
                cell_type=spec["cell_type"],
                name=spec["name"],
                content=spec.get("content", ""),
                config_json=json.dumps(spec.get("config") or {}),
                sort_order=spec.get("sort_order", i),
            )
        )
    db.commit()
    return list_cells(db, project.id)


def _save_cell_run(
    db: Session,
    project_id: int,
    cell_id: int,
    name: str,
    result: dict[str, Any],
) -> None:
    rows = result.get("rows") or []
    cap = config.NOTEBOOK_MAX_ROWS
    db.add(
        NotebookCellRun(
            project_id=project_id,
            cell_id=cell_id,
            cell_name=name,
            sql=result.get("sql") or "",
            summary="",
            columns_json=json.dumps(result.get("columns") or []),
            rows_json=json.dumps(rows[:cap], default=str),
            bytes_estimate=result.get("bytes_estimate"),
        )
    )


def execute_notebook(
    db: Session,
    project: Project,
    *,
    input_overrides: dict[str, str] | None = None,
    stop_at_cell_id: int | None = None,
) -> dict[str, Any]:
    cells_orm = db.scalars(
        select(NotebookCell)
        .where(NotebookCell.project_id == project.id)
        .order_by(NotebookCell.sort_order, NotebookCell.id)
    ).all()
    cells = [
        {
            "id": c.id,
            "cell_type": c.cell_type,
            "name": c.name,
            "content": c.content,
            "config": _parse_config(c.config_json),
            "sort_order": c.sort_order,
        }
        for c in cells_orm
    ]
    if not cells:
        return {"variables": {}, "results": {}, "run_log": [], "bytes_estimate": 0}

    variables, results, run_log = run_notebook(
        cells,
        input_overrides=input_overrides,
        stop_at_cell_id=stop_at_cell_id,
    )

    total_bytes = 0
    for name, res in results.items():
        total_bytes += int(res.get("bytes_estimate") or 0)
        _save_cell_run(db, project.id, res["cell_id"], name, res)
        summary = ""
        try:
            summary = llm.analyze(
                f"Notebook cell {name}",
                res["columns"],
                res["rows"][:25],
                res["row_count"],
            )
        except Exception:
            summary = f"Ran {res.get('row_count', 0)} rows"
        run = db.scalar(
            select(NotebookCellRun)
            .where(NotebookCellRun.cell_id == res["cell_id"])
            .order_by(NotebookCellRun.ran_at.desc())
            .limit(1)
        )
        if run:
            run.summary = summary[:2000]

    db.commit()
    return {
        "variables": variables,
        "results": results,
        "run_log": run_log,
        "bytes_estimate": total_bytes,
    }


def latest_cell_runs(db: Session, project_id: int) -> list[NotebookCellRun]:
    """Most recent run per SQL cell for cache context."""
    cells = db.scalars(
        select(NotebookCell)
        .where(NotebookCell.project_id == project_id, NotebookCell.cell_type == "sql")
    ).all()
    runs: list[NotebookCellRun] = []
    for c in cells:
        r = db.scalar(
            select(NotebookCellRun)
            .where(NotebookCellRun.cell_id == c.id)
            .order_by(NotebookCellRun.ran_at.desc())
            .limit(1)
        )
        if r:
            runs.append(r)
    return runs


def logic_graph(db: Session, project_id: int) -> dict[str, Any]:
    return build_logic_graph(list_cells(db, project_id))


def _ask_key(question: str) -> str:
    return hashlib.sha256(question.strip().lower().encode()).hexdigest()[:12]


def get_project_summary(db: Session, project_id: int) -> str:
    for c in db.scalars(select(NotebookCell).where(NotebookCell.project_id == project_id)).all():
        cfg = _parse_config(c.config_json)
        if cfg.get("role") == "summary":
            return (c.content or "").strip()
    return ""


def _ensure_summary_cell(db: Session, project_id: int) -> NotebookCell:
    for c in db.scalars(select(NotebookCell).where(NotebookCell.project_id == project_id)).all():
        cfg = _parse_config(c.config_json)
        if cfg.get("role") == "summary":
            return c
    cell = NotebookCell(
        project_id=project_id,
        cell_type="text",
        name="project_memory",
        content="(No queries yet.)",
        config_json=json.dumps(
            {"source": "thread", "role": "summary", "ask_key": "summary", "pinned": True}
        ),
        sort_order=-1000,
    )
    db.add(cell)
    db.flush()
    return cell


def refresh_project_summary(
    db: Session,
    project_id: int,
    *,
    question: str,
    analysis: str,
    sql: str = "",
) -> str:
    """Update rolling key-point summary after each Thread exchange."""
    cell = _ensure_summary_cell(db, project_id)
    prior = (cell.content or "").strip()
    if prior in ("(No queries yet.)", ""):
        prior = ""
    updated = llm.update_memory_summary(prior, question, analysis, sql)
    cell.content = updated.strip() or prior
    return cell.content


def rebuild_project_summary_from_memories(db: Session, project_id: int) -> None:
    """One-shot summary rebuild when backfilling notebook from Thread history."""
    from db import Memory

    memories = db.scalars(
        select(Memory)
        .where(Memory.project_id == project_id)
        .order_by(Memory.created_at, Memory.id)
    ).all()
    if not memories:
        return
    cell = _ensure_summary_cell(db, project_id)
    block = "\n\n".join(
        f"Q: {m.question}\nA: {(m.summary or '')[:400]}\nSQL: {(m.sql or '')[:200]}"
        for m in memories[-20:]
    )
    cell.content = llm.rebuild_memory_summary(block).strip() or cell.content


def sync_thread_memory(
    db: Session,
    project_id: int,
    *,
    question: str,
    analysis: str = "",
    sql: str = "",
    columns: list | None = None,
    rows: list | None = None,
    bytes_estimate: int | None = None,
    update_summary: bool = True,
) -> None:
    """
    Record every Thread Q&A in this project's notebook (project memory).
    Same question text updates its cells; new questions append in order.
    """
    qtext = (question or "").strip()
    if not qtext:
        return

    columns = columns or []
    rows = rows or []
    key = _ask_key(qtext)
    cells = db.scalars(
        select(NotebookCell)
        .where(NotebookCell.project_id == project_id)
        .order_by(NotebookCell.sort_order, NotebookCell.id)
    ).all()

    from debug_session import debug_log

    debug_log(
        "notebook_api.py:sync_thread_memory",
        "sync_start",
        {
            "project_id": project_id,
            "ask_key": key,
            "has_sql": bool((sql or "").strip()),
            "row_count": len(rows),
            "existing_cells": len(cells),
        },
        hypothesis_id="H7",
    )

    def _find(role: str, cell_type: str | None = None) -> NotebookCell | None:
        for c in cells:
            cfg = _parse_config(c.config_json)
            if (
                cfg.get("source") == "thread"
                and cfg.get("ask_key") == key
                and cfg.get("role") == role
            ):
                if cell_type is None or c.cell_type == cell_type:
                    return c
        return None

    max_order = max((c.sort_order for c in cells), default=-1)

    q_cell = _find("question", "text")
    if q_cell:
        q_cell.content = qtext
        q_cell.name = f"thread_q_{key[:8]}"
    else:
        max_order += 1
        q_cell = NotebookCell(
            project_id=project_id,
            cell_type="text",
            name=f"thread_q_{key[:8]}",
            content=qtext,
            config_json=json.dumps({"source": "thread", "ask_key": key, "role": "question"}),
            sort_order=max_order,
        )
        db.add(q_cell)
        db.flush()
        cells = list(cells) + [q_cell]

    has_sql = bool((sql or "").strip())

    if has_sql:
        a_cell = _find("answer", "text")
        if a_cell:
            db.delete(a_cell)
            db.flush()

        sql_cell = _find("sql", "sql")
        if sql_cell:
            sql_cell.content = sql.strip()
            sql_cell.name = f"thread_sql_{key[:8]}"
            db.execute(delete(NotebookCellRun).where(NotebookCellRun.cell_id == sql_cell.id))
        else:
            max_order += 1
            sql_cell = NotebookCell(
                project_id=project_id,
                cell_type="sql",
                name=f"thread_sql_{key[:8]}",
                content=sql.strip(),
                config_json=json.dumps(
                    {"source": "thread", "ask_key": key, "role": "sql", "question_id": q_cell.id}
                ),
                sort_order=max_order,
            )
            db.add(sql_cell)
            db.flush()

        cap = config.NOTEBOOK_MAX_ROWS
        db.add(
            NotebookCellRun(
                project_id=project_id,
                cell_id=sql_cell.id,
                cell_name=sql_cell.name,
                sql=sql.strip(),
                summary=(analysis or "")[:2000],
                columns_json=json.dumps(columns),
                rows_json=json.dumps(rows[:cap], default=str),
                bytes_estimate=bytes_estimate,
            )
        )
    else:
        sql_cell = _find("sql", "sql")
        if sql_cell:
            db.execute(delete(NotebookCellRun).where(NotebookCellRun.cell_id == sql_cell.id))
            db.delete(sql_cell)
            db.flush()

        answer_text = (analysis or "").strip() or "(No written answer)"
        a_cell = _find("answer", "text")
        if a_cell:
            a_cell.content = answer_text
            a_cell.name = f"thread_a_{key[:8]}"
        else:
            max_order += 1
            db.add(
                NotebookCell(
                    project_id=project_id,
                    cell_type="text",
                    name=f"thread_a_{key[:8]}",
                    content=answer_text,
                    config_json=json.dumps(
                        {"source": "thread", "ask_key": key, "role": "answer", "question_id": q_cell.id}
                    ),
                    sort_order=max_order,
                )
            )

    if update_summary:
        refresh_project_summary(
            db,
            project_id,
            question=qtext,
            analysis=analysis,
            sql=sql if has_sql else "",
        )


def sync_notebook_steps(
    db: Session,
    project_id: int,
    *,
    question: str,
    sql_steps: list[dict[str, Any]],
    analysis: str = "",
) -> None:
    """Persist Hex-style multi-step SQL as notebook cells (one cell per step)."""
    qtext = (question or "").strip()
    if not qtext or not sql_steps:
        return

    key = _ask_key(qtext)
    cells = db.scalars(
        select(NotebookCell)
        .where(NotebookCell.project_id == project_id)
        .order_by(NotebookCell.sort_order, NotebookCell.id)
    ).all()
    max_order = max((c.sort_order for c in cells), default=-1)

    # Remove prior step cells for this ask
    for c in list(cells):
        cfg = _parse_config(c.config_json)
        if cfg.get("source") == "thread" and cfg.get("ask_key") == key and cfg.get("role") == "chain_step":
            db.execute(delete(NotebookCellRun).where(NotebookCellRun.cell_id == c.id))
            db.delete(c)
    db.flush()

    cells = db.scalars(
        select(NotebookCell)
        .where(NotebookCell.project_id == project_id)
        .order_by(NotebookCell.sort_order, NotebookCell.id)
    ).all()
    max_order = max((c.sort_order for c in cells), default=-1)

    q_cell = None
    for c in cells:
        cfg = _parse_config(c.config_json)
        if cfg.get("source") == "thread" and cfg.get("ask_key") == key and cfg.get("role") == "question":
            q_cell = c
            break

    if not q_cell:
        max_order += 1
        q_cell = NotebookCell(
            project_id=project_id,
            cell_type="text",
            name=f"thread_q_{key[:8]}",
            content=qtext,
            config_json=json.dumps({"source": "thread", "ask_key": key, "role": "question"}),
            sort_order=max_order,
        )
        db.add(q_cell)
        db.flush()

    cap = config.NOTEBOOK_MAX_ROWS
    for i, step in enumerate(sql_steps, 1):
        sql = (step.get("sql") or "").strip()
        if not sql:
            continue
        label = step.get("label") or f"Step {i}"
        max_order += 1
        safe_label = re.sub(r"[^\w\s-]", "", label)[:40].strip().replace(" ", "_") or f"step_{i}"
        cell = NotebookCell(
            project_id=project_id,
            cell_type="sql",
            name=f"thread_{key[:6]}_{safe_label}",
            content=sql,
            config_json=json.dumps(
                {
                    "source": "thread",
                    "ask_key": key,
                    "role": "chain_step",
                    "step_index": i,
                    "step_label": label,
                    "question_id": q_cell.id,
                }
            ),
            sort_order=max_order,
        )
        db.add(cell)
        db.flush()
        db.add(
            NotebookCellRun(
                project_id=project_id,
                cell_id=cell.id,
                cell_name=cell.name,
                sql=sql,
                summary=(analysis or "")[:500] if i == len(sql_steps) else label,
                columns_json=json.dumps(step.get("columns") or []),
                rows_json=json.dumps((step.get("rows") or [])[:cap], default=str),
                bytes_estimate=step.get("bytes_estimate"),
            )
        )


def purge_thread_synced_cells(db: Session, project_id: int) -> int:
    """Remove legacy Thread→Notebook mirror cells (threads are independent now)."""
    cells = db.scalars(select(NotebookCell).where(NotebookCell.project_id == project_id)).all()
    removed = 0
    for c in cells:
        cfg = _parse_config(c.config_json)
        if not _is_thread_import(cfg):
            continue
        db.execute(delete(NotebookCellRun).where(NotebookCellRun.cell_id == c.id))
        db.delete(c)
        removed += 1
    return removed


def build_thread_summary_from_memories(
    db: Session,
    project_id: int,
    thread_id: int | None,
) -> str:
    """Compact rolling summary from thread memories (replaces notebook summary cell)."""
    from db import Memory

    q = select(Memory)
    if thread_id is not None:
        q = q.where(Memory.thread_id == thread_id)
    elif project_id is not None:
        q = q.where(Memory.project_id == project_id)
    else:
        return ""
    memories = db.scalars(q.order_by(Memory.created_at, Memory.id)).all()
    if not memories:
        return ""
    lines = []
    for m in memories[-12:]:
        lines.append(f"Q: {m.question}\nA: {(m.summary or '')[:350]}")
    return "\n\n".join(lines)[:4000]


def backfill_notebook_from_memories(db: Session, project_id: int) -> None:
    """Import any Thread memories not yet mirrored in the notebook."""
    from db import Memory

    memories = db.scalars(
        select(Memory)
        .where(Memory.project_id == project_id)
        .order_by(Memory.created_at, Memory.id)
    ).all()
    if not memories:
        return

    for m in memories:
        try:
            rows = json.loads(m.rows_json or "[]")
        except json.JSONDecodeError:
            rows = []
        try:
            columns = json.loads(m.columns_json or "[]")
        except json.JSONDecodeError:
            columns = []
        sync_thread_memory(
            db,
            project_id,
            question=m.question,
            analysis=m.summary or "",
            sql=m.sql or "",
            columns=columns,
            rows=rows,
            bytes_estimate=m.bytes_estimate,
            update_summary=False,
        )
    rebuild_project_summary_from_memories(db, project_id)
    db.flush()


# Backward-compatible alias
sync_thread_ask = sync_thread_memory
