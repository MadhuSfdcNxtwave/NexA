"""Unified RAG retrieval — glossary term match + vector/keyword fusion."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import config
import knowledge_base as kb
from metrics_registry import GlossaryTerm, glossary_snippets_for_model, match_glossary_terms
from term_resolver import ResolvedQuery, resolve


@dataclass
class RetrievalHit:
    full_table_id: str
    short_name: str
    score: float
    source: str  # glossary | vector | keyword | fusion
    reason: str = ""


@dataclass
class RetrievalResult:
    question: str
    resolved: ResolvedQuery | None
    glossary_hits: list[tuple[GlossaryTerm, str]] = field(default_factory=list)
    table_hits: list[RetrievalHit] = field(default_factory=list)
    selected_table_ids: list[str] = field(default_factory=list)
    kb_measure: str = ""
    kb_filters: list[str] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)

    def to_trace_dict(self) -> dict:
        return {
            "glossary": [t.id for t, _ in self.glossary_hits],
            "selected": [h.rsplit(".", 1)[-1] for h in self.selected_table_ids[:4]],
            "top_hits": [
                {"table": h.short_name, "score": round(h.score, 3), "source": h.source}
                for h in self.table_hits[:5]
            ],
            "resolved_model": self.resolved.model_id if self.resolved else "",
            "trace": self.trace[:6],
        }


def _table_for_model(model_id: str, included: list[Any]) -> str | None:
    from semantic_layer import semantic_by_model_id

    sem = semantic_by_model_id(model_id)
    if not sem:
        return None
    if sem.full_table_id:
        fq = sem.full_table_id.lower()
        for t in included:
            if t.full_table_id.lower() == fq:
                return t.full_table_id
    short = model_id.lower()
    for t in included:
        if t.full_table_id.rsplit(".", 1)[-1].lower() == short:
            return t.full_table_id
    return None


def _boost_from_glossary(
    matches: list[Any],
    glossary_hits: list[tuple[GlossaryTerm, str]],
    included: list[Any],
) -> list[RetrievalHit]:
    hits: list[RetrievalHit] = []
    boosted_ids: set[str] = set()

    for term, syn in glossary_hits:
        fq = _table_for_model(term.model_id, included)
        if not fq:
            continue
        boosted_ids.add(fq)
        for m in matches:
            if m.full_table_id == fq:
                m.score += 800
                hits.append(
                    RetrievalHit(
                        full_table_id=fq,
                        short_name=m.short_name,
                        score=float(m.score),
                        source="glossary",
                        reason=f"{term.id} via '{syn}'",
                    )
                )
                break
        else:
            short = fq.rsplit(".", 1)[-1]
            hits.append(
                RetrievalHit(
                    full_table_id=fq,
                    short_name=short,
                    score=900.0,
                    source="glossary",
                    reason=f"{term.id} via '{syn}'",
                )
            )

    return hits


def retrieve(
    question: str,
    project_tables: list[Any],
    *,
    keyword_matches: list[Any] | None = None,
    knowledges: list[kb.TableKnowledge] | None = None,
    resolved: ResolvedQuery | None = None,
) -> RetrievalResult:
    """
    RAG retrieval pipeline:
    1. Glossary term match
    2. Resolve question → model/measure
    3. Boost table scores from glossary + resolved model
    4. Fuse with vector/keyword scores when enabled
    """
    included = [t for t in project_tables if getattr(t, "included_for_ai", True)]
    glossary_hits = match_glossary_terms(question)

    if resolved is None:
        resolved = resolve(question, included, catalog_tables=included)

    if keyword_matches is None:
        from ask_plan import TableMatch

        keywords = kb.extract_keywords(question)
        keyword_matches = []
        for t in included:
            knowledge = kb.load_table_knowledge(t)
            keyword_matches.append(
                TableMatch(
                    full_table_id=knowledge.full_table_id,
                    short_name=knowledge.short_name,
                    score=kb.score_table_knowledge(question, knowledge, keywords),
                )
            )

    matches = list(keyword_matches)
    trace: list[str] = []

    glossary_table_hits = _boost_from_glossary(matches, glossary_hits, included)
    if glossary_table_hits:
        trace.append(f"glossary_boost:{len(glossary_table_hits)}")

    if resolved and resolved.model_id and resolved.model_id != "compound":
        fq = _table_for_model(resolved.model_id, included)
        if fq:
            for m in matches:
                if m.full_table_id == fq:
                    m.score += 600
                    trace.append(f"resolved_model:{resolved.model_id}")
                    break

    # Vector fusion when enabled
    table_hits: list[RetrievalHit] = list(glossary_table_hits)
    if config.ROUTING_FUSION_ENABLED and config.EMBEDDING_RETRIEVAL_ENABLED:
        try:
            import vector_index

            if knowledges is None:
                knowledges = [kb.load_table_knowledge(t) for t in included]
            fused, _ = vector_index.rank_all_tables(question, included, knowledges, matches)
            for fm in fused[: config.ROUTING_TOP_K]:
                table_hits.append(
                    RetrievalHit(
                        full_table_id=fm.full_table_id,
                        short_name=fm.short_name,
                        score=fm.fused_score,
                        source="fusion",
                        reason="vector+keyword fusion",
                    )
                )
            trace.append(f"fusion:{len(fused)}")
        except Exception:
            pass

    matches.sort(key=lambda m: (-m.score, m.short_name.lower()))

    selected: list[str] = []
    if resolved and resolved.model_id == "nps_all_form_responses":
        for mid in ("academy_nps_form_responses", "nps_form_responses_nov_and_dec_2025"):
            fq = _table_for_model(mid, included)
            if fq and fq not in selected:
                selected.append(fq)
    elif resolved and resolved.model_id and resolved.model_id != "compound":
        fq = _table_for_model(resolved.model_id, included)
        if fq:
            selected = [fq]

    if not selected and matches:
        selected = [m.full_table_id for m in matches[:3]]

    kb_measure = resolved.measure_id if resolved and resolved.measure_id else ""
    kb_filters = list(resolved.filters) if resolved else []

    return RetrievalResult(
        question=question,
        resolved=resolved,
        glossary_hits=glossary_hits,
        table_hits=table_hits,
        selected_table_ids=selected,
        kb_measure=kb_measure,
        kb_filters=kb_filters,
        trace=trace,
    )


def enrich_table_document(base_doc: str, model_id: str) -> str:
    """Append glossary snippets to table embedding card."""
    snippets = glossary_snippets_for_model(model_id)
    if not snippets:
        return base_doc
    block = "\nGlossary terms:\n" + "\n".join(f"- {s}" for s in snippets[:6])
    return (base_doc + block)[:3500]
