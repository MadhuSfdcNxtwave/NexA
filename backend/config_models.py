"""Model routing map for agent pipeline roles."""
from __future__ import annotations

import os

import config

MODEL_MAP: dict[str, str] = {
    "simple": os.environ.get("SQL_MODEL_SIMPLE", "claude-haiku-4-5"),
    "complex": os.environ.get("SQL_MODEL_COMPLEX", "claude-sonnet-4-6"),
    "critic": os.environ.get("SQL_MODEL_CRITIC", "gemini-2.5-flash"),
    "schema": os.environ.get("SQL_MODEL_SCHEMA", "gemini-2.5-flash"),
    "viz": os.environ.get("SQL_MODEL_VIZ", "claude-haiku-4-5"),
    "batch": os.environ.get("SQL_MODEL_BATCH", "gemini-2.5-pro"),
}


def model_for_role(role: str) -> str:
    return MODEL_MAP.get(role, config.FETCH_MODEL)


def provider_for_model(model: str) -> str:
    m = (model or "").lower()
    if "claude" in m or "anthropic" in m:
        return "anthropic"
    if "gpt" in m or "openai" in m:
        return "openai"
    return "gemini"
