"""Schema-safe SQL validation before BigQuery execution."""
from __future__ import annotations

import re
from typing import Any

import sql_parse

_TABLE_REF = re.compile(r"`([^`]+)`|(?:FROM|JOIN)\s+([a-zA-Z0-9_.-]+)", re.I)
_IDENT = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b")


def _sql_blob(sql: str) -> str:
    return sql.lower()


def _short_table_name(ref: str) -> str:
    """Normalize parsed table refs to bare table name for comparison."""
    ref = ref.strip().lower().strip('"').strip("`")
    if "." in ref:
        return ref.rsplit(".", 1)[-1]
    return ref


def _tables_in_sql(sql: str) -> set[str]:
    try:
        return sql_parse.table_refs(sql_parse.parse_bigquery(sql))
    except ValueError:
        pass
    found: set[str] = set()
    for m in _TABLE_REF.finditer(sql):
        ref = m.group(1) or m.group(2) or ""
        ref = ref.strip().lower()
        if ref and ref not in ("select", "with", "as", "on", "and", "or"):
            found.add(ref)
            if "." in ref:
                found.add(ref.rsplit(".", 1)[-1])
    return found


def _idents_in_sql(sql: str) -> set[str]:
    try:
        return sql_parse.column_names(sql_parse.parse_bigquery(sql))
    except ValueError:
        return {m.group(1).lower() for m in _IDENT.finditer(sql)}


def _hints_for_tables(
    selected_tables: list[Any],
    hints_map: dict[str, dict[str, str]],
    inferred: dict[str, dict[str, str]],
) -> dict[str, str]:
    merged: dict[str, str] = {}
    for t in selected_tables:
        fq = t.full_table_id
        for col, role in {**hints_map.get(fq, {}), **inferred.get(fq, {})}.items():
            merged[col.lower()] = role
    return merged


def validate_sql(
    sql: str,
    question: str,
    selected_tables: list[Any],
    hints_map: dict[str, dict[str, str]],
    inferred: dict[str, dict[str, str]],
    *,
    allowed_columns: set[str] | None = None,
    columns_by_table: dict[str, set[str]] | None = None,
) -> list[str]:
    """Return violations; empty list means passed. Schema safety only — no domain hardcoding."""
    _ = question  # kept for API compatibility
    col_roles = _hints_for_tables(selected_tables, hints_map, inferred)
    forbidden = [c for c, r in col_roles.items() if r == "deprecated_duplicate"]
    idents = _idents_in_sql(sql)
    issues: list[str] = []

    allowed_tables = {t.full_table_id for t in selected_tables}
    if columns_by_table is not None or allowed_columns is not None:
        issues.extend(
            sql_parse.validate_against_schema(
                sql,
                allowed_tables=allowed_tables,
                allowed_columns=allowed_columns,
                columns_by_table=columns_by_table,
            )
        )
        catalog = {t.full_table_id.rsplit(".", 1)[-1].lower(): t.full_table_id for t in selected_tables}
        for short, fq in catalog.items():
            wrong = re.compile(
                rf"`?[\w-]+\.[\w-]+\.{re.escape(short)}`?",
                re.IGNORECASE,
            )
            for m in wrong.finditer(sql):
                if m.group(0).strip("`").lower() != fq.lower():
                    issues.append(
                        f"Wrong table path `{m.group(0).strip('`')}` — use exactly `{fq}` from the schema."
                    )
                    break
    else:
        try:
            expr = sql_parse.parse_bigquery(sql)
            sql_parse.ensure_read_only(expr)
        except ValueError as e:
            issues.append(str(e))

    if allowed_columns is None:
        short_names = {t.full_table_id.rsplit(".", 1)[-1].lower() for t in selected_tables}
        tables = _tables_in_sql(sql)
        blob = _sql_blob(sql)
        if short_names and not (tables & short_names) and not any(sn in blob for sn in short_names):
            issues.append(
                f"Query must use one of these tables: {', '.join(sorted(short_names))}"
            )

    short_names = {t.full_table_id.rsplit(".", 1)[-1].lower() for t in selected_tables}
    issues.extend(sql_parse.validate_table_qualifiers(sql, short_names))
    issues.extend(sql_parse.validate_string_literal_misuse(sql, columns_by_table))

    primary_fields = [c for c, r in col_roles.items() if r == "primary_field"]
    preferred = primary_fields[0] if primary_fields else "the canonical column"
    for col in forbidden:
        if col.lower() in idents:
            issues.append(f"Do not use `{col}` — use `{preferred}` instead.")

    if len(selected_tables) > 1:
        blob = _sql_blob(sql)
        sql_tables = _tables_in_sql(sql)
        short_names = {t.full_table_id.rsplit(".", 1)[-1].lower() for t in selected_tables}
        referenced_short = {_short_table_name(t) for t in sql_tables}
        used_tables = referenced_short & short_names
        if len(used_tables) > 1 and " join " not in blob and "\njoin " not in blob:
            issues.append(
                "Query uses multiple tables — add JOIN with ON conditions from "
                "# Join hints / JOIN knowledge base in the schema."
            )

    from table_routing import validate_sql_table_choice

    ok, reason = validate_sql_table_choice(question, sql)
    if not ok:
        issues.append(f"Wrong table for this question ({reason}).")

    return issues


# Legacy stubs — other modules may import these; no-op for generation.
def build_constraints(*_args, **_kwargs) -> dict[str, Any]:
    return {}


def constraints_text(_req: dict[str, Any]) -> str:
    return ""
