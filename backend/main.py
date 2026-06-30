"""FastAPI entrypoint. Run locally:  uvicorn main:app --reload
On Render the start command is:  uvicorn main:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import json

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

import bq
import config
import credentials
import llm
import schemas
from db import Memory, Project, ProjectTable, get_db, init_db

credentials.bootstrap_gcp_credentials()  # must run before any GCP client is built

app = FastAPI(title="NexA API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS or ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/health")
def health():
    return {"ok": True}


# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------
@app.get("/projects", response_model=list[schemas.ProjectOut])
def list_projects(db: Session = Depends(get_db)):
    return db.scalars(select(Project).order_by(Project.created_at.desc())).all()


@app.post("/projects", response_model=schemas.ProjectOut)
def create_project(body: schemas.ProjectCreate, db: Session = Depends(get_db)):
    p = Project(name=body.name.strip() or "Untitled project")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _get_project(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "Project not found")
    return p


@app.get("/projects/{project_id}", response_model=schemas.ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db)):
    return _get_project(db, project_id)


@app.delete("/projects/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    db.delete(_get_project(db, project_id))
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# Data tables (the "add tables" section)
# --------------------------------------------------------------------------
@app.get("/projects/{project_id}/tables", response_model=list[schemas.TableOut])
def list_tables(project_id: int, db: Session = Depends(get_db)):
    _get_project(db, project_id)
    return db.scalars(
        select(ProjectTable).where(ProjectTable.project_id == project_id)
    ).all()


@app.post("/projects/{project_id}/tables", response_model=schemas.TableOut)
def add_table(project_id: int, body: schemas.TableCreate, db: Session = Depends(get_db)):
    _get_project(db, project_id)
    t = ProjectTable(project_id=project_id, full_table_id=body.full_table_id.strip())
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@app.delete("/projects/{project_id}/tables/{table_id}")
def remove_table(project_id: int, table_id: int, db: Session = Depends(get_db)):
    t = db.get(ProjectTable, table_id)
    if not t or t.project_id != project_id:
        raise HTTPException(404, "Table not found")
    db.delete(t)
    db.commit()
    return {"ok": True}


@app.put("/projects/{project_id}/join-hints", response_model=schemas.ProjectOut)
def save_join_hints(project_id: int, body: schemas.JoinHintsUpdate, db: Session = Depends(get_db)):
    p = _get_project(db, project_id)
    p.join_hints = body.join_hints
    db.commit()
    db.refresh(p)
    return p


@app.get("/projects/{project_id}/schema")
def get_schema(project_id: int, db: Session = Depends(get_db)):
    p = _get_project(db, project_id)
    table_ids = [t.full_table_id for t in p.tables]
    return {"schema": bq.schema_for_tables(table_ids, p.join_hints)}


# --------------------------------------------------------------------------
# Memory
# --------------------------------------------------------------------------
@app.get("/projects/{project_id}/memory", response_model=list[schemas.MemoryOut])
def list_memory(project_id: int, db: Session = Depends(get_db)):
    _get_project(db, project_id)
    return db.scalars(
        select(Memory).where(Memory.project_id == project_id).order_by(Memory.created_at.asc())
    ).all()


def _recent_memory_text(db: Session, project_id: int) -> str:
    rows = db.scalars(
        select(Memory)
        .where(Memory.project_id == project_id)
        .order_by(Memory.created_at.desc())
        .limit(config.MEMORY_CONTEXT_SIZE)
    ).all()
    rows = list(reversed(rows))  # oldest first
    return "\n\n".join(f"Q: {m.question}\nSQL: {m.sql}\nFinding: {m.summary}" for m in rows)


# --------------------------------------------------------------------------
# Ask  (the core pipeline)
# --------------------------------------------------------------------------
@app.post("/projects/{project_id}/ask", response_model=schemas.AskResponse)
def ask(project_id: int, body: schemas.AskRequest, db: Session = Depends(get_db)):
    p = _get_project(db, project_id)
    table_ids = [t.full_table_id for t in p.tables]
    if not table_ids:
        raise HTTPException(400, "Add at least one table to this project first.")

    schema_text = bq.schema_for_tables(table_ids, p.join_hints)
    memory_text = _recent_memory_text(db, project_id)

    # 1. FETCH model -> SQL
    try:
        sql = bq.validate_select_only(llm.question_to_sql(body.question, schema_text, memory_text))
    except Exception as e:
        raise HTTPException(400, f"Could not generate a safe query: {e}")

    # 2. cost estimate + execute
    try:
        bytes_estimate = bq.dry_run_bytes(sql)
        df = bq.run_query(sql)
    except Exception as e:
        raise HTTPException(400, f"Query failed: {e}")

    rows = json.loads(df.to_json(orient="records", date_format="iso"))
    columns = list(df.columns)
    sample = rows[:50]

    # 3. VIZ model (Vertex) -> chart spec + analysis
    chart_spec = llm.result_to_chart_spec(body.question, columns, sample)
    analysis = llm.analyze(body.question, columns, sample, len(rows))

    # 4. save to memory
    db.add(Memory(project_id=project_id, question=body.question, sql=sql, summary=analysis))
    db.commit()

    return schemas.AskResponse(
        question=body.question,
        sql=sql,
        columns=columns,
        rows=rows,
        chart_spec=chart_spec,
        analysis=analysis,
        bytes_estimate=bytes_estimate,
    )
