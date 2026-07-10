"""FastAPI entrypoint. Run locally:  uvicorn main:app --reload
On Render the start command is:  uvicorn main:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import json
import secrets

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, delete
from sqlalchemy.orm import Session, selectinload

import config
import credentials

credentials.bootstrap_gcp_credentials()  # before any GCP client is built

import bq
import llm
import model_yaml
import notebook_api
import schemas
from ask_pipeline import iter_ask
from result_cache import build_cache_entries
from db import (
    Collection,
    CollectionProject,
    DashboardItem,
    Memory,
    NotebookCell,
    NotebookCellRun,
    Project,
    ProjectTable,
    Thread,
    WorkspaceTable,
    UsageLog,
    SqlVerificationLog,
    User,
    get_db,
    get_workspace_join_hints,
    init_db,
    set_workspace_join_hints,
)
from sql_verify_log import SqlAuditContext
from auth import (
    create_access_token,
    ensure_project_access,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)
from credits import apply_usage_charge, enrich_result_with_credits

bq.reset_client()

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
    # Fail fast if sql_guard is broken (avoids obscure NameError on first ask).
    from sql_guard import build_constraints
    import sql_parse

    class _SmokeTable:
        full_table_id = "smoke.dataset.table"

    build_constraints("how many rows", [_SmokeTable()], {}, {})
    sql_parse.parse_bigquery("SELECT 1")

    from debug_session import clear_ask_log

    clear_ask_log()

    # One-time AI profiling of any tables missing an overview (non-blocking).
    import threading

    threading.Thread(target=_backfill_ai_overviews, daemon=True).start()
    if config.EMBEDDING_AUTO_INDEX_ON_STARTUP:
        threading.Thread(target=_backfill_table_embeddings, daemon=True).start()
    threading.Thread(target=_start_pattern_miner, daemon=True).start()


def _start_pattern_miner() -> None:
    from agents.pattern_miner import get_pattern_miner, start_pattern_miner_refresh_loop

    try:
        count = get_pattern_miner().refresh()
        print(f"[pattern-miner] initial load: {count} patterns")
    except Exception as exc:
        print(f"[pattern-miner] initial load failed: {exc}")
    start_pattern_miner_refresh_loop()


def _backfill_ai_overviews() -> None:
    """Profile workspace tables lacking an AI overview — runs once in background."""
    import table_profile
    import vector_index
    from db import SessionLocal

    db = SessionLocal()
    try:
        tables = db.scalars(select(WorkspaceTable)).all()
        for t in tables:
            if (t.ai_overview or "").strip():
                continue
            try:
                if table_profile.ensure_table_overview(db, t):
                    db.commit()
                    print(f"[ai-overview] profiled {t.full_table_id}")
                    if config.EMBEDDING_AUTO_INDEX_ON_STARTUP:
                        vector_index.ensure_table_embedding(db, t, force=True)
                        db.commit()
            except Exception as e:
                db.rollback()
                print(f"[ai-overview] failed {t.full_table_id}: {e}")
    finally:
        db.close()


def _backfill_table_embeddings() -> None:
    """Build semantic retrieval vectors for workspace tables in the background."""
    import vector_index
    from db import SessionLocal

    db = SessionLocal()
    try:
        result = vector_index.ensure_workspace_embeddings(db)
        db.commit()
        print(f"[vector-index] {result}")
    except Exception as e:
        db.rollback()
        print(f"[vector-index] failed: {e}")
    finally:
        db.close()


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/setup/status")
def setup_status():
    import credentials as creds

    issues: list[str] = []
    project = (config.GCP_PROJECT or "").strip()
    project_ok = bool(project) and project != "your-gcp-project-id"
    if not project_ok:
        issues.append("Set GCP_PROJECT in backend/.env to your real GCP project id.")

    if not creds.credentials_ready:
        issues.append(creds.credentials_message)

    warehouse_ok = False
    dataset_count = 0
    if creds.credentials_ready and project_ok:
        try:
            datasets = bq.warehouse_datasets()
            warehouse_ok = True
            dataset_count = len(datasets)
        except Exception as e:
            issues.append(str(e))

    sa_email = creds.service_account_email()

    return {
        "gcp_ok": warehouse_ok and project_ok,
        "issues": issues,
        "gcp_project": project or None,
        "service_account_email": sa_email,
        "key_file": creds.expected_key_path(),
        "credentials_ready": creds.credentials_ready,
        "credentials_message": creds.credentials_message,
        "default_dataset": config.BQ_DEFAULT_DATASET or None,
        "default_dataset_full_id": (
            f"{project}.{config.BQ_DEFAULT_DATASET}"
            if project and config.BQ_DEFAULT_DATASET
            else None
        ),
        "dataset_count": dataset_count,
        "bq_location": config.BQ_LOCATION or None,
        "llm": {
            "sql_provider": config.SQL_PROVIDER or config.FETCH_PROVIDER,
            "fetch_provider": config.FETCH_PROVIDER,
            "fetch_model": config.FETCH_MODEL,
            "viz_provider": config.VIZ_PROVIDER,
            "viz_model": config.VIZ_MODEL,
            "openai_base_url": config.OPENAI_BASE_URL or None,
            "openai_key_set": bool(config.OPENAI_API_KEY),
            "anthropic_key_set": bool(config.ANTHROPIC_API_KEY),
            "gemini_key_set": bool(config.GEMINI_API_KEY),
            "vertex_ready": bool(config.GCP_PROJECT),
        },
        "accuracy": {
            "sql_max_attempts": config.SQL_MAX_ATTEMPTS,
            "sql_verify_with_llm": config.SQL_VERIFY_WITH_LLM,
            "require_sql_approval": config.REQUIRE_SQL_APPROVAL,
            "sql_chain_enabled": config.SQL_CHAIN_ENABLED,
            "sql_chain_max_steps": config.SQL_CHAIN_MAX_STEPS,
            "cache_answer_enabled": config.CACHE_ANSWER_ENABLED,
            "embedding_retrieval_enabled": config.EMBEDDING_RETRIEVAL_ENABLED,
            "embedding_provider": config.EMBEDDING_PROVIDER,
            "embedding_model": config.EMBEDDING_MODEL,
        },
    }


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
@app.post("/auth/login", response_model=schemas.LoginResponse)
def login(body: schemas.LoginRequest, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    if not user:
        raise HTTPException(
            401,
            "No account for that email. Check spelling, or ask an admin to create/reset your user.",
        )
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(
            401,
            "Wrong password. Ask an admin to use Reset password on the Admin page.",
        )
    if not user.is_active:
        raise HTTPException(403, "Account disabled — ask an admin to reactivate it.")
    token = create_access_token(user)
    return schemas.LoginResponse(token=token, user=user)


@app.get("/auth/me", response_model=schemas.UserOut)
def auth_me(user: User = Depends(get_current_user)):
    return user


# --------------------------------------------------------------------------
# Admin — users & usage
# --------------------------------------------------------------------------
@app.get("/admin/users", response_model=list[schemas.UserOut])
def admin_list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return db.scalars(select(User).order_by(User.created_at.desc())).all()


@app.post("/admin/users", response_model=schemas.UserOut)
def admin_create_user(
    body: schemas.UserCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    email = body.email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(400, "Email already registered")
    u = User(
        email=email,
        name=(body.name or email.split("@")[0]).strip(),
        password_hash=hash_password(body.password),
        role="user",
        credits_balance=body.credits_balance
        if body.credits_balance is not None
        else config.DEFAULT_USER_CREDITS,
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@app.patch("/admin/users/{user_id}", response_model=schemas.UserOut)
def admin_update_user(
    user_id: int,
    body: schemas.UserUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    if body.name is not None:
        u.name = body.name.strip()
    if body.credits_balance is not None:
        u.credits_balance = body.credits_balance
    if body.is_active is not None:
        u.is_active = body.is_active
    if body.password:
        u.password_hash = hash_password(body.password)
    db.commit()
    db.refresh(u)
    return u


@app.delete("/admin/users/{user_id}")
def admin_delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    if u.id == admin.id:
        raise HTTPException(400, "You cannot delete your own account")
    if u.role == "admin":
        admin_count = db.scalar(
            select(func.count()).select_from(User).where(User.role == "admin")
        )
        if admin_count <= 1:
            raise HTTPException(400, "Cannot delete the only admin account")
    db.delete(u)
    db.commit()
    return {"ok": True}


@app.get("/admin/usage", response_model=list[schemas.UsageLogOut])
def admin_usage(
    limit: int = 100,
    user_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    q = select(UsageLog).order_by(UsageLog.created_at.desc()).limit(min(limit, 500))
    if user_id is not None:
        q = q.where(UsageLog.user_id == user_id)
    rows = db.scalars(q).all()
    return [
        schemas.UsageLogOut(
            id=r.id,
            user_id=r.user_id,
            project_id=r.project_id,
            action=r.action,
            bytes_estimate=r.bytes_estimate,
            credits_used=r.credits_used,
            detail=r.detail,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]


@app.get("/admin/sql-verification-logs", response_model=list[schemas.SqlVerificationLogOut])
def admin_sql_verification_logs(
    limit: int = 100,
    project_id: int | None = None,
    passed: bool | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    q = select(SqlVerificationLog).order_by(SqlVerificationLog.created_at.desc()).limit(
        min(limit, 500)
    )
    if project_id is not None:
        q = q.where(SqlVerificationLog.project_id == project_id)
    if passed is not None:
        q = q.where(SqlVerificationLog.passed == passed)
    rows = db.scalars(q).all()
    out: list[schemas.SqlVerificationLogOut] = []
    for r in rows:
        issues: list[str] = []
        try:
            issues = json.loads(r.issues_json or "[]")
            if not isinstance(issues, list):
                issues = []
        except json.JSONDecodeError:
            issues = []
        out.append(
            schemas.SqlVerificationLogOut(
                id=r.id,
                project_id=r.project_id,
                user_id=r.user_id,
                question=r.question,
                sql=r.sql,
                attempt=r.attempt,
                phase=r.phase,
                passed=r.passed,
                issues=[str(i) for i in issues if str(i).strip()],
                source=r.source,
                llm_provider=r.llm_provider,
                llm_model=r.llm_model,
                created_at=r.created_at.isoformat() if r.created_at else None,
            )
        )
    return out


@app.post("/admin/promote-templates")
def admin_promote_templates(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Promote frequently successful SQL patterns to learned templates."""
    from agents.template_promoter import TemplatePromoterAgent

    promoted = TemplatePromoterAgent().run_promotion_cycle(db)
    return {"promoted": promoted, "count": len(promoted)}


# --------------------------------------------------------------------------
# Admin — Org Schema (saved snapshot of the full workspace table catalog)
# --------------------------------------------------------------------------
@app.get("/admin/org-schema")
def admin_get_org_schema(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Return the saved org schema, building and saving it on first access."""
    import org_schema

    return org_schema.get_or_build_org_schema(db)


@app.post("/admin/org-schema/rebuild")
def admin_rebuild_org_schema(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Regenerate the org schema from the current workspace catalog and save it."""
    import org_schema

    return org_schema.get_or_build_org_schema(db, rebuild=True)


@app.get("/admin/org-schema/markdown")
def admin_org_schema_markdown(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Markdown export of the saved org schema (for copy/download)."""
    import org_schema

    doc = org_schema.get_or_build_org_schema(db)
    return {"markdown": org_schema.schema_markdown(doc)}


# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------
def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _project_status(last_activity, created_at) -> str:
    import datetime as _dt

    ref = last_activity or created_at
    if not ref:
        return ""
    now = _dt.datetime.now(ref.tzinfo) if ref.tzinfo else _dt.datetime.now()
    age = now - ref
    if age.days < 1:
        return "Active"
    if age.days < 7:
        return "In progress"
    return ""


@app.get("/projects", response_model=list[schemas.ProjectListOut])
def list_projects(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    projects = db.scalars(select(Project).order_by(Project.created_at.desc())).all()
    owner_ids = {p.owner_id for p in projects if p.owner_id}
    owners = {
        u.id: (u.name or u.email or "")
        for u in db.scalars(select(User).where(User.id.in_(owner_ids))).all()
    } if owner_ids else {}

    # One query for per-project thread stats.
    stats = {
        row[0]: (row[1], row[2])
        for row in db.execute(
            select(
                Memory.project_id,
                func.count(Memory.id),
                func.max(Memory.created_at),
            ).group_by(Memory.project_id)
        ).all()
    }

    out: list[schemas.ProjectListOut] = []
    for p in projects:
        thread_count, last_activity = stats.get(p.id, (0, None))
        try:
            categories = json.loads(getattr(p, "categories_json", "[]") or "[]")
            if not isinstance(categories, list):
                categories = []
        except json.JSONDecodeError:
            categories = []
        out.append(
            schemas.ProjectListOut(
                id=p.id,
                name=p.name,
                owner_name=owners.get(p.owner_id, ""),
                status=(getattr(p, "status", "") or "").strip()
                or _project_status(last_activity, p.created_at),
                categories=[str(c) for c in categories],
                thread_count=thread_count,
                view_count=int(getattr(p, "view_count", 0) or 0),
                created_at=_iso(p.created_at),
                last_activity_at=_iso(last_activity),
                last_viewed_at=_iso(getattr(p, "last_viewed_at", None)),
                share_token=p.share_token,
                notebook_enabled=p.notebook_enabled,
                reuse_cached_results=p.reuse_cached_results,
                join_hints=p.join_hints or "",
            )
        )
    return out


def _user_names(db: Session, ids: set[int | None]) -> dict[int, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {
        u.id: (u.name or u.email or "")
        for u in db.scalars(select(User).where(User.id.in_(ids))).all()
    }


@app.get("/threads", response_model=list[schemas.ThreadListOut])
def list_all_threads(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Hex-style global Threads list — standalone + optional project-linked."""
    threads = db.scalars(
        select(Thread)
        .where(Thread.created_by == user.id)
        .order_by(Thread.updated_at.desc())
    ).all()
    project_names = {p.id: p.name for p in db.scalars(select(Project)).all()}
    creators = _user_names(db, {t.created_by for t in threads})
    counts = {
        row[0]: (row[1], row[2])
        for row in db.execute(
            select(
                Memory.thread_id,
                func.count(Memory.id),
                func.max(Memory.created_at),
            ).group_by(Memory.thread_id)
        ).all()
    }

    out: list[schemas.ThreadListOut] = []
    for t in threads:
        turn_count, last_mem = counts.get(t.id, (0, None))
        out.append(
            schemas.ThreadListOut(
                id=t.id,
                project_id=t.project_id,
                project_name=project_names.get(t.project_id, "") if t.project_id else "",
                title=(t.title or "New thread").strip()[:140],
                creator=creators.get(t.created_by, ""),
                turn_count=int(turn_count),
                last_updated_at=_iso(last_mem or t.updated_at or t.created_at),
            )
        )
    out.sort(key=lambda t: t.last_updated_at or "", reverse=True)
    return out


@app.post("/threads", response_model=schemas.ThreadOut)
def create_standalone_thread(
    body: schemas.ThreadCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a thread that is not tied to any project."""
    t = Thread(
        project_id=None,
        title=(body.title or "").strip()[:300] or "New thread",
        created_by=user.id,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _thread_out(db, t)


def _get_user_thread(db: Session, thread_id: int, user: User) -> Thread:
    t = db.get(Thread, thread_id)
    if not t:
        raise HTTPException(404, "Thread not found")
    if t.created_by and t.created_by != user.id and user.role != "admin":
        raise HTTPException(403, "Not allowed")
    return t


@app.get("/threads/{thread_id}", response_model=schemas.ThreadOut)
def get_standalone_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = _get_user_thread(db, thread_id, user)
    if not (t.overview_kb or "").strip():
        from thread_kb import refresh_thread_overview

        refresh_thread_overview(db, t.id)
        db.commit()
        db.refresh(t)
    return _thread_out(db, t)


@app.patch("/threads/{thread_id}", response_model=schemas.ThreadOut)
def rename_standalone_thread(
    thread_id: int,
    body: schemas.ThreadPatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = _get_user_thread(db, thread_id, user)
    t.title = body.title.strip()[:300] or t.title
    db.commit()
    db.refresh(t)
    return _thread_out(db, t)


@app.delete("/threads/{thread_id}")
def delete_standalone_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = _get_user_thread(db, thread_id, user)
    db.execute(delete(Memory).where(Memory.thread_id == t.id))
    if t.project_id:
        p = db.get(Project, t.project_id)
        if p:
            notebook_api.purge_thread_synced_cells(db, p.id)
    db.delete(t)
    db.commit()
    return {"ok": True}


@app.get("/threads/{thread_id}/memory", response_model=list[schemas.MemoryOut])
def list_thread_memory(
    thread_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_user_thread(db, thread_id, user)
    rows = db.scalars(
        select(Memory)
        .where(Memory.thread_id == thread_id)
        .order_by(Memory.created_at.asc())
    ).all()
    return [_memory_out(m) for m in rows]


@app.delete("/threads/{thread_id}/memory")
def clear_thread_memory(
    thread_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_user_thread(db, thread_id, user)
    db.execute(delete(Memory).where(Memory.thread_id == thread_id))
    db.commit()
    return {"ok": True, "cleared": True}


def _cache_entries_for_thread(db: Session, thread_id: int) -> list[dict]:
    memories = db.scalars(
        select(Memory)
        .where(Memory.thread_id == thread_id)
        .order_by(Memory.created_at.desc())
        .limit(config.CACHE_LOOKUP_SIZE)
    ).all()
    return build_cache_entries(list(reversed(memories)), [])


def _thread_context_for_ask(
    db: Session,
    thread_id: int,
    join_hints: str = "",
    question: str = "",
) -> str:
    from project_context import build_sql_context
    from question_intent import detect_intent, question_is_breakdown_followup, question_wants_breakdown
    from result_cache import _FOLLOWUP, _REF_PRIOR
    from thread_kb import get_thread_overview

    latest = db.scalar(
        select(Memory)
        .where(Memory.thread_id == thread_id)
        .order_by(Memory.created_at.desc(), Memory.id.desc())
        .limit(1)
    )
    prior_sql = (latest.sql or "").strip() if latest else ""
    prior_q = (latest.question or "").strip() if latest else ""

    overview = get_thread_overview(db, thread_id)
    summary = overview or notebook_api.build_thread_summary_from_memories(db, None, thread_id)
    has_history = bool(
        db.scalar(select(Memory.id).where(Memory.thread_id == thread_id).limit(1))
    )
    q = (question or "").strip()
    references_prior = bool(
        _REF_PRIOR.search(q)
        or _FOLLOWUP.search(q)
        or question_is_breakdown_followup(q, prior_question=prior_q, prior_sql=prior_sql)
        or (question_wants_breakdown(q) and has_history and prior_sql)
    )
    compact = bool(
        summary
        and has_history
        and detect_intent(q, has_thread_history=has_history) == "data_query"
        and len(q.split()) <= 24
        and not references_prior
    )
    limit = 4 if references_prior else (2 if compact else config.MEMORY_CONTEXT_SIZE)
    rows = db.scalars(
        select(Memory)
        .where(Memory.thread_id == thread_id)
        .order_by(Memory.created_at.desc())
        .limit(limit)
    ).all()
    memories = list(reversed(rows))
    return build_sql_context(
        thread_memories=memories,
        notebook_runs=[],
        join_hints=join_hints,
        memory_summary=summary,
        compact=compact,
    )


def _ask_thread_stream_impl(body: schemas.AskRequest, thread_id: int, db: Session, user: User):
    t = _get_user_thread(db, thread_id, user)
    included = _included_tables(db, None)
    ws_hints = get_workspace_join_hints(db)
    memory_text = _thread_context_for_ask(db, t.id, ws_hints, body.question)
    # Always load thread cache for conversational continuity (prior SQL / table).
    # force_fresh only skips reusing cached *answers*, not thread memory.
    cache_entries = _cache_entries_for_thread(db, t.id)

    if not included:
        def empty_tables():
            yield f"data: {json.dumps({'type': 'error', 'message': 'No tables in the workspace yet. An admin must add tables in the Data tab first.'})}\n\n"
        return StreamingResponse(empty_tables(), media_type="text/event-stream")

    def event_stream():
        result = None
        audit = SqlAuditContext(db=db, project_id=t.project_id, user_id=user.id)
        try:
            for event in iter_ask(
                body.question,
                memory_text,
                included_tables=included,
                join_hints=ws_hints,
                cache_entries=cache_entries,
                reuse_cached=False,
                force_fresh=body.force_fresh,
                clarification_choice=body.clarification_choice,
                clarification_text=body.clarification_text,
                refined_question=body.refined_question,
                pinned_table_ids=body.pinned_table_ids,
                audit=audit,
            ):
                if event.get("type") == "complete":
                    result = event
                    result["thread_id"] = t.id
                    try:
                        _finish_ask_result(
                            db,
                            t.project_id,
                            result,
                            user,
                            thread_id=t.id,
                        )
                    except ValueError as e:
                        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                        return
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            return

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/threads/{thread_id}/ask/stream")
def ask_thread_stream(
    thread_id: int,
    body: schemas.AskRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _ask_thread_stream_impl(body, thread_id, db, user)


@app.post("/threads/{thread_id}/ask/confirm/stream")
def ask_thread_confirm_stream(
    thread_id: int,
    body: schemas.AskConfirmRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = _get_user_thread(db, thread_id, user)
    included = _included_tables(db, None)
    if not included:
        raise HTTPException(400, "No tables in the workspace yet.")
    ws_hints = get_workspace_join_hints(db)
    memory_text = _thread_context_for_ask(db, t.id, ws_hints, body.question)

    def event_stream():
        audit = SqlAuditContext(db=db, project_id=t.project_id, user_id=user.id)
        try:
            for event in iter_ask(
                body.question,
                memory_text,
                included_tables=included,
                join_hints=ws_hints,
                preapproved_sql=body.sql,
                audit=audit,
            ):
                if event.get("type") == "complete":
                    event["thread_id"] = t.id
                    _save_ask_memory(db, t.project_id, event, user, thread_id=t.id)
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/projects", response_model=schemas.ProjectOut)
def create_project(
    body: schemas.ProjectCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    p = Project(name=body.name.strip() or "Untitled project", owner_id=user.id, notebook_enabled=True)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _get_project(db: Session, project_id: int) -> Project:
    p = db.scalar(
        select(Project)
        .where(Project.id == project_id)
        .options(selectinload(Project.tables))
    )
    if not p:
        raise HTTPException(404, "Project not found")
    return p


def require_project(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    p = _get_project(db, project_id)
    ensure_project_access(p, user)
    return p


@app.post("/projects/{project_id}/view")
def track_project_view(
    p: Project = Depends(require_project),
    db: Session = Depends(get_db),
):
    """Increment view counter + stamp last_viewed_at (Hex-style tracking)."""
    import datetime as _dt

    p.view_count = int(getattr(p, "view_count", 0) or 0) + 1
    p.last_viewed_at = _dt.datetime.now(_dt.timezone.utc)
    db.commit()
    return {"ok": True, "view_count": p.view_count}


# --------------------------------------------------------------------------
# Collections (Hex-style groups of projects)
# --------------------------------------------------------------------------
def _collection_out(db: Session, c: Collection, owners: dict[int, str] | None = None) -> schemas.CollectionOut:
    project_ids = [
        row[0]
        for row in db.execute(
            select(CollectionProject.project_id).where(CollectionProject.collection_id == c.id)
        ).all()
    ]
    owner = ""
    if owners is not None:
        owner = owners.get(c.owner_id, "")
    elif c.owner_id:
        u = db.get(User, c.owner_id)
        owner = (u.name or u.email or "") if u else ""
    return schemas.CollectionOut(
        id=c.id,
        name=c.name,
        description=c.description or "",
        owner_name=owner,
        project_count=len(project_ids),
        project_ids=project_ids,
        created_at=_iso(c.created_at),
    )


@app.get("/collections", response_model=list[schemas.CollectionOut])
def list_collections(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    collections = db.scalars(select(Collection).order_by(Collection.name)).all()
    owners = _user_names(db, {c.owner_id for c in collections})
    return [_collection_out(db, c, owners) for c in collections]


@app.post("/collections", response_model=schemas.CollectionOut)
def create_collection(
    body: schemas.CollectionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    c = Collection(
        name=body.name.strip()[:200] or "Untitled collection",
        description=(body.description or "").strip(),
        owner_id=user.id,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return _collection_out(db, c)


def _get_collection(db: Session, collection_id: int) -> Collection:
    c = db.get(Collection, collection_id)
    if not c:
        raise HTTPException(404, "Collection not found")
    return c


@app.patch("/collections/{collection_id}", response_model=schemas.CollectionOut)
def update_collection(
    collection_id: int,
    body: schemas.CollectionPatch,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    c = _get_collection(db, collection_id)
    if body.name is not None and body.name.strip():
        c.name = body.name.strip()[:200]
    if body.description is not None:
        c.description = body.description.strip()
    db.commit()
    db.refresh(c)
    return _collection_out(db, c)


@app.delete("/collections/{collection_id}")
def delete_collection(
    collection_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    c = _get_collection(db, collection_id)
    db.delete(c)
    db.commit()
    return {"ok": True}


@app.post("/collections/{collection_id}/projects", response_model=schemas.CollectionOut)
def add_project_to_collection(
    collection_id: int,
    body: schemas.CollectionProjectIn,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    c = _get_collection(db, collection_id)
    if not db.get(Project, body.project_id):
        raise HTTPException(404, "Project not found")
    exists = db.scalar(
        select(CollectionProject).where(
            CollectionProject.collection_id == c.id,
            CollectionProject.project_id == body.project_id,
        )
    )
    if not exists:
        db.add(CollectionProject(collection_id=c.id, project_id=body.project_id))
        db.commit()
    return _collection_out(db, c)


@app.delete("/collections/{collection_id}/projects/{project_id}", response_model=schemas.CollectionOut)
def remove_project_from_collection(
    collection_id: int,
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    c = _get_collection(db, collection_id)
    db.execute(
        delete(CollectionProject).where(
            CollectionProject.collection_id == c.id,
            CollectionProject.project_id == project_id,
        )
    )
    db.commit()
    return _collection_out(db, c)


# --------------------------------------------------------------------------
# Threads (first-class conversations inside a project)
# --------------------------------------------------------------------------
def _thread_out(db: Session, t: Thread, creators: dict[int, str] | None = None) -> schemas.ThreadOut:
    count = db.scalar(select(func.count(Memory.id)).where(Memory.thread_id == t.id)) or 0
    creator = ""
    if creators is not None:
        creator = creators.get(t.created_by, "")
    elif t.created_by:
        u = db.get(User, t.created_by)
        creator = (u.name or u.email or "") if u else ""
    return schemas.ThreadOut(
        id=t.id,
        project_id=t.project_id,
        title=t.title or "New thread",
        creator=creator,
        turn_count=int(count),
        overview_kb=(t.overview_kb or "").strip(),
        created_at=_iso(t.created_at),
        updated_at=_iso(t.updated_at),
    )


def _get_thread(db: Session, project_id: int, thread_id: int) -> Thread:
    t = db.scalar(
        select(Thread).where(Thread.id == thread_id, Thread.project_id == project_id)
    )
    if not t:
        raise HTTPException(404, "Thread not found in this project")
    return t


def _resolve_thread(
    db: Session,
    project_id: int,
    thread_id: int | None,
    user: User,
) -> Thread:
    """Return the requested thread, or fall back to the latest / a fresh one."""
    if thread_id:
        return _get_thread(db, project_id, thread_id)
    t = db.scalar(
        select(Thread)
        .where(Thread.project_id == project_id)
        .order_by(Thread.updated_at.desc())
        .limit(1)
    )
    if t:
        return t
    t = Thread(project_id=project_id, title="New thread", created_by=user.id)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@app.get("/projects/{project_id}/threads", response_model=list[schemas.ThreadOut])
def list_project_threads(
    p: Project = Depends(require_project),
    db: Session = Depends(get_db),
):
    threads = db.scalars(
        select(Thread).where(Thread.project_id == p.id).order_by(Thread.updated_at.desc())
    ).all()
    creators = _user_names(db, {t.created_by for t in threads})
    return [_thread_out(db, t, creators) for t in threads]


@app.post("/projects/{project_id}/threads", response_model=schemas.ThreadOut)
def create_thread(
    body: schemas.ThreadCreate,
    p: Project = Depends(require_project),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = Thread(
        project_id=p.id,
        title=(body.title or "").strip()[:300] or "New thread",
        created_by=user.id,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _thread_out(db, t)


@app.patch("/projects/{project_id}/threads/{thread_id}", response_model=schemas.ThreadOut)
def rename_thread(
    thread_id: int,
    body: schemas.ThreadPatch,
    p: Project = Depends(require_project),
    db: Session = Depends(get_db),
):
    t = _get_thread(db, p.id, thread_id)
    t.title = body.title.strip()[:300] or t.title
    db.commit()
    db.refresh(t)
    return _thread_out(db, t)


@app.delete("/projects/{project_id}/threads/{thread_id}")
def delete_thread(
    thread_id: int,
    p: Project = Depends(require_project),
    db: Session = Depends(get_db),
):
    t = _get_thread(db, p.id, thread_id)
    db.execute(delete(Memory).where(Memory.thread_id == t.id))
    db.delete(t)
    _ensure_notebook(db, p)
    notebook_api.purge_thread_synced_cells(db, p.id)
    db.commit()
    return {"ok": True}


def _workspace_tables(db: Session) -> list[WorkspaceTable]:
    """All tables in the workspace catalog — shared across every project."""
    return list(
        db.scalars(select(WorkspaceTable).order_by(WorkspaceTable.full_table_id)).all()
    )


def _included_tables(db: Session, p: Project | None = None) -> list[WorkspaceTable]:
    """Tables available for Ask — workspace-wide once added by any admin."""
    _ = p
    return [t for t in _workspace_tables(db) if t.included_for_ai]


def _parse_column_descriptions(raw: str | None) -> dict[str, str]:
    try:
        data = json.loads(raw or "{}")
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items() if str(v).strip()}
    except (json.JSONDecodeError, TypeError):
        return {}


def _parse_column_hints(raw: str | None) -> dict[str, str]:
    try:
        data = json.loads(raw or "{}")
        if not isinstance(data, dict):
            return {}
        allowed = {"primary_field", "primary_key", "primary_date", "feedback_field", "deprecated_duplicate"}
        return {str(k): str(v) for k, v in data.items() if str(v) in allowed}
    except (json.JSONDecodeError, TypeError):
        return {}


def _has_embedding(raw: str | None) -> bool:
    try:
        data = json.loads(raw or "[]")
        return isinstance(data, list) and len(data) > 0
    except (json.JSONDecodeError, TypeError):
        return False


def _project_table_out(t: WorkspaceTable) -> schemas.TableOut:
    return schemas.TableOut(
        id=t.id,
        full_table_id=t.full_table_id,
        description=t.description or "",
        column_descriptions=_parse_column_descriptions(t.column_descriptions_json),
        column_hints=_parse_column_hints(t.column_hints_json),
        business_rules=getattr(t, "business_rules", "") or "",
        ai_overview=getattr(t, "ai_overview", "") or "",
        embedding_indexed=_has_embedding(getattr(t, "embedding_json", "") or ""),
        embedding_model=getattr(t, "embedding_model", "") or "",
        embedding_updated_at=(
            t.embedding_updated_at.isoformat()
            if getattr(t, "embedding_updated_at", None)
            else None
        ),
        included_for_ai=t.included_for_ai,
        endorsed=t.endorsed,
    )


def _validate_table_id(full_table_id: str) -> str:
    """BigQuery requires project.dataset.table — not just the table name."""
    fq = full_table_id.strip()
    parts = fq.split(".")
    if len(parts) != 3 or not all(parts):
        raise HTTPException(
            400,
            "Table ID must be project.dataset.table "
            f"(e.g. kossip-helpers.analytics.z_ccbp_academy_users_jobs_details). Got: {fq!r}",
        )
    return fq


@app.get("/projects/{project_id}", response_model=schemas.ProjectOut)
def get_project(p: Project = Depends(require_project)):
    return p


@app.patch("/projects/{project_id}/settings", response_model=schemas.ProjectOut)
def update_project_settings(
    body: schemas.ProjectSettingsUpdate,
    db: Session = Depends(get_db),
    p: Project = Depends(require_project),
):
    if body.notebook_enabled is not None:
        p.notebook_enabled = body.notebook_enabled
    if body.reuse_cached_results is not None:
        p.reuse_cached_results = body.reuse_cached_results
    if body.status is not None:
        p.status = body.status.strip()[:40]
    if body.categories is not None:
        p.categories_json = json.dumps([str(c).strip()[:60] for c in body.categories if str(c).strip()])
    if body.name is not None and body.name.strip():
        p.name = body.name.strip()[:200]
    db.commit()
    db.refresh(p)
    return p


@app.delete("/projects/{project_id}")
def delete_project(db: Session = Depends(get_db), p: Project = Depends(require_project)):
    import model_yaml

    hint = (p.join_hints or "").strip()
    if hint:
        current = get_workspace_join_hints(db)
        merged = model_yaml.merge_join_hints(current, [hint])
        if merged.strip() and merged != current:
            set_workspace_join_hints(db, merged)
    db.delete(p)
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# Workspace catalog (tables, join hints, YAML — shared; survives project delete)
# --------------------------------------------------------------------------
@app.get("/workspace/join-hints", response_model=schemas.WorkspaceJoinHintsOut)
def get_workspace_join_hints_api(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return schemas.WorkspaceJoinHintsOut(join_hints=get_workspace_join_hints(db))


@app.put("/workspace/join-hints", response_model=schemas.WorkspaceJoinHintsOut)
def save_workspace_join_hints_api(
    body: schemas.JoinHintsUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    set_workspace_join_hints(db, body.join_hints)
    db.commit()
    return schemas.WorkspaceJoinHintsOut(join_hints=get_workspace_join_hints(db))


@app.get("/workspace/tables/{table_id}/join-hints", response_model=schemas.TableJoinHintsOut)
def get_table_join_hints(
    table_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    import join_graph as jg

    t = db.get(WorkspaceTable, table_id)
    if not t:
        raise HTTPException(404, "Table not found")
    short = t.full_table_id.rsplit(".", 1)[-1]
    full = get_workspace_join_hints(db)
    return schemas.TableJoinHintsOut(
        table_id=t.id,
        table_short=short,
        join_hints=jg.join_hints_for_table(full, short),
    )


@app.put("/workspace/tables/{table_id}/join-hints", response_model=schemas.TableJoinHintsOut)
def save_table_join_hints(
    table_id: int,
    body: schemas.JoinHintsUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    import join_graph as jg

    t = db.get(WorkspaceTable, table_id)
    if not t:
        raise HTTPException(404, "Table not found")
    short = t.full_table_id.rsplit(".", 1)[-1]
    merged = jg.merge_join_hints_for_table(
        get_workspace_join_hints(db),
        short,
        body.join_hints,
    )
    set_workspace_join_hints(db, merged)
    db.commit()
    return schemas.TableJoinHintsOut(
        table_id=t.id,
        table_short=short,
        join_hints=jg.join_hints_for_table(merged, short),
    )


@app.get("/workspace/tables", response_model=list[schemas.TableOut])
def list_workspace_tables(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return [_project_table_out(t) for t in _workspace_tables(db)]


@app.post("/workspace/tables", response_model=schemas.TableOut)
def add_workspace_table(
    body: schemas.TableCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    import table_profile
    import vector_index

    fq = _validate_table_id(body.full_table_id)
    existing = db.scalar(select(WorkspaceTable).where(WorkspaceTable.full_table_id == fq))
    if existing:
        return _project_table_out(existing)
    desc = ""
    try:
        desc = bq.table_metadata(fq).get("description", "") or ""
    except Exception:
        pass
    t = WorkspaceTable(full_table_id=fq, description=desc, included_for_ai=True)
    db.add(t)
    db.commit()
    db.refresh(t)
    # One-time AI profile so SQL generation knows real data coverage from day one.
    if table_profile.ensure_table_overview(db, t):
        db.commit()
        db.refresh(t)
    try:
        if vector_index.ensure_table_embedding(db, t, force=True):
            db.commit()
            db.refresh(t)
    except Exception as e:
        print(f"[vector-index] failed {t.full_table_id}: {e}")
    return _project_table_out(t)


@app.post("/workspace/tables/bulk-add", response_model=schemas.BulkAddOut)
def bulk_add_workspace_tables(
    body: schemas.BulkAddDatasetRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Add every table in a dataset to the workspace catalog in one shot.

    Rows are created immediately; AI profiling + embeddings run in the
    background so the request returns fast even for large datasets.
    """
    import threading

    dataset = (body.dataset or "").strip()
    if not dataset or dataset.count(".") < 1:
        raise HTTPException(400, "dataset must be in the form project.dataset_id")
    try:
        tables = bq.list_tables_in_dataset(dataset)
    except Exception as e:
        raise HTTPException(502, f"Could not list tables in {dataset}: {e}")

    existing = {t.full_table_id for t in db.scalars(select(WorkspaceTable)).all()}
    added: list[str] = []
    for tbl in tables:
        fq = tbl.get("full_table_id")
        if not fq or fq in existing:
            continue
        db.add(WorkspaceTable(full_table_id=fq, description="", included_for_ai=True))
        added.append(fq)
    if added:
        db.commit()
        threading.Thread(target=_backfill_ai_overviews, daemon=True).start()
        threading.Thread(target=_backfill_table_embeddings, daemon=True).start()

    return schemas.BulkAddOut(
        added=added,
        skipped=len(tables) - len(added),
        total=len(tables),
    )


@app.post("/workspace/tables/{table_id}/ai-overview", response_model=schemas.TableOut)
def refresh_table_ai_overview(
    table_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Generate (or regenerate) the AI overview: column review + sample data profile."""
    import table_profile
    import vector_index

    t = db.get(WorkspaceTable, table_id)
    if not t:
        raise HTTPException(404, "Table not found")
    if not table_profile.ensure_table_overview(db, t, force=True):
        db.commit()
        raise HTTPException(
            502,
            "Could not profile this table — check BigQuery access and try again.",
        )
    if config.EMBEDDING_RETRIEVAL_ENABLED:
        try:
            vector_index.ensure_table_embedding(db, t, force=True)
        except Exception as e:
            print(f"[vector-index] failed {t.full_table_id}: {e}")
    db.commit()
    db.refresh(t)
    return _project_table_out(t)


@app.post("/workspace/tables/vector-index", response_model=schemas.VectorIndexOut)
def rebuild_workspace_vector_index(
    force: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Build or refresh semantic embeddings for all workspace tables."""
    import vector_index

    try:
        result = vector_index.ensure_workspace_embeddings(db, force=force)
        db.commit()
        return schemas.VectorIndexOut(**result)
    except Exception as e:
        db.rollback()
        raise HTTPException(502, f"Could not build vector index: {e}")


@app.post("/workspace/tables/{table_id}/vector-index", response_model=schemas.TableOut)
def refresh_table_vector_index(
    table_id: int,
    force: bool = True,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    """Build or refresh semantic embedding for one workspace table."""
    import vector_index

    t = db.get(WorkspaceTable, table_id)
    if not t:
        raise HTTPException(404, "Table not found")
    try:
        vector_index.ensure_table_embedding(db, t, force=force)
        db.commit()
        db.refresh(t)
        return _project_table_out(t)
    except Exception as e:
        db.rollback()
        raise HTTPException(502, f"Could not build vector index: {e}")


@app.patch("/workspace/tables/{table_id}", response_model=schemas.TableOut)
def update_workspace_table(
    table_id: int,
    body: schemas.TableUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    import vector_index

    t = db.get(WorkspaceTable, table_id)
    if not t:
        raise HTTPException(404, "Table not found")
    if body.description is not None:
        t.description = body.description
    if body.column_descriptions is not None:
        t.column_descriptions_json = json.dumps(body.column_descriptions)
    if body.column_hints is not None:
        t.column_hints_json = json.dumps(body.column_hints)
    if body.business_rules is not None:
        t.business_rules = body.business_rules
    if body.included_for_ai is not None:
        t.included_for_ai = body.included_for_ai
    if body.endorsed is not None:
        t.endorsed = body.endorsed
    t.embedding_hash = ""
    t.embedding_json = "[]"
    t.embedding_updated_at = None
    if config.EMBEDDING_RETRIEVAL_ENABLED:
        try:
            vector_index.ensure_table_embedding(db, t, force=True)
        except Exception as e:
            print(f"[vector-index] failed {t.full_table_id}: {e}")
    db.commit()
    db.refresh(t)
    return _project_table_out(t)


@app.delete("/workspace/tables/{table_id}")
def remove_workspace_table(
    table_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    t = db.get(WorkspaceTable, table_id)
    if not t:
        raise HTTPException(404, "Table not found")
    db.delete(t)
    db.commit()
    return {"ok": True}


@app.post("/workspace/models/import", response_model=schemas.ModelImportOut)
def import_workspace_models(
    body: schemas.ModelYamlImportIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    return _import_models_impl(body, db)


@app.post("/workspace/models/prune-to-yaml")
def prune_workspace_to_yaml(
    _: User = Depends(require_admin),
):
    """Remove catalog tables that are not in workspace_models.yaml (admin only)."""
    from prune_workspace_to_yaml import prune

    return prune(dry_run=False)


def _model_description_extras(model: dict) -> str:
    """Append measures + business aliases to table description for routing/SQL."""
    parts: list[str] = []
    aliases = model.get("aliases") or []
    if aliases:
        parts.append("Also known as: " + "; ".join(aliases))
    measure_lines = model.get("measure_lines") or []
    if measure_lines:
        parts.append("Measures:\n" + "\n".join(f"- {line}" for line in measure_lines))
    return "\n\n".join(parts).strip()


def _import_models_impl(body: schemas.ModelYamlImportIn, db: Session) -> schemas.ModelImportOut:
    texts: list[str] = []
    if body.yaml and body.yaml.strip():
        texts.append(body.yaml)
    if body.yamls:
        texts.extend(y for y in body.yamls if y and y.strip())
    if not texts:
        raise HTTPException(400, "Provide yaml or yamls with model content")

    results: list[schemas.ModelImportTableResult] = []
    errors: list[str] = []
    all_relation_lines: list[str] = []
    touched_tables: list[WorkspaceTable] = []

    for text in texts:
        try:
            models = model_yaml.parse_yaml_documents(text)
        except model_yaml.ModelYamlError as e:
            errors.append(str(e))
            continue

        for model in models:
            model_id = model.get("model_id", "?")
            try:
                fq = _validate_table_id(model["full_table_id"])
                existing = db.scalar(
                    select(WorkspaceTable).where(WorkspaceTable.full_table_id == fq)
                )
                created = False
                if existing:
                    t = existing
                    merged = _parse_column_descriptions(t.column_descriptions_json)
                    merged.update(model["column_descriptions"])
                    t.column_descriptions_json = json.dumps(merged)
                    merged_hints = _parse_column_hints(t.column_hints_json)
                    merged_hints.update(model.get("column_hints") or {})
                    t.column_hints_json = json.dumps(merged_hints)
                    desc = model["description"]
                    extra = _model_description_extras(model)
                    if extra:
                        desc = f"{desc.rstrip()}\n\n{extra}".strip()
                    if desc:
                        t.description = desc
                else:
                    desc = model["description"]
                    extra = _model_description_extras(model)
                    if extra:
                        desc = f"{desc.rstrip()}\n\n{extra}".strip()
                    try:
                        bq_desc = bq.table_metadata(fq).get("description", "") or ""
                        if bq_desc and not desc:
                            desc = bq_desc
                    except Exception:
                        pass
                    t = WorkspaceTable(
                        full_table_id=fq,
                        description=desc,
                        column_descriptions_json=json.dumps(model["column_descriptions"]),
                        column_hints_json=json.dumps(model.get("column_hints") or {}),
                        included_for_ai=True,
                    )
                    db.add(t)
                    created = True
                db.flush()
                touched_tables.append(t)
                all_relation_lines.extend(model["relation_lines"])
                overview_generated = False
                if body.generate_overviews:
                    import table_profile

                    overview_generated = table_profile.ensure_table_overview(
                        db, t, force=True
                    )
                results.append(
                    schemas.ModelImportTableResult(
                        model_id=model_id,
                        full_table_id=fq,
                        table_db_id=t.id,
                        columns_imported=len(model["column_descriptions"]),
                        relations_imported=len(model["relation_lines"]),
                        created=created,
                        overview_generated=overview_generated,
                    )
                )
            except HTTPException as e:
                errors.append(f"{model_id}: {e.detail}")
            except Exception as e:
                errors.append(f"{model_id}: {e}")

    join_hints_updated = False
    if all_relation_lines:
        current = get_workspace_join_hints(db)
        new_hints = model_yaml.merge_join_hints(current, all_relation_lines)
        if new_hints != current:
            set_workspace_join_hints(db, new_hints)
            join_hints_updated = True

    if config.EMBEDDING_RETRIEVAL_ENABLED and touched_tables:
        import vector_index

        for t in touched_tables:
            try:
                vector_index.ensure_table_embedding(db, t, force=True)
            except Exception as e:
                errors.append(f"{t.full_table_id}: vector index failed: {e}")

    db.commit()
    return schemas.ModelImportOut(
        tables=results,
        join_hints_updated=join_hints_updated,
        errors=errors,
    )


# --------------------------------------------------------------------------
# Data tables (project-scoped URLs — data lives in workspace catalog)
# --------------------------------------------------------------------------
@app.get("/projects/{project_id}/tables", response_model=list[schemas.TableOut])
def list_tables(db: Session = Depends(get_db), p: Project = Depends(require_project)):
    _ = p
    return [_project_table_out(t) for t in _workspace_tables(db)]


@app.post("/projects/{project_id}/tables", response_model=schemas.TableOut)
def add_table(
    body: schemas.TableCreate,
    db: Session = Depends(get_db),
    p: Project = Depends(require_project),
    _: User = Depends(require_admin),
):
    import vector_index

    project_id = p.id
    fq = _validate_table_id(body.full_table_id)
    existing = db.scalar(select(WorkspaceTable).where(WorkspaceTable.full_table_id == fq))
    if existing:
        return _project_table_out(existing)

    desc = ""
    try:
        desc = bq.table_metadata(fq).get("description", "") or ""
    except Exception:
        pass

    t = WorkspaceTable(
        full_table_id=fq,
        description=desc,
        included_for_ai=True,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    try:
        if vector_index.ensure_table_embedding(db, t, force=True):
            db.commit()
            db.refresh(t)
    except Exception as e:
        print(f"[vector-index] failed {t.full_table_id}: {e}")
    return _project_table_out(t)


@app.patch("/projects/{project_id}/tables/{table_id}", response_model=schemas.TableOut)
def update_table(
    table_id: int,
    body: schemas.TableUpdate,
    db: Session = Depends(get_db),
    p: Project = Depends(require_project),
    _: User = Depends(require_admin),
):
    import vector_index

    project_id = p.id
    _ = project_id
    t = db.get(WorkspaceTable, table_id)
    if not t:
        raise HTTPException(404, "Table not found")
    if body.description is not None:
        t.description = body.description
    if body.column_descriptions is not None:
        t.column_descriptions_json = json.dumps(body.column_descriptions)
    if body.column_hints is not None:
        t.column_hints_json = json.dumps(body.column_hints)
    if body.business_rules is not None:
        t.business_rules = body.business_rules
    if body.included_for_ai is not None:
        t.included_for_ai = body.included_for_ai
    if body.endorsed is not None:
        t.endorsed = body.endorsed
    t.embedding_hash = ""
    t.embedding_json = "[]"
    t.embedding_updated_at = None
    if config.EMBEDDING_RETRIEVAL_ENABLED:
        try:
            vector_index.ensure_table_embedding(db, t, force=True)
        except Exception as e:
            print(f"[vector-index] failed {t.full_table_id}: {e}")
    db.commit()
    db.refresh(t)
    return _project_table_out(t)


@app.delete("/projects/{project_id}/tables/{table_id}")
def remove_table(
    table_id: int,
    db: Session = Depends(get_db),
    p: Project = Depends(require_project),
    _: User = Depends(require_admin),
):
    project_id = p.id
    _ = project_id
    t = db.get(WorkspaceTable, table_id)
    if not t:
        raise HTTPException(404, "Table not found")
    db.delete(t)
    db.commit()
    return {"ok": True}


@app.put("/projects/{project_id}/join-hints", response_model=schemas.WorkspaceJoinHintsOut)
def save_join_hints(
    body: schemas.JoinHintsUpdate,
    db: Session = Depends(get_db),
    p: Project = Depends(require_project),
    _: User = Depends(require_admin),
):
    _ = p
    set_workspace_join_hints(db, body.join_hints)
    db.commit()
    return schemas.WorkspaceJoinHintsOut(join_hints=get_workspace_join_hints(db))


@app.post("/projects/{project_id}/models/import", response_model=schemas.ModelImportOut)
def import_models(
    body: schemas.ModelYamlImportIn,
    db: Session = Depends(get_db),
    p: Project = Depends(require_project),
    _: User = Depends(require_admin),
):
    _ = p
    return _import_models_impl(body, db)


def _table_notes(tables: list) -> dict[str, str]:
    out: dict[str, str] = {}
    for t in tables:
        parts: list[str] = []
        if (t.description or "").strip():
            parts.append(t.description.strip())
        if t.endorsed:
            parts.append("[ENDORSED — preferred table for AI queries across the workspace]")
        if parts:
            out[t.full_table_id] = " | ".join(parts)
    return out


def _column_notes(tables: list) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for t in tables:
        cols = _parse_column_descriptions(t.column_descriptions_json)
        if cols:
            out[t.full_table_id] = cols
    return out


def _column_hints(tables: list) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for t in tables:
        hints = _parse_column_hints(t.column_hints_json)
        if hints:
            out[t.full_table_id] = hints
    return out


@app.get("/projects/{project_id}/schema")
def get_schema(db: Session = Depends(get_db), p: Project = Depends(require_project)):
    included = [t for t in _workspace_tables(db) if t.included_for_ai]
    table_ids = [t.full_table_id for t in included]
    if not table_ids:
        return {"schema": "(no tables included for AI in this project)"}
    try:
        return {
            "schema": bq.schema_for_tables(
                table_ids,
                get_workspace_join_hints(db),
                table_notes=_table_notes(included),
                column_notes=_column_notes(included),
                column_hints=_column_hints(included),
            )
        }
    except Exception as e:
        raise HTTPException(503, f"BigQuery unavailable: {e}")


# --------------------------------------------------------------------------
# Warehouse — tables the service account can access
# --------------------------------------------------------------------------
@app.get("/warehouse/datasets", response_model=list[schemas.DatasetOut])
def warehouse_datasets(user: User = Depends(get_current_user)):
    try:
        return bq.warehouse_datasets()
    except Exception as e:
        raise HTTPException(503, f"Could not list datasets: {e}")


@app.get("/warehouse/catalog", response_model=schemas.WarehouseCatalogOut)
def warehouse_catalog(user: User = Depends(get_current_user)):
    """Datasets and tables for the configured warehouse scope."""
    try:
        datasets, tables_by_dataset = bq.warehouse_catalog()
        return schemas.WarehouseCatalogOut(datasets=datasets, tables_by_dataset=tables_by_dataset)
    except Exception as e:
        raise HTTPException(503, f"Could not load warehouse catalog: {e}")


@app.get("/warehouse/tables", response_model=list[schemas.WarehouseTableOut])
def warehouse_tables(dataset: str, user: User = Depends(get_current_user)):
    """dataset = project.dataset_id"""
    try:
        return bq.list_tables_in_dataset(dataset)
    except Exception as e:
        raise HTTPException(400, f"Could not list tables: {e}")


@app.get("/warehouse/table/metadata", response_model=schemas.TableMetadataOut)
def warehouse_table_metadata(full_table_id: str, user: User = Depends(get_current_user)):
    try:
        return bq.table_metadata(full_table_id)
    except Exception as e:
        raise HTTPException(400, f"Could not load table: {e}")


@app.get("/warehouse/table/preview", response_model=schemas.TablePreviewOut)
def warehouse_table_preview(full_table_id: str, limit: int = 25, user: User = Depends(get_current_user)):
    try:
        df, note = bq.preview_table(full_table_id, limit=min(limit, 100))
        return schemas.TablePreviewOut(
            columns=list(df.columns),
            rows=json.loads(df.to_json(orient="records", date_format="iso")),
            note=note,
        )
    except Exception as e:
        raise HTTPException(400, f"Preview failed: {e}")


# --------------------------------------------------------------------------
# Memory
# --------------------------------------------------------------------------
@app.delete("/projects/{project_id}/memory")
def clear_memory(
    thread_id: int | None = None,
    p: Project = Depends(require_project),
    db: Session = Depends(get_db),
):
    """Clear conversation history — one thread, or the whole project."""
    q = delete(Memory).where(Memory.project_id == p.id)
    if thread_id is not None:
        q = q.where(Memory.thread_id == thread_id)
    db.execute(q)
    db.commit()
    return {"ok": True, "cleared": True}


@app.get("/projects/{project_id}/memory", response_model=list[schemas.MemoryOut])
def list_memory(
    thread_id: int | None = None,
    p: Project = Depends(require_project),
    db: Session = Depends(get_db),
):
    from memory_lookup import stored_answer_matches_question

    q = select(Memory).where(Memory.project_id == p.id).order_by(Memory.created_at.asc())
    if thread_id is not None:
        q = q.where(Memory.thread_id == thread_id)
    rows = db.scalars(q).all()
    out: list[schemas.MemoryOut] = []
    purged = False
    for m in rows:
        try:
            columns = json.loads(m.columns_json or "[]")
        except json.JSONDecodeError:
            columns = []
        try:
            rows_data = json.loads(m.rows_json or "[]")
        except json.JSONDecodeError:
            rows_data = []
        if not stored_answer_matches_question(
            m.question,
            sql=m.sql or "",
            columns=columns,
            rows=rows_data,
            summary=m.summary or "",
        ):
            db.delete(m)
            purged = True
            continue
        out.append(_memory_out(m))
    if purged:
        db.commit()
    return out


def _memory_out(m: Memory) -> schemas.MemoryOut:
    try:
        chart_spec = json.loads(m.chart_spec_json or "{}")
    except json.JSONDecodeError:
        chart_spec = {}
    try:
        rows = json.loads(m.rows_json or "[]")
    except json.JSONDecodeError:
        rows = []
    try:
        columns = json.loads(m.columns_json or "[]")
    except json.JSONDecodeError:
        columns = []
    return schemas.MemoryOut(
        id=m.id,
        thread_id=m.thread_id,
        question=m.question,
        sql=m.sql,
        summary=m.summary,
        columns=columns,
        rows=rows,
        chart_spec=chart_spec,
        bytes_estimate=m.bytes_estimate,
        credits_used=m.credits_used,
        from_cache=bool(getattr(m, "from_cache", False)),
    )


def _project_context_for_ask(
    db: Session,
    project_id: int,
    join_hints: str = "",
    question: str = "",
    thread_id: int | None = None,
) -> str:
    """Thread memory + notebook summary — compact for follow-ups to save tokens."""
    from debug_session import debug_log
    from project_context import build_sql_context
    from question_intent import detect_intent
    from result_cache import _FOLLOWUP, _REF_PRIOR

    summary = notebook_api.build_thread_summary_from_memories(db, project_id, thread_id)
    mem_filter = select(Memory.id).where(Memory.project_id == project_id)
    if thread_id is not None:
        mem_filter = mem_filter.where(Memory.thread_id == thread_id)
    has_history = bool(db.scalar(mem_filter.limit(1)))
    q = (question or "").strip()
    references_prior = bool(_REF_PRIOR.search(q) or _FOLLOWUP.search(q))
    compact = bool(
        summary
        and has_history
        and detect_intent(q, has_thread_history=has_history) == "data_query"
        and len(q.split()) <= 24
        and not references_prior
    )

    limit = 2 if compact else config.MEMORY_CONTEXT_SIZE
    mem_q = select(Memory).where(Memory.project_id == project_id)
    if thread_id is not None:
        mem_q = mem_q.where(Memory.thread_id == thread_id)
    rows = db.scalars(mem_q.order_by(Memory.created_at.desc()).limit(limit)).all()
    memories = list(reversed(rows))
    all_runs = notebook_api.latest_cell_runs(db, project_id)
    # Keep notebook SQL in context even in compact mode (log H9: was 0).
    cell_runs = all_runs[-2:] if compact else all_runs
    debug_log(
        "main.py:_project_context_for_ask",
        "context_built",
        {
            "project_id": project_id,
            "compact": compact,
            "memory_count": len(memories),
            "cell_run_count": len(cell_runs),
            "has_summary": bool(summary and summary not in ("(No queries yet.)", "(No queries yet)")),
            "question": (question or "")[:120],
        },
        hypothesis_id="H9",
        run_id="post-fix",
    )
    return build_sql_context(
        thread_memories=memories,
        notebook_runs=cell_runs,
        join_hints=join_hints,
        memory_summary=summary,
        compact=compact,
    )


def _cache_entries_for_project(
    db: Session, project_id: int, thread_id: int | None = None
) -> list[dict]:
    mem_q = select(Memory).where(Memory.project_id == project_id)
    if thread_id is not None:
        mem_q = mem_q.where(Memory.thread_id == thread_id)
    memories = db.scalars(
        mem_q.order_by(Memory.created_at.desc()).limit(config.CACHE_LOOKUP_SIZE)
    ).all()
    cell_runs = notebook_api.latest_cell_runs(db, project_id)
    return build_cache_entries(list(reversed(memories)), cell_runs)


def _ensure_notebook(db: Session, p: Project) -> Project:
    """Notebook is always available — auto-enable on first use."""
    if not p.notebook_enabled:
        p.notebook_enabled = True
        db.commit()
        db.refresh(p)
    return p


# --------------------------------------------------------------------------
# Notebook (Hex-style cells + cached runs)
# --------------------------------------------------------------------------
@app.post("/projects/{project_id}/notebook/enable", response_model=schemas.ProjectOut)
def enable_notebook(db: Session = Depends(get_db), p: Project = Depends(require_project)):
    return _ensure_notebook(db, p)


@app.post("/projects/{project_id}/notebook/seed-template", response_model=list[schemas.NotebookCellOut])
def seed_notebook_template(db: Session = Depends(get_db), p: Project = Depends(require_project)):
    """Optional NPS starter cells — only when user explicitly requests."""
    _ensure_notebook(db, p)
    return [schemas.NotebookCellOut(**c) for c in notebook_api.seed_notebook_if_empty(db, p)]


@app.get("/projects/{project_id}/notebook/cells", response_model=list[schemas.NotebookCellOut])
def list_notebook_cells(db: Session = Depends(get_db), p: Project = Depends(require_project)):
    p = _ensure_notebook(db, p)
    notebook_api.purge_thread_synced_cells(db, p.id)
    db.commit()
    return [schemas.NotebookCellOut(**c) for c in notebook_api.list_cells(db, p.id)]


@app.post("/projects/{project_id}/notebook/cells", response_model=schemas.NotebookCellOut)
def create_notebook_cell(
    body: schemas.NotebookCellCreate,
    db: Session = Depends(get_db),
    p: Project = Depends(require_project),
):
    project_id = p.id
    _ensure_notebook(db, p)
    if body.cell_type not in ("input", "sql", "text", "code"):
        raise HTTPException(400, "cell_type must be input, sql, text, or code")
    c = NotebookCell(
        project_id=project_id,
        cell_type=body.cell_type,
        name=body.name.strip(),
        content=body.content or "",
        config_json=json.dumps(body.config or {}),
        sort_order=body.sort_order,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    match = next((x for x in notebook_api.list_cells(db, project_id) if x["id"] == c.id), None)
    return schemas.NotebookCellOut(**(match or notebook_api.list_cells(db, project_id)[-1]))


@app.patch("/projects/{project_id}/notebook/cells/{cell_id}", response_model=schemas.NotebookCellOut)
def update_notebook_cell(
    cell_id: int,
    body: schemas.NotebookCellUpdate,
    db: Session = Depends(get_db),
    p: Project = Depends(require_project),
):
    project_id = p.id
    _ensure_notebook(db, p)
    c = db.scalar(
        select(NotebookCell).where(
            NotebookCell.project_id == project_id,
            NotebookCell.id == cell_id,
        )
    )
    if not c:
        raise HTTPException(404, "Cell not found")
    if body.cell_type is not None:
        c.cell_type = body.cell_type
    if body.name is not None:
        c.name = body.name.strip()
    if body.content is not None:
        old_content = c.content
        c.content = body.content
        if c.cell_type == "sql" and (body.content or "").strip() != (old_content or "").strip():
            db.execute(delete(NotebookCellRun).where(NotebookCellRun.cell_id == cell_id))
    if body.config is not None:
        c.config_json = json.dumps(body.config)
    if body.sort_order is not None:
        c.sort_order = body.sort_order
    db.commit()
    cells = notebook_api.list_cells(db, project_id)
    match = next((x for x in cells if x["id"] == cell_id), None)
    if not match:
        raise HTTPException(404, "Cell not found")
    return schemas.NotebookCellOut(**match)


@app.delete("/projects/{project_id}/notebook/cells/{cell_id}")
def delete_notebook_cell(cell_id: int, db: Session = Depends(get_db), p: Project = Depends(require_project)):
    project_id = p.id
    _ensure_notebook(db, p)
    c = db.scalar(
        select(NotebookCell).where(
            NotebookCell.project_id == project_id,
            NotebookCell.id == cell_id,
        )
    )
    if not c:
        raise HTTPException(404, "Cell not found")
    db.execute(delete(NotebookCellRun).where(NotebookCellRun.cell_id == cell_id))
    db.delete(c)
    db.commit()
    return {"ok": True}


@app.post("/projects/{project_id}/notebook/run", response_model=schemas.NotebookRunOut)
def run_notebook_cells(
    body: schemas.NotebookRunIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    p: Project = Depends(require_project),
):
    project_id = p.id
    _ensure_notebook(db, p)
    try:
        out = notebook_api.execute_notebook(
            db,
            p,
            input_overrides=body.input_overrides,
            stop_at_cell_id=body.cell_id,
        )
    except Exception as e:
        raise HTTPException(400, f"Notebook run failed: {e}")
    try:
        used, remaining = apply_usage_charge(
            db,
            user,
            bytes_estimate=out.get("bytes_estimate"),
            from_cache=False,
            project_id=project_id,
            action="notebook",
            detail=f"notebook run cell={body.cell_id or 'all'}",
        )
        db.commit()
        db.refresh(user)
    except ValueError as e:
        raise HTTPException(402, str(e))
    out["credits_used"] = used
    out["credits_remaining"] = remaining
    return schemas.NotebookRunOut(**out)


@app.get("/projects/{project_id}/notebook/graph", response_model=schemas.LogicGraphOut)
def notebook_logic_graph(db: Session = Depends(get_db), p: Project = Depends(require_project)):
    _ensure_notebook(db, p)
    return schemas.LogicGraphOut(**notebook_api.logic_graph(db, p.id))


# --------------------------------------------------------------------------
# Ask  (the core pipeline)
# --------------------------------------------------------------------------
def _finish_ask_result(
    db: Session,
    project_id: int | None,
    result: dict,
    user: User,
    thread_id: int | None = None,
) -> tuple[float, float]:
    """Persist a new Thread memory row, or skip when serving an exact memory hit."""
    if result.get("skip_memory_save"):
        enrich_result_with_credits(result, 0, user.credits_balance)
        return 0.0, user.credits_balance
    return _save_ask_memory(db, project_id, result, user, thread_id=thread_id)


def _find_memory_for_question(
    db: Session,
    question: str,
    *,
    project_id: int | None = None,
    thread_id: int | None = None,
) -> Memory | None:
    from memory_lookup import normalize_question

    key = normalize_question(question)
    if not key:
        return None
    q = select(Memory)
    if thread_id is not None:
        q = q.where(Memory.thread_id == thread_id)
    elif project_id is not None:
        q = q.where(Memory.project_id == project_id)
    else:
        return None
    for row in db.scalars(q.order_by(Memory.created_at.desc())).all():
        if normalize_question(row.question) == key:
            return row
    return None


def _touch_thread(db: Session, thread_id: int | None, question: str) -> None:
    """Bump thread activity; adopt the first question as the thread title."""
    if not thread_id:
        return
    import datetime as _dt

    t = db.get(Thread, thread_id)
    if not t:
        return
    t.updated_at = _dt.datetime.now(_dt.timezone.utc)
    if (t.title or "").strip() in ("", "New thread") and question.strip():
        t.title = question.strip()[:300]


def _should_persist_ask_result(result: dict) -> bool:
    """Skip saving empty LLM recovery text that would block future good answers."""
    from memory_lookup import _is_bad_stored_analysis

    rows = result.get("rows") or []
    sql = (result.get("sql") or "").strip()
    analysis = (result.get("analysis") or "").strip()
    if rows and sql:
        return True
    if _is_bad_stored_analysis(result.get("question") or "", analysis):
        return False
    if not rows and not sql:
        return False
    return bool(sql)


def _save_ask_memory(
    db: Session,
    project_id: int | None,
    result: dict,
    user: User,
    thread_id: int | None = None,
) -> tuple[float, float]:
    if not _should_persist_ask_result(result):
        enrich_result_with_credits(result, 0, user.credits_balance)
        return 0.0, user.credits_balance
    cap = config.MEMORY_MAX_ROWS
    used, remaining = apply_usage_charge(
        db,
        user,
        bytes_estimate=result.get("bytes_estimate"),
        from_cache=bool(result.get("from_cache")),
        project_id=project_id,
        action="ask",
        detail=(result.get("question") or "")[:200],
    )
    existing = _find_memory_for_question(
        db, result["question"], project_id=project_id, thread_id=thread_id
    )
    payload = {
        "question": result["question"],
        "sql": result.get("sql") or "",
        "summary": result["analysis"],
        "chart_spec_json": json.dumps(result.get("chart_spec") or {}),
        "rows_json": json.dumps((result.get("rows") or [])[:cap], default=str),
        "columns_json": json.dumps(result.get("columns") or []),
        "bytes_estimate": result.get("bytes_estimate"),
        "credits_used": used,
        "from_cache": bool(result.get("from_cache")),
    }
    if existing:
        for field, value in payload.items():
            setattr(existing, field, value)
        if thread_id and not existing.thread_id:
            existing.thread_id = thread_id
    else:
        db.add(Memory(project_id=project_id, thread_id=thread_id, **payload))
    _touch_thread(db, thread_id, result["question"])
    if project_id and result.get("sql_steps"):
        try:
            import notebook_api

            notebook_api.sync_notebook_steps(
                db,
                project_id,
                question=result["question"],
                sql_steps=result.get("sql_steps") or [],
                analysis=result.get("analysis") or "",
            )
        except Exception:
            pass
    try:
        from thread_kb import refresh_thread_overview

        refresh_thread_overview(db, thread_id)
    except Exception:
        pass
    db.commit()
    db.refresh(user)
    enrich_result_with_credits(result, used, remaining)
    return used, remaining


def _ask_from_result(result: dict) -> schemas.AskResponse:
    sql_steps = result.get("sql_steps")
    return schemas.AskResponse(
        question=result["question"],
        sql=result.get("sql") or "",
        columns=result.get("columns") or [],
        rows=result.get("rows") or [],
        chart_spec=result.get("chart_spec") or {"chart": "none"},
        analysis=result["analysis"],
        bytes_estimate=result.get("bytes_estimate") or 0,
        credits_used=result.get("credits_used") or 0,
        credits_remaining=result.get("credits_remaining"),
        sql_steps=sql_steps if sql_steps else None,
        from_cache=bool(result.get("from_cache")),
        cache_source=result.get("cache_source"),
        suggestions=result.get("suggestions") or [],
        response_mode=result.get("response_mode") or "data",
    )


@app.post("/projects/{project_id}/ask", response_model=schemas.AskResponse)
def ask(
    project_id: int,
    body: schemas.AskRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    p = _get_project(db, project_id)
    ensure_project_access(p, user)
    included = _included_tables(db, p)
    if not included:
        raise HTTPException(
            400,
            "No tables in the workspace yet. An admin must add tables in the Data tab first.",
        )
    thread = _resolve_thread(db, project_id, body.thread_id, user)
    ws_hints = get_workspace_join_hints(db)
    memory_text = _project_context_for_ask(
        db, project_id, ws_hints, body.question, thread_id=thread.id
    )
    # Always load this thread's cache for prior SQL continuity.
    # force_fresh only controls answer reuse (reuse_cached), not memory.
    cache_entries = _cache_entries_for_project(db, project_id, thread_id=thread.id)
    try:
        result = None
        audit = SqlAuditContext(db=db, project_id=project_id, user_id=user.id)
        for event in iter_ask(
            body.question,
            memory_text,
            included_tables=included,
            join_hints=ws_hints,
            cache_entries=cache_entries,
            reuse_cached=p.reuse_cached_results and not body.force_fresh,
            force_fresh=body.force_fresh,
            clarification_choice=body.clarification_choice,
            clarification_text=body.clarification_text,
            refined_question=body.refined_question,
            pinned_table_ids=body.pinned_table_ids,
            audit=audit,
        ):
            if event.get("type") == "complete":
                result = event
            elif event.get("type") == "error":
                raise HTTPException(400, event.get("message", "Ask failed"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except HTTPException:
        raise
    except Exception as e:
        err = str(e)
        if "Could not generate" in err or "Only SELECT" in err:
            raise HTTPException(400, f"Could not generate a safe query: {e}")
        raise HTTPException(400, f"Query failed: {e}")

    if not result:
        raise HTTPException(500, "Ask pipeline did not return a result")

    _finish_ask_result(db, project_id, result, user, thread_id=thread.id)
    return _ask_from_result(result)


@app.post("/projects/{project_id}/ask/stream")
def ask_stream(
    project_id: int,
    body: schemas.AskRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    p = _get_project(db, project_id)
    ensure_project_access(p, user)
    included = _included_tables(db, p)
    thread = _resolve_thread(db, project_id, body.thread_id, user)
    ws_hints = get_workspace_join_hints(db)
    memory_text = _project_context_for_ask(
        db, project_id, ws_hints, body.question, thread_id=thread.id
    )
    join_hints = ws_hints
    # Always load this thread's cache for prior SQL continuity.
    cache_entries = _cache_entries_for_project(db, project_id, thread_id=thread.id)

    if not included:
        def empty_tables():
            yield f"data: {json.dumps({'type': 'error', 'message': 'No tables in the workspace yet. An admin must add tables in the Data tab first.'})}\n\n"
        return StreamingResponse(empty_tables(), media_type="text/event-stream")

    def event_stream():
        result = None
        audit = SqlAuditContext(db=db, project_id=project_id, user_id=user.id)
        from debug_session import ask_trace

        ask_trace(
            "ask_request",
            project_id=project_id,
            question=(body.question or "")[:200],
            force_fresh=body.force_fresh,
            table_count=len(included),
        )
        try:
            for event in iter_ask(
                body.question,
                memory_text,
                included_tables=included,
                join_hints=join_hints,
                cache_entries=cache_entries,
                reuse_cached=p.reuse_cached_results and not body.force_fresh,
                force_fresh=body.force_fresh,
                clarification_choice=body.clarification_choice,
                clarification_text=body.clarification_text,
                refined_question=body.refined_question,
                pinned_table_ids=body.pinned_table_ids,
                audit=audit,
            ):
                if event.get("type") == "complete":
                    result = event
                    result["thread_id"] = thread.id
                    try:
                        _finish_ask_result(db, project_id, result, user, thread_id=thread.id)
                    except ValueError as e:
                        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                        return
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            return

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/projects/{project_id}/ask/confirm", response_model=schemas.AskResponse)
def ask_confirm(
    project_id: int,
    body: schemas.AskConfirmRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Run a validated SQL query after user approval (when REQUIRE_SQL_APPROVAL is on)."""
    p = _get_project(db, project_id)
    ensure_project_access(p, user)
    included = _included_tables(db, p)
    if not included:
        raise HTTPException(
            400,
            "No tables in the workspace yet. An admin must add tables in the Data tab first.",
        )
    thread = _resolve_thread(db, project_id, body.thread_id, user)
    ws_hints = get_workspace_join_hints(db)
    memory_text = _project_context_for_ask(
        db, project_id, ws_hints, body.question, thread_id=thread.id
    )
    try:
        result = None
        audit = SqlAuditContext(db=db, project_id=project_id, user_id=user.id)
        for event in iter_ask(
            body.question,
            memory_text,
            included_tables=included,
            join_hints=ws_hints,
            preapproved_sql=body.sql,
            audit=audit,
        ):
            if event.get("type") == "complete":
                result = event
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Query failed: {e}")

    if not result:
        raise HTTPException(500, "Ask pipeline did not return a result")

    _save_ask_memory(db, project_id, result, user, thread_id=thread.id)
    return _ask_from_result(result)


@app.post("/projects/{project_id}/ask/confirm/stream")
def ask_confirm_stream(
    project_id: int,
    body: schemas.AskConfirmRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    p = _get_project(db, project_id)
    ensure_project_access(p, user)
    included = _included_tables(db, p)
    thread = _resolve_thread(db, project_id, body.thread_id, user)
    ws_hints = get_workspace_join_hints(db)
    memory_text = _project_context_for_ask(
        db, project_id, ws_hints, body.question, thread_id=thread.id
    )
    join_hints = ws_hints

    def event_stream():
        result = None
        audit = SqlAuditContext(db=db, project_id=project_id, user_id=user.id)
        try:
            for event in iter_ask(
                body.question,
                memory_text,
                included_tables=included,
                join_hints=join_hints,
                preapproved_sql=body.sql,
                audit=audit,
            ):
                if event.get("type") == "complete":
                    event["thread_id"] = thread.id
                    try:
                        _save_ask_memory(db, project_id, event, user, thread_id=thread.id)
                    except ValueError as e:
                        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                        return
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            return

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------
# Dashboard (pin thread answers + shareable live view)
# --------------------------------------------------------------------------
def _parse_chart_spec(raw: str | None) -> dict:
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}


def _refresh_dashboard_data(
    item: DashboardItem,
    *,
    variables: dict[str, str] | None = None,
) -> schemas.DashboardItemOut:
    import app_builder

    raw_sql = item.sql
    sql = raw_sql
    if variables and app_builder.sql_has_variables(raw_sql):
        try:
            sql = app_builder.apply_variables(raw_sql, variables)
        except ValueError as e:
            return schemas.DashboardItemOut(
                id=item.id,
                question=item.question,
                sql=raw_sql,
                analysis=f"Input error: {e}",
                chart_spec=_parse_chart_spec(item.chart_spec_json),
            )
    sql = bq.validate_select_only(sql)
    bytes_estimate = bq.dry_run_bytes(sql)
    df = bq.run_query(sql)
    rows = json.loads(df.to_json(orient="records", date_format="iso"))
    columns = list(df.columns)
    sample = rows[:50]
    viz_rows, chart_spec, analysis = llm.build_presentation(
        item.question, columns, rows, sample=sample
    )
    return schemas.DashboardItemOut(
        id=item.id,
        question=item.question,
        sql=sql,
        analysis=analysis,
        chart_spec=chart_spec,
        columns=columns,
        rows=rows[:100],
        viz_rows=viz_rows,
        bytes_estimate=bytes_estimate,
    )


def _app_inputs_for_project(db: Session, p: Project) -> tuple[dict, list[dict]]:
    import app_builder

    config = app_builder.parse_app_config(p.app_config_json)
    cells = db.scalars(
        select(NotebookCell)
        .where(NotebookCell.project_id == p.id)
        .order_by(NotebookCell.sort_order, NotebookCell.id)
    ).all()
    inputs = app_builder.list_app_inputs(cells, config)
    return config, inputs


def _query_input_overrides(request: Request) -> dict[str, str]:
    skip = {"refresh", "token"}
    return {k: v for k, v in request.query_params.items() if k not in skip and v}


def _refresh_dashboard_items(
    items: list[DashboardItem],
    db: Session,
    p: Project,
    request: Request | None = None,
    *,
    refresh: bool,
) -> list[schemas.DashboardItemOut]:
    if not refresh:
        return [_dashboard_item_out(i, refresh=False) for i in items]
    _, inputs = _app_inputs_for_project(db, p)
    import app_builder

    overrides = _query_input_overrides(request) if request else {}
    variables = app_builder.merge_input_overrides(inputs, overrides or None)
    out: list[schemas.DashboardItemOut] = []
    for item in items:
        try:
            spec = _parse_chart_spec(item.chart_spec_json)
            if spec.get("chart") == "code":
                out.append(_dashboard_item_out(item))
                continue
            out.append(_refresh_dashboard_data(item, variables=variables))
        except Exception as e:
            out.append(
                schemas.DashboardItemOut(
                    id=item.id,
                    question=item.question,
                    sql=item.sql,
                    analysis=f"Could not refresh: {bq.format_query_error(e)}",
                    chart_spec=_parse_chart_spec(item.chart_spec_json),
                )
            )
    # Attach SQL rows to code widgets from their data_source SQL widget
    by_slug = {
        (it.question or "").replace(" ", "_").lower(): it for it in out if it.rows
    }
    for i, it in enumerate(out):
        spec = it.chart_spec or {}
        if spec.get("chart") != "code":
            continue
        ds = (spec.get("data_source") or "").replace(" ", "_").lower()
        src = by_slug.get(ds)
        if src and src.rows:
            out[i] = it.model_copy(update={"rows": src.rows, "columns": src.columns})
    return out


def _dashboard_item_out(item: DashboardItem, *, refresh: bool = False) -> schemas.DashboardItemOut:
    if refresh:
        return _refresh_dashboard_data(item)
    return schemas.DashboardItemOut(
        id=item.id,
        question=item.question,
        sql=item.sql,
        analysis=item.analysis or "",
        chart_spec=_parse_chart_spec(item.chart_spec_json),
        columns=[],
        rows=[],
        bytes_estimate=None,
    )


@app.get("/projects/{project_id}/dashboard", response_model=list[schemas.DashboardItemOut])
def list_dashboard(
    request: Request,
    refresh: bool = False,
    db: Session = Depends(get_db),
    p: Project = Depends(require_project),
):
    items = db.scalars(
        select(DashboardItem)
        .where(DashboardItem.project_id == p.id)
        .order_by(DashboardItem.sort_order, DashboardItem.created_at)
    ).all()
    return _refresh_dashboard_items(items, db, p, request, refresh=refresh)


@app.get("/projects/{project_id}/app", response_model=schemas.AppOut)
def get_app(
    request: Request,
    refresh: bool = False,
    db: Session = Depends(get_db),
    p: Project = Depends(require_project),
):
    import app_builder

    config, inputs = _app_inputs_for_project(db, p)
    items = db.scalars(
        select(DashboardItem)
        .where(DashboardItem.project_id == p.id)
        .order_by(DashboardItem.sort_order, DashboardItem.created_at)
    ).all()
    refreshed = _refresh_dashboard_items(items, db, p, request, refresh=refresh)
    title = config.get("title") or p.name
    return schemas.AppOut(
        project_name=p.name,
        config=schemas.AppConfigOut(
            title=title,
            description=config.get("description") or "",
            input_cell_ids=config.get("input_cell_ids") or [],
        ),
        inputs=[schemas.AppInputOut(**i) for i in inputs],
        items=refreshed,
        share_token=p.share_token,
    )


@app.patch("/projects/{project_id}/app", response_model=schemas.AppConfigOut)
def update_app_config(
    body: schemas.AppConfigUpdate,
    db: Session = Depends(get_db),
    p: Project = Depends(require_project),
):
    import app_builder

    config = app_builder.parse_app_config(p.app_config_json)
    if body.title is not None:
        config["title"] = body.title.strip()
    if body.description is not None:
        config["description"] = body.description.strip()
    if body.input_cell_ids is not None:
        config["input_cell_ids"] = body.input_cell_ids
    p.app_config_json = app_builder.serialize_app_config(config)
    db.commit()
    return schemas.AppConfigOut(**config)


@app.post("/projects/{project_id}/dashboard/from-notebook/{cell_id}", response_model=schemas.DashboardItemOut)
def add_dashboard_from_notebook(
    cell_id: int,
    db: Session = Depends(get_db),
    p: Project = Depends(require_project),
):
    cell = db.scalar(
        select(NotebookCell).where(
            NotebookCell.project_id == p.id,
            NotebookCell.id == cell_id,
        )
    )
    if not cell:
        raise HTTPException(404, "Notebook cell not found")
    existing = db.scalars(select(DashboardItem).where(DashboardItem.project_id == p.id)).all()
    question = cell.name.replace("_", " ").strip() or "Notebook widget"

    if cell.cell_type == "code":
        try:
            cfg = json.loads(cell.config_json or "{}")
        except json.JSONDecodeError:
            cfg = {}
        item = DashboardItem(
            project_id=p.id,
            question=question,
            sql="",
            analysis="",
            chart_spec_json=json.dumps({
                "chart": "code",
                "code": cell.content or "",
                "data_source": cfg.get("data_source") or "",
                "notebook_cell_id": cell.id,
            }),
            sort_order=len(existing),
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return _dashboard_item_out(item)

    if cell.cell_type != "sql":
        raise HTTPException(400, "Only SQL or Code notebook cells can be pinned to the app")
    sql = (cell.content or "").strip()
    if not sql:
        raise HTTPException(400, "Add SQL to this notebook cell first")
    existing = db.scalars(select(DashboardItem).where(DashboardItem.project_id == p.id)).all()
    question = cell.name.replace("_", " ").strip() or "Notebook query"
    item = DashboardItem(
        project_id=p.id,
        question=question,
        sql=bq.validate_select_only(sql),
        analysis="",
        chart_spec_json="{}",
        sort_order=len(existing),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _dashboard_item_out(item)


@app.post("/projects/{project_id}/dashboard", response_model=schemas.DashboardItemOut)
def add_dashboard_item(
    body: schemas.DashboardItemCreate, db: Session = Depends(get_db), p: Project = Depends(require_project)
):
    if not body.sql.strip():
        raise HTTPException(400, "SQL is required")
    existing = db.scalars(
        select(DashboardItem).where(DashboardItem.project_id == p.id)
    ).all()
    item = DashboardItem(
        project_id=p.id,
        question=body.question.strip(),
        sql=bq.validate_select_only(body.sql),
        analysis=body.analysis or "",
        chart_spec_json=json.dumps(body.chart_spec or {}),
        sort_order=len(existing),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _dashboard_item_out(item)


@app.delete("/projects/{project_id}/dashboard/{item_id}")
def remove_dashboard_item(item_id: int, db: Session = Depends(get_db), p: Project = Depends(require_project)):
    item = db.get(DashboardItem, item_id)
    if not item or item.project_id != p.id:
        raise HTTPException(404, "Dashboard item not found")
    db.delete(item)
    db.commit()
    return {"ok": True}


@app.post("/projects/{project_id}/dashboard/publish", response_model=schemas.PublishDashboardOut)
def publish_dashboard(db: Session = Depends(get_db), p: Project = Depends(require_project)):
    if not p.share_token:
        p.share_token = secrets.token_urlsafe(24)
        db.commit()
        db.refresh(p)
    return schemas.PublishDashboardOut(share_token=p.share_token)


@app.get("/shared/{share_token}", response_model=schemas.SharedDashboardOut)
def get_shared_dashboard(share_token: str, request: Request, db: Session = Depends(get_db)):
    p = db.scalar(select(Project).where(Project.share_token == share_token))
    if not p:
        raise HTTPException(404, "Shared app not found")
    items = db.scalars(
        select(DashboardItem)
        .where(DashboardItem.project_id == p.id)
        .order_by(DashboardItem.sort_order, DashboardItem.created_at)
    ).all()
    if not items:
        raise HTTPException(404, "This app has no widgets yet")
    config, inputs = _app_inputs_for_project(db, p)
    refreshed = _refresh_dashboard_items(items, db, p, request, refresh=True)
    title = config.get("title") or p.name
    return schemas.SharedDashboardOut(
        project_name=p.name,
        config=schemas.AppConfigOut(
            title=title,
            description=config.get("description") or "",
            input_cell_ids=config.get("input_cell_ids") or [],
        ),
        inputs=[schemas.AppInputOut(**i) for i in inputs],
        items=refreshed,
    )
