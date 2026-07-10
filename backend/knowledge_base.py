"""Table/column knowledge base for Ask — match questions to schema descriptions."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import bq
from question_intent import expand_question_abbreviations

_STOP = frozenset(
    """
    a an the and or but in on at to for of is are was were be been being
    how what which who when where why show me tell give count many much all
    any some this that these those with from than then also just only about
    into over under between among through during before after above below
    do does did can could should would will shall may might must each per
    """.split()
)

_MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)
_MONTH_SHORT = {m[:3]: i + 1 for i, m in enumerate(_MONTHS)}

# Weak signals — matching these alone should not beat a strong domain table name.
_GENERIC_KW = frozenset(
    """
    user users month week last show many how what count number total day today
    yesterday this these data table tables
    """.split()
)


def _keyword_in_name(keyword: str, name: str) -> bool:
    """Match keyword to underscore table name, including simple stems."""
    kw = keyword.lower()
    if kw in name:
        return True
    if kw.endswith("ed"):
        stem = kw[:-2]
        if stem and (stem in name or f"{stem}ion" in name):
            return True
    if kw.endswith("s") and len(kw) > 3 and kw[:-1] in name:
        return True
    return False


def _phrase_hits(question: str, name: str) -> int:
    """Bonus when consecutive question words appear as an underscore phrase in the table name."""
    words = re.findall(r"[a-z0-9]+", question.lower())
    hits = 0
    for size in (3, 2):
        for i in range(len(words) - size + 1):
            chunk = words[i : i + size]
            if any(w in _GENERIC_KW for w in chunk):
                continue
            phrase = "_".join(chunk)
            if phrase in name:
                hits += size
    return hits


def _domain_name_hits(keywords: list[str], name: str) -> int:
    return sum(
        1
        for kw in keywords
        if kw not in _GENERIC_KW and len(kw) >= 3 and _keyword_in_name(kw, name)
    )


@dataclass
class ColumnMatch:
    name: str
    score: int
    description: str
    selected: bool = False


# Hex model YAML often ends descriptions with these blocks — keep them for SQL/answers.
_GUIDANCE_SECTION_START = re.compile(
    r"(?im)^\s*(?:"
    r"Analytics Guidance|"
    r"Recommended Metrics|"
    r"Best Practices|"
    r"Important considerations|"
    r"IMPORTANT(?:\s+SCOPE)?(?:\s+NOTE)?|"
    r"IMPORTANT\s*[—\-]|"
    r"(?:Event\s+)?Grain(?:\s*&\s*uniqueness)?|"
    r"Primary use cases"
    r")\s*:?\s*$"
)


def split_table_description(description: str) -> tuple[str, str]:
    """Split model description into summary vs analytics-guidance tail."""
    text = (description or "").strip()
    if not text:
        return "", ""
    m = _GUIDANCE_SECTION_START.search(text)
    if not m:
        return text, ""
    summary = text[: m.start()].strip()
    guidance = text[m.start() :].strip()
    return summary, guidance


@dataclass
class TableKnowledge:
    full_table_id: str
    short_name: str
    table_description: str
    column_descriptions: dict[str, str]
    column_types: dict[str, str]
    ai_overview: str = ""
    operational_guidance: str = ""
    endorsed: bool = False
    included_for_ai: bool = True


def _short_name(full_table_id: str) -> str:
    return full_table_id.rsplit(".", 1)[-1]


def _parse_json_dict(raw: str | None) -> dict[str, str]:
    try:
        data = json.loads(raw or "{}")
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items() if str(v).strip()}
    except (json.JSONDecodeError, TypeError):
        return {}


def load_table_knowledge(table: Any) -> TableKnowledge:
    """Merge workspace notes + BigQuery metadata into one knowledge record."""
    fq = table.full_table_id
    ws_table_desc = (getattr(table, "description", None) or "").strip()
    ws_col_desc = _parse_json_dict(getattr(table, "column_descriptions_json", "{}"))

    column_descriptions: dict[str, str] = dict(ws_col_desc)
    column_types: dict[str, str] = {}
    bq_table_desc = ""

    # Skip BigQuery metadata when workspace YAML already supplies column descriptions
    # (avoids 50+ sequential API calls per question on large catalogs).
    if not column_descriptions:
        try:
            meta = bq.table_metadata(fq)
            bq_table_desc = (meta.get("description") or "").strip()
            for col in meta.get("columns") or []:
                name = col["name"]
                column_types[name] = col.get("type", "")
                bq_desc = (col.get("description") or "").strip()
                column_descriptions[name] = bq_desc
        except Exception:
            pass

    table_description = ws_table_desc or bq_table_desc
    if ws_table_desc and bq_table_desc and bq_table_desc not in ws_table_desc:
        table_description = f"{ws_table_desc} | {bq_table_desc}"

    _, operational_guidance = split_table_description(table_description)

    return TableKnowledge(
        full_table_id=fq,
        short_name=_short_name(fq),
        table_description=table_description,
        column_descriptions=column_descriptions,
        column_types=column_types,
        ai_overview=(getattr(table, "ai_overview", "") or "").strip(),
        operational_guidance=operational_guidance,
        endorsed=bool(getattr(table, "endorsed", False)),
        included_for_ai=bool(getattr(table, "included_for_ai", True)),
    )


def extract_keywords(question: str) -> list[str]:
    q = expand_question_abbreviations(question).lower()
    words = re.findall(r"[a-z0-9]+", q)
    keywords: list[str] = []
    seen: set[str] = set()

    def add(word: str) -> None:
        if word and word not in seen and word not in _STOP and len(word) > 1:
            seen.add(word)
            keywords.append(word)

    for w in words:
        add(w)

    if re.search(r"\bnps\b|rating|recommend|score|promoter", q):
        for k in ("nps", "responses", "rating"):
            add(k)
    if re.search(r"\bresponse|submission|survey|form\b", q):
        add("responses")
    if re.search(r"\b(job|jobs|applied|application|hiring|placement|placed|internship|ctc|salary|offer)\b", q):
        for k in ("jobs", "applied", "application", "hiring", "placement", "placed", "ctc", "internship"):
            add(k)
    if re.search(r"\bgrowth\s*cycle|\bgc\b|\bgcs\b", q):
        for k in ("growth", "cycle", "growth_cycle"):
            add(k)
    if re.search(r"\bunique|distinct|users?\b", q):
        add("user")
        add("users")
    if re.search(
        r"\bactive\b.{0,30}\b(platform|yesterday|today|day|users?)\b|"
        r"\b(platform|learning portal)\b.{0,30}\bactive\b|"
        r"\btime spent\b|\bengagement\b",
        q,
    ):
        for k in ("engagement", "active", "calendar_date", "time_spent", "platform", "daily_engagement"):
            add(k)
    if re.search(r"\blive\s+class|\battended\b|\battendance\b", q):
        for k in ("live_classes", "live_class", "attendance", "attended", "slot_date"):
            add(k)
    if re.search(r"\bnbfc\b", q) and re.search(r"\brenew", q):
        for k in ("nbfc", "renewals", "renewal", "conversion", "converted"):
            add(k)
    if re.search(r"\bcontextual\b.*\bfeedback|\bemoji\b", q):
        for k in ("contextual_feedback", "contextual", "feedback", "emoji"):
            add(k)
    if re.search(r"\bfeedback\b", q):
        add("feedback")

    for m in _MONTHS:
        if m in q:
            add(m)
            break
    else:
        for token in words:
            if token in _MONTH_SHORT:
                add(_MONTHS[_MONTH_SHORT[token] - 1])
                break

    return keywords[:16]


def score_table_knowledge(
    question: str,
    knowledge: TableKnowledge,
    keywords: list[str],
) -> int:
    """Score how well table + column descriptions (+ AI overview) match the question.

    Table description and AI overview are first-class signals for routing —
    not just table-name keyword overlap.
    """
    name = knowledge.short_name.lower()
    desc = knowledge.table_description.lower()
    guidance = (knowledge.operational_guidance or "").lower()
    overview = (knowledge.ai_overview or "").lower()
    # Combined prose KB used for phrase / multi-token matching
    prose = f"{desc}\n{guidance}\n{overview}".strip()
    score = 0

    for kw in keywords:
        if _keyword_in_name(kw, name):
            score += 18 if kw not in _GENERIC_KW else 8
        if kw in desc:
            score += 16  # table description is primary routing text
        if guidance and kw in guidance:
            score += 8
        if overview and kw in overview:
            score += 14  # AI overview = curated "when to use" profile
        for col, col_desc in knowledge.column_descriptions.items():
            col_lower = col.lower()
            blob = f"{col_lower} {col_desc}".lower()
            if kw in col_lower:
                score += 8
            if kw in col_desc.lower():
                score += 7
            if kw in blob:
                score += 4

    # Multi-word question phrases in description / overview beat single tokens
    if prose:
        score += _phrase_hits(question, prose) * 22
        q_tokens = [w for w in re.findall(r"[a-z0-9]+", question.lower()) if len(w) > 3]
        # Reward dense overlap with description/overview (not just name)
        prose_hits = sum(1 for t in q_tokens if t in prose and t not in _STOP)
        if prose_hits >= 3:
            score += 25 + min(prose_hits, 8) * 4

    domain_hits = _domain_name_hits(keywords, name)
    if domain_hits >= 2:
        score += 28 * domain_hits
    elif domain_hits == 1 and any(kw not in _GENERIC_KW for kw in keywords):
        score += 12

    score += _phrase_hits(question, name) * 18

    from table_routing import score_adjustment

    score += score_adjustment(question, name)

    q_lower = question.lower()
    if re.search(r"\battend", q_lower):
        if "attendance" in name or "attended" in name:
            score += 80
        if "cloudwatch" in name or "interaction" in name:
            score -= 70
        if "feedback" in name or "registration" in name:
            score -= 35

    if re.search(r"\bactive\b", q_lower) and re.search(r"\bplatform\b", q_lower):
        if "daily_engagement" in name or "time_spent" in name:
            score += 70
        if "event_engagement" in name:
            score -= 40

    # Prefer tables that actually have curated KB text filled in
    if (knowledge.table_description or "").strip():
        score += 8
    if (knowledge.ai_overview or "").strip():
        score += 12

    if knowledge.endorsed:
        score += 120

    return score


def normalize_keyword_scores(scores: dict[str, int]) -> dict[str, float]:
    """Map raw keyword scores to 0..1 for fusion with vector scores."""
    if not scores:
        return {}
    vals = list(scores.values())
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return {k: 1.0 if v > 0 else 0.0 for k, v in scores.items()}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


def match_columns(
    question: str,
    knowledge: TableKnowledge,
    column_hints: dict[str, str] | None = None,
    *,
    max_selected: int = 15,
) -> list[ColumnMatch]:
    """Rank columns using names + descriptions as the knowledge base."""
    keywords = extract_keywords(question)
    q = question.lower()
    hints = column_hints or {}
    matches: list[ColumnMatch] = []

    for col_name, desc in knowledge.column_descriptions.items():
        score = 0
        col_lower = col_name.lower()
        desc_lower = desc.lower()

        for kw in keywords:
            if kw in col_lower:
                score += 12
            if kw in desc_lower:
                score += 10

        role = hints.get(col_name, "")
        if role == "primary_date" and re.search(r"\b(month|date|when|daily|weekly|each)\b", q):
            score += 22
        if role == "primary_key" and re.search(r"\b(unique|distinct|users?|user_id)\b", q):
            score += 22
        if role == "primary_field":
            score += 8

        if re.search(r"\bunique users?\b|\bdistinct users?\b", q) and (
            col_lower == "user_id" or "unique" in desc_lower or "identifier" in desc_lower
        ):
            score += 25
        if re.search(r"\bmonth\b", q) and (
            "datetime" in col_lower or "date" in col_lower or "month" in desc_lower
        ):
            score += 18
        if re.search(r"\bapplied\b", q) and "applied" in col_lower:
            score += 28
        if re.search(r"\bjob\b", q) and "job" in col_lower:
            score += 10
        if re.search(r"\brating\b|\bnps\b", q) and re.search(r"rating|nps|scale", col_lower):
            score += 20

        matches.append(ColumnMatch(name=col_name, score=score, description=desc))

    matches.sort(key=lambda m: (-m.score, m.name.lower()))
    top_score = matches[0].score if matches else 0
    threshold = max(6, int(top_score * 0.45)) if top_score else 6

    selected_count = 0
    for m in matches:
        if m.score >= threshold and selected_count < max_selected:
            m.selected = True
            selected_count += 1

    # Always include canonical hint columns when present.
    for role in ("primary_date", "primary_key", "primary_field"):
        for col, r in hints.items():
            if r == role and col in knowledge.column_descriptions:
                for m in matches:
                    if m.name == col:
                        if not m.selected and selected_count < max_selected + 3:
                            m.selected = True
                            selected_count += 1
                        break

    return matches


def build_table_reasoning(
    question: str,
    knowledges: list[TableKnowledge],
    selected_ids: list[str],
) -> str:
    selected = [k for k in knowledges if k.full_table_id in selected_ids]
    if not selected:
        return "Matching this question to workspace tables using table and column descriptions."

    parts: list[str] = []
    for k in selected[:2]:
        snippet = (k.table_description or k.short_name)[:120].strip()
        if snippet:
            parts.append(f"`{k.short_name}` — {snippet}")
        else:
            parts.append(f"`{k.short_name}`")

    focus = "; ".join(parts)
    return (
        f"Knowledge base match: {focus}. "
        "Column descriptions below were scanned to pick fields for SQL."
    )


def build_column_reasoning(
    column_matches: dict[str, list[ColumnMatch]],
) -> str:
    lines: list[str] = []
    for fq, cols in column_matches.items():
        picked = [c for c in cols if c.selected]
        if not picked:
            continue
        short = _short_name(fq)
        names = ", ".join(f"`{c.name}`" for c in picked[:6])
        extra = f" (+{len(picked) - 6} more)" if len(picked) > 6 else ""
        lines.append(f"{short}: {names}{extra}")
    if not lines:
        return ""
    return "Relevant columns from descriptions: " + " | ".join(lines)


def build_knowledge_header(
    question: str,
    knowledges: list[TableKnowledge],
    column_matches: dict[str, list[ColumnMatch]],
) -> str:
    """Compact knowledge-base block prepended to schema for SQL generation.

    Table description + AI overview are the primary business context for both
    SQL generation and the analysis answer — treat them as authoritative KB.
    """
    lines = [
        "# Knowledge base (AUTHORITATIVE — use for table meaning, SQL, and answers)",
        "# Prefer TABLE DESCRIPTION and AI OVERVIEW over guessing from column names alone.",
        f"# Question: {question.strip()}",
    ]
    for k in knowledges:
        summary, guidance = split_table_description(k.table_description)
        if summary:
            lines.append(f"# TABLE DESCRIPTION `{k.short_name}`: {summary[:800]}")
        elif k.table_description:
            lines.append(f"# TABLE DESCRIPTION `{k.short_name}`: {k.table_description[:800]}")
        else:
            lines.append(f"# TABLE `{k.short_name}`: (no description — rely on columns carefully)")
        guidance = guidance or (k.operational_guidance or "").strip()
        if guidance:
            lines.append(
                f"# ANALYTICS GUIDANCE for `{k.short_name}` "
                "(metrics, grain, best practices — follow for SQL and caveats in answers):"
            )
            for ln in guidance.splitlines():
                ln = ln.strip()
                if ln:
                    lines.append(f"#   {ln[:240]}")
        if k.ai_overview:
            lines.append(
                f"# AI OVERVIEW for `{k.short_name}` "
                "(curated profile of what this table is for — trust for routing intent & answer framing):"
            )
            for ln in k.ai_overview.splitlines():
                ln = ln.strip()
                if ln:
                    lines.append(f"#   {ln[:220]}")
        picked = [c for c in column_matches.get(k.full_table_id, []) if c.selected]
        if picked:
            lines.append(f"# USE THESE COLUMNS for `{k.short_name}` (from column descriptions):")
            for c in picked[:12]:
                desc = (c.description or "").strip()
                if desc:
                    lines.append(f"#   - `{c.name}` — {desc[:160]}")
                else:
                    lines.append(f"#   - `{c.name}`")
    return "\n".join(lines)


def merged_column_notes(knowledges: list[TableKnowledge]) -> dict[str, dict[str, str]]:
    return {k.full_table_id: dict(k.column_descriptions) for k in knowledges}


def build_answer_kb_context(knowledges: list[TableKnowledge], *, max_chars: int = 1800) -> str:
    """Compact description + AI overview for the analysis / answer step."""
    parts: list[str] = []
    for k in knowledges:
        block: list[str] = [f"Source: {k.short_name}"]
        summary, _ = split_table_description(k.table_description)
        desc = (summary or k.table_description or "").strip()
        if desc:
            block.append(f"Description: {desc[:500]}")
        overview = (k.ai_overview or "").strip()
        if overview:
            block.append(f"AI overview: {overview[:600]}")
        if len(block) > 1:
            parts.append("\n".join(block))
    text = "\n\n".join(parts).strip()
    return text[:max_chars]
