"""Parse project join hints and expand table selection for multi-table questions."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import knowledge_base as kb
from question_intent import question_wants_breakdown

_CROSS_TABLE_DIMS = re.compile(
    r"\b(gender|retention|coach|institute|growth cycle|graduation|bachelors)\b",
    re.I,
)

_REL_LINE = re.compile(
    r"^-\s+([^\s]+)\s*->\s*([^\s(]+)\s*\(([^)]+)\)\s*:\s*(.+)$",
    re.MULTILINE,
)

_JOIN_INTENT = re.compile(
    r"\b(join|combined|along with|together with|with their|and their|"
    r"and also|as well as|correlate|versus|vs\.?|compare)\b",
    re.IGNORECASE,
)

_SINGLE_TABLE = re.compile(
    r"\b(summarize|summary|overview|describe|explain)\b(?:\s+the)?\s+"
    r"(?:`?[\w.]+\.?`?\s+)?(?:table\b)?",
    re.IGNORECASE,
)


@dataclass
class JoinRelation:
    source: str
    target: str
    rel_type: str
    join_sql: str

    @property
    def pair(self) -> frozenset[str]:
        return frozenset({self.source.lower(), self.target.lower()})


def parse_join_hints(text: str) -> list[JoinRelation]:
    if not (text or "").strip():
        return []
    out: list[JoinRelation] = []
    for m in _REL_LINE.finditer(text):
        out.append(
            JoinRelation(
                source=m.group(1).strip(),
                target=m.group(2).strip(),
                rel_type=m.group(3).strip(),
                join_sql=m.group(4).strip(),
            )
        )
    return out


def catalog_short_names(project_tables: list[Any]) -> dict[str, str]:
    """Map model short name (lowercase) -> full_table_id."""
    catalog: dict[str, str] = {}
    for t in project_tables:
        if not getattr(t, "included_for_ai", True):
            continue
        short = t.full_table_id.rsplit(".", 1)[-1]
        catalog[short.lower()] = t.full_table_id
    return catalog


def resolve_model_id(model_id: str, catalog: dict[str, str]) -> str | None:
    """Match YAML model id to a workspace table short name."""
    mid = model_id.lower().strip()
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


def relations_for_tables(
    relations: list[JoinRelation],
    selected_short: set[str],
) -> list[JoinRelation]:
    """Join hints that connect two or more selected tables."""
    sel = {s.lower() for s in selected_short}
    picked: list[JoinRelation] = []
    seen: set[frozenset[str]] = set()
    for rel in relations:
        src = rel.source.lower()
        tgt = rel.target.lower()
        if src in sel and tgt in sel and rel.pair not in seen:
            picked.append(rel)
            seen.add(rel.pair)
    return picked


def filter_join_hints_text(join_hints: str, selected_short: set[str]) -> str:
    """Keep only join lines relevant to the selected table set."""
    rels = relations_for_tables(parse_join_hints(join_hints), selected_short)
    if not rels:
        return ""
    lines = [
        f"- {r.source} -> {r.target} ({r.rel_type}): {r.join_sql}"
        for r in rels
    ]
    return "# Join hints for selected tables (use these ON conditions exactly)\n" + "\n".join(lines)


def relation_line(rel: JoinRelation) -> str:
    return f"- {rel.source} -> {rel.target} ({rel.rel_type}): {rel.join_sql}"


def relations_involving_table(
    relations: list[JoinRelation],
    table_short: str,
) -> list[JoinRelation]:
    """Relations where the given table is source or target."""
    t = table_short.lower().strip()
    return [r for r in relations if r.source.lower() == t or r.target.lower() == t]


def split_join_hints(text: str) -> tuple[str, list[JoinRelation]]:
    """Split preamble comments from structured relation lines."""
    preamble: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _REL_LINE.match(stripped):
            continue
        preamble.append(line)
    return "\n".join(preamble).strip(), parse_join_hints(text)


def format_join_hints(preamble: str, relations: list[JoinRelation]) -> str:
    if not relations:
        return preamble.strip()
    body = "\n".join(relation_line(r) for r in relations)
    if preamble.strip():
        return f"{preamble.strip()}\n{body}"
    return body


def join_hints_for_table(join_hints: str, table_short: str) -> str:
    """Join hint lines that involve one workspace table (for per-table editing)."""
    rels = relations_involving_table(parse_join_hints(join_hints), table_short)
    if not rels:
        return ""
    return "\n".join(relation_line(r) for r in rels)


def merge_join_hints_for_table(
    existing: str,
    table_short: str,
    table_hints: str,
) -> str:
    """Replace relations for one table; keep all other tables' relations unchanged."""
    t = table_short.lower().strip()
    preamble, all_rels = split_join_hints(existing)
    kept = [r for r in all_rels if r.source.lower() != t and r.target.lower() != t]
    new_rels = [
        r for r in parse_join_hints(table_hints)
        if r.source.lower() == t or r.target.lower() == t
    ]
    seen: set[tuple[str, str, str]] = set()
    merged: list[JoinRelation] = []
    for r in kept + new_rels:
        key = (r.source.lower(), r.target.lower(), r.join_sql)
        if key in seen:
            continue
        seen.add(key)
        merged.append(r)
    return format_join_hints(preamble, merged)


def explicit_table_target(question: str, catalog: dict[str, str]) -> str | None:
    """When the user names one table, return its full_table_id."""
    if not catalog:
        return None
    for m in re.finditer(r"`([^`]+)`", question):
        token = m.group(1).strip().lower().split(".")[-1]
        resolved = resolve_model_id(token, catalog)
        if resolved:
            return catalog[resolved]
    q = question.lower()
    for short in sorted(catalog.keys(), key=len, reverse=True):
        if short in q:
            return catalog[short]
    return None


def multi_table_intent(question: str) -> bool:
    """True when the question clearly needs data from more than one table."""
    try:
        from table_routing import is_compound_domain_question

        if is_compound_domain_question(question):
            return True
    except Exception:
        pass
    if _JOIN_INTENT.search(question):
        return True
    q = question.lower()
    # "jobs and NPS", "applications by institute" — two concepts linked by and/by
    if re.search(
        r"\b(jobs?|applied|application|nps|rating|institute|college|gender|"
        r"engagement|feedback|master)\b.*\b(and|with|by)\b.*\b(jobs?|applied|"
        r"application|nps|rating|institute|college|gender|engagement|feedback|master)\b",
        q,
    ):
        return True
    return False


def pin_single_table_if_needed(
    question: str,
    matches: list[Any],
    catalog: dict[str, str],
) -> list[str] | None:
    """Force one table when the user clearly asks about only one."""
    explicit = explicit_table_target(question, catalog)
    if explicit:
        for m in matches:
            m.selected = m.full_table_id == explicit
        return [explicit]

    if _SINGLE_TABLE.search(question) and not multi_table_intent(question):
        if matches:
            for m in matches:
                m.selected = False
            matches[0].selected = True
            return [matches[0].full_table_id]
    return None


def expand_selection_with_joins(
    question: str,
    selected_ids: list[str],
    matches: list[Any],
    knowledges: list[kb.TableKnowledge],
    join_hints: str,
    catalog: dict[str, str],
    keywords: list[str],
    *,
    max_tables: int = 4,
) -> tuple[list[str], list[JoinRelation], str]:
    """
    Add related tables from join hints when the knowledge base supports a join.
    Returns (expanded_ids, active_relations, reasoning_snippet).
    """
    relations = parse_join_hints(join_hints)
    if not relations or not catalog:
        return selected_ids, [], ""

    join_intent = multi_table_intent(question)
    # Breakdown by a demographic dimension (gender, retention…) needs joined tables.
    if (
        len(selected_ids) == 1
        and not join_intent
        and question_wants_breakdown(question)
        and _CROSS_TABLE_DIMS.search(question)
    ):
        join_intent = True
    # Single-table questions stay focused unless the user asks for combined data.
    if len(selected_ids) == 1 and not join_intent:
        return selected_ids, [], ""

    explicit = explicit_table_target(question, catalog)
    if explicit and explicit in selected_ids and len(selected_ids) == 1:
        return selected_ids, [], ""

    id_to_short = {fq: fq.rsplit(".", 1)[-1].lower() for fq in catalog.values()}
    selected_short: set[str] = set()
    for fq in selected_ids:
        short = id_to_short.get(fq)
        if short:
            selected_short.add(short)

    score_by_fq = {m.full_table_id: m.score for m in matches}
    knowledge_by_short = {k.short_name.lower(): k for k in knowledges}
    q_lower = question.lower()
    added_reasons: list[str] = []

    try:
        from table_routing import compound_domain_table_shorts, is_compound_domain_question

        if is_compound_domain_question(question):
            join_intent = True
            for short in compound_domain_table_shorts(question):
                fq = catalog.get(short.lower())
                if not fq or fq in selected_ids:
                    continue
                selected_ids.append(fq)
                selected_short.add(short.lower())
                added_reasons.append(f"`{short}` (compound domain join)")
    except Exception:
        pass

    frontier = list(selected_short)
    visited = set(selected_short)

    while frontier and len(selected_ids) < max_tables:
        current = frontier.pop(0)
        for rel in relations:
            other = None
            if rel.source.lower() == current:
                other = rel.target
            elif rel.target.lower() == current:
                other = rel.source
            if not other:
                continue

            resolved = resolve_model_id(other, catalog)
            if not resolved or resolved in visited:
                continue

            fq = catalog.get(resolved)
            if not fq or fq in selected_ids:
                continue

            k = knowledge_by_short.get(resolved)
            rel_score = kb.score_table_knowledge(question, k, keywords) if k else 0
            anchor_score = max(score_by_fq.get(selected_ids[0], 0), 1)

            should_add = False
            if rel_score >= max(8, int(anchor_score * 0.3)):
                should_add = True
            elif join_intent and rel_score >= 5:
                should_add = True
            elif any(kw in resolved for kw in keywords if len(kw) > 4):
                should_add = True
            elif any(kw in (k.table_description.lower() if k else "") for kw in keywords):
                should_add = True
            elif re.search(r"\bnps\b|rating", q_lower) and "nps" in resolved:
                should_add = True
            elif re.search(r"\bmaster\b|institute|profile|gender|plan\b", q_lower) and "master" in resolved:
                should_add = True
            elif re.search(r"\bengagement\b|cloudwatch|nav", q_lower) and (
                "engagement" in resolved or "cloudwatch" in resolved
            ):
                should_add = True
            elif re.search(r"\bfeedback\b", q_lower) and "feedback" in resolved:
                should_add = True

            if not should_add:
                continue

            selected_ids.append(fq)
            selected_short.add(resolved)
            visited.add(resolved)
            frontier.append(resolved)
            for m in matches:
                if m.full_table_id == fq:
                    m.selected = True
            added_reasons.append(f"`{resolved}` (linked from `{current}` via join hints)")
            if len(selected_ids) >= max_tables:
                break

    active = relations_for_tables(relations, selected_short)
    if not added_reasons and not active:
        return selected_ids, [], ""

    reasoning = ""
    if added_reasons:
        reasoning = "Added related tables from join hints: " + ", ".join(added_reasons) + "."
    if active:
        pairs = ", ".join(f"{r.source}↔{r.target}" for r in active[:4])
        reasoning = (reasoning + " " if reasoning else "") + f"Use JOINs: {pairs}."
    return selected_ids, active, reasoning.strip()


_MODEL_REF = re.compile(r"\$\{([^}]+)\}")


def _split_model_token(token: str) -> tuple[str | None, str]:
    token = (token or "").strip()
    if "." in token:
        model, col = token.split(".", 1)
        return model.strip(), col.strip()
    return None, token


def expand_join_sql(
    join_sql: str,
    catalog: dict[str, str],
    *,
    source: str,
    target: str,
) -> str:
    """Expand ${model.column} join hints to BigQuery backtick table paths."""
    source_key = resolve_model_id(source, catalog) or source.lower()
    source_fq = catalog.get(source_key, "")

    def repl(match: re.Match[str]) -> str:
        model_part, col = _split_model_token(match.group(1))
        if model_part:
            resolved = resolve_model_id(model_part, catalog) or model_part.lower()
            fq = catalog.get(resolved)
            if fq:
                return f"`{fq}`.{col}"
            return f"{model_part}.{col}"
        if source_fq:
            return f"`{source_fq}`.{col}"
        return col

    return _MODEL_REF.sub(repl, join_sql)


def build_join_knowledge_block(
    relations: list[JoinRelation],
    catalog: dict[str, str] | None = None,
) -> str:
    if not relations:
        return ""
    catalog = catalog or {}
    lines = [
        "# JOIN knowledge base (required when querying multiple tables)",
        "# NEVER use bare table names like z_ccbp_academy_users_master_data.column — BigQuery will fail.",
        "# Always write FROM `project.dataset.table` AS alias, then alias.column in SELECT/WHERE/ON.",
        "# Example pattern:",
        "#   FROM `project.dataset.jobs_table` j",
        "#   JOIN `project.dataset.master_table` m ON j.user_id = m.user_id",
    ]
    for r in relations:
        expanded = expand_join_sql(
            r.join_sql,
            catalog,
            source=r.source,
            target=r.target,
        )
        src_fq = catalog.get(resolve_model_id(r.source, catalog) or r.source.lower(), r.source)
        tgt_fq = catalog.get(resolve_model_id(r.target, catalog) or r.target.lower(), r.target)
        src_alias = (resolve_model_id(r.source, catalog) or r.source)[:10]
        tgt_alias = (resolve_model_id(r.target, catalog) or r.target)[:10]
        lines.append(
            f"# {r.source} -> {r.target} ({r.rel_type}):\n"
            f"#   ON {expanded}\n"
            f"#   FROM `{src_fq}` {src_alias}\n"
            f"#   JOIN `{tgt_fq}` {tgt_alias} ON {src_alias}.user_id = {tgt_alias}.user_id"
        )
    lines.append(
        "# Write JOIN ... ON using full `project.dataset.table` paths or short aliases defined in FROM."
    )
    return "\n".join(lines)
