"""Load business_rules.yaml — metrics and date keyword resolution."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_RULES_PATH = Path(__file__).parent / "business_rules.yaml"


@lru_cache(maxsize=1)
def load_rules() -> dict[str, Any]:
    if not _RULES_PATH.is_file():
        return {"metrics": {}, "date_keywords": {}}
    with _RULES_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def get_metric(term: str) -> dict[str, Any] | None:
    key = (term or "").strip().lower().replace(" ", "_")
    metrics = load_rules().get("metrics") or {}
    if key in metrics:
        return metrics[key]
    for mid, spec in metrics.items():
        if key in mid or mid in key:
            return spec
    return None


def get_metric_filter(term: str) -> list[str]:
    spec = get_metric(term)
    if not spec:
        return []
    return list(spec.get("filters") or [])


def resolve_date_keyword(word: str) -> str | None:
    w = (word or "").strip().lower()
    return (load_rules().get("date_keywords") or {}).get(w)


def date_hints_for_question(question: str) -> list[str]:
    """Return SQL date expressions implied by the question."""
    q = (question or "").lower()
    hints: list[str] = []
    for keyword, expr in (load_rules().get("date_keywords") or {}).items():
        if re.search(rf"\b{re.escape(keyword)}\b", q):
            hints.append(f"{keyword} -> {expr}")
    return hints


def prompt_block_for_question(question: str) -> str:
    """Compact block injected into SQL generation prompts."""
    lines: list[str] = []
    q = (question or "").lower()

    if re.search(r"\bactive\b.{0,30}\b(learning[\s_-]*portal|portal)\b", q):
        spec = get_metric("active_learning_portal_users")
        if spec:
            lines.append(f"# Business rule: {spec.get('description', '')}")
            for f in spec.get("filters") or []:
                lines.append(f"- WHERE {f}")

    if re.search(r"\blp_status\b", q):
        spec = get_metric("lp_status_active_users")
        if spec:
            lines.append(f"# Business rule: {spec.get('description', '')}")
            for f in spec.get("filters") or []:
                lines.append(f"- WHERE {f}")

    if re.search(r"\battend", q) and re.search(r"\blive[\s_-]*class|\bclass\b", q):
        spec = get_metric("live_class_attendance")
        if spec:
            lines.append(f"# Business rule: {spec.get('description', '')}")
            for f in spec.get("filters") or []:
                lines.append(f"- WHERE {f}")

    date_hints = date_hints_for_question(question)
    if date_hints:
        lines.append("# Date keywords detected:")
        lines.extend(f"- {h}" for h in date_hints)

    return "\n".join(lines)
