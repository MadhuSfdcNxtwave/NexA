"""Resolve cross-table dimensions via workspace model relations."""
from __future__ import annotations

import re

from semantic_layer import RelationDef, TableSemantic, semantic_by_model_id


def materialize_join_on(
    join_sql: str,
    *,
    base_alias: str,
    base_sem: TableSemantic,
    target_alias: str,
    target_sem: TableSemantic,
) -> str:
    """Expand ${model.col} placeholders in YAML join_sql to aliased SQL."""
    text = (join_sql or "").strip()
    if not text:
        return f"{base_alias}.`user_id` = {target_alias}.`user_id`"

    def repl(m: re.Match[str]) -> str:
        ref = (m.group(1) or "").strip()
        if "." in ref:
            model_part, col = ref.rsplit(".", 1)
            model_part = model_part.strip().lower()
            col = col.strip()
            base_keys = {base_sem.model_id.lower(), base_sem.short_name.lower()}
            target_keys = {target_sem.model_id.lower(), target_sem.short_name.lower()}
            if model_part in base_keys:
                return f"{base_alias}.`{col}`"
            if model_part in target_keys:
                return f"{target_alias}.`{col}`"
            return f"{target_alias}.`{col}`"
        return f"{base_alias}.`{ref}`"

    expanded = re.sub(r"\$\{([^}]+)\}", repl, text)
    # Bare function calls like REPLACE(...) without alias prefix
    if "REPLACE(" in expanded and base_alias not in expanded.split("REPLACE", 1)[0]:
        return expanded
    return expanded


def resolve_dimension_join(
    base_sem: TableSemantic,
    dim_id: str,
) -> tuple[TableSemantic, RelationDef] | None:
    """Return (target_sem, relation) when dim_id lives on a related model."""
    key = (dim_id or "").lower().strip()
    if not key or base_sem.has_dimension(key):
        return None
    rel = base_sem.relation_for_dimension(key)
    if not rel:
        return None
    target = semantic_by_model_id(rel.target_model_id)
    if not target or not target.full_table_id:
        return None
    if not target.has_dimension(key):
        return None
    return target, rel
