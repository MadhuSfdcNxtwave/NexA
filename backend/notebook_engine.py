"""Hex-style notebook execution: inputs, SQL cells, chaining, template vars."""
from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from datetime import date
from typing import Any

import bq
import pandas as pd

_VAR_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
_FROM_CELL = re.compile(
    r"\bFROM\s+([a-zA-Z_][a-zA-Z0-9_]*)\b",
    re.IGNORECASE,
)


def _month_end_today() -> str:
    today = date.today()
    if today.month == 12:
        nxt = date(today.year + 1, 1, 1)
    else:
        nxt = date(today.year, today.month + 1, 1)
    from datetime import timedelta

    return (nxt - timedelta(days=1)).isoformat()


def default_input_values(config: dict[str, Any]) -> dict[str, str]:
    """Resolve dynamic defaults for input cells (e.g. Apr-2025 → current month-end)."""
    out: dict[str, str] = {}
    itype = config.get("input_type", "date_range")
    if itype == "date_range":
        start_var = config.get("start_var", "range_start")
        end_var = config.get("end_var", "range_end")
        start_default = config.get("default_start", "2025-04-01")
        end_default = config.get("default_end", "CURRENT_MONTH_END")
        out[start_var] = start_default
        if end_default == "CURRENT_MONTH_END":
            out[end_var] = _month_end_today()
        else:
            out[end_var] = end_default
    elif itype == "text":
        out[config.get("var", "value")] = str(config.get("default", ""))
    return out


def extract_dependencies(sql: str, variables: set[str]) -> set[str]:
    """Cell names referenced via FROM identifier (excluding SQL keywords)."""
    skip = {
        "select", "with", "where", "group", "order", "limit", "join", "left",
        "right", "inner", "outer", "cross", "unnest", "lateral", "as", "on",
        "and", "or", "not", "in", "between", "case", "when", "then", "else",
        "end", "having", "union", "all", "distinct", "from",
    }
    deps: set[str] = set()
    for m in _FROM_CELL.finditer(sql):
        name = m.group(1).lower()
        if name not in skip and name not in variables:
            deps.add(m.group(1))
    return deps


def _input_var_map(cells: list[dict[str, Any]]) -> dict[str, str]:
    """Map template variable names to the input cell that defines them."""
    out: dict[str, str] = {}
    for c in cells:
        if c.get("cell_type") != "input":
            continue
        cfg = c.get("config") or {}
        if cfg.get("input_type") == "date_range":
            for key in (cfg.get("start_var"), cfg.get("end_var")):
                if key:
                    out[key] = c["name"]
        elif cfg.get("var"):
            out[cfg["var"]] = c["name"]
    return out


def build_dependency_edges(cells: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Directed edges between notebook cells (variable refs + FROM chain)."""
    by_name = {c["name"]: c for c in cells if c.get("name")}
    var_to_input = _input_var_map(cells)
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def add_edge(src: str, dst: str, kind: str, label: str = "") -> None:
        key = (src, dst, kind)
        if key in seen or src == dst:
            return
        seen.add(key)
        edges.append({"from": src, "to": dst, "kind": kind, "label": label})

    for c in cells:
        if c.get("cell_type") == "code":
            cfg = c.get("config") or {}
            src = cfg.get("data_source")
            if src and src in by_name:
                add_edge(src, c["name"], "cell", "data")
            continue
        if c.get("cell_type") != "sql":
            continue
        content = c.get("content") or ""
        vars_used = {m.group(1) for m in _VAR_PATTERN.finditer(content)}
        for var in vars_used:
            if var in var_to_input:
                add_edge(var_to_input[var], c["name"], "variable", var)
        for dep in extract_dependencies(content, vars_used):
            if dep in by_name:
                add_edge(dep, c["name"], "cell")
    return edges


def topological_order(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order cells by dependency (inputs first, then SQL chain)."""
    by_name = {c["name"]: c for c in cells if c.get("name")}
    graph: dict[str, set[str]] = defaultdict(set)
    indegree: dict[str, int] = defaultdict(int)

    for c in cells:
        indegree.setdefault(c["name"], 0)

    for e in build_dependency_edges(cells):
        graph[e["from"]].add(e["to"])
        indegree[e["to"]] += 1

    queue = deque(
        sorted(
            [c for c in cells if indegree.get(c["name"], 0) == 0],
            key=lambda x: x.get("sort_order", 0),
        )
    )
    ordered: list[dict[str, Any]] = []
    while queue:
        c = queue.popleft()
        ordered.append(c)
        for nxt in sorted(graph.get(c["name"], set()), key=lambda n: by_name[n].get("sort_order", 0)):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(by_name[nxt])

    seen = {c["id"] for c in ordered}
    for c in sorted(cells, key=lambda x: x.get("sort_order", 0)):
        if c["id"] not in seen:
            ordered.append(c)
    return ordered


def build_logic_graph(cells: list[dict[str, Any]]) -> dict[str, Any]:
    """Layout nodes and edges for the logic DAG view."""
    if not cells:
        return {"nodes": [], "edges": [], "width": 320, "height": 200}

    edges = build_dependency_edges(cells)
    names = [c["name"] for c in cells if c.get("name")]
    layer: dict[str, int] = {n: 0 for n in names}
    changed = True
    while changed:
        changed = False
        for e in edges:
            nxt = layer[e["from"]] + 1
            if nxt > layer[e["to"]]:
                layer[e["to"]] = nxt
                changed = True

    by_layer: dict[int, list[str]] = defaultdict(list)
    for n in names:
        by_layer[layer[n]].append(n)
    for ly in by_layer:
        by_layer[ly].sort(
            key=lambda nm: next(
                (c.get("sort_order", 0) for c in cells if c.get("name") == nm),
                0,
            )
        )

    node_w, node_h, gap_x, gap_y = 220, 76, 56, 64
    by_name = {c["name"]: c for c in cells}
    nodes: list[dict[str, Any]] = []

    for c in cells:
        name = c["name"]
        ly = layer.get(name, 0)
        row = by_layer[ly]
        idx = row.index(name) if name in row else 0
        lr = c.get("last_run")
        if c.get("cell_type") == "sql":
            status = "ok" if lr else "idle"
        elif c.get("cell_type") == "input":
            status = "ok"
        elif c.get("cell_type") == "code":
            status = "ok" if (c.get("config") or {}).get("data_source") else "idle"
        else:
            status = "idle"

        vars_out: list[str] = []
        if c.get("cell_type") == "input":
            cfg = c.get("config") or {}
            if cfg.get("input_type") == "date_range":
                for k in (cfg.get("start_var"), cfg.get("end_var")):
                    if k:
                        vars_out.append(k)
            elif cfg.get("var"):
                vars_out.append(cfg["var"])

        nodes.append({
            "id": c["id"],
            "name": name,
            "cell_type": c["cell_type"],
            "x": idx * (node_w + gap_x) + 32,
            "y": ly * (node_h + gap_y) + 32,
            "width": node_w,
            "height": node_h,
            "layer": ly,
            "status": status,
            "row_count": lr.get("row_count") if lr else None,
            "variables": vars_out,
        })

    width = max((n["x"] + n["width"] for n in nodes), default=280) + 48
    height = max((n["y"] + n["height"] for n in nodes), default=200) + 48
    return {"nodes": nodes, "edges": edges, "width": width, "height": height}


def substitute_variables(sql: str, variables: dict[str, str]) -> str:
    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in variables:
            raise ValueError(f"Unknown template variable: {key}")
        val = variables[key]
        if re.match(r"^\d{4}-\d{2}-\d{2}$", val):
            return f"DATE '{val}'"
        if val.isdigit():
            return val
        return f"'{val}'"

    return _VAR_PATTERN.sub(repl, sql)


def chain_from_prior(sql: str, prior_sql_by_name: dict[str, str]) -> str:
    """Replace FROM cell_name with subquery of prior cell SQL."""
    out = sql
    for name, prior_sql in prior_sql_by_name.items():
        pattern = re.compile(rf"\bFROM\s+{re.escape(name)}\b", re.IGNORECASE)

        def subquery_repl(_m: re.Match[str], ps: str = prior_sql, n: str = name) -> str:
            return f"FROM (\n{ps.strip().rstrip(';')}\n) AS {n}"

        if pattern.search(out):
            out = pattern.sub(subquery_repl, out, count=1)
    return out


def run_notebook(
    cells: list[dict[str, Any]],
    *,
    input_overrides: dict[str, str] | None = None,
    stop_at_cell_id: int | None = None,
) -> tuple[dict[str, str], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """
    Execute notebook cells in dependency order.
    Returns (variables, results_by_cell_name, run_log).
    """
    variables: dict[str, str] = dict(input_overrides or {})
    sql_by_name: dict[str, str] = {}
    results: dict[str, dict[str, Any]] = {}
    run_log: list[dict[str, Any]] = []

    ordered = topological_order(cells)

    for cell in ordered:
        cid = cell["id"]
        name = cell["name"]
        ctype = cell["cell_type"]
        content = cell.get("content") or ""
        config = cell.get("config") or {}

        if stop_at_cell_id is not None and cid != stop_at_cell_id:
            # Still need upstream for deps — only skip running if after stop? 
            # For partial run we run up to and including stop_at_cell_id
            pass

        try:
            if ctype == "input":
                defaults = default_input_values(config)
                for k, v in defaults.items():
                    variables.setdefault(k, v)
                if input_overrides:
                    for k, v in input_overrides.items():
                        if k in defaults or k in {config.get("start_var"), config.get("end_var"), config.get("var")}:
                            variables[k] = v
                run_log.append({"cell_id": cid, "name": name, "status": "ok", "type": "input", "variables": dict(variables)})
                if stop_at_cell_id == cid:
                    break
                continue

            if ctype == "text":
                run_log.append({"cell_id": cid, "name": name, "status": "ok", "type": "text"})
                if stop_at_cell_id == cid:
                    break
                continue

            if ctype != "sql":
                run_log.append({"cell_id": cid, "name": name, "status": "skipped", "error": f"Unknown type {ctype}"})
                continue

            deps = extract_dependencies(content, set(variables.keys()))
            prior = {d: sql_by_name[d] for d in deps if d in sql_by_name}
            sql = substitute_variables(content, variables)
            sql = chain_from_prior(sql, prior)
            sql = bq.validate_select_only(sql)

            bytes_estimate = bq.dry_run_bytes(sql)
            df = bq.run_query(sql)
            rows = json.loads(df.to_json(orient="records", date_format="iso"))
            columns = list(df.columns)

            sql_by_name[name] = sql
            results[name] = {
                "cell_id": cid,
                "name": name,
                "sql": sql,
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "bytes_estimate": bytes_estimate,
            }
            run_log.append({
                "cell_id": cid,
                "name": name,
                "status": "ok",
                "type": "sql",
                "row_count": len(rows),
                "bytes_estimate": bytes_estimate,
            })
        except Exception as e:
            run_log.append({"cell_id": cid, "name": name, "status": "error", "error": str(e)})
            raise

        if stop_at_cell_id == cid:
            break

    return variables, results, run_log


def nps_starter_cells(project_table_fq: str) -> list[dict[str, Any]]:
    """Default notebook template for NPS analytics."""
    return [
        {
            "cell_type": "input",
            "name": "month_filter",
            "content": "NPS month filter",
            "sort_order": 0,
            "config": {
                "input_type": "date_range",
                "label": "Response month range",
                "start_var": "month_range_start",
                "end_var": "month_range_end",
                "default_start": "2025-04-01",
                "default_end": "CURRENT_MONTH_END",
            },
        },
        {
            "cell_type": "sql",
            "name": "nps_responses_by_month",
            "sort_order": 1,
            "content": (
                f"SELECT\n"
                f"  form_submission_month,\n"
                f"  COUNT(*) AS response_count\n"
                f"FROM `{project_table_fq}`\n"
                f"WHERE form_submission_month BETWEEN {{{{ month_range_start }}}} AND {{{{ month_range_end }}}}\n"
                f"GROUP BY form_submission_month\n"
                f"ORDER BY form_submission_month"
            ),
        },
        {
            "cell_type": "sql",
            "name": "nps_breakdown",
            "sort_order": 2,
            "content": (
                f"SELECT\n"
                f"  COUNTIF(rating_on_scale_of_0_to_10 >= 9) AS promoters,\n"
                f"  COUNTIF(rating_on_scale_of_0_to_10 BETWEEN 7 AND 8) AS passives,\n"
                f"  COUNTIF(rating_on_scale_of_0_to_10 <= 6) AS detractors,\n"
                f"  COUNT(*) AS total,\n"
                f"  ROUND(100.0 * (COUNTIF(rating_on_scale_of_0_to_10 >= 9) - COUNTIF(rating_on_scale_of_0_to_10 <= 6)) / NULLIF(COUNT(*), 0), 1) AS nps_score\n"
                f"FROM `{project_table_fq}`\n"
                f"WHERE form_submission_month BETWEEN {{{{ month_range_start }}}} AND {{{{ month_range_end }}}}"
            ),
        },
    ]
