"""Unified RAG context builder — glossary + resolved query + retrieval hits."""
from __future__ import annotations

from typing import Any

from metrics_registry import load_glossary
from retrieval_service import RetrievalResult
from semantic_layer import semantic_by_model_id
from term_resolver import ResolvedQuery


def _format_glossary_block(resolved: ResolvedQuery | None) -> str:
    if not resolved or not resolved.glossary_terms:
        return ""
    glossary = load_glossary()
    lines = ["# Business glossary (matched terms)"]
    for tid in resolved.glossary_terms:
        term = glossary.get(tid)
        if not term:
            continue
        lines.append(f"- **{term.label}** (`{term.id}`)")
        if term.description:
            lines.append(f"  Definition: {term.description[:300]}")
        if term.measure_id:
            lines.append(f"  Measure: `{term.measure_id}` on `{term.model_id}`")
        if term.filters:
            lines.append(f"  Filters: {', '.join(term.filters)}")
    return "\n".join(lines)


def _format_resolved_block(resolved: ResolvedQuery) -> str:
    lines = [
        "# Resolved query",
        f"intent={resolved.intent}, model={resolved.model_id}, confidence={resolved.confidence:.2f}",
    ]
    if resolved.measure_id:
        lines.append(f"measure={resolved.measure_id}")
    if resolved.topic:
        lines.append(f'topic="{resolved.topic}"')
    if resolved.dimensions:
        lines.append(f"dimensions={', '.join(resolved.dimensions)}")
    if resolved.filters:
        lines.append("filters:")
        for f in resolved.filters[:8]:
            lines.append(f"  - {f}")
    if resolved.viz_hint:
        lines.append(f"viz_hint={resolved.viz_hint}")
    if resolved.reason:
        lines.append(f"reason={resolved.reason[:240]}")
    if resolved.trace:
        lines.append(f"sources={', '.join(resolved.trace[:5])}")
    return "\n".join(lines)


def _format_model_block(model_id: str) -> str:
    sem = semantic_by_model_id(model_id)
    if not sem:
        return ""
    lines = [f"# Model: {sem.model_id}"]
    if sem.description:
        lines.append(sem.description[:600].strip())
    if sem.measures:
        meas = []
        for m in sem.measures[:8]:
            part = f"{m.id} ({m.func}"
            if m.of_column:
                part += f" {m.of_column}"
            part += ")"
            if m.filters:
                part += f" filters={','.join(m.filters)}"
            meas.append(part)
        lines.append("Measures: " + "; ".join(meas))
    if sem.is_logical_union and sem.union_members:
        lines.append(f"Union: {', '.join(sem.union_members)}")
    return "\n".join(lines)


def build_rag_context(
    retrieval: RetrievalResult | None = None,
    *,
    resolved: ResolvedQuery | None = None,
    selected_tables: list[Any] | None = None,
    thread_memory: str = "",
    date_hints: str = "",
    join_block: str = "",
) -> str:
    """One context block for SQL generation and LLM fallback."""
    if retrieval:
        resolved = retrieval.resolved or resolved

    parts: list[str] = []

    glossary_block = _format_glossary_block(resolved)
    if glossary_block:
        parts.append(glossary_block)

    if resolved:
        parts.append(_format_resolved_block(resolved))
        model_block = _format_model_block(resolved.model_id)
        if model_block:
            parts.append(model_block)

    if retrieval and retrieval.table_hits:
        hit_lines = ["# Retrieval hits"]
        for h in retrieval.table_hits[:5]:
            hit_lines.append(f"- `{h.short_name}` score={h.score:.0f} ({h.source}: {h.reason})")
        parts.append("\n".join(hit_lines))

    if selected_tables:
        shorts = [t.full_table_id.rsplit(".", 1)[-1] for t in selected_tables[:4]]
        parts.append("Selected tables: " + ", ".join(f"`{s}`" for s in shorts))

    if join_block:
        parts.append("# Join hints\n" + join_block.strip()[:1000])
    if date_hints:
        parts.append("# Date hints\n" + date_hints.strip()[:600])
    if thread_memory:
        parts.append("# Thread memory\n" + thread_memory.strip()[:1000])

    return "\n\n".join(p for p in parts if p).strip()
