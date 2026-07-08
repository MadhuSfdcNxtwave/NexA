"""Smart chart defaults and business-friendly result shaping."""
from __future__ import annotations

import re
from typing import Any

_DATE_COL = re.compile(r"(date|month|week|day|time|period|year)", re.I)
_COUNT_COL = re.compile(r"(count|total|number|sum|avg|average|score|nps|rating)", re.I)


def _is_numeric(val: Any) -> bool:
    if val is None or val == "":
        return False
    try:
        float(val)
        return True
    except (TypeError, ValueError):
        return False


def _friendly_label(col: str) -> str:
    return col.replace("_", " ").strip().title()


def infer_chart_spec(
    question: str,
    columns: list[str],
    rows: list[dict],
) -> dict[str, Any]:
    """
    Deterministic chart choice before LLM — ensures sensible defaults for every result shape.
    """
    if not rows or not columns:
        return {"chart": "none"}

    n_rows = len(rows)
    n_cols = len(columns)
    numeric_cols = [c for c in columns if _is_numeric(rows[0].get(c))]
    text_cols = [c for c in columns if c not in numeric_cols]

    # Single KPI answer
    if n_rows == 1 and n_cols == 1:
        col = columns[0]
        val = rows[0].get(col)
        title = _friendly_label(col)
        if _is_numeric(val):
            return {"chart": "none", "title": title, "kpi": True}
        return {"chart": "none", "title": title}

    if n_rows == 1 and n_cols <= 3:
        return {"chart": "none", "title": "Summary"}

    if n_rows == 1 and n_cols >= 2 and len(numeric_cols) >= 2:
        return {"chart": "table", "title": "Summary"}

    date_cols = [c for c in columns if _DATE_COL.search(c)]
    if date_cols and numeric_cols and n_rows >= 2:
        return {
            "chart": "line",
            "x": date_cols[0],
            "y": numeric_cols[0],
            "title": f"{_friendly_label(numeric_cols[0])} over time",
            "horizontal": False,
        }

    if len(text_cols) >= 1 and len(numeric_cols) >= 1 and n_rows >= 2:
        x = text_cols[0]
        y = numeric_cols[0]
        if not _COUNT_COL.search(y):
            y = next((c for c in numeric_cols if _COUNT_COL.search(c)), numeric_cols[0])
        sample_labels = [str(rows[i].get(x) or "") for i in range(min(8, n_rows))]
        avg_label = sum(len(s) for s in sample_labels) / max(1, len(sample_labels))
        # Long category names (growth cycles) read better as a table than a wide bar chart.
        max_label = max((len(s) for s in sample_labels), default=0)
        if avg_label > 24 or max_label > 32:
            return {
                "chart": "table",
                "title": f"{_friendly_label(y)} by {_friendly_label(x)}",
            }
        horizontal = n_rows > 5 or any(len(s) > 14 for s in sample_labels)
        return {
            "chart": "bar",
            "x": x,
            "y": y,
            "title": f"{_friendly_label(y)} by {_friendly_label(x)}",
            "horizontal": horizontal,
        }

    if n_cols == 2 and n_rows <= 8 and numeric_cols and text_cols:
        return {
            "chart": "pie",
            "x": text_cols[0],
            "y": numeric_cols[0],
            "title": f"Share of {_friendly_label(numeric_cols[0])}",
            "horizontal": False,
        }

    if n_cols >= 3 or n_rows > 15:
        return {"chart": "table", "title": "Detailed breakdown"}

    return {"chart": "bar" if numeric_cols else "table", "title": "Results"}


def format_metric_value(val: Any) -> str:
    if val is None:
        return "no value"
    try:
        num = float(val)
        if num == int(num):
            return f"{int(num):,}"
        return f"{num:,.2f}"
    except (TypeError, ValueError):
        return str(val)


def heuristic_analyze(
    question: str,
    columns: list[str],
    rows: list[dict],
    row_count: int,
) -> str:
    """Plain-English summary when the VIZ LLM is unavailable."""
    q = (question or "").strip().rstrip("?")
    if not rows:
        return f"No data was returned for: {q}."
    if row_count == 1 and len(columns) == 1:
        col = columns[0]
        label = _friendly_label(col)
        val = format_metric_value(rows[0].get(col))
        return f"The answer is {val} ({label}) for: {q}."
    if row_count <= 8 and columns:
        parts: list[str] = []
        for row in rows[:8]:
            bits = [f"{_friendly_label(c)}: {format_metric_value(row.get(c))}" for c in columns[:4]]
            parts.append("; ".join(bits))
        preview = " | ".join(parts)
        return f"Found {row_count} row(s) for: {q}. {preview}."
    return f"Found {row_count} row(s) for: {q}."


def merge_chart_specs(primary: dict, fallback: dict) -> dict:
    """Prefer LLM spec when valid; fill gaps from deterministic inference."""
    out = dict(fallback or {})
    p = primary or {}
    if p.get("chart") and p.get("chart") != "none":
        out.update(p)
        if not out.get("x") and fallback.get("x"):
            out["x"] = fallback["x"]
        if not out.get("y") and fallback.get("y"):
            out["y"] = fallback["y"]
        if not out.get("title") and fallback.get("title"):
            out["title"] = fallback["title"]
    # Long-label breakdowns: deterministic table beats LLM horizontal bar.
    if fallback.get("chart") == "table" and out.get("chart") == "bar":
        out["chart"] = "table"
        if fallback.get("title"):
            out["title"] = fallback["title"]
    return out
