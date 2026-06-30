"""All configuration comes from environment variables. On Render you set
these in the dashboard (or via render.yaml). Locally, a .env file is loaded."""
import os

from dotenv import load_dotenv

load_dotenv()  # no-op on Render; loads .env in local dev

# --- GCP / BigQuery ---
GCP_PROJECT = os.environ.get("GCP_PROJECT", "")
BQ_LOCATION = os.environ.get("BQ_LOCATION", "US")            # data location
VERTEX_LOCATION = os.environ.get("VERTEX_LOCATION", "us-central1")  # Vertex region
MAX_BYTES_BILLED = int(os.environ.get("MAX_BYTES_BILLED", 2 * 1024**3))  # 2 GB cap/query

# --- Model roles -----------------------------------------------------------
# FETCH role  = turns a question into SQL  ("fetch the data")
# VIZ role    = chart spec + written analysis  ("generation / visualization")
#
# The VIZ role always runs on Vertex AI (Gemini), per the design.
# The FETCH role defaults to Vertex too, but can be switched to OpenAI by
# setting FETCH_PROVIDER=openai + OPENAI_API_KEY + FETCH_MODEL=gpt-4o-mini.
FETCH_PROVIDER = os.environ.get("FETCH_PROVIDER", "vertex")   # "vertex" | "openai"
FETCH_MODEL = os.environ.get("FETCH_MODEL", "gemini-2.5-flash")
VIZ_MODEL = os.environ.get("VIZ_MODEL", "gemini-2.5-pro")     # Vertex AI only
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# --- App -------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./hexlite.db")
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]

# How many past Q&A entries to feed back into a new question as memory.
MEMORY_CONTEXT_SIZE = int(os.environ.get("MEMORY_CONTEXT_SIZE", 5))
