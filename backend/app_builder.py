"""Hex-style App Builder — notebook inputs + dashboard widgets as a live app."""
from __future__ import annotations

import json
import re
from typing import Any

import notebook_engine as ne

_VAR_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def parse_app_config(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
        if not isinstance(data, dict):
            return _default_app_config()
    except json.JSONDecodeError:
        return _default_app_config()
    return {
        "title": str(data.get("title") or "").strip(),
        "description": str(data.get("description") or "").strip(),
        "input_cell_ids": [
            int(x) for x in (data.get("input_cell_ids") or []) if str(x).isdigit()
        ],
    }


def _default_app_config() -> dict[str, Any]:
    return {"title": "", "description": "", "input_cell_ids": []}


def serialize_app_config(config: dict[str, Any]) -> str:
    return json.dumps(
        {
            "title": config.get("title") or "",
            "description": config.get("description") or "",
            "input_cell_ids": config.get("input_cell_ids") or [],
        }
    )


def input_cell_to_def(cell: Any) -> dict[str, Any] | None:
    if getattr(cell, "cell_type", None) != "input":
        return None
    try:
        cfg = json.loads(cell.config_json or "{}")
    except json.JSONDecodeError:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    itype = cfg.get("input_type", "date_range")
    base = {
        "cell_id": cell.id,
        "name": cell.name or "",
        "input_type": itype,
        "label": cfg.get("label") or cell.name or "Filter",
    }
    if itype == "date_range":
        base.update(
            {
                "start_var": cfg.get("start_var", "range_start"),
                "end_var": cfg.get("end_var", "range_end"),
                "default_start": cfg.get("default_start", "2025-04-01"),
                "default_end": cfg.get("default_end", "CURRENT_MONTH_END"),
            }
        )
    else:
        base.update(
            {
                "var": cfg.get("var", "value"),
                "default": str(cfg.get("default", "")),
            }
        )
    return base


def list_app_inputs(cells: list[Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    """Input controls exposed on the published app."""
    input_cells = [c for c in cells if c.cell_type == "input"]
    picked_ids = set(config.get("input_cell_ids") or [])
    if picked_ids:
        input_cells = [c for c in input_cells if c.id in picked_ids]
    out: list[dict[str, Any]] = []
    for c in sorted(input_cells, key=lambda x: (x.sort_order, x.id)):
        d = input_cell_to_def(c)
        if d:
            out.append(d)
    return out


def default_input_values(inputs: list[dict[str, Any]]) -> dict[str, str]:
    values: dict[str, str] = {}
    for inp in inputs:
        if inp.get("input_type") == "date_range":
            sk = inp.get("start_var") or "range_start"
            ek = inp.get("end_var") or "range_end"
            values[sk] = inp.get("default_start") or "2025-04-01"
            end = inp.get("default_end") or ""
            if end == "CURRENT_MONTH_END":
                values[ek] = ne._month_end_today()  # noqa: SLF001
            else:
                values[ek] = end or ne._month_end_today()  # noqa: SLF001
        else:
            values[inp.get("var") or "value"] = str(inp.get("default") or "")
    return values


def merge_input_overrides(
    inputs: list[dict[str, Any]],
    overrides: dict[str, str] | None,
) -> dict[str, str]:
    values = default_input_values(inputs)
    if overrides:
        for k, v in overrides.items():
            if v is not None and str(v).strip():
                values[k] = str(v).strip()
    return values


def sql_has_variables(sql: str) -> bool:
    return bool(_VAR_PATTERN.search(sql or ""))


def apply_variables(sql: str, variables: dict[str, str]) -> str:
    if not sql_has_variables(sql):
        return sql
    return ne.substitute_variables(sql, variables)
