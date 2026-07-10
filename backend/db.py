"""Persistence layer. Three tables:

  projects        - one workspace; holds a name + free-text join hints
  project_tables  - which BigQuery tables belong to a project (the "data tables" section)
  memories        - per-project episodic memory (past question / SQL / finding)

Works with SQLite locally and Postgres on Render — just change DATABASE_URL.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, String, Text, create_engine, func
from sqlalchemy.orm import (DeclarativeBase, Mapped, mapped_column,
                            relationship, sessionmaker)

import config

# Render's Postgres connection string sometimes uses the postgres:// scheme,
# which SQLAlchemy no longer accepts. Normalise it.
_url = config.DATABASE_URL
if _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql+psycopg2://", 1)

_connect_args = {"check_same_thread": False} if _url.startswith("sqlite") else {}
engine = create_engine(_url, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="user")  # admin | user
    credits_balance: Mapped[float] = mapped_column(default=0.0)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class UsageLog(Base):
    __tablename__ = "usage_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(40), default="ask")
    bytes_estimate: Mapped[int] = mapped_column(default=0)
    credits_used: Mapped[float] = mapped_column(default=0.0)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SqlVerificationLog(Base):
    """Audit trail for SQL validation / LLM review before BigQuery runs."""
    __tablename__ = "sql_verification_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    question: Mapped[str] = mapped_column(Text, default="")
    sql: Mapped[str] = mapped_column(Text, default="")
    attempt: Mapped[int] = mapped_column(default=1)
    phase: Mapped[str] = mapped_column(String(40), default="llm_review")
    passed: Mapped[bool] = mapped_column(default=False)
    issues_json: Mapped[str] = mapped_column(Text, default="[]")
    source: Mapped[str] = mapped_column(String(40), default="")
    llm_provider: Mapped[str] = mapped_column(String(40), default="")
    llm_model: Mapped[str] = mapped_column(String(120), default="")
    result_row_count: Mapped[int | None] = mapped_column(default=None)
    model_used: Mapped[str] = mapped_column(String(100), default="")
    user_feedback: Mapped[str] = mapped_column(String(20), default="")
    feedback_reason: Mapped[str] = mapped_column(String(100), default="")
    issues_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class LearnedTemplate(Base):
    """SQL patterns promoted from repeated successful verification logs."""
    __tablename__ = "learned_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    trigger_question: Mapped[str] = mapped_column(Text, unique=True)
    sql_template: Mapped[str] = mapped_column(Text)
    expected_row_count: Mapped[int | None] = mapped_column(default=None)
    promoted_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    use_count: Mapped[int] = mapped_column(default=0)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    join_hints: Mapped[str] = mapped_column(Text, default="")
    share_token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    notebook_enabled: Mapped[bool] = mapped_column(default=True)
    reuse_cached_results: Mapped[bool] = mapped_column(default=False)
    # App builder: title, description, which notebook input cells appear on published app.
    app_config_json: Mapped[str] = mapped_column(Text, default="{}")
    # Tracking (Hex-style lists): opens counter + last time anyone viewed it.
    view_count: Mapped[int] = mapped_column(default=0)
    last_viewed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Hex-style organization: manual status + category tags.
    status: Mapped[str] = mapped_column(String(40), default="")
    categories_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    tables: Mapped[list["ProjectTable"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    memories: Mapped[list["Memory"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    threads: Mapped[list["Thread"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    dashboard_items: Mapped[list["DashboardItem"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    notebook_cells: Mapped[list["NotebookCell"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ProjectTable(Base):
    """Legacy per-project rows — migrated into workspace_tables on startup."""
    __tablename__ = "project_tables"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    full_table_id: Mapped[str] = mapped_column(String(500))  # project.dataset.table
    description: Mapped[str] = mapped_column(Text, default="")
    column_descriptions_json: Mapped[str] = mapped_column(Text, default="{}")
    column_hints_json: Mapped[str] = mapped_column(Text, default="{}")
    included_for_ai: Mapped[bool] = mapped_column(default=True)
    endorsed: Mapped[bool] = mapped_column(default=False)
    project: Mapped[Project] = relationship(back_populates="tables")


class WorkspaceSettings(Base):
    """Singleton workspace config — survives project delete."""
    __tablename__ = "workspace_settings"
    id: Mapped[int] = mapped_column(primary_key=True)  # always 1
    join_hints: Mapped[str] = mapped_column(Text, default="")
    # Saved Org Schema snapshot — the full table catalog "memory" admins can view.
    org_schema_json: Mapped[str] = mapped_column(Text, default="{}")
    org_schema_updated_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WorkspaceTable(Base):
    """Workspace-wide BigQuery catalog — shared by all projects; survives project delete."""
    __tablename__ = "workspace_tables"
    id: Mapped[int] = mapped_column(primary_key=True)
    full_table_id: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    column_descriptions_json: Mapped[str] = mapped_column(Text, default="{}")
    column_hints_json: Mapped[str] = mapped_column(Text, default="{}")
    # Free-text SQL / metric rules for Ask (overrides default measure filters when set).
    business_rules: Mapped[str] = mapped_column(Text, default="")
    # One-time AI profile: columns + sample data + date ranges + join/query guidance.
    ai_overview: Mapped[str] = mapped_column(Text, default="")
    ai_profile_json: Mapped[str] = mapped_column(Text, default="{}")
    # Pre-indexed semantic retrieval vector for Hex-style table routing.
    embedding_model: Mapped[str] = mapped_column(String(120), default="")
    embedding_hash: Mapped[str] = mapped_column(String(64), default="")
    embedding_json: Mapped[str] = mapped_column(Text, default="[]")
    embedding_updated_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    included_for_ai: Mapped[bool] = mapped_column(default=True)
    endorsed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Collection(Base):
    """Hex-style collection — a named group of projects."""
    __tablename__ = "collections"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    items: Mapped[list["CollectionProject"]] = relationship(
        back_populates="collection", cascade="all, delete-orphan"
    )


class CollectionProject(Base):
    """Membership link — one project can live in many collections."""
    __tablename__ = "collection_projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    collection_id: Mapped[int] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    added_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    collection: Mapped[Collection] = relationship(back_populates="items")


class Thread(Base):
    """Standalone conversation — optionally linked to a project notebook."""
    __tablename__ = "threads"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(300), default="New thread")
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    overview_kb: Mapped[str] = mapped_column(Text, default="")
    project: Mapped[Project] = relationship(back_populates="threads")
    memories: Mapped[list["Memory"]] = relationship(back_populates="thread")


class Memory(Base):
    __tablename__ = "memories"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    thread_id: Mapped[int | None] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), nullable=True, index=True
    )
    question: Mapped[str] = mapped_column(Text)
    sql: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    chart_spec_json: Mapped[str] = mapped_column(Text, default="{}")
    rows_json: Mapped[str] = mapped_column(Text, default="[]")
    columns_json: Mapped[str] = mapped_column(Text, default="[]")
    bytes_estimate: Mapped[int | None] = mapped_column(default=None)
    credits_used: Mapped[float | None] = mapped_column(default=None)
    from_cache: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    project: Mapped[Project] = relationship(back_populates="memories")
    thread: Mapped["Thread | None"] = relationship(back_populates="memories")


class DashboardItem(Base):
    __tablename__ = "dashboard_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    question: Mapped[str] = mapped_column(Text)
    sql: Mapped[str] = mapped_column(Text, default="")
    analysis: Mapped[str] = mapped_column(Text, default="")
    chart_spec_json: Mapped[str] = mapped_column(Text, default="{}")
    sort_order: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    project: Mapped[Project] = relationship(back_populates="dashboard_items")


class NotebookCell(Base):
    __tablename__ = "notebook_cells"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    cell_type: Mapped[str] = mapped_column(String(20))  # input | sql | text
    name: Mapped[str] = mapped_column(String(120))
    content: Mapped[str] = mapped_column(Text, default="")
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    sort_order: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    project: Mapped[Project] = relationship(back_populates="notebook_cells")
    runs: Mapped[list["NotebookCellRun"]] = relationship(
        back_populates="cell", cascade="all, delete-orphan"
    )


class NotebookCellRun(Base):
    """Latest cached execution result per notebook SQL cell."""
    __tablename__ = "notebook_cell_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    cell_id: Mapped[int] = mapped_column(ForeignKey("notebook_cells.id", ondelete="CASCADE"))
    cell_name: Mapped[str] = mapped_column(String(120))
    sql: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    columns_json: Mapped[str] = mapped_column(Text, default="[]")
    rows_json: Mapped[str] = mapped_column(Text, default="[]")
    bytes_estimate: Mapped[int | None] = mapped_column(default=None)
    ran_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    cell: Mapped[NotebookCell] = relationship(back_populates="runs")


def _migrate_project_tables() -> None:
    """Add columns introduced after first deploy (SQLite / Postgres safe)."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "project_tables" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("project_tables")}
    alters = []
    if "description" not in existing:
        alters.append("ALTER TABLE project_tables ADD COLUMN description TEXT DEFAULT ''")
    if "included_for_ai" not in existing:
        alters.append("ALTER TABLE project_tables ADD COLUMN included_for_ai BOOLEAN DEFAULT 1")
    if "endorsed" not in existing:
        alters.append("ALTER TABLE project_tables ADD COLUMN endorsed BOOLEAN DEFAULT 0")
    if "column_descriptions_json" not in existing:
        alters.append(
            "ALTER TABLE project_tables ADD COLUMN column_descriptions_json TEXT DEFAULT '{}'"
        )
    if "column_hints_json" not in existing:
        alters.append(
            "ALTER TABLE project_tables ADD COLUMN column_hints_json TEXT DEFAULT '{}'"
        )
    if not alters:
        return
    with engine.begin() as conn:
        for stmt in alters:
            conn.execute(text(stmt))


def _migrate_memories() -> None:
    """Add chart/result columns to memories (SQLite / Postgres safe)."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "memories" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("memories")}
    alters = []
    if "chart_spec_json" not in existing:
        alters.append("ALTER TABLE memories ADD COLUMN chart_spec_json TEXT DEFAULT '{}'")
    if "rows_json" not in existing:
        alters.append("ALTER TABLE memories ADD COLUMN rows_json TEXT DEFAULT '[]'")
    if "columns_json" not in existing:
        alters.append("ALTER TABLE memories ADD COLUMN columns_json TEXT DEFAULT '[]'")
    if "bytes_estimate" not in existing:
        alters.append("ALTER TABLE memories ADD COLUMN bytes_estimate INTEGER")
    if "from_cache" not in existing:
        alters.append("ALTER TABLE memories ADD COLUMN from_cache BOOLEAN DEFAULT 0")
    if not alters:
        return
    with engine.begin() as conn:
        for stmt in alters:
            conn.execute(text(stmt))


def _migrate_projects() -> None:
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "projects" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("projects")}
    if "share_token" not in existing:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE projects ADD COLUMN share_token VARCHAR(64)"))
    if "notebook_enabled" not in existing:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE projects ADD COLUMN notebook_enabled BOOLEAN DEFAULT 0"))
    if "reuse_cached_results" not in existing:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE projects ADD COLUMN reuse_cached_results BOOLEAN DEFAULT 1"))
    if "app_config_json" not in existing:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE projects ADD COLUMN app_config_json TEXT DEFAULT '{}'"))
    if "view_count" not in existing:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE projects ADD COLUMN view_count INTEGER DEFAULT 0"))
    if "last_viewed_at" not in existing:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE projects ADD COLUMN last_viewed_at TIMESTAMP"))
    if "status" not in existing:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE projects ADD COLUMN status VARCHAR(40) DEFAULT ''"))
    if "categories_json" not in existing:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE projects ADD COLUMN categories_json TEXT DEFAULT '[]'"))


def _migrate_notebook() -> None:
    """Notebook tables are created via create_all; no column migrations yet."""
    pass


def _migrate_users_and_usage() -> None:
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "projects" in insp.get_table_names():
        existing = {c["name"] for c in insp.get_columns("projects")}
        if "owner_id" not in existing:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE projects ADD COLUMN owner_id INTEGER"))
    if "memories" in insp.get_table_names():
        existing = {c["name"] for c in insp.get_columns("memories")}
        if "credits_used" not in existing:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE memories ADD COLUMN credits_used REAL"))


def _migrate_workspace_tables() -> None:
    """Create workspace catalog and import any legacy project_tables rows."""
    from sqlalchemy import func, inspect, select, text

    _migrate_project_tables()

    # New AI overview columns on workspace_tables.
    insp = inspect(engine)
    if "workspace_tables" in insp.get_table_names():
        existing = {c["name"] for c in insp.get_columns("workspace_tables")}
        alters = []
        if "ai_overview" not in existing:
            alters.append("ALTER TABLE workspace_tables ADD COLUMN ai_overview TEXT DEFAULT ''")
        if "ai_profile_json" not in existing:
            alters.append("ALTER TABLE workspace_tables ADD COLUMN ai_profile_json TEXT DEFAULT '{}'")
        if "business_rules" not in existing:
            alters.append("ALTER TABLE workspace_tables ADD COLUMN business_rules TEXT DEFAULT ''")
        if "embedding_model" not in existing:
            alters.append("ALTER TABLE workspace_tables ADD COLUMN embedding_model VARCHAR(120) DEFAULT ''")
        if "embedding_hash" not in existing:
            alters.append("ALTER TABLE workspace_tables ADD COLUMN embedding_hash VARCHAR(64) DEFAULT ''")
        if "embedding_json" not in existing:
            alters.append("ALTER TABLE workspace_tables ADD COLUMN embedding_json TEXT DEFAULT '[]'")
        if "embedding_updated_at" not in existing:
            alters.append("ALTER TABLE workspace_tables ADD COLUMN embedding_updated_at TIMESTAMP")
        if alters:
            with engine.begin() as conn:
                for stmt in alters:
                    conn.execute(text(stmt))
    db = SessionLocal()
    try:
        legacy = db.scalars(select(ProjectTable)).all()
        for pt in legacy:
            exists = db.scalar(
                select(WorkspaceTable).where(WorkspaceTable.full_table_id == pt.full_table_id)
            )
            if exists:
                # Merge legacy column metadata into workspace row when missing.
                merged_desc = exists.description or pt.description or ""
                ws_cols = exists.column_descriptions_json or "{}"
                pt_cols = pt.column_descriptions_json or "{}"
                if pt_cols.strip() not in ("", "{}"):
                    import json

                    try:
                        a = json.loads(ws_cols) if ws_cols.strip() else {}
                        b = json.loads(pt_cols) if pt_cols.strip() else {}
                        if not isinstance(a, dict):
                            a = {}
                        if isinstance(b, dict):
                            for k, v in b.items():
                                if v and not a.get(k):
                                    a[k] = v
                        exists.column_descriptions_json = json.dumps(a)
                    except json.JSONDecodeError:
                        pass
                if merged_desc and not exists.description:
                    exists.description = merged_desc
                if pt.column_hints_json and exists.column_hints_json in ("", "{}"):
                    exists.column_hints_json = pt.column_hints_json
                continue
            db.add(
                WorkspaceTable(
                    full_table_id=pt.full_table_id,
                    description=pt.description or "",
                    column_descriptions_json=pt.column_descriptions_json or "{}",
                    column_hints_json=pt.column_hints_json or "{}",
                    included_for_ai=pt.included_for_ai,
                    endorsed=pt.endorsed,
                )
            )
        db.commit()
    finally:
        db.close()


def get_workspace_settings(db) -> WorkspaceSettings:
    row = db.get(WorkspaceSettings, 1)
    if not row:
        row = WorkspaceSettings(id=1, join_hints="")
        db.add(row)
        db.flush()
    return row


def get_workspace_join_hints(db) -> str:
    return get_workspace_settings(db).join_hints or ""


def set_workspace_join_hints(db, text: str) -> str:
    row = get_workspace_settings(db)
    row.join_hints = text or ""
    return row.join_hints


def _migrate_workspace_settings() -> None:
    """Create workspace settings and merge join hints from any legacy projects."""
    import model_yaml
    from sqlalchemy import inspect, select, text

    insp = inspect(engine)
    if "workspace_settings" in insp.get_table_names():
        existing = {c["name"] for c in insp.get_columns("workspace_settings")}
        alters = []
        if "org_schema_json" not in existing:
            alters.append(
                "ALTER TABLE workspace_settings ADD COLUMN org_schema_json TEXT DEFAULT '{}'"
            )
        if "org_schema_updated_at" not in existing:
            alters.append(
                "ALTER TABLE workspace_settings ADD COLUMN org_schema_updated_at TIMESTAMP"
            )
        if alters:
            with engine.begin() as conn:
                for stmt in alters:
                    conn.execute(text(stmt))

    db = SessionLocal()
    try:
        row = get_workspace_settings(db)
        merged = row.join_hints or ""
        for p in db.scalars(select(Project).order_by(Project.id)):
            hint = (p.join_hints or "").strip()
            if hint:
                merged = model_yaml.merge_join_hints(merged, [hint])
        if merged.strip() and merged != (row.join_hints or ""):
            row.join_hints = merged
        db.commit()
    finally:
        db.close()


def _sync_default_workspace_tables() -> None:
    """Ensure every table in DEFAULT_WORKSPACE_TABLES exists in the workspace catalog."""
    import config
    from sqlalchemy import select

    raw = getattr(config, "DEFAULT_WORKSPACE_TABLES", "") or ""
    ids = [x.strip() for x in raw.split(",") if x.strip()]
    if not ids:
        return
    db = SessionLocal()
    try:
        import bq

        added = False
        for fq in ids:
            if db.scalar(select(WorkspaceTable).where(WorkspaceTable.full_table_id == fq)):
                continue
            desc = ""
            try:
                desc = bq.table_metadata(fq).get("description", "") or ""
            except Exception:
                pass
            db.add(
                WorkspaceTable(
                    full_table_id=fq,
                    description=desc,
                    included_for_ai=True,
                )
            )
            added = True
        if added:
            db.commit()
    finally:
        db.close()


def _sync_workspace_from_default_dataset() -> None:
    """Add all tables from BQ_DEFAULT_DATASET missing from the workspace catalog.

    Local dev often has 50+ tables from manual adds / YAML import; fresh Render
    deploys only seed DEFAULT_WORKSPACE_TABLES (~13). This keeps production in
    sync with the full dataset without clicking Add all in the Data tab.
    """
    import config
    from sqlalchemy import select

    if not config.SYNC_WORKSPACE_FROM_DATASET:
        return
    dataset_id = (config.BQ_DEFAULT_DATASET or "").strip()
    project = (config.GCP_PROJECT or "").strip()
    if not dataset_id or not project or project == "your-gcp-project-id":
        return
    full_dataset = f"{project}.{dataset_id}"
    try:
        import bq

        tables = bq.list_tables_in_dataset(full_dataset)
    except Exception as e:
        print(f"[workspace-sync] could not list {full_dataset}: {e}")
        return
    if not tables:
        return

    db = SessionLocal()
    try:
        existing = {t.full_table_id for t in db.scalars(select(WorkspaceTable)).all()}
        added = 0
        for tbl in tables:
            fq = tbl.get("full_table_id")
            if not fq or fq in existing:
                continue
            db.add(
                WorkspaceTable(
                    full_table_id=fq,
                    description="",
                    included_for_ai=True,
                )
            )
            existing.add(fq)
            added += 1
        if added:
            db.commit()
            print(f"[workspace-sync] added {added} tables from {full_dataset}")
    finally:
        db.close()


def _sync_workspace_from_yaml_models() -> None:
    """Ensure every table in workspace_models.yaml exists in the workspace catalog.

    Fresh deploys previously only got DEFAULT_WORKSPACE_TABLES (~13). YAML has ~55
    models — without this sync, Ask cannot see attendance / master / placements etc.
    Lightweight: adds missing table rows only (no BQ profiling / AI overviews).
    """
    import config
    from pathlib import Path
    from sqlalchemy import select

    if not getattr(config, "SYNC_WORKSPACE_FROM_YAML", True):
        return

    yaml_path = Path(__file__).resolve().parent / "workspace_models.yaml"
    if not yaml_path.is_file():
        print(f"[workspace-yaml-sync] missing {yaml_path}")
        return

    try:
        import model_yaml

        models = model_yaml.parse_yaml_documents(yaml_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[workspace-yaml-sync] could not parse YAML: {e}")
        return

    ids: list[str] = []
    seen: set[str] = set()
    for model in models:
        fq = (model.get("full_table_id") or "").strip()
        if not fq or fq in seen:
            continue
        seen.add(fq)
        ids.append(fq)
    if not ids:
        return

    db = SessionLocal()
    try:
        existing = {t.full_table_id for t in db.scalars(select(WorkspaceTable)).all()}
        added = 0
        for fq in ids:
            if fq in existing:
                continue
            desc = ""
            for model in models:
                if model.get("full_table_id") == fq:
                    desc = (model.get("description") or "")[:2000]
                    break
            db.add(
                WorkspaceTable(
                    full_table_id=fq,
                    description=desc,
                    included_for_ai=True,
                )
            )
            existing.add(fq)
            added += 1
        if added:
            db.commit()
            print(f"[workspace-yaml-sync] added {added} tables from workspace_models.yaml")
    finally:
        db.close()


def _migrate_threads() -> None:
    """Add memories.thread_id and backfill a default thread per project."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "memories" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("memories")}
    if "thread_id" not in existing:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE memories ADD COLUMN thread_id INTEGER"))

    # Backfill: every project with orphan memories gets one default thread.
    db = SessionLocal()
    try:
        orphan_projects = db.execute(
            text("SELECT DISTINCT project_id FROM memories WHERE thread_id IS NULL")
        ).fetchall()
        for (pid,) in orphan_projects:
            first_q = db.execute(
                text(
                    "SELECT question FROM memories WHERE project_id = :pid "
                    "ORDER BY created_at ASC LIMIT 1"
                ),
                {"pid": pid},
            ).scalar()
            proj = db.get(Project, pid)
            title = (first_q or (proj.name if proj else "") or "Thread").strip()[:300]
            t = Thread(project_id=pid, title=title, created_by=proj.owner_id if proj else None)
            db.add(t)
            db.flush()
            db.execute(
                text("UPDATE memories SET thread_id = :tid WHERE project_id = :pid AND thread_id IS NULL"),
                {"tid": t.id, "pid": pid},
            )
        db.commit()
    finally:
        db.close()


def _migrate_thread_overview() -> None:
    """Add threads.overview_kb for per-thread follow-up memory."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "threads" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("threads")}
    if "overview_kb" not in existing:
        with engine.begin() as conn:
            if engine.dialect.name == "postgresql":
                conn.execute(text("ALTER TABLE threads ADD COLUMN overview_kb TEXT DEFAULT ''"))
            else:
                conn.execute(text("ALTER TABLE threads ADD COLUMN overview_kb TEXT DEFAULT ''"))


def _migrate_standalone_threads() -> None:
    """Allow threads and memories without a project (standalone conversations)."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    tables = insp.get_table_names()
    if "threads" not in tables:
        return

    thread_cols = {c["name"]: c for c in insp.get_columns("threads")}
    if thread_cols.get("project_id", {}).get("nullable") is False:
        with engine.begin() as conn:
            conn.execute(text("PRAGMA foreign_keys=OFF"))
            conn.execute(
                text(
                    """
                    CREATE TABLE threads_new (
                        id INTEGER PRIMARY KEY,
                        project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
                        title VARCHAR(300) DEFAULT 'New thread',
                        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO threads_new (id, project_id, title, created_by, created_at, updated_at)
                    SELECT id, project_id, title, created_by, created_at, updated_at FROM threads
                    """
                )
            )
            conn.execute(text("DROP TABLE threads"))
            conn.execute(text("ALTER TABLE threads_new RENAME TO threads"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_threads_project_id ON threads (project_id)"))
            conn.execute(text("PRAGMA foreign_keys=ON"))

    if "memories" in tables:
        mem_cols = {c["name"]: c for c in insp.get_columns("memories")}
        if mem_cols.get("project_id", {}).get("nullable") is False:
            with engine.begin() as conn:
                conn.execute(text("PRAGMA foreign_keys=OFF"))
                conn.execute(
                    text(
                        """
                        CREATE TABLE memories_new (
                            id INTEGER PRIMARY KEY,
                            project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
                            thread_id INTEGER REFERENCES threads(id) ON DELETE CASCADE,
                            question TEXT NOT NULL,
                            sql TEXT DEFAULT '',
                            summary TEXT DEFAULT '',
                            chart_spec_json TEXT DEFAULT '{}',
                            rows_json TEXT DEFAULT '[]',
                            columns_json TEXT DEFAULT '[]',
                            bytes_estimate INTEGER,
                            credits_used FLOAT,
                            from_cache BOOLEAN DEFAULT 0,
                            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                    )
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO memories_new (
                            id, project_id, thread_id, question, sql, summary,
                            chart_spec_json, rows_json, columns_json,
                            bytes_estimate, credits_used, from_cache, created_at
                        )
                        SELECT
                            id, project_id, thread_id, question, sql, summary,
                            chart_spec_json, rows_json, columns_json,
                            bytes_estimate, credits_used, from_cache, created_at
                        FROM memories
                        """
                    )
                )
                conn.execute(text("DROP TABLE memories"))
                conn.execute(text("ALTER TABLE memories_new RENAME TO memories"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_memories_thread_id ON memories (thread_id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_memories_project_id ON memories (project_id)"))
                conn.execute(text("PRAGMA foreign_keys=ON"))


def _migrate_sql_verification_logs() -> None:
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "sql_verification_logs" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("sql_verification_logs")}
    alters: list[str] = []
    if "result_row_count" not in existing:
        alters.append("ALTER TABLE sql_verification_logs ADD COLUMN result_row_count INTEGER")
    if "model_used" not in existing:
        alters.append("ALTER TABLE sql_verification_logs ADD COLUMN model_used VARCHAR(100) DEFAULT ''")
    if "user_feedback" not in existing:
        alters.append("ALTER TABLE sql_verification_logs ADD COLUMN user_feedback VARCHAR(20) DEFAULT ''")
    if "feedback_reason" not in existing:
        alters.append("ALTER TABLE sql_verification_logs ADD COLUMN feedback_reason VARCHAR(100) DEFAULT ''")
    if "issues_count" not in existing:
        alters.append("ALTER TABLE sql_verification_logs ADD COLUMN issues_count INTEGER DEFAULT 0")
    if not alters:
        return
    with engine.begin() as conn:
        for stmt in alters:
            conn.execute(text(stmt))


def _migrate_learned_templates() -> None:
    """learned_templates is created via create_all; no column migrations yet."""
    _migrate_sql_verification_logs()


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate_project_tables()
    _migrate_memories()
    _migrate_threads()
    _migrate_thread_overview()
    _migrate_standalone_threads()
    _migrate_projects()
    _migrate_users_and_usage()
    _migrate_notebook()
    _migrate_workspace_tables()
    _migrate_workspace_settings()
    _migrate_learned_templates()
    _sync_default_workspace_tables()
    _sync_workspace_from_yaml_models()
    _sync_workspace_from_default_dataset()
    from auth import bootstrap_admin

    db = SessionLocal()
    try:
        bootstrap_admin(db)
    finally:
        db.close()


def get_db():
    """FastAPI dependency — yields a session, always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
