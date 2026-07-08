"""Hex-style semantic layer — measures and dimensions from workspace model YAML."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_YAML_PATH = Path(__file__).parent / "workspace_models.yaml"


@dataclass
class MeasureDef:
    id: str
    func: str
    of_column: str = ""
    description: str = ""


@dataclass
class DimensionDef:
    id: str
    description: str = ""
    unique: bool = False
    dim_type: str = ""


@dataclass
class TableSemantic:
    model_id: str
    full_table_id: str
    description: str
    measures: list[MeasureDef] = field(default_factory=list)
    dimensions: list[DimensionDef] = field(default_factory=list)

    @property
    def short_name(self) -> str:
        return self.full_table_id.rsplit(".", 1)[-1]


def _parse_measures(raw: Any) -> list[MeasureDef]:
    out: list[MeasureDef] = []
    if not isinstance(raw, list):
        return out
    for m in raw:
        if not isinstance(m, dict):
            continue
        mid = (m.get("id") or "").strip()
        if not mid:
            continue
        out.append(
            MeasureDef(
                id=mid,
                func=(m.get("func") or m.get("func_sql") or "count").strip().lower(),
                of_column=(m.get("of") or "").strip(),
                description=(m.get("description") or "").strip(),
            )
        )
    return out


def _parse_dimensions(raw: Any) -> list[DimensionDef]:
    out: list[DimensionDef] = []
    if not isinstance(raw, list):
        return out
    for d in raw:
        if not isinstance(d, dict):
            continue
        did = (d.get("id") or "").strip()
        if not did:
            continue
        out.append(
            DimensionDef(
                id=did,
                description=(d.get("description") or "").strip(),
                unique=bool(d.get("unique")),
                dim_type=(d.get("type") or "").strip(),
            )
        )
    return out


def _normalize_fq(raw: str) -> str:
    s = (raw or "").strip().replace("`", "").replace('"', "").replace("'", "")
    s = re.sub(r"\s+", "", s)
    return s


def _load_documents(text: str) -> list[dict[str, Any]]:
    chunks = re.split(r"(?=^id:\s)", text.strip(), flags=re.MULTILINE)
    docs: list[dict[str, Any]] = []
    for chunk in chunks:
        if not chunk.strip():
            continue
        try:
            doc = yaml.safe_load(chunk)
        except yaml.YAMLError:
            continue
        if isinstance(doc, dict):
            docs.append(doc)
    if docs:
        return docs
    try:
        return [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
    except yaml.YAMLError:
        return []


@lru_cache(maxsize=1)
def load_semantic_catalog() -> dict[str, TableSemantic]:
    """Map lowercase short_name and full_table_id -> TableSemantic."""
    if not _YAML_PATH.is_file():
        return {}
    text = _YAML_PATH.read_text(encoding="utf-8")
    catalog: dict[str, TableSemantic] = {}
    for doc in _load_documents(text):
        model_id = (doc.get("id") or "").strip()
        base = doc.get("base_sql_table") or doc.get("base_table") or ""
        fq = _normalize_fq(str(base))
        if not model_id or not fq or fq.count(".") != 2:
            continue
        sem = TableSemantic(
            model_id=model_id,
            full_table_id=fq,
            description=(doc.get("description") or "").strip(),
            measures=_parse_measures(doc.get("measures")),
            dimensions=_parse_dimensions(doc.get("dimensions")),
        )
        catalog[fq.lower()] = sem
        catalog[sem.short_name.lower()] = sem
    return catalog


def semantic_for_table(table: Any) -> TableSemantic | None:
    catalog = load_semantic_catalog()
    fq = (getattr(table, "full_table_id", "") or "").lower()
    short = fq.rsplit(".", 1)[-1] if fq else ""
    return catalog.get(fq) or catalog.get(short)


def measures_block(semantic: TableSemantic) -> str:
    if not semantic.measures:
        return ""
    lines = ["# Defined measures (prefer these over inventing SQL aggregates)"]
    for m in semantic.measures:
        parts = [f"- {m.id}: {m.func}"]
        if m.of_column:
            parts.append(f"of `{m.of_column}`")
        if m.description:
            parts.append(f"— {m.description[:120]}")
        lines.append(" ".join(parts))
    return "\n".join(lines)
