"""Parse Hex-style model YAML and import into project tables + join hints."""
from __future__ import annotations

import re
from typing import Any

import yaml


class ModelYamlError(ValueError):
    pass


def _normalize_bq_table(raw: str) -> str:
    if not raw or not str(raw).strip():
        raise ModelYamlError("Missing base_sql_table")
    s = str(raw).strip()
    s = s.replace("\\\n", "").replace("\\", "")
    s = s.replace("`", "").replace('"', "").replace("'", "")
    s = re.sub(r"\s+", "", s)
    parts = [p for p in s.split(".") if p]
    if len(parts) != 3 or not all(parts):
        raise ModelYamlError(
            f"base_sql_table must be project.dataset.table after parsing; got {raw!r}"
        )
    return ".".join(parts)


def _dimensions_to_metadata(dimensions: Any) -> tuple[dict[str, str], dict[str, str]]:
    descriptions: dict[str, str] = {}
    hints: dict[str, str] = {}
    if not isinstance(dimensions, list):
        return descriptions, hints
    for dim in dimensions:
        if not isinstance(dim, dict):
            continue
        col_id = dim.get("id")
        if not col_id:
            continue
        col_id = str(col_id)
        desc = (dim.get("description") or "").strip()
        if desc:
            descriptions[col_id] = desc
        if dim.get("unique"):
            hints[col_id] = "primary_key"
        if dim.get("primary") or dim.get("primary_field"):
            hints[col_id] = "primary_field"
    return descriptions, hints


def _relation_hint_lines(model_id: str, relations: Any) -> list[str]:
    lines: list[str] = []
    if not isinstance(relations, list):
        return lines
    for rel in relations:
        if not isinstance(rel, dict):
            continue
        rel_id = rel.get("id")
        rel_type = rel.get("type") or "related"
        join_sql = (rel.get("join_sql") or "").strip()
        if rel_id and join_sql:
            lines.append(f"- {model_id} -> {rel_id} ({rel_type}): {join_sql}")
    return lines


def parse_model_document(doc: Any) -> dict[str, Any]:
    """Parse one YAML document into import-ready fields."""
    if not isinstance(doc, dict):
        raise ModelYamlError("Each YAML document must be a mapping (object)")

    model_id = (doc.get("id") or "").strip()
    if not model_id:
        raise ModelYamlError("Model YAML must include id")

    base_sql = doc.get("base_sql_table") or doc.get("base_table") or doc.get("sql_table")
    full_table_id = _normalize_bq_table(str(base_sql or ""))

    column_descriptions, column_hints = _dimensions_to_metadata(doc.get("dimensions"))
    relations = doc.get("relations") or []
    relation_lines = _relation_hint_lines(model_id, relations)

    description = (doc.get("description") or "").strip()
    if not description:
        description = f"Model {model_id}"

    measures = doc.get("measures") or []
    measure_count = len(measures) if isinstance(measures, list) else 0
    measure_lines: list[str] = []
    if isinstance(measures, list):
        for m in measures:
            if not isinstance(m, dict):
                continue
            mid = (m.get("id") or "").strip()
            if not mid:
                continue
            func = (m.get("func") or m.get("func_sql") or "").strip()
            of_col = (m.get("of") or "").strip()
            desc = (m.get("description") or "").strip()
            parts = [f"{mid}: {func}" if func else mid]
            if of_col:
                parts.append(f"of {of_col}")
            if desc:
                parts.append(f"— {desc}")
            measure_lines.append(" ".join(parts))

    aliases: list[str] = []
    raw_aliases = doc.get("aliases") or doc.get("also_known_as")
    if isinstance(raw_aliases, list):
        aliases = [str(a).strip() for a in raw_aliases if str(a).strip()]
    elif isinstance(raw_aliases, str) and raw_aliases.strip():
        aliases = [raw_aliases.strip()]

    return {
        "model_id": model_id,
        "full_table_id": full_table_id,
        "description": description,
        "column_descriptions": column_descriptions,
        "column_hints": column_hints,
        "relation_lines": relation_lines,
        "measure_count": measure_count,
        "measure_lines": measure_lines,
        "aliases": aliases,
    }


def _split_model_documents(text: str) -> list[str]:
    """Split a file containing multiple Hex models (each starts with id:)."""
    chunks = re.split(r"(?=^id:\s)", text.strip(), flags=re.MULTILINE)
    return [c.strip() for c in chunks if c.strip()]


def parse_yaml_documents(text: str) -> list[dict[str, Any]]:
    """Parse one or more YAML documents (--- separated or repeated id: blocks)."""
    text = (text or "").strip()
    if not text:
        raise ModelYamlError("Empty YAML")

    raw_docs: list[Any] = []
    try:
        raw_docs = [d for d in yaml.safe_load_all(text) if d is not None]
    except yaml.YAMLError as e:
        raise ModelYamlError(f"Invalid YAML: {e}") from e

    if len(raw_docs) <= 1:
        chunks = _split_model_documents(text)
        if len(chunks) > 1:
            raw_docs = []
            for chunk in chunks:
                try:
                    doc = yaml.safe_load(chunk)
                except yaml.YAMLError as e:
                    raise ModelYamlError(f"Invalid YAML: {e}") from e
                if doc is not None:
                    raw_docs.append(doc)

    if not raw_docs:
        single = yaml.safe_load(text)
        if single is not None:
            raw_docs = [single]

    if not raw_docs:
        raise ModelYamlError("No model found in YAML")

    return [parse_model_document(d) for d in raw_docs]


def merge_join_hints(existing: str, new_lines: list[str]) -> str:
    """Append relation hints that are not already present."""
    if not new_lines:
        return existing or ""
    block = "\n".join(new_lines)
    if block in (existing or ""):
        return existing or ""
    prefix = (existing or "").rstrip()
    if prefix:
        return f"{prefix}\n\n# Imported relations\n{block}"
    return f"# Imported relations\n{block}"
