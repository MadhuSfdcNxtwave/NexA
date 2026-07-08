"""Request/response shapes for the API."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    role: str
    credits_balance: float
    is_active: bool

    class Config:
        from_attributes = True


class LoginResponse(BaseModel):
    token: str
    user: UserOut


class UserCreate(BaseModel):
    email: str
    name: str = ""
    password: str = Field(min_length=6)
    credits_balance: float | None = None


class UserUpdate(BaseModel):
    name: str | None = None
    password: str | None = Field(default=None, min_length=6)
    credits_balance: float | None = None
    is_active: bool | None = None


class UsageLogOut(BaseModel):
    id: int
    user_id: int
    project_id: int | None
    action: str
    bytes_estimate: int
    credits_used: float
    detail: str
    created_at: str | None = None

    class Config:
        from_attributes = True


class SqlVerificationLogOut(BaseModel):
    id: int
    project_id: int | None
    user_id: int | None
    question: str
    sql: str
    attempt: int
    phase: str
    passed: bool
    issues: list[str] = []
    source: str = ""
    llm_provider: str = ""
    llm_model: str = ""
    created_at: str | None = None

    class Config:
        from_attributes = True


class ProjectCreate(BaseModel):
    name: str


class ProjectOut(BaseModel):
    id: int
    name: str
    join_hints: str
    share_token: str | None = None
    notebook_enabled: bool = True
    reuse_cached_results: bool = False

    class Config:
        from_attributes = True


class ProjectListOut(BaseModel):
    """Hex-style project row with tracking metadata."""
    id: int
    name: str
    owner_name: str = ""
    status: str = ""
    categories: list[str] = []
    thread_count: int = 0
    view_count: int = 0
    created_at: str | None = None
    last_activity_at: str | None = None
    last_viewed_at: str | None = None
    share_token: str | None = None
    notebook_enabled: bool = True
    reuse_cached_results: bool = False
    join_hints: str = ""


class ThreadListOut(BaseModel):
    """One row per thread — Hex-style global Threads list."""
    id: int
    project_id: int | None = None
    project_name: str = ""
    title: str
    creator: str = ""
    turn_count: int = 0
    last_updated_at: str | None = None


class ProjectSettingsUpdate(BaseModel):
    notebook_enabled: bool | None = None
    reuse_cached_results: bool | None = None
    status: str | None = None
    categories: list[str] | None = None
    name: str | None = None


class AppConfigOut(BaseModel):
    title: str = ""
    description: str = ""
    input_cell_ids: list[int] = []


class AppConfigUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    input_cell_ids: list[int] | None = None


class AppInputOut(BaseModel):
    cell_id: int
    name: str
    input_type: str = "date_range"
    label: str = ""
    start_var: str | None = None
    end_var: str | None = None
    var: str | None = None
    default_start: str | None = None
    default_end: str | None = None
    default: str | None = None


class AppOut(BaseModel):
    project_name: str
    config: AppConfigOut
    inputs: list[AppInputOut] = []
    items: list["DashboardItemOut"] = []
    share_token: str | None = None


class TableCreate(BaseModel):
    full_table_id: str  # project.dataset.table


class BulkAddDatasetRequest(BaseModel):
    dataset: str  # project.dataset_id


class BulkAddOut(BaseModel):
    added: list[str] = []
    skipped: int = 0
    total: int = 0


class TableOut(BaseModel):
    id: int
    full_table_id: str
    description: str = ""
    column_descriptions: dict[str, str] = {}
    column_hints: dict[str, str] = {}
    ai_overview: str = ""
    embedding_indexed: bool = False
    embedding_model: str = ""
    embedding_updated_at: str | None = None
    included_for_ai: bool = True
    endorsed: bool = False

    class Config:
        from_attributes = True


class TableUpdate(BaseModel):
    description: str | None = None
    column_descriptions: dict[str, str] | None = None
    column_hints: dict[str, str] | None = None
    included_for_ai: bool | None = None
    endorsed: bool | None = None


class VectorIndexOut(BaseModel):
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    total: int = 0


class DatasetOut(BaseModel):
    dataset_id: str
    project_id: str
    full_id: str


class WarehouseTableOut(BaseModel):
    table_id: str
    full_table_id: str
    table_type: str


class WarehouseCatalogOut(BaseModel):
    datasets: list[DatasetOut]
    tables_by_dataset: dict[str, list[WarehouseTableOut]]


class ColumnOut(BaseModel):
    name: str
    type: str
    description: str = ""
    mode: str = "NULLABLE"


class TableMetadataOut(BaseModel):
    full_table_id: str
    description: str
    num_rows: int | None
    num_bytes: int | None
    table_type: str
    preview_note: str | None = None
    columns: list[ColumnOut]


class TablePreviewOut(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    note: str | None = None


class JoinHintsUpdate(BaseModel):
    join_hints: str


class WorkspaceJoinHintsOut(BaseModel):
    join_hints: str = ""


class TableJoinHintsOut(BaseModel):
    table_id: int
    table_short: str
    join_hints: str = ""


class ModelYamlImportIn(BaseModel):
    yaml: str | None = None
    yamls: list[str] | None = None
    generate_overviews: bool = True


class ModelImportTableResult(BaseModel):
    model_id: str
    full_table_id: str
    table_db_id: int
    columns_imported: int
    relations_imported: int
    created: bool
    overview_generated: bool = False


class ModelImportOut(BaseModel):
    tables: list[ModelImportTableResult]
    join_hints_updated: bool
    errors: list[str] = []


class CollectionOut(BaseModel):
    id: int
    name: str
    description: str = ""
    owner_name: str = ""
    project_count: int = 0
    project_ids: list[int] = []
    created_at: str | None = None


class CollectionCreate(BaseModel):
    name: str
    description: str = ""


class CollectionPatch(BaseModel):
    name: str | None = None
    description: str | None = None


class CollectionProjectIn(BaseModel):
    project_id: int


class ThreadOut(BaseModel):
    id: int
    project_id: int | None = None
    title: str
    creator: str = ""
    turn_count: int = 0
    overview_kb: str = ""
    created_at: str | None = None
    updated_at: str | None = None


class ThreadCreate(BaseModel):
    title: str = ""


class ThreadPatch(BaseModel):
    title: str


class MemoryOut(BaseModel):
    id: int
    thread_id: int | None = None
    question: str
    sql: str
    summary: str
    columns: list[str] = []
    rows: list[dict[str, Any]] = []
    chart_spec: dict[str, Any] = {}
    bytes_estimate: int | None = None
    credits_used: float | None = None
    from_cache: bool = False

    class Config:
        from_attributes = True


class AskRequest(BaseModel):
    question: str
    thread_id: int | None = None
    force_fresh: bool = False
    clarification_choice: str | None = None
    clarification_text: str | None = None
    refined_question: str | None = None


class ClarificationOption(BaseModel):
    id: str
    label: str
    refined_question: str


class AskClarificationEvent(BaseModel):
    clarification_id: str
    prompt: str
    question: str
    options: list[ClarificationOption]
    allow_custom: bool = True


class AskConfirmRequest(BaseModel):
    question: str
    sql: str
    thread_id: int | None = None


class SqlChainStepOut(BaseModel):
    label: str
    question: str
    sql: str
    columns: list[str] = []
    rows: list[dict[str, Any]] = []
    bytes_estimate: int | None = None


class AskResponse(BaseModel):
    question: str
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    viz_rows: list[dict[str, Any]] | None = None
    chart_spec: dict[str, Any]
    analysis: str
    bytes_estimate: int
    credits_used: float = 0
    credits_remaining: float | None = None
    sql_steps: list[SqlChainStepOut] | None = None
    from_cache: bool = False
    cache_source: str | None = None
    suggestions: list[str] = []
    response_mode: str = "data"


class NotebookCellOut(BaseModel):
    id: int
    cell_type: str
    name: str
    content: str
    config: dict[str, Any] = {}
    sort_order: int = 0
    last_run: dict[str, Any] | None = None

    class Config:
        from_attributes = True


class NotebookCellCreate(BaseModel):
    cell_type: str
    name: str
    content: str = ""
    config: dict[str, Any] = {}
    sort_order: int = 0


class NotebookCellUpdate(BaseModel):
    cell_type: str | None = None
    name: str | None = None
    content: str | None = None
    config: dict[str, Any] | None = None
    sort_order: int | None = None


class NotebookRunIn(BaseModel):
    input_overrides: dict[str, str] | None = None
    cell_id: int | None = None


class NotebookRunOut(BaseModel):
    variables: dict[str, str]
    results: dict[str, Any]
    run_log: list[dict[str, Any]]
    bytes_estimate: int
    credits_used: float = 0
    credits_remaining: float | None = None


class LogicGraphNodeOut(BaseModel):
    id: int
    name: str
    cell_type: str
    x: float
    y: float
    width: float
    height: float
    layer: int
    status: str
    row_count: int | None = None
    variables: list[str] = []


class LogicGraphOut(BaseModel):
    nodes: list[LogicGraphNodeOut]
    edges: list[dict[str, str]]
    width: float
    height: float


class DashboardItemCreate(BaseModel):
    question: str
    sql: str
    analysis: str = ""
    chart_spec: dict[str, Any] = {}


class DashboardItemOut(BaseModel):
    id: int
    question: str
    sql: str
    analysis: str
    chart_spec: dict[str, Any] = {}
    columns: list[str] = []
    rows: list[dict[str, Any]] = []
    viz_rows: list[dict[str, Any]] | None = None
    bytes_estimate: int | None = None


class PublishDashboardOut(BaseModel):
    share_token: str


class SharedDashboardOut(BaseModel):
    project_name: str
    config: AppConfigOut = AppConfigOut()
    inputs: list[AppInputOut] = []
    items: list[DashboardItemOut]
