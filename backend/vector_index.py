"""Pre-indexed semantic table retrieval for Hex-style Ask routing."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any

import config
import knowledge_base as kb
import llm


@dataclass
class VectorMatch:
    full_table_id: str
    short_name: str
    score: float


@dataclass
class FusedMatch:
    full_table_id: str
    short_name: str
    fused_score: float
    vector_score: float
    keyword_score: int
    keyword_norm: float


def _parse_embedding(raw: str | None) -> list[float]:
    try:
        data = json.loads(raw or "[]")
        if not isinstance(data, list):
            return []
        return [float(v) for v in data]
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if not mag_a or not mag_b:
        return 0.0
    return dot / (mag_a * mag_b)


def table_document(knowledge: kb.TableKnowledge) -> str:
    """Stable compact card embedded once per table."""
    try:
        import kb_articles as kba

        return kba.build_table_card(knowledge)[:3500]
    except Exception:
        pass
    lines = [
        f"Table: {knowledge.short_name}",
        f"Full table id: {knowledge.full_table_id}",
    ]
    if knowledge.endorsed:
        lines.append("Endorsed: preferred table for analytics questions")
    if knowledge.table_description:
        lines.append(f"Description: {knowledge.table_description[:400]}")
    if knowledge.ai_overview:
        lines.append(f"Profile: {knowledge.ai_overview[:400]}")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()[:3500]


def content_hash(knowledge: kb.TableKnowledge) -> str:
    payload = {
        "doc_type": "table_card",
        "full_table_id": knowledge.full_table_id,
        "description": knowledge.table_description,
        "columns": knowledge.column_descriptions,
        "types": knowledge.column_types,
        "ai_overview": knowledge.ai_overview,
        "endorsed": knowledge.endorsed,
        "included_for_ai": knowledge.included_for_ai,
        "model": config.EMBEDDING_MODEL,
        "provider": config.EMBEDDING_PROVIDER,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def embedding_is_current(table: Any, knowledge: kb.TableKnowledge | None = None) -> bool:
    if not config.EMBEDDING_RETRIEVAL_ENABLED:
        return False
    raw = getattr(table, "embedding_json", "") or ""
    if not _parse_embedding(raw):
        return False
    if (getattr(table, "embedding_model", "") or "") != config.EMBEDDING_MODEL:
        return False
    knowledge = knowledge or kb.load_table_knowledge(table)
    return (getattr(table, "embedding_hash", "") or "") == content_hash(knowledge)


def ensure_table_embedding(db: Any, table: Any, *, force: bool = False) -> bool:
    """Create or refresh one table vector. Returns True when table was updated."""
    if not config.EMBEDDING_RETRIEVAL_ENABLED:
        return False
    knowledge = kb.load_table_knowledge(table)
    doc_hash = content_hash(knowledge)
    if (
        not force
        and (getattr(table, "embedding_hash", "") or "") == doc_hash
        and (getattr(table, "embedding_model", "") or "") == config.EMBEDDING_MODEL
        and _parse_embedding(getattr(table, "embedding_json", "") or "")
    ):
        return False

    vector = llm.embed_text(
        table_document(knowledge),
        task_type="RETRIEVAL_DOCUMENT",
    )
    if not vector:
        return False
    table.embedding_model = config.EMBEDDING_MODEL
    table.embedding_hash = doc_hash
    table.embedding_json = json.dumps(vector, separators=(",", ":"))
    table.embedding_updated_at = dt.datetime.now(dt.timezone.utc)
    db.add(table)
    return True


def ensure_workspace_embeddings(db: Any, tables: list[Any] | None = None, *, force: bool = False) -> dict[str, int]:
    """Backfill all workspace table embeddings."""
    from sqlalchemy import select
    from db import WorkspaceTable

    rows = tables
    if rows is None:
        rows = list(db.scalars(select(WorkspaceTable).order_by(WorkspaceTable.full_table_id)).all())

    updated = 0
    skipped = 0
    failed = 0
    for table in rows:
        if not getattr(table, "included_for_ai", True):
            skipped += 1
            continue
        try:
            if ensure_table_embedding(db, table, force=force):
                updated += 1
            else:
                skipped += 1
        except Exception as e:
            failed += 1
            print(f"[vector-index] failed {getattr(table, 'full_table_id', '?')}: {e}")
    return {"updated": updated, "skipped": skipped, "failed": failed, "total": len(rows)}


def _vector_scores_all(
    question: str,
    tables: list[Any],
    knowledges: list[kb.TableKnowledge],
) -> dict[str, float]:
    """Cosine similarity for every indexed table (no min-score cutoff)."""
    if not config.EMBEDDING_RETRIEVAL_ENABLED:
        return {}
    by_id = {k.full_table_id: k for k in knowledges}
    indexed: list[tuple[kb.TableKnowledge, list[float]]] = []
    for table in tables:
        knowledge = by_id.get(table.full_table_id)
        if not knowledge or not embedding_is_current(table, knowledge):
            continue
        vector = _parse_embedding(getattr(table, "embedding_json", "") or "")
        if vector:
            indexed.append((knowledge, vector))
    if not indexed:
        return {}

    try:
        query_vector = llm.embed_text(question, task_type="RETRIEVAL_QUERY")
    except Exception as e:
        print(f"[vector-index] query embedding failed: {e}")
        return {}
    if not query_vector:
        return {}

    scores: dict[str, float] = {}
    for knowledge, table_vector in indexed:
        scores[knowledge.full_table_id] = _cosine(query_vector, table_vector)
    return scores


def rank_all_tables(
    question: str,
    tables: list[Any],
    knowledges: list[kb.TableKnowledge],
    keyword_scores: dict[str, int],
    *,
    top_k: int | None = None,
) -> list[FusedMatch]:
    """Fuse vector + keyword scores over all tables; return top-K candidates."""
    top_k = top_k or config.ROUTING_TOP_K
    kw_norm = kb.normalize_keyword_scores(keyword_scores)
    vector_scores = _vector_scores_all(question, tables, knowledges)

    has_vector = bool(vector_scores)
    vw = config.ROUTING_VECTOR_WEIGHT if has_vector else 0.0
    kw_w = config.ROUTING_KEYWORD_WEIGHT if has_vector else 1.0
    # Renormalize when vector missing
    total_w = vw + kw_w
    if total_w > 0:
        vw /= total_w
        kw_w /= total_w

    by_id = {k.full_table_id: k for k in knowledges}
    fused: list[FusedMatch] = []
    for fq, kw_score in keyword_scores.items():
        knowledge = by_id.get(fq)
        if not knowledge:
            continue
        vec = vector_scores.get(fq, 0.0)
        kn = kw_norm.get(fq, 0.0)
        score = vw * vec + kw_w * kn
        fused.append(
            FusedMatch(
                full_table_id=fq,
                short_name=knowledge.short_name,
                fused_score=score,
                vector_score=vec,
                keyword_score=kw_score,
                keyword_norm=kn,
            )
        )

    fused.sort(key=lambda m: (-m.fused_score, -m.keyword_score, m.short_name.lower()))
    top = fused[: max(1, top_k)]

    try:
        from debug_session import ask_trace

        ask_trace(
            "fusion_rank",
            question=question[:200],
            top=[
                {
                    "table": m.short_name,
                    "fused": round(m.fused_score, 4),
                    "vector": round(m.vector_score, 4),
                    "keyword": m.keyword_score,
                }
                for m in top
            ],
            indexed_vectors=len(vector_scores),
            catalog_size=len(keyword_scores),
        )
    except Exception:
        pass

    return top


def route_tables(
    question: str,
    tables: list[Any],
    knowledges: list[kb.TableKnowledge],
) -> tuple[list[VectorMatch], str]:
    """Rank pre-indexed tables for a question using cosine similarity."""
    if not config.EMBEDDING_RETRIEVAL_ENABLED:
        return [], ""
    by_id = {k.full_table_id: k for k in knowledges}
    indexed: list[tuple[Any, kb.TableKnowledge, list[float]]] = []
    for table in tables:
        knowledge = by_id.get(table.full_table_id)
        if not knowledge or not embedding_is_current(table, knowledge):
            continue
        vector = _parse_embedding(getattr(table, "embedding_json", "") or "")
        if vector:
            indexed.append((table, knowledge, vector))
    if not indexed:
        return [], ""

    try:
        query_vector = llm.embed_text(question, task_type="RETRIEVAL_QUERY")
    except Exception as e:
        print(f"[vector-index] query embedding failed: {e}")
        return [], ""
    if not query_vector:
        return [], ""

    matches: list[VectorMatch] = []
    for _table, knowledge, table_vector in indexed:
        score = _cosine(query_vector, table_vector)
        if score >= config.EMBEDDING_MIN_SCORE:
            matches.append(
                VectorMatch(
                    full_table_id=knowledge.full_table_id,
                    short_name=knowledge.short_name,
                    score=score,
                )
            )
    matches.sort(key=lambda m: (-m.score, m.short_name.lower()))
    top = matches[: max(1, config.EMBEDDING_TOP_K)]
    if not top:
        return [], ""
    names = ", ".join(f"`{m.short_name}` ({m.score:.2f})" for m in top)
    return top, f"Vector semantic search over pre-indexed metadata: {names}"
