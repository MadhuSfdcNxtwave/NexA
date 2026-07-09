"""Global metrics registry — glossary + semantic layer as one lookup index."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from semantic_layer import MeasureDef, TableSemantic, load_semantic_catalog, semantic_by_model_id

_GLOSSARY_PATH = Path(__file__).parent / "glossary.yaml"


@dataclass
class GlossaryTerm:
    id: str
    label: str
    synonyms: list[str] = field(default_factory=list)
    model_id: str = ""
    measure_id: str = ""
    intent: str = ""
    filters: list[str] = field(default_factory=list)
    description: str = ""
    avoid: list[str] = field(default_factory=list)


@dataclass
class MeasureEntry:
    measure_id: str
    model_id: str
    measure: MeasureDef
    glossary_id: str = ""
    label: str = ""
    description: str = ""


def _normalize(text: str) -> str:
    from question_intent import expand_question_abbreviations

    q = expand_question_abbreviations(text or "").strip().lower()
    q = re.sub(r"\s+", " ", q)
    return q.rstrip("?.!").strip()


@lru_cache(maxsize=1)
def load_glossary() -> dict[str, GlossaryTerm]:
    """Load glossary terms keyed by id."""
    if not _GLOSSARY_PATH.is_file():
        return {}
    with _GLOSSARY_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    out: dict[str, GlossaryTerm] = {}
    for raw in data.get("terms") or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        term = GlossaryTerm(
            id=str(raw["id"]),
            label=str(raw.get("label") or raw["id"]),
            synonyms=[str(s) for s in (raw.get("synonyms") or [])],
            model_id=str(raw.get("model_id") or ""),
            measure_id=str(raw.get("measure_id") or ""),
            intent=str(raw.get("intent") or ""),
            filters=[str(f) for f in (raw.get("filters") or [])],
            description=str(raw.get("description") or "").strip(),
            avoid=[str(a) for a in (raw.get("avoid") or [])],
        )
        out[term.id] = term
    return out


@lru_cache(maxsize=1)
def synonym_index() -> list[tuple[str, str]]:
    """Sorted (synonym, term_id) pairs — longest synonym first for greedy match."""
    pairs: list[tuple[str, str]] = []
    for term in load_glossary().values():
        for syn in term.synonyms:
            pairs.append((_normalize(syn), term.id))
        pairs.append((_normalize(term.label), term.id))
        pairs.append((_normalize(term.id.replace("_", " ")), term.id))
    pairs.sort(key=lambda x: (-len(x[0]), x[0]))
    return pairs


@lru_cache(maxsize=1)
def global_measure_index() -> dict[str, MeasureEntry]:
    """Flatten all YAML measures + glossary overrides into one index."""
    index: dict[str, MeasureEntry] = {}
    catalog = load_semantic_catalog()
    glossary = load_glossary()

    for model_id, sem in catalog.items():
        for m in sem.measures:
            key = f"{model_id}.{m.id}"
            index[key] = MeasureEntry(
                measure_id=m.id,
                model_id=sem.model_id,
                measure=m,
                description=m.description,
            )
            # Also index bare measure_id when unique enough
            bare = m.id
            if bare not in index:
                index[bare] = index[key]

    for term in glossary.values():
        if not term.measure_id or not term.model_id:
            continue
        sem = semantic_by_model_id(term.model_id)
        if not sem:
            continue
        meas = next((m for m in sem.measures if m.id == term.measure_id), None)
        if not meas:
            continue
        key = f"{term.model_id}.{term.measure_id}"
        index[key] = MeasureEntry(
            measure_id=term.measure_id,
            model_id=term.model_id,
            measure=meas,
            glossary_id=term.id,
            label=term.label,
            description=term.description or meas.description,
        )
        index[term.id] = index[key]
        index[term.measure_id] = index[key]

    return index


def match_glossary_terms(question: str) -> list[tuple[GlossaryTerm, str]]:
    """
    Return matched glossary terms with the synonym that fired.
    Skips terms whose avoid patterns appear in the question.
    """
    q = _normalize(question)
    if not q:
        return []
    glossary = load_glossary()
    matched: list[tuple[GlossaryTerm, str]] = []
    seen: set[str] = set()
    covered: set[str] = set()

    for syn, term_id in synonym_index():
        if term_id in seen:
            continue
        if not syn or len(syn) < 3:
            continue
        if syn not in q:
            continue
        term = glossary.get(term_id)
        if not term:
            continue
        if any(_normalize(a) in q for a in term.avoid):
            continue
        # Skip if a longer match already covers this span
        if any(syn in prev for prev in covered):
            continue
        matched.append((term, syn))
        seen.add(term_id)
        covered.add(syn)

    return matched


def resolve_measure(model_id: str, measure_id: str) -> MeasureEntry | None:
    index = global_measure_index()
    return index.get(f"{model_id}.{measure_id}") or index.get(measure_id)


def glossary_snippets_for_model(model_id: str) -> list[str]:
    """Compact glossary lines for embedding / RAG cards."""
    lines: list[str] = []
    for term in load_glossary().values():
        if term.model_id != model_id:
            continue
        parts = [f"{term.label} ({term.id})"]
        if term.measure_id:
            parts.append(f"measure={term.measure_id}")
        if term.synonyms:
            parts.append("also: " + "; ".join(term.synonyms[:4]))
        if term.description:
            parts.append(term.description[:200])
        lines.append(" | ".join(parts))
    return lines


def glossary_context_for_question(question: str) -> tuple[str, list[str]]:
    """Build glossary text block for knowledge answers and analysis enrichment."""
    matches = match_glossary_terms(question)
    if not matches:
        q = _normalize(question)
        glossary = load_glossary()
        for term in glossary.values():
            for token in re.findall(r"[a-z]{2,}", q):
                if token in _normalize(term.label) or any(token in _normalize(s) for s in term.synonyms[:6]):
                    matches.append((term, token))
                    break
            if len(matches) >= 3:
                break
    blocks: list[str] = []
    term_ids: list[str] = []
    seen: set[str] = set()
    for term, syn in matches[:4]:
        if term.id in seen:
            continue
        seen.add(term.id)
        term_ids.append(term.id)
        line = f"**{term.label}**"
        if term.description:
            line += f": {term.description}"
        if term.avoid:
            line += f" (Note: not {', '.join(term.avoid[:2])})"
        blocks.append(line)
    return "\n".join(blocks), term_ids


def reload_registry() -> None:
    load_glossary.cache_clear()
    synonym_index.cache_clear()
    global_measure_index.cache_clear()
