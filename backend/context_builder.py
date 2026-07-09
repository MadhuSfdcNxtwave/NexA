"""Compact model-facing context from QueryPlan + semantic layer (not full schema dump)."""
from __future__ import annotations

from typing import Any

from query_planner import QueryPlan
from semantic_layer import TableSemantic, semantic_by_model_id


def _format_measure(sem: TableSemantic, measure_id: str | None) -> str:
    if not measure_id:
        return ""
    for m in sem.measures:
        if m.id == measure_id:
            label = f"{m.id}: {m.func.upper()}"
            if m.of_column:
                label += f"({m.of_column})"
            if m.description:
                label += f" — {m.description[:120]}"
            if m.filters:
                label += f" [filters: {', '.join(m.filters)}]"
            return label
    return measure_id


def _format_filter_lines(sem: TableSemantic, filters: list[str]) -> list[str]:
    dim_by_id = {d.id: d for d in sem.dimensions}
    lines: list[str] = []
    for fid in filters:
        if any(tok in fid for tok in ("`", "=", " IS ", " is ")):
            lines.append(fid)
            continue
        dim = dim_by_id.get(fid)
        if dim and dim.expr_sql:
            lines.append(f"{fid}: {dim.expr_sql}")
        elif dim:
            lines.append(f"{fid}: column `{dim.id}`")
        else:
            lines.append(fid)
    return lines


def build_model_context(
    plan: QueryPlan,
    *,
    selected_tables: list[Any] | None = None,
    thread_memory: str = "",
    date_hints: str = "",
    join_block: str = "",
) -> str:
    """Build a small, precise context block for SQL generation and LLM fallback."""
    lines = [
        "# QueryPlan",
        f"intent={plan.intent}, model={plan.model_id}, viz_hint={plan.viz_hint}",
    ]
    if plan.topic:
        lines.append(f'topic="{plan.topic}"')
    if plan.measure_id:
        lines.append(f"measure={plan.measure_id}")
    if plan.dimensions:
        lines.append(f"dimensions={', '.join(plan.dimensions[:8])}")
    if plan.entity and plan.entity != "general":
        lines.append(f"entity={plan.entity}")
    if plan.domain_signals:
        lines.append(f"domain_signals={', '.join(plan.domain_signals)}")
    if plan.reason:
        lines.append(f"plan_reason={plan.reason[:240]}")

    sem = semantic_by_model_id(plan.model_id) if plan.model_id not in ("compound", "") else None
    if sem:
        lines.append("")
        lines.append(f"# Selected model: {sem.model_id}")
        if sem.description:
            lines.append(sem.description[:700].strip())

        if plan.measure_id:
            meas = _format_measure(sem, plan.measure_id)
            if meas:
                lines.append(f"Selected measure: {meas}")
        elif sem.measures:
            preview = [_format_measure(sem, m.id) for m in sem.measures[:6]]
            lines.append("Measures: " + "; ".join(x for x in preview if x))

        if plan.filters:
            lines.append("Filters (auto):")
            for fl in _format_filter_lines(sem, plan.filters)[:8]:
                lines.append(f"  - {fl}")

        members = plan.union_member_ids or (sem.union_members if sem.is_logical_union else [])
        if members:
            lines.append(f"Union members: {', '.join(members)}")

        key_dims = [d for d in sem.dimensions if d.description and d.visibility != "internal"][:10]
        if key_dims:
            lines.append("Key dimensions: " + ", ".join(d.id for d in key_dims))

    if selected_tables:
        fq_list = [t.full_table_id.rsplit(".", 1)[-1] for t in selected_tables[:4]]
        lines.append("")
        lines.append("Selected tables: " + ", ".join(f"`{s}`" for s in fq_list))

    if join_block:
        lines.append("")
        lines.append("# Join hints")
        lines.append(join_block.strip()[:1200])

    if date_hints:
        lines.append("")
        lines.append("# Date hints")
        lines.append(date_hints.strip()[:800])

    if thread_memory:
        lines.append("")
        lines.append("# Thread memory (recent turns)")
        lines.append(thread_memory.strip()[:1200])

    return "\n".join(lines).strip()
