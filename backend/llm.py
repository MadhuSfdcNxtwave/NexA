"""All LLM calls live here. Two roles:

  FETCH  -> question + schema -> SQL          (config.FETCH_PROVIDER: vertex|openai)
  VIZ    -> result -> chart spec + analysis    (always Vertex AI / Gemini)

Swapping a provider is a change in this file only.
"""
from __future__ import annotations

import json
import re

import config

# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------
_vertex_client = None
_openai_client = None


def _vertex_generate(model: str, prompt: str, system: str | None, temperature: float) -> str:
    global _vertex_client
    from google import genai
    from google.genai import types

    if _vertex_client is None:
        _vertex_client = genai.Client(
            vertexai=True, project=config.GCP_PROJECT, location=config.VERTEX_LOCATION
        )
    cfg = types.GenerateContentConfig(temperature=temperature)
    if system:
        cfg.system_instruction = system
    resp = _vertex_client.models.generate_content(model=model, contents=prompt, config=cfg)
    return (resp.text or "").strip()


def _openai_generate(model: str, prompt: str, system: str | None, temperature: float) -> str:
    global _openai_client
    from openai import OpenAI

    if _openai_client is None:
        _openai_client = OpenAI(api_key=config.OPENAI_API_KEY)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = _openai_client.chat.completions.create(
        model=model, messages=messages, temperature=temperature
    )
    return (resp.choices[0].message.content or "").strip()


def _fetch(prompt: str, system: str | None = None, temperature: float = 0.0) -> str:
    if config.FETCH_PROVIDER == "openai":
        return _openai_generate(config.FETCH_MODEL, prompt, system, temperature)
    return _vertex_generate(config.FETCH_MODEL, prompt, system, temperature)


def _viz(prompt: str, system: str | None = None, temperature: float = 0.0) -> str:
    # VIZ role is always Vertex AI.
    return _vertex_generate(config.VIZ_MODEL, prompt, system, temperature)


# --------------------------------------------------------------------------
# 1. FETCH: question -> SQL
# --------------------------------------------------------------------------
SQL_SYSTEM = (
    "You are a BigQuery SQL expert. Given a schema and a question, return ONE "
    "valid BigQuery Standard SQL SELECT query. Rules: read-only SELECT/WITH "
    "only; fully-qualify tables exactly as shown; prefer explicit columns; add "
    "a sensible LIMIT for sample-type questions. Output ONLY SQL, no prose, no "
    "markdown fences."
)


def question_to_sql(question: str, schema_text: str, memory_text: str = "") -> str:
    prompt = (
        f"# Schema\n{schema_text}\n\n"
        + (f"# Earlier in this project\n{memory_text}\n\n" if memory_text else "")
        + f"# Question\n{question}\n\n# SQL"
    )
    return _fetch(prompt, system=SQL_SYSTEM)


# --------------------------------------------------------------------------
# 2. VIZ: result -> chart spec (strict JSON)
# --------------------------------------------------------------------------
CHART_SYSTEM = (
    "You choose how to visualize a query result. Respond with ONLY a JSON "
    'object: {"chart":"bar|line|scatter|pie|none","x":"<col>","y":"<col>",'
    '"color":"<col or null>","title":"<short title>"}. Use "none" for a single '
    "value or non-chartable data."
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
    "You are a data analyst writing for an internal team. Given a question and "
    "result, write 2-4 sentences of plain-English findings: the direct answer "
    "first, then one notable pattern or caveat. No preamble."
)


def analyze(question: str, columns: list[str], sample_rows: list[dict], row_count: int) -> str:
    prompt = (
        f"Question: {question}\nReturned {row_count} rows. Columns: {columns}\n"
        f"Data (sample): {json.dumps(sample_rows, default=str)[:4000]}\n\nFindings:"
    )
    return _viz(prompt, system=ANALYSIS_SYSTEM, temperature=0.3)
