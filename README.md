# NexA

An internal "ask BigQuery in plain English" tool — **NexA** — with **projects**, a **data-tables
manager**, **per-project memory**, and a **two-model pipeline** (one model fetches
data as SQL, Vertex AI generates the visualization and analysis). Full-stack:
FastAPI backend + React frontend + Postgres, deployable to Render.

---

## 1. What it does

- **Projects** — each project is an isolated workspace with its own tables and memory.
- **Data tables section** — add/remove the BigQuery tables a project can see, plus
  plain-English join hints. The model only ever sees that project's tables.
- **Ask** — type a question; the tool writes SQL, runs it read-only, and returns a
  chart plus a written finding.
- **Memory, per project** — every question, its SQL, and the finding are stored in
  Postgres and fed back as context on future questions *within the same project*.
- **Two models** — a FETCH model turns questions into SQL; Vertex AI (the VIZ model)
  produces the chart spec and the written analysis.

```
React (static site)  --HTTP-->  FastAPI (web service)  -->  BigQuery  (your data)
       |                              |                 -->  Vertex AI (Gemini)
   projects, tables,                  +-->  Postgres  (projects, tables, memory)
   ask, charts
```

## 2. Repository layout

```
NexA/
├── render.yaml              # one-file deploy of all 3 resources
├── backend/
│   ├── main.py              # FastAPI routes + the ask pipeline
│   ├── db.py                # SQLAlchemy models: Project, ProjectTable, Memory
│   ├── bq.py                # BigQuery: schema introspection + safe read-only queries
│   ├── llm.py               # the two model roles (FETCH=SQL, VIZ=Vertex AI)
│   ├── credentials.py       # turns one env var into GCP auth (Render-friendly)
│   ├── config.py            # all settings from env vars
│   ├── schemas.py           # request/response shapes
│   └── requirements.txt
└── frontend/
    ├── src/
    │   ├── api.js            # all backend calls
    │   ├── pages/ProjectsPage.jsx   # list + create projects
    │   ├── pages/ProjectPage.jsx    # the two sections: Data tables + Ask
    │   └── components/Chart.jsx     # renders the model's chart spec (Recharts)
    └── package.json
```

## 3. How a question flows (the core loop)

When you ask a question in a project, `POST /projects/{id}/ask` does this:

1. Loads the project's table list and builds a schema description (column names,
   types, descriptions) + the join hints. — `bq.schema_for_tables`
2. Loads the last few memory entries for *this* project as context. — `_recent_memory_text`
3. **FETCH model** turns question + schema + memory into SQL. — `llm.question_to_sql`
4. The SQL is validated as SELECT-only, cost-estimated with a dry run, and executed
   with a byte cap. — `bq.validate_select_only`, `dry_run_bytes`, `run_query`
5. **VIZ model (Vertex AI)** picks a chart type and writes the analysis. —
   `llm.result_to_chart_spec`, `llm.analyze`
6. The question, SQL, and finding are saved to memory. — `db.Memory`
7. Columns, rows, chart spec, and analysis return to the frontend, which renders the
   chart and an expandable data/SQL panel.

**Why two models:** SQL generation is a cheap, structured task — a fast model handles
it. The visualization and the written insight are the "generation" step you wanted on
Vertex AI. The split lives entirely in `llm.py`; changing either is a one-line config
edit (`FETCH_PROVIDER`, `FETCH_MODEL`, `VIZ_MODEL`).

## 4. Prerequisites (GCP)

You need a service account with read-only BigQuery access and Vertex AI access:

```bash
export PROJECT_ID="your-project-id"
export SA="nexa@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud iam service-accounts create nexa --project=$PROJECT_ID
gcloud services enable bigquery.googleapis.com aiplatform.googleapis.com --project=$PROJECT_ID

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA" --role="roles/bigquery.jobUser"
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA" --role="roles/aiplatform.user"
# Per dataset (tightest scope):
bq query --use_legacy_sql=false \
  "GRANT \`roles/bigquery.dataViewer\` ON SCHEMA \`${PROJECT_ID}\`.analytics \
   TO \"serviceAccount:$SA\""
```

For Render you also need a key file (its contents go into one env var):

```bash
gcloud iam service-accounts keys create key.json --iam-account=$SA
# the CONTENTS of key.json become GCP_SA_KEY_JSON — never commit this file
```

## 5. Run it locally

**Backend:**

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in GCP_PROJECT
gcloud auth application-default login   # local auth — no key file needed
uvicorn main:app --reload     # http://localhost:8000
```

**Frontend (new terminal):**

```bash
cd frontend
npm install
cp .env.example .env          # VITE_API_URL=http://localhost:8000
npm run dev                    # http://localhost:5173
```

Open the frontend, create a project, add a table like
`your-project.analytics.users`, then ask a question.

## 6. Environment variables

| Variable | Where | Purpose |
|---|---|---|
| `GCP_PROJECT` | backend | Your GCP project id |
| `GCP_SA_KEY_JSON` | backend (Render) | Full service-account JSON, one var. Local dev leaves this empty and uses `gcloud auth`. |
| `BQ_LOCATION` | backend | BigQuery data location (`US`, `asia-south1`, …) |
| `VERTEX_LOCATION` | backend | Vertex AI region (`us-central1`) |
| `FETCH_PROVIDER` | backend | `vertex` (default) or `openai` |
| `FETCH_MODEL` | backend | SQL model (`gemini-2.5-flash`, or `gpt-4o-mini` for openai) |
| `VIZ_MODEL` | backend | Vertex model for charts + analysis (`gemini-2.5-pro`) |
| `OPENAI_API_KEY` | backend | Only if `FETCH_PROVIDER=openai` |
| `DATABASE_URL` | backend | Set automatically from the Render database |
| `CORS_ORIGINS` | backend | Your frontend URL(s), comma-separated |
| `VITE_API_URL` | frontend | Your backend URL (build-time) |
| `MAX_BYTES_BILLED` | backend | Per-query cost cap in bytes (default 2 GB) |

## 7. Deploy to Render

1. Push this repo to GitHub.
2. In Render: **New > Blueprint**, connect the repo. It reads `render.yaml` and
   creates the Postgres database, the API, and the static site.
3. When prompted, fill the `sync: false` secrets:
   - `GCP_PROJECT` — your project id
   - `GCP_SA_KEY_JSON` — paste the entire contents of `key.json`
   - `OPENAI_API_KEY` — only if you switched `FETCH_PROVIDER` to `openai`
4. Let it deploy once. Note the two generated URLs, e.g.
   `https://nexa-api.onrender.com` and `https://nexa-web.onrender.com`.
5. **Wire the two URLs together** (they reference each other, so this is a second pass):
   - On `nexa-api`, set `CORS_ORIGINS` to the web URL.
   - On `nexa-web`, set `VITE_API_URL` to the API URL, then **Clear cache & deploy**
     (Vite bakes this in at build time, so a rebuild is required).
6. Open the web URL and create your first project.

Notes:
- **Free tier:** free web services cold-start after ~15 min idle (first request is
  slow), and free Postgres expires after ~30 days. Bump both plans for real use.
- **Region:** the blueprint uses `oregon`. For a team in India, change all three
  `region:` values to `singapore` for lower latency. Keep them the same so the API and
  database share Render's private network.

## 8. Code explanation, file by file

**`backend/config.py`** — reads every setting from env vars, including the two model
roles. Nothing sensitive is hard-coded.

**`backend/credentials.py`** — Render can't run `gcloud auth`, so at startup this
writes `GCP_SA_KEY_JSON` to a temp file and points Application Default Credentials at
it. After that, the BigQuery and Vertex clients authenticate with zero extra code.

**`backend/db.py`** — three SQLAlchemy tables. `Project` holds the name and join hints;
`ProjectTable` is the per-project table allow-list; `Memory` is the episodic memory
(question / SQL / finding). Cascades mean deleting a project cleans up its tables and
memory. Works on SQLite locally and Postgres in prod by swapping `DATABASE_URL`.

**`backend/bq.py`** — `schema_for_tables` introspects only the tables a project lists
(plus its join hints) into the text the model reads. `validate_select_only` +
`run_query` enforce read-only and cap cost; the real guard is the service account's
read-only IAM role.

**`backend/llm.py`** — the model layer. `_fetch` routes to Vertex or OpenAI for SQL;
`_viz` always uses Vertex AI for the chart spec and analysis. To add Anthropic or
another provider, copy the `_openai_generate` pattern and add a branch in `_fetch`.

**`backend/main.py`** — the REST API: project CRUD, table CRUD, join-hints save, schema
preview, memory list, and the `ask` pipeline described in section 3.

**`frontend/src/api.js`** — every backend call in one place; base URL from `VITE_API_URL`.

**`frontend/src/pages/ProjectsPage.jsx`** — lists and creates projects.

**`frontend/src/pages/ProjectPage.jsx`** — two sections. *Data tables*: add/remove
tables, edit join hints, preview the schema. *Ask*: loads this project's memory as a
thread, sends new questions, and renders each answer with a chart and an expandable
SQL/data panel.

**`frontend/src/components/Chart.jsx`** — turns the model's `{chart, x, y, …}` spec
into a Recharts bar / line / scatter / pie chart.

## 9. Limitations and next steps

- **Memory is recency-based.** It feeds back the last few Q&A per project. For large
  histories, upgrade to relevance retrieval (embeddings + `pgvector`) so the most
  *related* past questions surface, not just the most recent.
- **Memory stores findings, not full result sets.** Replayed history shows the question,
  SQL, and finding, but not the old chart. Storing result snapshots is an easy add if
  you want fully reproducible report history.
- **No auth yet.** Add SSO / a login layer before exposing this beyond a trusted network.
- **Schema is fetched live per ask.** Fine for tens of tables; for hundreds, cache the
  schema and retrieve only the relevant tables per question.
