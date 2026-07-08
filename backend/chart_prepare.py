"""Prepare query results for modern dashboard charts (Tableau-style)."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

_TOP_N = 12
_TEXT_LIKE = re.compile(
    r"(reason|feedback|comment|description|title|name|label|text|verbatim)",
    re.I,
)


def _is_numeric(val: Any) -> bool:
    if val is None or val == "":
        return False
    try:
        float(val)
        return True
    except (TypeError, ValueError):
        return False


def _looks_text_column(rows: list[dict], col: str) -> bool:
    sample = [r.get(col) for r in rows[:40] if r.get(col) is not None]
    if not sample:
        return False
    if any(_is_numeric(v) for v in sample[:8]):
        return False
    avg_len = sum(len(str(v)) for v in sample) / len(sample)
    if avg_len > 18:
        return True
    if _TEXT_LIKE.search(col):
        return True
    unique = len({str(v).strip().lower() for v in sample})
    return unique >= min(8, len(sample) * 0.6)


def _truncate_label(text: str, max_len: int = 72) -> str:
    s = " ".join(str(text).split())
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def aggregate_value_counts(
    rows: list[dict],
    col: str,
    *,
    top_n: int = _TOP_N,
) -> tuple[list[dict], str, str]:
    """Collapse many text rows into category + count for ranked bar charts."""
    counts: Counter[str] = Counter()
    for row in rows:
        raw = row.get(col)
        if raw is None or str(raw).strip() == "":
            counts["(blank)"] += 1
        else:
            counts[_truncate_label(str(raw).strip())] += 1

    ranked = counts.most_common(top_n)
    shown_total = sum(c for _, c in ranked)
    other = sum(counts.values()) - shown_total
    if other > 0:
        ranked.append(("Other", other))

    count_col = "count"
    out = [{col: label, count_col: n} for label, n in ranked]
    return out, col, count_col


def _cap_categories(
    rows: list[dict],
    x: str,
    y: str,
    *,
    top_n: int = _TOP_N,
) -> list[dict]:
    """Keep top categories by y; bucket the rest as Other."""
    if len(rows) <= top_n + 1:
        return rows
    try:
        sorted_rows = sorted(rows, key=lambda r: float(r.get(y) or 0), reverse=True)
    except (TypeError, ValueError):
        return rows[: top_n + 1]

    head = sorted_rows[:top_n]
    other_sum = sum(float(r.get(y) or 0) for r in sorted_rows[top_n:])
    if other_sum > 0:
        head.append({x: "Other", y: other_sum})
    return head


def prepare_chart(
    rows: list[dict],
    columns: list[str],
    spec: dict[str, Any] | None,
    question: str = "",
) -> tuple[list[dict], dict[str, Any]]:
    """
    Normalize rows + chart spec for readable Tableau-style visuals.
    - Free-text lists → top-N horizontal bar of counts
    - Too many bar categories → top-N + Other
  """
    spec = dict(spec or {"chart": "none"})
    if not rows or not columns:
        return rows, spec

    chart = str(spec.get("chart") or "none").lower()

    # Single text column (e.g. list of reasons) — never chart raw rows.
    if len(columns) == 1:
        col = columns[0]
        if len(rows) > 6 and _looks_text_column(rows, col):
            agg_rows, x, y = aggregate_value_counts(rows, col)
            title = (spec.get("title") or "").strip()
            if not title:
                label = col.replace("_", " ")
                title = f"Top {label} (by count)"
            return agg_rows, {
                "chart": "bar",
                "x": x,
                "y": y,
                "color": None,
                "title": title,
                "horizontal": True,
                "variant": "ranked",
            }

    x = spec.get("x")
    y = spec.get("y")

    # LLM picked bar but only one column — aggregate counts.
    if chart == "bar" and x and (not y or y not in columns) and len(columns) == 1:
        agg_rows, x2, y2 = aggregate_value_counts(rows, columns[0])
        return agg_rows, {
            **spec,
            "x": x2,
            "y": y2,
            "horizontal": True,
            "variant": "ranked",
        }

    if chart == "bar" and x and y and x in columns and y in columns:
        out_rows = list(rows)
        if len(out_rows) > _TOP_N + 1 and _looks_text_column(out_rows, x):
            out_rows = _cap_categories(out_rows, x, y)
            spec = {**spec, "horizontal": True, "variant": "ranked"}
        elif len(out_rows) > 10:
            spec = {**spec, "horizontal": spec.get("horizontal", True)}

    if chart == "pie" and len(rows) > 8:
        if x and y and x in columns and y in columns:
            out_rows = _cap_categories(rows, x, y, top_n=8)
            return out_rows, spec
        spec["chart"] = "bar"
        spec["horizontal"] = True

    # High-cardinality text on x with numeric y — prefer ranked horizontal bar.
    if (
        chart in ("bar", "line")
        and x
        and y
        and x in columns
        and y in columns
        and _looks_text_column(rows, x)
        and len(rows) > 8
    ):
        if chart == "line":
            spec["chart"] = "bar"
        agg_rows, x2, y2 = aggregate_value_counts(rows, x)
        return agg_rows, {
            **spec,
            "x": x2,
            "y": y2,
            "horizontal": True,
            "variant": "ranked",
        }

    if chart == "table" and len(columns) == 1 and len(rows) > 25 and _looks_text_column(rows, columns[0]):
        agg_rows, x, y = aggregate_value_counts(rows, columns[0])
        return agg_rows, {
            "chart": "bar",
            "x": x,
            "y": y,
            "color": None,
            "title": spec.get("title") or f"Top {columns[0].replace('_', ' ')}",
            "horizontal": True,
            "variant": "ranked",
        }

    return rows, spec
