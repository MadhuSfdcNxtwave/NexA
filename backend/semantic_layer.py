"""Hex-style semantic layer — measures, dimensions, and model metadata from YAML."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_YAML_PATH = Path(__file__).parent / "workspace_models.yaml"

_TEXT_DIM_PREFIXES = ("what_", "please_share", "do_you_feel")
_SKIP_TEXT_COLS = frozenset(
    {
        "user_id",
        "form_submission_month",
        "form_submission_datetime",
        "submitted_at",
        "first_name",
        "last_name",
        "user_month_key",
        "user_submission_key",
    }
)


@dataclass
class MeasureDef:
    id: str
    func: str
    of_column: str = ""
    func_sql: str = ""
    filters: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class DimensionDef:
    id: str
    description: str = ""
    unique: bool = False
    dim_type: str = ""
    expr_sql: str = ""
    visibility: str = "public"


@dataclass
class SurveyWideMeta:
    score_column: str = ""
    submitted_at_column: str = ""
    month_column: str = ""
    text_columns: list[str] = field(default_factory=list)


@dataclass
class SurveyLongMeta:
    question_col: str = "question_text"
    answer_col: str = "user_answer"
    trigger_col: str = ""
    type_col: str = ""
    topic_columns: list[str] = field(default_factory=list)


@dataclass
class UnionAlignDef:
    """Map a canonical alias to physical column per union member (model id order)."""

    alias: str
    columns: list[str]


@dataclass
class RelationDef:
    target_model_id: str
    rel_type: str = "many_to_one"
    join_sql: str = ""


@dataclass
class TableSemantic:
    model_id: str
    full_table_id: str
    description: str
    model_type: str = "model"
    format: str = "fact"
    grain: str = ""
    measures: list[MeasureDef] = field(default_factory=list)
    dimensions: list[DimensionDef] = field(default_factory=list)
    relations: list[RelationDef] = field(default_factory=list)
    topic_columns: list[str] = field(default_factory=list)
    survey_wide: SurveyWideMeta | None = None
    survey_long: SurveyLongMeta | None = None
    union_members: list[str] = field(default_factory=list)
    union_align: list[UnionAlignDef] = field(default_factory=list)

    @property
    def short_name(self) -> str:
        if self.full_table_id:
            return self.full_table_id.rsplit(".", 1)[-1]
        return self.model_id

    @property
    def is_logical_union(self) -> bool:
        return self.model_type == "logical_model" and bool(self.union_members)

    @property
    def is_wide_survey(self) -> bool:
        return self.format == "wide" or self.survey_wide is not None

    @property
    def is_long_survey(self) -> bool:
        return self.format == "long" or self.survey_long is not None

    def dimension_by_id(self, dim_id: str) -> DimensionDef | None:
        key = (dim_id or "").lower()
        for d in self.dimensions:
            if d.id.lower() == key:
                return d
        return None

    def has_dimension(self, dim_id: str) -> bool:
        return self.dimension_by_id(dim_id) is not None

    def relation_for_dimension(self, dim_id: str) -> RelationDef | None:
        """Relation whose target model exposes dim_id (cross-table breakdown)."""
        key = (dim_id or "").lower()
        catalog = load_semantic_catalog()
        for rel in self.relations:
            target = catalog.get(rel.target_model_id.lower())
            if target and target.has_dimension(key):
                return rel
        return None

    def dim_sql(self, dim_id: str) -> str:
        d = self.dimension_by_id(dim_id)
        if not d:
            return f"`{dim_id}`"
        if d.expr_sql:
            return _resolve_expr_sql(d.expr_sql, self)
        return f"`{d.id}`"

    def inferred_text_columns(self) -> list[str]:
        if self.topic_columns:
            return list(self.topic_columns)
        if self.survey_wide and self.survey_wide.text_columns:
            return list(self.survey_wide.text_columns)
        if self.survey_long and self.survey_long.topic_columns:
            return list(self.survey_long.topic_columns)
        out: list[str] = []
        for d in self.dimensions:
            if d.visibility == "internal":
                continue
            if d.dim_type in ("number", "timestamp", "timestamp_naive", "date"):
                continue
            lid = d.id.lower()
            if lid in _SKIP_TEXT_COLS:
                continue
            if any(lid.startswith(p) for p in _TEXT_DIM_PREFIXES):
                out.append(d.id)
        return out


def _resolve_expr_sql(expr: str, semantic: TableSemantic) -> str:
    """Expand ${column} placeholders to backtick-quoted BigQuery columns."""
    s = (expr or "").strip()
    if not s:
        return s

    def repl(m: re.Match[str]) -> str:
        col = (m.group(1) or "").strip()
        return f"`{col}`"

    return re.sub(r"\$\{([^}]+)\}", repl, s)


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
        raw_filters = m.get("filters") or []
        filters = [str(f).strip() for f in raw_filters if str(f).strip()]
        func_sql = (m.get("func_sql") or "").strip()
        func = (m.get("func") or "").strip().lower()
        if not func and func_sql:
            func = "custom"
        out.append(
            MeasureDef(
                id=mid,
                func=func or "count",
                of_column=(m.get("of") or "").strip(),
                func_sql=func_sql,
                filters=filters,
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
                expr_sql=(d.get("expr_sql") or "").strip(),
                visibility=(d.get("visibility") or "public").strip(),
            )
        )
    return out


def _parse_relations(raw: Any) -> list[RelationDef]:
    out: list[RelationDef] = []
    if not isinstance(raw, list):
        return out
    for rel in raw:
        if not isinstance(rel, dict):
            continue
        target = (rel.get("id") or "").strip()
        if not target:
            continue
        out.append(
            RelationDef(
                target_model_id=target,
                rel_type=(rel.get("type") or "many_to_one").strip(),
                join_sql=(rel.get("join_sql") or "").strip(),
            )
        )
    return out


def _parse_survey_wide(raw: Any) -> SurveyWideMeta | None:
    if not isinstance(raw, dict):
        return None
    text_cols = raw.get("text_columns") or []
    if isinstance(text_cols, str):
        text_cols = [text_cols]
    return SurveyWideMeta(
        score_column=(raw.get("score_column") or "").strip(),
        submitted_at_column=(raw.get("submitted_at_column") or "").strip(),
        month_column=(raw.get("month_column") or "").strip(),
        text_columns=[str(c).strip() for c in text_cols if str(c).strip()],
    )


def _parse_survey_long(raw: Any) -> SurveyLongMeta | None:
    if not isinstance(raw, dict):
        return None
    topic_cols = raw.get("topic_columns") or []
    if isinstance(topic_cols, str):
        topic_cols = [topic_cols]
    return SurveyLongMeta(
        question_col=(raw.get("question_col") or "question_text").strip(),
        answer_col=(raw.get("answer_col") or "user_answer").strip(),
        trigger_col=(raw.get("trigger_col") or "").strip(),
        type_col=(raw.get("type_col") or "").strip(),
        topic_columns=[str(c).strip() for c in topic_cols if str(c).strip()],
    )


def _parse_union_align(raw: Any) -> list[UnionAlignDef]:
    if not isinstance(raw, dict):
        return []
    out: list[UnionAlignDef] = []
    for alias, cols in raw.items():
        if not alias:
            continue
        if isinstance(cols, str):
            col_list = [cols]
        elif isinstance(cols, list):
            col_list = [str(c).strip() for c in cols]
        else:
            continue
        out.append(UnionAlignDef(alias=str(alias).strip(), columns=col_list))
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


def _build_semantic(doc: dict[str, Any]) -> TableSemantic | None:
    model_id = (doc.get("id") or "").strip()
    if not model_id:
        return None
    model_type = (doc.get("type") or "model").strip()
    base = doc.get("base_sql_table") or doc.get("base_table") or ""
    fq = _normalize_fq(str(base)) if base else ""
    if model_type != "logical_model" and (not fq or fq.count(".") != 2):
        return None

    survey_raw = doc.get("survey") or {}
    survey_wide = None
    survey_long = None
    if isinstance(survey_raw, dict):
        survey_wide = _parse_survey_wide(survey_raw.get("wide"))
        survey_long = _parse_survey_long(survey_raw.get("long"))

    topic_cols = doc.get("topic_columns") or []
    if isinstance(topic_cols, str):
        topic_cols = [topic_cols]

    union_members = doc.get("union") or []
    if isinstance(union_members, str):
        union_members = [union_members]

    return TableSemantic(
        model_id=model_id,
        full_table_id=fq,
        description=(doc.get("description") or "").strip(),
        model_type=model_type,
        format=(doc.get("format") or ("wide" if survey_wide else "long" if survey_long else "fact")).strip(),
        grain=(doc.get("grain") or "").strip(),
        measures=_parse_measures(doc.get("measures")),
        dimensions=_parse_dimensions(doc.get("dimensions")),
        relations=_parse_relations(doc.get("relations")),
        topic_columns=[str(c).strip() for c in topic_cols if str(c).strip()],
        survey_wide=survey_wide,
        survey_long=survey_long,
        union_members=[str(m).strip() for m in union_members if str(m).strip()],
        union_align=_parse_union_align(doc.get("union_align")),
    )


@lru_cache(maxsize=1)
def load_semantic_catalog() -> dict[str, TableSemantic]:
    """Map lowercase short_name, model_id, and full_table_id -> TableSemantic."""
    if not _YAML_PATH.is_file():
        return {}
    text = _YAML_PATH.read_text(encoding="utf-8")
    catalog: dict[str, TableSemantic] = {}
    for doc in _load_documents(text):
        sem = _build_semantic(doc)
        if not sem:
            continue
        catalog[sem.model_id.lower()] = sem
        if sem.full_table_id:
            catalog[sem.full_table_id.lower()] = sem
            catalog[sem.short_name.lower()] = sem
    return catalog


def reload_semantic_catalog() -> dict[str, TableSemantic]:
    load_semantic_catalog.cache_clear()
    return load_semantic_catalog()


def semantic_for_table(table: Any) -> TableSemantic | None:
    catalog = load_semantic_catalog()
    fq = (getattr(table, "full_table_id", "") or "").lower()
    short = fq.rsplit(".", 1)[-1] if fq else ""
    return catalog.get(fq) or catalog.get(short)


def semantic_by_model_id(model_id: str) -> TableSemantic | None:
    return load_semantic_catalog().get((model_id or "").lower())


def measures_block(semantic: TableSemantic) -> str:
    if not semantic.measures:
        return ""
    lines = ["# Defined measures (prefer these over inventing SQL aggregates)"]
    for m in semantic.measures:
        parts = [f"- {m.id}: {m.func}"]
        if m.func_sql:
            parts.append(f"expr `{m.func_sql[:80]}`")
        elif m.of_column:
            parts.append(f"of `{m.of_column}`")
        if m.description:
            parts.append(f"— {m.description[:120]}")
        lines.append(" ".join(parts))
    return "\n".join(lines)
