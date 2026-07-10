"""All LLM calls live here. Two roles:

  FETCH  -> question + schema -> SQL          (gemini | openai | anthropic SDKs)
  VIZ    -> result -> chart spec + analysis    (same provider options)

Swapping a provider is a config change only (FETCH_PROVIDER / VIZ_PROVIDER).
"""
from __future__ import annotations

import json
import re

import config

# --------------------------------------------------------------------------
# Providers — Google GenAI, OpenAI, Anthropic SDKs
# --------------------------------------------------------------------------
_gemini_client = None
_openai_client = None
_anthropic_client = None

_PROVIDER_ALIASES = {
    "vertex": "gemini",
    "google": "gemini",
    "gpt": "openai",
    "chatgpt": "openai",
    "claude": "anthropic",
}


def _normalize_provider(name: str) -> str:
    p = (name or "gemini").strip().lower()
    return _PROVIDER_ALIASES.get(p, p)


def _resolve_provider(role: str) -> str:
    if role == "fetch":
        raw = config.SQL_PROVIDER or config.FETCH_PROVIDER
    else:
        raw = config.VIZ_PROVIDER
    return _normalize_provider(raw)


def _normalize_model(provider: str, model: str) -> str:
    m = (model or "").strip()
    if provider == "anthropic":
        return m.removeprefix("anthropic/")
    if provider == "openai" and m.startswith("openai/"):
        return m
    if provider == "gemini":
        return m.removeprefix("google/").removeprefix("gemini/")
    return m


def _gemini_generate(model: str, prompt: str, system: str | None, temperature: float) -> str:
    global _gemini_client
    from google import genai
    from google.genai import types

    if _gemini_client is None:
        if config.GCP_PROJECT:
            _gemini_client = genai.Client(
                vertexai=True,
                project=config.GCP_PROJECT,
                location=config.VERTEX_LOCATION,
            )
        elif config.GEMINI_API_KEY:
            _gemini_client = genai.Client(api_key=config.GEMINI_API_KEY)
        else:
            raise ValueError(
                "Google GenAI SDK needs GCP_PROJECT (Vertex) or GEMINI_API_KEY (AI Studio)."
            )
    cfg = types.GenerateContentConfig(temperature=temperature)
    if system:
        cfg.system_instruction = system
    model_id = _normalize_model("gemini", model)
    resp = _gemini_client.models.generate_content(
        model=model_id, contents=prompt, config=cfg
    )
    return (resp.text or "").strip()


def _gemini_embed(text: str, *, task_type: str) -> list[float]:
    global _gemini_client
    from google import genai
    from google.genai import types

    if _gemini_client is None:
        if config.GCP_PROJECT:
            _gemini_client = genai.Client(
                vertexai=True,
                project=config.GCP_PROJECT,
                location=config.VERTEX_LOCATION,
            )
        elif config.GEMINI_API_KEY:
            _gemini_client = genai.Client(api_key=config.GEMINI_API_KEY)
        else:
            raise ValueError(
                "Google GenAI SDK needs GCP_PROJECT (Vertex) or GEMINI_API_KEY (AI Studio)."
            )
    model_id = _normalize_model("gemini", config.EMBEDDING_MODEL)
    resp = _gemini_client.models.embed_content(
        model=model_id,
        contents=text,
        config=types.EmbedContentConfig(task_type=task_type),
    )
    embeddings = getattr(resp, "embeddings", None) or []
    if not embeddings:
        return []
    values = getattr(embeddings[0], "values", None) or []
    return [float(v) for v in values]


def _openai_generate(
    model: str,
    prompt: str,
    system: str | None,
    temperature: float,
    *,
    max_tokens: int | None = None,
) -> str:
    global _openai_client
    from openai import OpenAI

    if _openai_client is None:
        kwargs = {"api_key": config.OPENAI_API_KEY}
        if config.OPENAI_BASE_URL:
            kwargs["base_url"] = config.OPENAI_BASE_URL
        _openai_client = OpenAI(**kwargs)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    cap = max_tokens if max_tokens is not None else config.OPENAI_MAX_TOKENS
    resp = _openai_client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=cap,
    )
    return (resp.choices[0].message.content or "").strip()


def _openai_embed(text: str) -> list[float]:
    global _openai_client
    from openai import OpenAI

    if _openai_client is None:
        kwargs = {"api_key": config.OPENAI_API_KEY}
        if config.OPENAI_BASE_URL:
            kwargs["base_url"] = config.OPENAI_BASE_URL
        _openai_client = OpenAI(**kwargs)
    resp = _openai_client.embeddings.create(
        model=config.EMBEDDING_MODEL,
        input=text,
    )
    values = resp.data[0].embedding if resp.data else []
    return [float(v) for v in values]


def _anthropic_generate(
    model: str,
    prompt: str,
    system: str | None,
    temperature: float,
    *,
    max_tokens: int | None = None,
) -> str:
    global _anthropic_client
    from anthropic import Anthropic

    if _anthropic_client is None:
        if not config.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY is required when FETCH/VIZ provider is anthropic.")
        _anthropic_client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    cap = max_tokens if max_tokens is not None else config.ANTHROPIC_MAX_TOKENS
    model_id = _normalize_model("anthropic", model)
    kwargs: dict = {
        "model": model_id,
        "max_tokens": cap,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }
    if system:
        kwargs["system"] = system
    resp = _anthropic_client.messages.create(**kwargs)
    parts = [b.text for b in resp.content if getattr(b, "text", None)]
    return "".join(parts).strip()


def _generate(
    provider: str,
    model: str,
    prompt: str,
    system: str | None = None,
    temperature: float = 0.0,
    *,
    max_tokens: int | None = None,
) -> str:
    p = _normalize_provider(provider)
    if p == "openai":
        return _openai_generate(
            model, prompt, system, temperature, max_tokens=max_tokens
        )
    if p == "anthropic":
        return _anthropic_generate(
            model, prompt, system, temperature, max_tokens=max_tokens
        )
    if p == "gemini":
        return _gemini_generate(model, prompt, system, temperature)
    raise ValueError(
        f"Unknown LLM provider {provider!r}. Use gemini, openai, or anthropic."
    )


def embed_text(text: str, *, task_type: str = "RETRIEVAL_QUERY") -> list[float]:
    """Create an embedding for semantic table retrieval."""
    provider = _normalize_provider(config.EMBEDDING_PROVIDER)
    compact = re.sub(r"\s+", " ", (text or "").strip())
    if not compact:
        return []
    if provider == "gemini":
        return _gemini_embed(compact, task_type=task_type)
    if provider == "openai":
        return _openai_embed(compact)
    raise ValueError("Embeddings support gemini or openai providers.")


def _fetch(
    prompt: str,
    system: str | None = None,
    temperature: float = 0.0,
    *,
    max_tokens: int | None = None,
    model: str | None = None,
    provider: str | None = None,
) -> str:
    provider = _normalize_provider(provider or _resolve_provider("fetch"))
    model_id = _normalize_model(provider, model or config.FETCH_MODEL)
    cap = max_tokens if max_tokens is not None else config.OPENAI_MAX_TOKENS
    return _generate(
        provider,
        model_id,
        prompt,
        system,
        temperature,
        max_tokens=cap,
    )


def _viz(prompt: str, system: str | None = None, temperature: float = 0.0) -> str:
    provider = _resolve_provider("viz")
    models = [config.VIZ_MODEL]
    for fb in config.VIZ_FALLBACK_MODELS:
        if fb not in models:
            models.append(fb)
    last_err = None
    for model in models:
        try:
            return _generate(
                provider,
                model,
                prompt,
                system,
                temperature,
                max_tokens=config.VIZ_MAX_TOKENS,
            )
        except Exception as e:
            last_err = e
            err = str(e).lower()
            if (
                "402" in err
                or "403" in err
                or "credits" in err
                or "key limit" in err
                or "max_tokens" in err
            ):
                continue
    return ""


# --------------------------------------------------------------------------
# 1. FETCH: question -> SQL
# --------------------------------------------------------------------------
SQL_SYSTEM = (
    "You are a senior analytics engineer writing BigQuery Standard SQL, in the style of a "
    "polished Hex/dbt analyst. Return ONE read-only SELECT or WITH query.\n"
    "\n"
    "## Structure — prefer simple, runnable SQL\n"
    "- Default to ONE SELECT from the approved table — WITH/CTE is allowed when it helps.\n"
    "- For answer distributions: SELECT user_answer, COUNT(*) … GROUP BY user_answer ORDER BY 2 DESC.\n"
    "- For feedback lookup: filter question_text with LIKE or TRIM = exact prompt, never keyword soup.\n"
    "- Format multi-line with clear indentation when helpful.\n"
    "- Be COMPLETE for detail questions: return useful columns with short aliases.\n"
    "- In every SELECT list use ONLY `actual_schema_column AS alias` — never bare alias names "
    "as if they were real columns.\n"
    "- Use the # DATE FILTER block when present — never default to 2025 when the profile "
    "shows 2026 data.\n"
    "- ORDER BY the most useful column (usually date DESC or metric DESC). "
    "Add LIMIT 200 for row-level detail lists.\n"
    "\n"
    "## Correctness rules (violations cause hard failures)\n"
    "- Read the # Knowledge base and AI OVERVIEW sections first — they contain REAL data "
    "coverage (date ranges), join keys, and ready-made topic-search expressions. Trust them.\n"
    "- When the question gives a month/period WITHOUT a year, use the MOST RECENT matching "
    "period inside the table's data coverage (e.g. «June» with coverage Jan–Jun 2026 means "
    "June 2026, never an out-of-coverage year).\n"
    "- Use ONLY column names listed in the schema — never invent columns. "
    "For counts use COUNT(*) or COUNT(column).\n"
    "- FROM must use backtick table paths copied EXACTLY from the schema block "
    "— never alter dataset or table spellings.\n"
    "- When joining, define aliases in FROM/JOIN and qualify every column with its alias. "
    "Use the exact ON conditions from # Join hints. Never list two tables without a JOIN. "
    "Never write bare table_short_name.column unless it is a declared alias.\n"
    "- Balance every parenthesis — re-count before answering.\n"
    "- Follow schema annotations: [PRIMARY FIELD], [PRIMARY DATE], [PRIMARY KEY], "
    "[FEEDBACK FIELD], [DO NOT USE]. No ${placeholders}.\n"
    "\n"
    "## Survey answer distribution (question looks like a survey prompt)\n"
    "- When the user question IS or QUOTES a survey prompt (e.g. «Which of these updates "
    "did you find most valuable?»), treat it as: find rows where question_text matches "
    "that prompt, then GROUP BY user_answer with COUNT(*).\n"
    "- Match question_text with TRIM(question_text) = 'exact text' OR LIKE with a long "
    "substring — NOT broad keyword search on 'update' or 'valuable' alone.\n"
    "- For SINGLE_SELECT / MULTI_SELECT, user_answer holds the chosen option(s); "
    "multi-select may be comma-separated — count with GROUP BY user_answer first.\n"
    "- Never emit DML keywords (UPDATE/DELETE/INSERT) even inside comments.\n"
    "\n"
    "## Topic/keyword questions (e.g. «responses about IRP 2.0», «feedback mentioning placements»)\n"
    "- The topic lives INSIDE free-text columns, never as a category value. Search ALL text "
    "columns at once: REGEXP_CONTAINS(CONCAT(COALESCE(c1,''),' ',COALESCE(c2,''),...), "
    "r'(?i)(variants)').\n"
    "- Build variants: abbreviation with word boundaries + spelled-out form "
    "(e.g. 'IRP 2.0' -> r'(?i)(\\bIRP\\b|internship\\s*readiness)').\n"
    "- NEVER use = or LIKE on one column for topics — it silently returns 0 rows.\n"
    "- Return matched rows with ratings and the text columns (aliased), newest first.\n"
    "\n"
    "## Context reuse\n"
    "- When project context shows prior Thread/Notebook SQL for similar questions, reuse "
    "those patterns and adapt only what differs.\n"
    "- For drill-down follow-ups («give uid for those 6»), keep the prior FROM/WHERE and "
    "change only the SELECT list.\n"
    "\n"
    "## Counting rules\n"
    "- «How many <entities>» = COUNT(DISTINCT <entity id column>) when an id column exists; "
    "pick the id column from the schema, not from guesses.\n"
    "- «How many companies/organisations» must count the company/organisation id column — "
    "never COUNT(*) of rows and never a user/student id.\n"
    "- «<X> wise <Y>» = GROUP BY the X column, aggregate Y.\n"
    "- Never quote column names: use `applied_datetime`, not 'applied_datetime'.\n"
    "- Never use COUNT(DISTINCT 'column') — a quoted string literal always returns 1.\n"
    "- Read the schema column descriptions and AI profile for units, enum values, and "
    "status values (e.g. hired/placed status columns) — use the EXACT values shown there.\n"
    "\n"
    "## Filters — do NOT invent them\n"
    "- If TABLE BUSINESS RULES say not to add WHERE filters (or that every row is already "
    "active), follow those rules and do NOT add pause_status / onboarding filters.\n"
    "- Otherwise: add pause_status IS NULL ONLY when the question explicitly says active, live, "
    "not paused, or current students. If the question just says students/users/count, "
    "do NOT filter by pause_status.\n"
    "- Add date/month filters ONLY when the question mentions a time period.\n"
    "- Do not copy example filters from AI overview unless the question asks for them.\n"
    "\n"
    "Output ONLY SQL — no prose, no markdown fences."
)


def question_to_sql(
    question: str,
    schema_text: str,
    project_context: str = "",
    *,
    prior_error: str = "",
    chain_context: str = "",
    sql_entity_hint: str = "",
    temperature: float = 0.0,
    model: str | None = None,
    provider: str | None = None,
) -> str:
    error_block = (
        f"# Previous SQL failed\nFix the query. Error:\n{prior_error}\n\n"
        if prior_error
        else ""
    )
    context_block = f"{project_context.strip()}\n\n" if project_context.strip() else ""
    chain_block = f"{chain_context.strip()}\n\n" if chain_context.strip() else ""
    entity_hint = ""
    from question_intent import question_asks_growth_cycle_count

    if question_asks_growth_cycle_count(question):
        entity_hint = (
            "# Entity hint\n"
            "Count DISTINCT growth_cycle_title (or growth_cycle_name_enum) — "
            "this is NOT a user/student count.\n\n"
        )
    if sql_entity_hint:
        entity_hint = f"# Entity hint\n{sql_entity_hint.strip()}\n\n"
    prompt = (
        f"# Schema\n{schema_text}\n\n"
        + context_block
        + chain_block
        + entity_hint
        + error_block
        + f"# Question\n{question}\n\n# SQL"
    )
    # Structured CTE queries over wide feedback tables can be long — allow room.
    return _fetch(
        prompt,
        system=SQL_SYSTEM,
        temperature=temperature,
        max_tokens=4096,
        model=model,
        provider=provider,
    )


_REWRITE_SYSTEM = (
    "You fix BigQuery SQL queries. Output ONLY corrected SQL — no prose, no markdown fences."
)


def rewrite_sql(
    question: str,
    sql: str,
    schema_context: str,
    *,
    issues: list[str],
    model: str | None = None,
    provider: str | None = None,
) -> str:
    """Rewrite SQL to fix validation issues (QueryCritic self-correction)."""
    issue_block = "\n".join(f"- {i}" for i in issues)
    prompt = (
        f"The following SQL has these problems:\n{issue_block}\n\n"
        f"Original question: {question}\n\n"
        f"Schema:\n{schema_context[:12000]}\n\n"
        f"Current SQL:\n{sql}\n\n"
        "Rewrite the SQL to fix ALL problems. Output BigQuery SQL only."
    )
    raw = _fetch(
        prompt,
        system=_REWRITE_SYSTEM,
        temperature=0.0,
        max_tokens=4096,
        model=model,
        provider=provider,
    )
    return _strip_sql_fences(raw)


def _strip_sql_fences(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^```(?:sql)?\s*", "", t, flags=re.I)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


TABLE_ROUTER_SYSTEM = (
    "You route an analytics question to the right BigQuery table(s). "
    "You get a catalog: every table's name, TABLE DESCRIPTION, analytics guidance, "
    "and AI OVERVIEW / data profile. "
    "Treat TABLE DESCRIPTION and AI OVERVIEW as the primary knowledge base for routing — "
    "prefer them over table-name word overlap. "
    'Respond with ONLY JSON: {"tables": ["full_table_id", ...], "reason": "one sentence"}. '
    "Rules: pick the FEWEST tables that fully answer the question — usually 1. "
    "Add a second/third table ONLY when a join is genuinely required "
    "(e.g. metrics from one table filtered by attributes stored in another). "
    "Match on what the DESCRIPTION / OVERVIEW say the table is FOR "
    "(NPS, placements, portal page activity, attendance, etc.) — "
    "not a generic feedback/survey table that merely mentions a word. "
    "Prefer tables whose data coverage includes the asked period. "
    "Use exact full_table_id values from the catalog."
)


KB_ROUTER_SYSTEM = (
    "You are an analytics knowledge-base router. Each catalog entry is a KB article "
    "describing one BigQuery table: what it contains, when to use it, columns, and measures. "
    "Given a user question, pick the FEWEST table(s) that fully answer it (usually 1), "
    "the columns needed for SQL, optional WHERE filters, and an optional semantic measure id. "
    'Respond with ONLY JSON: '
    '{"tables":["full_table_id",...],'
    '"columns":{"full_table_id":["column_name",...]},'
    '"filters":["SQL predicate",...],'
    '"measure":"measure_id_or_empty",'
    '"reason":"one sentence"}. '
    "Rules: use exact full_table_id and column names from the articles. "
    "Pick columns that exist in the article — do not invent names. "
    "filters are BigQuery WHERE fragments (no WHERE keyword). "
    "Prefer tables whose purpose matches the question semantics, not word overlap. "
    "For user counts prefer the table that stores the entity grain (one row per user vs per event). "
    "For learning portal active users use master data; follow that table's BUSINESS RULES "
    "(if rules say no WHERE filters, count all rows — do not invent pause_status filters)."
)


DISAMBIGUATE_SYSTEM = (
    "You pick the single best BigQuery table for an analytics question. "
    "You receive compact table cards (purpose, grain, use/avoid hints, key columns, measures) "
    "for a pre-filtered shortlist — usually 8 of 56 tables. "
    'Respond with ONLY JSON: '
    '{"table":"full_table_id","measure":"semantic_measure_id_or_empty",'
    '"reason":"one sentence","confidence":"high"|"medium"|"low"}. '
    "Rules: table must be one of the provided full_table_id values. "
    "Pick by data MEANING and grain (one row per user vs per event), not word overlap. "
    "For live class attendance use attendance table, not cloudwatch or registration. "
    "For learning portal active users use user master data, not question-level tables. "
    "For platform active users use daily engagement, not event telemetry."
)


def disambiguate_tables(question: str, catalog: list[dict]) -> dict:
    """LLM picks 1 table from fused top-K compact cards."""
    lines = []
    for item in catalog:
        card = item.get("card") or item.get("article") or ""
        score = item.get("fused_score", item.get("score", ""))
        header = f"## {item.get('short_name')} (full_table_id: {item['full_table_id']})"
        if score != "":
            header += f" [fusion_score={score}]"
        lines.append(header)
        lines.append(card)
        lines.append("")
    prompt = (
        "# Table shortlist (pick exactly one)\n"
        + "\n".join(lines)
        + f"\n# Question\n{question}\n\nJSON:"
    )
    raw = _fetch(prompt, system=DISAMBIGUATE_SYSTEM, temperature=0.0, max_tokens=512)
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.IGNORECASE | re.MULTILINE).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"table": "", "measure": "", "reason": "", "confidence": "low"}

    return {
        "table": str(data.get("table") or "").strip(),
        "measure": str(data.get("measure") or "").strip(),
        "reason": str(data.get("reason") or "").strip(),
        "confidence": str(data.get("confidence") or "medium").strip().lower(),
    }


def route_with_kb(question: str, catalog: list[dict]) -> dict:
    """Route using full KB articles. catalog items: full_table_id, short_name, article."""
    lines = []
    for item in catalog:
        lines.append(item.get("article") or "")
        lines.append("")
    prompt = (
        "# Table knowledge base\n"
        + "\n---\n".join(lines)
        + f"\n\n# Question\n{question}\n\nJSON:"
    )
    raw = _fetch(prompt, system=KB_ROUTER_SYSTEM, temperature=0.0, max_tokens=768)
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.IGNORECASE | re.MULTILINE).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"tables": [], "columns": {}, "filters": [], "measure": "", "reason": ""}

    tables = [str(t) for t in (data.get("tables") or []) if str(t).strip()]
    columns_raw = data.get("columns") or {}
    columns: dict[str, list[str]] = {}
    if isinstance(columns_raw, dict):
        for k, v in columns_raw.items():
            if isinstance(v, list):
                columns[str(k)] = [str(c) for c in v if str(c).strip()]
    filters = [str(f) for f in (data.get("filters") or []) if str(f).strip()]
    return {
        "tables": tables,
        "columns": columns,
        "filters": filters,
        "measure": str(data.get("measure") or "").strip(),
        "reason": str(data.get("reason") or "").strip(),
    }


def select_tables(question: str, catalog: list[dict]) -> dict:
    """LLM table router. catalog items: {full_table_id, short_name, description, profile}."""
    lines = []
    for t in catalog:
        lines.append(f"## {t['short_name']}  (full_table_id: {t['full_table_id']})")
        if t.get("description"):
            lines.append(f"Description: {t['description'][:800]}")
        if t.get("guidance"):
            lines.append(
                "Analytics guidance / recommended metrics / best practices:\n"
                f"{t['guidance'][:2000]}"
            )
        if t.get("profile"):
            lines.append(f"AI profile:\n{t['profile'][:1200]}")
        lines.append("")
    prompt = (
        "# Table catalog\n" + "\n".join(lines) + f"\n# Question\n{question}\n\nJSON:"
    )
    raw = _fetch(prompt, system=TABLE_ROUTER_SYSTEM, temperature=0.0, max_tokens=512)
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.IGNORECASE | re.MULTILINE).strip()
    try:
        data = json.loads(raw)
        tables = [str(t) for t in (data.get("tables") or []) if str(t).strip()]
        return {"tables": tables, "reason": str(data.get("reason") or "").strip()}
    except json.JSONDecodeError:
        return {"tables": [], "reason": ""}


CHAIN_PLAN_SYSTEM = (
    "You plan how to answer analytics questions with BigQuery SQL. "
    "Respond with ONLY JSON: "
    '{"mode":"single"|"chain","steps":[{"label":"short label","question":"sub-question"}]}. '
    "Use mode=single when ONE SELECT answers the full question (counts, filters, "
    "GROUP BY, NPS breakdown in one query). "
    "Use mode=chain when the question needs SEPARATE queries: period comparisons "
    "(June vs May), multiple independent metrics, or steps that cannot share one "
    "GROUP BY. Each steps[].question must be self-contained and answerable alone. "
    "Max steps as requested. labels should be short (e.g. 'June NPS', 'May NPS')."
)


def plan_sql_chain(question: str, schema_text: str, *, max_steps: int = 3) -> dict:
    prompt = (
        f"Max steps: {max_steps}\n\n"
        f"Schema excerpt:\n{schema_text[:5000]}\n\n"
        f"Question: {question}\n\nJSON:"
    )
    raw = _fetch(prompt, system=CHAIN_PLAN_SYSTEM, temperature=0.0, max_tokens=512)
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.IGNORECASE | re.MULTILINE).strip()
    try:
        data = json.loads(raw)
        return {
            "mode": str(data.get("mode") or "single").lower(),
            "steps": data.get("steps") or [],
        }
    except json.JSONDecodeError:
        return {"mode": "single", "steps": []}


VERIFY_SQL_SYSTEM = (
    "You verify whether BigQuery SQL correctly answers a business question. "
    'Respond with ONLY JSON: {"pass": true|false, "issues": ["…"]}. '
    "Fail (pass=false) when ANY of these apply:\n"
    "- SQL is not read-only SELECT/WITH\n"
    "- Uses columns not in the schema\n"
    "- Counts the wrong entity (e.g. COUNT(*) or COUNT(user_id) when question asks "
    "how many COMPANIES — must be COUNT(DISTINCT organisation_id))\n"
    "- Question asks companies + placements/LPA but SQL lacks user_job_application_status = 'Hired' "
    "and max_ctc threshold filter\n"
    "- Question asks distinct companies but SQL counts rows or users instead\n"
    "- GROUP BY missing when question asks breakdown/per/wise/by dimension\n"
    "- Date filter missing when question specifies a time period\n"
    "- Would return a number that answers a different question than asked\n"
    "- Question asks NPS score but SQL only uses AVG(rating) — must use promoter/detractor formula\n"
    "- NPS above/below threshold needs COUNTIF on score column, not AVG\n"
    "Pass only when the SQL would return data that directly and completely answers the question."
)


def verify_sql(question: str, sql: str, schema_text: str, project_context: str = "") -> dict:
    prompt = (
        f"Question: {question}\n\n"
        f"Project context:\n{project_context or '(none)'}\n\n"
        f"Schema excerpt:\n{schema_text[:6000]}\n\n"
        f"SQL:\n{sql}\n\nJSON:"
    )
    raw = _fetch(prompt, system=VERIFY_SQL_SYSTEM, temperature=0.0, max_tokens=384)
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.IGNORECASE | re.MULTILINE).strip()
    try:
        data = json.loads(raw)
        return {
            "pass": bool(data.get("pass")),
            "issues": [str(i) for i in (data.get("issues") or []) if str(i).strip()],
        }
    except json.JSONDecodeError:
        return {"pass": False, "issues": ["LLM verify returned invalid JSON — retry required"]}


# --------------------------------------------------------------------------
# 2. VIZ: result -> chart spec (strict JSON)
# --------------------------------------------------------------------------
CHART_SYSTEM = (
    "You are a data visualization designer for business owners (non-technical). "
    "Choose the clearest chart for query results. Respond with ONLY JSON: "
    '{"chart":"bar|line|scatter|pie|table|none","x":"<col>","y":"<col>",'
    '"color":"<col or null>","title":"<plain English insight title>","horizontal":true|false}. '
    "Rules: "
    "Titles must be plain English a business owner understands — no column names or SQL. "
    "Single number (1 row, 1 metric) → chart none. "
    "Category breakdown (name + count) → horizontal bar, ranked. "
    "Time series → line. "
    "Parts of a whole (≤8 slices) → pie. "
    "Long text lists → table or ranked bar after aggregation. "
    "Never bar-chart hundreds of raw free-text rows."
)


def result_to_chart_spec(question: str, columns: list[str], sample_rows: list[dict]) -> dict:
    prompt = (
        f"Question: {question}\nColumns: {columns}\n"
        f"Sample rows: {json.dumps(sample_rows, default=str)[:2000]}\n\nJSON:"
    )
    raw = _viz(prompt, system=CHART_SYSTEM)
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.IGNORECASE | re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"chart": "none"}


# --------------------------------------------------------------------------
# 3. VIZ: result -> written analysis
# --------------------------------------------------------------------------
ANALYSIS_SYSTEM = (
    "You are NexA — an intelligent analytics copilot (think Jarvis for data).\n"
    "Rules:\n"
    "- Answer EVERY part of the user's question. Do not skip dimensions they asked about.\n"
    "- If TABLE KNOWLEDGE (description / AI overview) is provided, use it to explain "
    "what was measured and why that table is the right source — in plain English.\n"
    "- Structure your response naturally covering:\n"
    "  (1) Direct answer — headline finding first\n"
    "  (2) What happened — key numbers and patterns from the data\n"
    "  (3) Why it matters — business interpretation\n"
    "  (4) How to read it — what was measured, in plain English\n"
    "  (5) Caveats — limitations or what the data cannot tell us\n"
    "- For 'which X' questions, name the top categories with their values.\n"
    "- For company/placement questions with a per-company table, lead with how many "
    "distinct companies and total students placed, then highlight top companies.\n"
    "- If conversation history is provided, briefly connect to prior context.\n"
    "- For multi-part questions, answer EVERY part with its number.\n"
    "- Never mention SQL, BigQuery, or internal row counts. You may name the business "
    "metric/source in plain language (e.g. 'portal page activity', 'NPS responses').\n"
    "- Do NOT invent trends, comparisons, or numbers not in the data.\n"
    "- Use 6–10 sentences for breakdowns or analytical questions; 5–7 for single metrics.\n"
    "- Conversational, precise, no markdown headers.\n"
)


def analyze(
    question: str,
    columns: list[str],
    sample_rows: list[dict],
    row_count: int,
    *,
    sql: str = "",
    presentation_hints: list[str] | None = None,
    entity_label: str = "",
    conversation_context: str = "",
    glossary_context: str = "",
    query_reason: str = "",
    table_kb_context: str = "",
) -> str:
    hints = "\n".join(f"- {h}" for h in (presentation_hints or []))
    entity_block = f"Topic: {entity_label}\n" if entity_label else ""
    conv_block = (
        f"\nConversation history (prior Thread turns):\n{conversation_context.strip()}\n"
        if (conversation_context or "").strip()
        else ""
    )
    glossary_block = (
        f"\nBusiness context (use for interpretation, do not quote verbatim):\n"
        f"{glossary_context.strip()}\n"
        if (glossary_context or "").strip()
        else ""
    )
    kb_block = (
        f"\nTABLE KNOWLEDGE (description + AI overview — use for 'what was measured'):\n"
        f"{table_kb_context.strip()}\n"
        if (table_kb_context or "").strip()
        else ""
    )
    reason_block = (
        f"\nQuery intent: {query_reason.strip()}\n" if (query_reason or "").strip() else ""
    )
    sql_block = (
        f"(Internal context only — do not mention in answer: query targets {entity_label or 'the requested metric'}.)\n"
        if sql
        else ""
    )
    prompt = (
        f"Question: {question}\n{entity_block}{conv_block}{glossary_block}{kb_block}"
        f"{reason_block}{sql_block}"
        f"Returned {row_count} rows. Columns: {columns}\n"
        f"Data (sample): {json.dumps(sample_rows, default=str)[:5000]}\n"
    )
    if hints:
        prompt += f"\nPresentation rules:\n{hints}\n"
    prompt += "\nFindings:"
    from presentation import heuristic_analyze

    text = _viz(prompt, system=ANALYSIS_SYSTEM, temperature=0.2)
    return text.strip() or heuristic_analyze(question, columns, sample_rows, row_count)


def analyze_chain(
    question: str,
    steps: list[dict],
    *,
    conversation_context: str = "",
) -> str:
    """Synthesize analysis across multiple SQL chain steps."""
    blocks: list[str] = [f"Original question: {question}\n"]
    if (conversation_context or "").strip():
        blocks.append(
            f"Conversation history:\n{conversation_context.strip()}\n"
        )
    for i, step in enumerate(steps, 1):
        rows = step.get("rows") or []
        blocks.append(
            f"Step {i} ({step.get('label', '')}): {len(rows)} rows\n"
            f"Columns: {step.get('columns', [])}\n"
            f"Data: {json.dumps(rows[:25], default=str)}\n"
        )
    prompt = "\n".join(blocks) + "\n\nFindings:"
    from presentation import heuristic_analyze

    text = _viz(prompt, system=ANALYSIS_SYSTEM, temperature=0.3)
    if text.strip():
        return text
    total_rows = sum(len(s.get("rows") or []) for s in steps)
    return heuristic_analyze(question, [], [], total_rows)


def build_presentation(
    question: str,
    columns: list[str],
    rows: list[dict],
    *,
    sample: list[dict] | None = None,
    chain_steps: list[dict] | None = None,
    sql: str = "",
    entity_label: str = "",
    presentation_hints: list[str] | None = None,
    conversation_context: str = "",
    glossary_context: str = "",
    query_reason: str = "",
    table_kb_context: str = "",
) -> tuple[list[dict], dict, str]:
    """Chart spec + dashboard prep + business-friendly analysis."""
    from chart_prepare import prepare_chart
    from presentation import infer_chart_spec, merge_chart_specs

    sample = sample if sample is not None else rows[:50]
    fallback_spec = infer_chart_spec(question, columns, rows)
    if config.PRESENTATION_MODE == "hex":
        chart_spec = merge_chart_specs({}, fallback_spec)
    else:
        llm_spec = result_to_chart_spec(question, columns, sample)
        chart_spec = merge_chart_specs(llm_spec, fallback_spec)
    viz_rows, chart_spec = prepare_chart(rows, columns, chart_spec, question)
    if chain_steps:
        analysis = analyze_chain(
            question, chain_steps, conversation_context=conversation_context
        )
    else:
        analysis = analyze(
            question,
            columns,
            sample,
            len(rows),
            sql=sql,
            presentation_hints=presentation_hints,
            entity_label=entity_label,
            conversation_context=conversation_context,
            glossary_context=glossary_context,
            query_reason=query_reason,
            table_kb_context=table_kb_context,
        )
    return viz_rows, chart_spec, analysis


EXPLAIN_SYSTEM = (
    "You are a friendly analytics assistant in a data workspace. The user is asking "
    "about a PRIOR answer in this Thread — they are NOT requesting a new warehouse query. "
    "Explain clearly and honestly: what the previous answer showed, why numbers or context "
    "may differ (different tables, filters, date ranges, cache vs fresh BigQuery, or a "
    "follow-up that used another dataset). If the assistant shifted context, acknowledge it. "
    "Write 3-5 sentences in plain English. Do not invent new numbers."
)

ASSISTANT_SYSTEM = (
    "You are NexA — an intelligent analytics assistant (like Jarvis for data teams).\n"
    "The user's message is conversational or about how to use the product.\n"
    "Answer warmly and helpfully:\n"
    "- Explain what NexA can do: natural-language questions → SQL → charts and insights\n"
    "- Clarify Thread memory, table pinning (@table), and how to ask better questions\n"
    "- If they greet you, respond naturally and offer to help with data\n"
    "- Use 3–5 sentences. Be confident, clear, and approachable — not robotic.\n"
)

KNOWLEDGE_SYSTEM = (
    "You are NexA — an intelligent analytics assistant (like Jarvis for data teams).\n"
    "The user is asking for a definition, abbreviation, or concept explanation — "
    "NOT requesting a warehouse query.\n"
    "Rules:\n"
    "- Explain clearly what the term/concept means in this analytics context\n"
    "- If glossary context is provided, use it as the authoritative source\n"
    "- Mention how the user might query it in NexA if relevant (e.g. 'ask: average NPS by month')\n"
    "- Cover: what it is, why it matters, and how it's typically measured\n"
    "- Do not invent specific numbers or run pretend queries\n"
    "- 4–8 sentences. Conversational, precise, no markdown headers.\n"
)

SUGGESTIONS_SYSTEM = (
    "Suggest 4 short follow-up questions the user could ask next to explore the data deeper it should be in similar to follow up    . "
    "Use real column/table themes from the schema when provided. "
    'Respond ONLY JSON: {"suggestions":["question 1","question 2","question 3"]}. '
    "Each suggestion must be a complete natural-language question under 12 words."
)

RECOVERY_SYSTEM = (
    "You are a helpful analytics assistant. A query returned no usable data. "
    "In 2-3 short sentences, summarize what the empty result likely means and one "
    "concrete follow-up question. Do NOT explain SQL validation, CTEs, or internal errors. "
    "Do NOT use headings like 'What went wrong'. Be direct and user-friendly."
)

CLARIFICATION_SYSTEM = (
    "You help disambiguate analytics questions before SQL is generated. "
    "Given a question, table catalog with AI profiles, and why the system is unsure, "
    "decide if the user must pick an interpretation. "
    'Respond ONLY JSON: {"needs_clarification":true|false,"prompt":"question to user",'
    '"options":[{"id":"a","label":"short button text","refined_question":"full NL question for SQL"}],'
    '"allow_custom":true}. '
    "Set needs_clarification=true ONLY when the metric definition is genuinely unclear "
    "(e.g. what counts as an active user, which time period, which survey wording). "
    "NEVER ask the user to pick between raw database table names or schema identifiers. "
    "NEVER use labels like 'Use academy_nps_form_responses' — users do not know table names. "
    "If two tables could answer, set needs_clarification=false and pick the best default. "
    "If you must disambiguate data sources, use plain business language only, e.g. "
    "'Ongoing monthly NPS surveys' vs 'Nov–Dec 2025 one-off snapshot'. "
    "Each refined_question must be a complete natural-language question with no table names. "
    "Set needs_clarification=false when the question is already clear enough to query."
)


def build_clarification_options(
    question: str,
    *,
    catalog: list[dict],
    schema_excerpt: str = "",
    reasons: list[str] | None = None,
    join_block: str = "",
    error_detail: str = "",
    alternative_tables: list[str] | None = None,
) -> dict:
    lines = []
    for t in catalog:
        sel = " [SELECTED]" if t.get("selected") else ""
        lines.append(f"## {t['short_name']}{sel}")
        if t.get("description"):
            lines.append(t["description"][:600])
        if t.get("guidance"):
            lines.append(
                "Analytics guidance / recommended metrics / best practices:\n"
                f"{t['guidance'][:1500]}"
            )
        if t.get("profile"):
            lines.append(t["profile"][:900])
        lines.append("")
    prompt = (
        f"# Question\n{question}\n\n"
        f"# Why unsure\n{', '.join(reasons or []) or 'general ambiguity'}\n\n"
    )
    if alternative_tables:
        prompt += (
            "# Note: multiple data sources exist — if disambiguation is needed, "
            "describe them in business terms only (never expose internal table names).\n\n"
        )
    if join_block:
        prompt += f"# Join hints\n{join_block[:2000]}\n\n"
    if error_detail:
        prompt += f"# SQL error\n{error_detail[:1200]}\n\n"
    if schema_excerpt:
        prompt += f"# Schema excerpt\n{schema_excerpt[:3000]}\n\n"
    prompt += "# Table catalog\n" + "\n".join(lines) + "\nJSON:"
    raw = _fetch(prompt, system=CLARIFICATION_SYSTEM, temperature=0.1, max_tokens=1024)
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.I | re.M)
    try:
        data = json.loads(raw)
        opts = []
        for i, o in enumerate(data.get("options") or []):
            if not isinstance(o, dict):
                continue
            oid = str(o.get("id") or chr(97 + i))
            label = str(o.get("label") or "").strip()
            refined = str(o.get("refined_question") or "").strip()
            if label and refined:
                opts.append({"id": oid, "label": label, "refined_question": refined})
        return {
            "needs_clarification": bool(data.get("needs_clarification")) and len(opts) >= 2,
            "prompt": str(data.get("prompt") or "").strip(),
            "options": opts,
            "allow_custom": bool(data.get("allow_custom", True)),
        }
    except json.JSONDecodeError:
        return {"needs_clarification": False, "options": [], "prompt": "", "allow_custom": True}


MEMORY_SUMMARY_SYSTEM = (
    "You maintain a compact project memory for an analytics workspace. "
    "Output 6-12 bullet points covering: tables used, date filters, key metrics/numbers, "
    "columns referenced, and findings users may follow up on. "
    "When updating, merge the new exchange into prior bullets — dedupe, keep most recent numbers. "
    "Use plain English. No preamble. Format: lines starting with '• '. "
    "Max ~600 words total."
)


def update_memory_summary(
    prior_summary: str,
    question: str,
    analysis: str,
    sql: str = "",
) -> str:
    """Merge one Q&A into rolling project memory bullets (cheap FETCH call)."""
    prompt = (
        f"Prior summary:\n{prior_summary or '(empty)'}\n\n"
        f"New exchange:\nQ: {question}\n"
        f"A: {analysis[:1200]}\n"
        f"SQL (snippet): {(sql or '')[:400]}\n\n"
        "Updated summary:"
    )
    return _fetch(prompt, system=MEMORY_SUMMARY_SYSTEM, temperature=0.0, max_tokens=768)


def rebuild_memory_summary(exchanges_block: str) -> str:
    """Build project memory from a batch of past Q&As (backfill)."""
    prompt = f"Thread exchanges:\n{exchanges_block[:8000]}\n\nSummary:"
    return _fetch(prompt, system=MEMORY_SUMMARY_SYSTEM, temperature=0.0, max_tokens=768)


def explain_from_thread(question: str, thread_block: str) -> str:
    prompt = (
        f"# Conversation history\n{thread_block}\n\n"
        f"# User follow-up\n{question}\n\n"
        "Explain:"
    )
    return _viz(prompt, system=EXPLAIN_SYSTEM, temperature=0.2)


def assistant_reply(question: str, context: str = "") -> str:
    ctx = f"Project context:\n{context[:4000]}\n\n" if context.strip() else ""
    prompt = f"{ctx}User: {question}\n\nReply:"
    return _viz(prompt, system=ASSISTANT_SYSTEM, temperature=0.3)


def knowledge_reply(
    question: str,
    glossary_context: str = "",
    project_context: str = "",
) -> str:
    blocks: list[str] = []
    if (project_context or "").strip():
        blocks.append(f"Project context:\n{project_context[:3000]}")
    if (glossary_context or "").strip():
        blocks.append(f"Glossary / business definitions:\n{glossary_context.strip()}")
    blocks.append(f"User question: {question}\n\nExplain:")
    prompt = "\n\n".join(blocks)
    return _viz(prompt, system=KNOWLEDGE_SYSTEM, temperature=0.25)


def sql_failure_reply(
    question: str,
    error_detail: str,
    schema_excerpt: str = "",
    thread_block: str = "",
) -> str:
    prompt = (
        f"Question: {question}\n\n"
        f"Error detail:\n{error_detail[:2000]}\n\n"
        f"Schema excerpt:\n{schema_excerpt[:4000]}\n\n"
        f"Prior conversation:\n{thread_block[:3000] or '(none)'}\n\n"
        "Help the user:"
    )
    text = _viz(prompt, system=RECOVERY_SYSTEM, temperature=0.2)
    if text.strip():
        return text
    if re.search(r"\blive[\s_-]*class", question, re.I) and re.search(r"\battend", question, re.I):
        return (
            "Live class attendance is tracked in the attendance table "
            "(users who joined a session with status JOINED). "
            "Try asking again in a new thread with Fresh BigQuery checked."
        )
    return (
        "Could not complete that query with the current table selection. "
        "Try rephrasing with a specific metric or date, or start a new thread."
    )


def _heuristic_suggest_followups(
    question: str,
    *,
    columns: list[str] | None = None,
) -> list[str]:
    """Hex-style follow-ups without a VIZ LLM call."""
    out: list[str] = []
    if columns:
        for col in columns[:3]:
            label = col.replace("_", " ").strip()
            if re.search(r"month|date|week|period|year", col, re.I):
                out.append(f"Show {label} trend over the last 6 months")
            elif re.search(r"count|total|score|nps|rating", col, re.I):
                out.append(f"What drives changes in {label}?")
            else:
                out.append(f"Break this down by {label}")
    generic = [
        "Show this broken down by month",
        "What are the top 10 by volume?",
        "Compare to the previous period",
    ]
    for g in generic:
        if len(out) >= 4:
            break
        if g not in out:
            out.append(g)
    return out[:4]


def suggest_followups(
    question: str,
    *,
    analysis: str = "",
    columns: list[str] | None = None,
    schema_excerpt: str = "",
    error_context: str = "",
) -> list[str]:
    if config.PRESENTATION_MODE == "hex":
        return _heuristic_suggest_followups(question, columns=columns)
    parts = [f"Last question: {question}"]
    if analysis:
        parts.append(f"Answer summary: {analysis[:1500]}")
    if columns:
        parts.append(f"Result columns: {columns}")
    if schema_excerpt:
        parts.append(f"Schema:\n{schema_excerpt[:2500]}")
    if error_context:
        parts.append(f"Note: {error_context[:800]}")
    parts.append("\nJSON:")
    raw = _viz("\n".join(parts), system=SUGGESTIONS_SYSTEM, temperature=0.4)
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.I | re.M)
    try:
        data = json.loads(raw)
        items = data.get("suggestions") or []
        return [str(s).strip() for s in items if str(s).strip()][:3]
    except json.JSONDecodeError:
        return []


CACHE_DECISION_SYSTEM = (
    "You decide if an analytics question can be answered ONLY from cached query results "
    "already in memory (prior Thread answers or notebook runs). "
    "Respond ONLY JSON: "
    '{"use_cache":true|false,"source_id":"<id>","reason":"..."}. '
    "use_cache=true ONLY when cached rows and columns fully answer the question "
    "(filtering, breakdowns, or counts on the EXACT same dataset already loaded). "
    "use_cache=false when: the question needs a NEW warehouse query; asks for metrics or "
    "dimensions not present in cached columns; mentions other aspects/tables/time periods; "
    "asks for trends, monthly breakdowns, or multiple months over time; "
    "cached data is only a single aggregate (e.g. one AVG) but the question needs a time series; "
    "or the only matching SQL is a placeholder (e.g. SELECT 1). "
    "Calendar phrases like 'previous months' or 'monthly trends' always need use_cache=false "
    "unless cached rows already contain month-by-month data for the same metric. "
    "When uncertain, use_cache=false."
)


def cache_decision(question: str, cache_block: str) -> dict:
    prompt = f"Question: {question}\n\nCached datasets:\n{cache_block}\n\nJSON:"
    raw = _fetch(prompt, system=CACHE_DECISION_SYSTEM, temperature=0.0, max_tokens=256)
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.I | re.M)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"use_cache": False}
