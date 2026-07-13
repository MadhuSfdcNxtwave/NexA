"""Detect pasted user IDs and build hyphen-safe SQL filters.

Users often paste one or many UUIDs (with or without hyphens) and ask
follow-up questions about those people. Always compare via:
  REPLACE(CAST(user_id AS STRING), '-', '') IN (...)
"""
from __future__ import annotations

import re
from typing import Iterable

# UUID with hyphens (8-4-4-4-12)
_UUID_HYPHEN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
# 32-char hex (common in placements / some fact tables)
_UUID_FLAT = re.compile(r"\b[0-9a-fA-F]{32}\b")

_REF_PRIOR_USERS = re.compile(
    r"\b("
    r"this\s+user|these\s+users?|those\s+users?|that\s+user|"
    r"this\s+id|these\s+ids?|those\s+ids?|that\s+id|"
    r"the\s+(?:same\s+|above\s+|previous\s+)?users?|"
    r"for\s+them|about\s+them|are\s+they|is\s+(?:he|she)|"
    r"their\s+(?:feedback|nps|attendance|placement|data|profile|names?|status)"
    r")\b",
    re.I,
)

_MAX_IDS = 200


def normalize_user_id(value: str) -> str:
    """Strip hyphens/spaces; lowercase hex for stable IN lists."""
    return re.sub(r"[-\s]", "", (value or "").strip()).lower()


def extract_user_ids_from_text(text: str) -> list[str]:
    """Return unique pasted user ids (normalized, no hyphens), order preserved."""
    raw = text or ""
    found: list[str] = []
    seen: set[str] = set()
    for m in _UUID_HYPHEN.finditer(raw):
        nid = normalize_user_id(m.group(0))
        if len(nid) == 32 and nid not in seen:
            seen.add(nid)
            found.append(nid)
    for m in _UUID_FLAT.finditer(raw):
        nid = normalize_user_id(m.group(0))
        if len(nid) == 32 and nid not in seen:
            seen.add(nid)
            found.append(nid)
    return found[:_MAX_IDS]


def strip_user_ids_from_text(text: str) -> str:
    """Remove pasted UUIDs so they are not treated as feature keywords."""
    out = text or ""
    out = _UUID_HYPHEN.sub(" ", out)
    out = _UUID_FLAT.sub(" ", out)
    return re.sub(r"\s+", " ", out).strip()


def extract_user_ids_from_sql(sql: str) -> list[str]:
    """Pull ids from prior SQL IN ('...') / IN ("...") lists near user_id."""
    text = sql or ""
    if not text:
        return []
    chunks: list[str] = []
    for m in re.finditer(
        r"user_id[\s\S]{0,120}IN\s*\(([^)]+)\)",
        text,
        flags=re.I,
    ):
        chunks.append(m.group(1))
    if not chunks:
        return extract_user_ids_from_text(text)
    found: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        for lit in re.findall(r"'([^']+)'|\"([^\"]+)\"", chunk):
            val = lit[0] or lit[1]
            nid = normalize_user_id(val)
            if len(nid) == 32 and nid not in seen:
                seen.add(nid)
                found.append(nid)
    return found[:_MAX_IDS] or extract_user_ids_from_text(text)


def references_prior_users(question: str) -> bool:
    return bool(_REF_PRIOR_USERS.search(question or ""))


def resolve_user_ids(
    question: str,
    *,
    prior_question: str = "",
    prior_sql: str = "",
) -> list[str]:
    """IDs from this question, else from prior question/SQL when referring to them."""
    ids = extract_user_ids_from_text(question)
    if ids:
        return ids
    if not references_prior_users(question):
        return []
    prior_ids = extract_user_ids_from_text(prior_question)
    if not prior_ids:
        prior_ids = extract_user_ids_from_sql(prior_sql)
    return prior_ids


def user_id_in_sql(
    ids: Iterable[str],
    *,
    column_sql: str = "`user_id`",
) -> str:
    """Hyphen-safe IN filter for one or many ids."""
    norm: list[str] = []
    seen: set[str] = set()
    for raw in ids:
        nid = normalize_user_id(str(raw))
        if len(nid) == 32 and nid not in seen:
            seen.add(nid)
            norm.append(nid)
    if not norm:
        return ""
    literals = ", ".join(f"'{i}'" for i in norm)
    return f"REPLACE(CAST({column_sql} AS STRING), '-', '') IN ({literals})"


def prompt_block(ids: list[str]) -> str:
    if not ids:
        return ""
    sample = ", ".join(ids[:5])
    more = f" (+{len(ids) - 5} more)" if len(ids) > 5 else ""
    clause = user_id_in_sql(ids)
    return (
        "# MANDATORY — PASTED USER ID FILTER\n"
        f"# The user pasted {len(ids)} user id(s): {sample}{more}\n"
        "# Scope ALL SQL to only these users. Use this exact predicate "
        "(hyphen formats differ across tables):\n"
        f"#   AND {clause}\n"
        "# Do not drop this filter. Do not invent other user ids."
    )


def ensure_user_id_filter(sql: str, ids: list[str]) -> str:
    """Inject the IN filter when missing from generated SQL (CTE-safe)."""
    text = (sql or "").strip().rstrip(";")
    if not text or not ids:
        return sql
    clause = user_id_in_sql(ids)
    if not clause:
        return sql
    # Already present?
    if any(i in text.lower() for i in ids[:3]) and re.search(
        r"user_id[\s\S]{0,160}IN\s*\(", text, re.I
    ):
        return text

    # Prefer first real table FROM (works inside WITH CTEs).
    m = re.search(
        r"(FROM\s+`[^`]+`(?:\s+(?:AS\s+)?[A-Za-z_][\w]*)?)\s*(WHERE\b)?",
        text,
        re.I,
    )
    if m:
        end = m.end()
        if m.group(2):
            return (text[:end] + f" {clause} AND " + text[end:]).strip()
        return (text[:end] + f"\nWHERE {clause}\n" + text[end:]).strip()

    # Fallback: before ORDER BY / GROUP BY / LIMIT
    m2 = re.search(r"\b(ORDER\s+BY|GROUP\s+BY|LIMIT|QUALIFY)\b", text, re.I)
    head = text[: m2.start()] if m2 else text
    tail = text[m2.start() :] if m2 else ""
    if re.search(r"\bWHERE\b", head, re.I):
        head = head.rstrip() + f"\n  AND {clause}\n"
    else:
        head = head.rstrip() + f"\nWHERE {clause}\n"
    return (head + tail).strip()
