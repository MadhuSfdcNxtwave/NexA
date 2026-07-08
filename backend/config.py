"""All configuration comes from environment variables. On Render you set
these in the dashboard (or via render.yaml). Locally, a .env file is loaded."""
import os
from pathlib import Path

from dotenv import load_dotenv

# Always load backend/.env regardless of shell cwd (fixes uvicorn on Windows).
_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV_PATH, override=True)

# --- GCP / BigQuery ---
GCP_PROJECT = os.environ.get("GCP_PROJECT", "")
BQ_LOCATION = os.environ.get("BQ_LOCATION", "US")            # data location
BQ_DEFAULT_DATASET = os.environ.get("BQ_DEFAULT_DATASET", "").strip()  # warehouse + SQL focus dataset
# Comma-separated BigQuery tables seeded into workspace catalog when empty (all projects share).
_DATASET = "kossip-helpers.academy_success_ai_analytics_worksapce"
_DEFAULT_WS_TABLES = ",".join([
    f"{_DATASET}.y_academy_users_placements_details",
    f"{_DATASET}.z_ccbp_academy_users_jobs_details",
    f"{_DATASET}.z_ccbp_academy_users_master_data",
    f"{_DATASET}.academy_nps_form_responses",
    f"{_DATASET}.users_contextual_feedback_details",
    f"{_DATASET}.z_users_contextual_feedback_details",
    f"{_DATASET}.y_academy_user_daily_engagement_time_spent",
    f"{_DATASET}.z_academy_users_npc_master_table",
    f"{_DATASET}.academy_user_profile_basic_details",
    f"{_DATASET}.academy_user_profile_education_details",
    f"{_DATASET}.z_academy_placement_eligibility_user_wise_stage_wise_best_attempts",
    f"{_DATASET}.feedback_successcoach_support",
    f"{_DATASET}.z_ccbp_users_cloudwatch_interactions_with_nav_bar",
])
DEFAULT_WORKSPACE_TABLES = os.environ.get("DEFAULT_WORKSPACE_TABLES", _DEFAULT_WS_TABLES).strip()
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")  # Vertex region
MAX_BYTES_BILLED = int(os.environ.get("MAX_BYTES_BILLED", 2 * 1024**3))  # 2 GB cap/query (Ask)
# Row preview on large views can scan many GB even with LIMIT; default 15 GB.
PREVIEW_MAX_BYTES_BILLED = int(
    os.environ.get("PREVIEW_MAX_BYTES_BILLED", 15 * 1024**3)
)

# --- Model roles -----------------------------------------------------------
# SQL generation (FETCH) uses one of: gemini | openai | anthropic
# Presentation (VIZ) uses the same provider options.
#
# SDK mapping:
#   gemini   -> Google GenAI SDK (Vertex AI or GEMINI_API_KEY)
#   openai   -> OpenAI SDK (OpenAI API or OpenRouter via OPENAI_BASE_URL)
#   anthropic -> Anthropic SDK (native Claude)
#
# Recommended SQL models:
#   gemini:    gemini-2.5-flash
#   openai:    gpt-4o  (or openai/gpt-4o on OpenRouter)
#   anthropic: claude-sonnet-4-20250514
SQL_PROVIDER = os.environ.get("SQL_PROVIDER", "").strip().lower()  # optional override for FETCH
FETCH_PROVIDER = os.environ.get("FETCH_PROVIDER", "gemini").strip().lower()
FETCH_MODEL = os.environ.get("FETCH_MODEL", "gemini-2.5-flash").strip()
VIZ_PROVIDER = os.environ.get("VIZ_PROVIDER", "gemini").strip().lower()
VIZ_MODEL = os.environ.get("VIZ_MODEL", "gemini-2.5-pro").strip()

# Google GenAI — Vertex (GCP_PROJECT) or AI Studio (GEMINI_API_KEY)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

# OpenAI SDK — also works with OpenRouter when OPENAI_BASE_URL is set
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "").strip()

# Anthropic SDK — native Claude API
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

# SQL generation output cap (keep moderate).
OPENAI_MAX_TOKENS = int(os.environ.get("OPENAI_MAX_TOKENS", "4096"))
ANTHROPIC_MAX_TOKENS = int(os.environ.get("ANTHROPIC_MAX_TOKENS", "4096"))
# Presentation can use more tokens for richer analysis titles.
VIZ_MAX_TOKENS = int(os.environ.get("VIZ_MAX_TOKENS", "4096"))
VIZ_FALLBACK_MODELS = [
    m.strip()
    for m in os.environ.get(
        "VIZ_FALLBACK_MODELS",
        "google/gemini-2.1-pro-preview,anthropic/claude-sonnet-4,openai/gpt-4o",
    ).split(",")
    if m.strip()
]

# --- App -------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./hexlite.db")
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]

# How many past Q&A entries to feed back into a new question as memory.
MEMORY_CONTEXT_SIZE = int(os.environ.get("MEMORY_CONTEXT_SIZE", "12"))
# Prior Thread turns injected before each new question (Cursor-style conversation).
THREAD_CONVERSATION_TURNS = int(os.environ.get("THREAD_CONVERSATION_TURNS", "8"))
# How many prior Thread answers to scan for cache hits (can be > memory context).
CACHE_LOOKUP_SIZE = int(os.environ.get("CACHE_LOOKUP_SIZE", "12"))

# SQL accuracy: validation retries + optional human approval before BigQuery run.
# Hex-style defaults: fewer retries, dry-run verification (no LLM verify per attempt).
HEX_STYLE_PIPELINE = os.environ.get("HEX_STYLE_PIPELINE", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)
# Table routing: retrieval (vector first, keyword fallback) | vector | llm | hybrid
TABLE_ROUTER_MODE = os.environ.get(
    "TABLE_ROUTER_MODE",
    "retrieval" if HEX_STYLE_PIPELINE else "hybrid",
).strip().lower()
EMBEDDING_RETRIEVAL_ENABLED = os.environ.get(
    "EMBEDDING_RETRIEVAL_ENABLED",
    "true" if HEX_STYLE_PIPELINE else "false",
).strip().lower() in ("1", "true", "yes")
# Pre-indexed semantic table search. Default to Gemini/Vertex because this app already uses GCP.
EMBEDDING_PROVIDER = os.environ.get(
    "EMBEDDING_PROVIDER",
    "gemini" if (GCP_PROJECT or GEMINI_API_KEY) else "openai",
).strip().lower()
EMBEDDING_MODEL = os.environ.get(
    "EMBEDDING_MODEL",
    "gemini-embedding-001" if EMBEDDING_PROVIDER in ("gemini", "google", "vertex") else "text-embedding-3-small",
).strip()
# Fused routing: vector + keyword over all tables, then LLM disambiguation on top-K.
ROUTING_FUSION_ENABLED = os.environ.get(
    "ROUTING_FUSION_ENABLED",
    "true" if HEX_STYLE_PIPELINE else "false",
).strip().lower() in ("1", "true", "yes")
ROUTING_TOP_K = int(os.environ.get("ROUTING_TOP_K", "8"))
ROUTING_VECTOR_WEIGHT = float(os.environ.get("ROUTING_VECTOR_WEIGHT", "0.55"))
ROUTING_KEYWORD_WEIGHT = float(os.environ.get("ROUTING_KEYWORD_WEIGHT", "0.35"))

# Legacy KB full-article router (disabled when fusion routing is on).
KB_AI_ROUTING = os.environ.get(
    "KB_AI_ROUTING",
    "false" if ROUTING_FUSION_ENABLED else ("true" if HEX_STYLE_PIPELINE else "false"),
).strip().lower() in ("1", "true", "yes")
KB_AI_TOP_CANDIDATES = int(os.environ.get("KB_AI_TOP_CANDIDATES", "12"))

EMBEDDING_TOP_K = int(os.environ.get("EMBEDDING_TOP_K", "8"))
EMBEDDING_MIN_SCORE = float(os.environ.get("EMBEDDING_MIN_SCORE", "0.22"))
EMBEDDING_AUTO_INDEX_ON_STARTUP = os.environ.get(
    "EMBEDDING_AUTO_INDEX_ON_STARTUP",
    "true" if HEX_STYLE_PIPELINE else "false",
).strip().lower() in ("1", "true", "yes")
EMBEDDING_AUTO_INDEX_ON_ASK = os.environ.get(
    "EMBEDDING_AUTO_INDEX_ON_ASK",
    "false",
).strip().lower() in ("1", "true", "yes")
SCHEMA_CONTEXT_MAX_CHARS = int(os.environ.get("SCHEMA_CONTEXT_MAX_CHARS", "4500" if HEX_STYLE_PIPELINE else "12000"))
SCHEMA_MAX_COLUMNS_PER_TABLE = int(os.environ.get("SCHEMA_MAX_COLUMNS_PER_TABLE", "28" if HEX_STYLE_PIPELINE else "0"))
# presentation: hex = heuristic chart + 1 analysis call | full = chart LLM + analyze + suggest LLM
PRESENTATION_MODE = os.environ.get(
    "PRESENTATION_MODE",
    "hex" if HEX_STYLE_PIPELINE else "full",
).strip().lower()
# Cache: hex = rule-based only (no LLM cache_decision) | llm = LLM picks cache source
CACHE_ROUTER_MODE = os.environ.get(
    "CACHE_ROUTER_MODE",
    "rules" if HEX_STYLE_PIPELINE else "llm",
).strip().lower()

# Domain SQL templates (NPS/feedback/overview) — off by default: the generic
# LLM pipeline handles any table. Enable only for curated small workspaces.
SQL_TEMPLATES_ENABLED = os.environ.get("SQL_TEMPLATES_ENABLED", "false").strip().lower() in (
    "1",
    "true",
    "yes",
)

SQL_MAX_ATTEMPTS = int(os.environ.get("SQL_MAX_ATTEMPTS", "3" if HEX_STYLE_PIPELINE else "6"))
# Keep retrying while validation errors shrink, up to this hard cap.
SQL_MAX_ATTEMPTS_CAP = int(os.environ.get("SQL_MAX_ATTEMPTS_CAP", "5" if HEX_STYLE_PIPELINE else "10"))
SQL_RETRY_STALE_LIMIT = int(os.environ.get("SQL_RETRY_STALE_LIMIT", "2"))
_default_verify = "false" if HEX_STYLE_PIPELINE else "true"
SQL_VERIFY_WITH_LLM = os.environ.get("SQL_VERIFY_WITH_LLM", _default_verify).strip().lower() in (
    "1",
    "true",
    "yes",
)
# When true, pause for user to click Run query. Default off — AI runs after validation.
REQUIRE_SQL_APPROVAL = os.environ.get("REQUIRE_SQL_APPROVAL", "false").strip().lower() in (
    "1",
    "true",
    "yes",
)
# Pause to ask the user when table/metric/join intent is ambiguous (Hex-style).
ASK_CLARIFICATION_ENABLED = os.environ.get("ASK_CLARIFICATION_ENABLED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)

# Chain SQL: break complex questions into multiple validated queries (comparison, multi-period).
# Hex-style: rule-based chain detection only (no LLM chain planner).
SQL_CHAIN_ENABLED = os.environ.get("SQL_CHAIN_ENABLED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)
SQL_CHAIN_PLANNER = os.environ.get(
    "SQL_CHAIN_PLANNER",
    "rules" if HEX_STYLE_PIPELINE else "llm",
).strip().lower()
SQL_CHAIN_MAX_STEPS = int(os.environ.get("SQL_CHAIN_MAX_STEPS", "3"))
CACHE_ANSWER_ENABLED = os.environ.get("CACHE_ANSWER_ENABLED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)
MEMORY_MAX_ROWS = int(os.environ.get("MEMORY_MAX_ROWS", "500"))
NOTEBOOK_MAX_ROWS = int(os.environ.get("NOTEBOOK_MAX_ROWS", "2000"))

# Verbose ask-pipeline tracing (routing, semantic SQL, preflight) → ask-debug.log + console.
ASK_DEBUG_LOG = os.environ.get("ASK_DEBUG_LOG", "false").strip().lower() in (
    "1",
    "true",
    "yes",
)

# --- Auth & credits ---
JWT_SECRET = os.environ.get("JWT_SECRET", "change-me-in-production-nexa")
JWT_EXPIRE_MINUTES = int(os.environ.get("JWT_EXPIRE_MINUTES", str(60 * 24 * 7)))
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
DEFAULT_USER_CREDITS = float(os.environ.get("DEFAULT_USER_CREDITS", "100"))
CREDITS_PER_GB = float(os.environ.get("CREDITS_PER_GB", "1"))
