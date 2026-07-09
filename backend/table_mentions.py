"""Hex-style @table mentions and user-pinned tables for Ask routing."""
from __future__ import annotations

import re
from typing import Any

_MENTION = re.compile(r"@([a-zA-Z0-9][a-zA-Z0-9_\-.]{2,140})")


def _catalog(included: list[Any]) -> dict[str, str]:
    catalog: dict[str, str] = {}
    for t in included:
        if not getattr(t, "included_for_ai", True):
            continue
        short = t.full_table_id.rsplit(".", 1)[-1]
        catalog[short.lower()] = t.full_table_id
    return catalog


def _resolve_short(short: str, catalog: dict[str, str]) -> str | None:
    """Match token to catalog short name (same rules as join_graph.resolve_model_id)."""
    mid = short.lower().strip()
    if mid in catalog:
        return mid
    keys = list(catalog.keys())
    for k in keys:
        if k == mid:
            return k
    for k in keys:
        if k.endswith(mid) or mid.endswith(k):
            return k
        if mid.replace("z_", "") == k.replace("z_", ""):
            return k
        if mid in k or k in mid:
            return k
    return None


def resolve_table_token(token: str, included: list[Any]) -> str | None:
    """Map @token or short name → full_table_id (must be in included set)."""
    catalog = _catalog(included)
    if not catalog or not token:
        return None
    raw = token.strip().strip("`").lower()
    if not raw:
        return None
    short = raw.rsplit(".", 1)[-1]
    resolved = _resolve_short(short, catalog)
    if resolved and resolved in catalog:
        return catalog[resolved]
    for fq in {t.full_table_id for t in included if getattr(t, "included_for_ai", True)}:
        if fq.lower() == raw or fq.rsplit(".", 1)[-1].lower() == short:
            return fq
    return None


def parse_mention_tokens(question: str) -> list[str]:
    return [m.group(1).strip() for m in _MENTION.finditer(question or "")]


def strip_mentions(question: str) -> str:
    text = _MENTION.sub(" ", question or "")
    return re.sub(r"\s+", " ", text).strip()


def apply_table_pins(
    question: str,
    included: list[Any],
    *,
    pinned_table_ids: list[str] | None = None,
) -> tuple[str, list[str], str]:
    """
    Resolve explicit API pins + @mentions in the question.
    Returns (cleaned_question, full_table_ids, routing_reason).
    """
    allowed = {
        t.full_table_id
        for t in included
        if getattr(t, "included_for_ai", True)
    }
    pins: list[str] = []
    for fq in pinned_table_ids or []:
        if fq in allowed and fq not in pins:
            pins.append(fq)

    mention_labels: list[str] = []
    for token in parse_mention_tokens(question):
        fq = resolve_table_token(token, included)
        if fq and fq not in pins:
            pins.append(fq)
            mention_labels.append(token)

    clean = strip_mentions(question) if mention_labels else (question or "").strip()

    if not pins:
        return clean, [], ""

    shorts = [fq.rsplit(".", 1)[-1] for fq in pins]
    if mention_labels:
        reason = f"User @mention pin: {', '.join(f'`{s}`' for s in shorts)}"
    else:
        reason = f"User pinned table(s): {', '.join(f'`{s}`' for s in shorts)}"
    return clean, pins, reason
