"""BigQuery SQL parsing and schema checks via sqlglot.

sqlglot does not generate SQL — it parses, validates, and normalizes it.
Used as a guardrail after the LLM produces a query.
"""
from __future__ import annotations

import re

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

_WRITE_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Merge,
)

_GLUE_BEFORE_KW = re.compile(
    r"([a-zA-Z0-9_\)])(?=(FROM|WHERE|JOIN|GROUP|ORDER|LIMIT|HAVING|UNION|INTERSECT|EXCEPT)\b)",
    re.IGNORECASE,
)


def extract_sql_from_text(text: str) -> str | None:
    """Pull an embedded SELECT/WITH query from a long user message (Hex-style paste)."""
    raw = (text or "").strip()
    if not raw:
        return None
    fenced = re.search(r"```(?:sql)?\s*(.*?)```", raw, re.I | re.S)
    if fenced:
        candidate = fenced.group(1).strip()
        if re.search(r"\b(SELECT|WITH)\b", candidate, re.I) and re.search(
            r"\bFROM\b", candidate, re.I
        ):
            return candidate.rstrip(";").strip()
    match = re.search(
        r"(?:^|\n)\s*((?:WITH|SELECT)\b[\s\S]+)",
        raw,
        re.I,
    )
    if not match:
        return None
    candidate = match.group(1).strip()
    if not re.search(r"\bFROM\b", candidate, re.I):
        return None
    # Trim trailing prose after the SQL statement.
    candidate = re.split(r"\n\s*\n", candidate, maxsplit=1)[0].strip()
    return candidate.rstrip(";").strip()


def normalize_llm_sql(sql: str) -> str:
    """Fix common formatting glitches in LLM-generated SQL."""
    text = (sql or "").strip()
    if not text:
        return text
    text = _GLUE_BEFORE_KW.sub(r"\1 ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_bigquery(sql: str) -> exp.Expression:
    """Parse SQL as BigQuery dialect; raises ValueError on failure."""
    text = (sql or "").strip()
    if not text:
        raise ValueError("Empty SQL.")
    try:
        expr = parse_one(text, read="bigquery")
    except ParseError as e:
        raise ValueError(f"Invalid BigQuery SQL: {e}") from e
    if expr is None:
        raise ValueError("Could not parse SQL for BigQuery.")
    return expr


def ensure_read_only(expr: exp.Expression) -> None:
    """Block writes/DDL even if regex checks were bypassed."""
    for node in expr.walk():
        if isinstance(node, _WRITE_TYPES):
            kind = type(node).__name__.upper()
            raise ValueError(f"Only read-only SELECT queries are allowed (found {kind}).")


def table_refs(expr: exp.Expression) -> set[str]:
    """Fully-qualified and short table names referenced in the query."""
    refs: set[str] = set()
    for table in expr.find_all(exp.Table):
        parts: list[str] = []
        for key in ("catalog", "db"):
            part = table.args.get(key)
            if part is not None:
                parts.append(str(part).strip("`").lower())
        name = table.name
        if name:
            name = str(name).strip("`").lower()
            parts.append(name)
        if parts:
            fq = ".".join(parts)
            refs.add(fq)
            refs.add(parts[-1])
        raw = table.sql(dialect="bigquery").strip("`").lower()
        if raw:
            refs.add(raw)
            if "." in raw:
                refs.add(raw.rsplit(".", 1)[-1])
    return refs


def column_names(expr: exp.Expression) -> set[str]:
    """Column identifiers used in the query (lowercase)."""
    names: set[str] = set()
    for col in expr.find_all(exp.Column):
        name = (col.name or "").strip("`").lower()
        if name and name != "*":
            names.add(name)
    return names


def defined_aliases(expr: exp.Expression) -> set[str]:
    """Names defined INSIDE the query: CTE names and SELECT aliases.

    References to these are legal even though they are not table columns
    (e.g. WITH matched AS (SELECT long_col AS worked_well ...) SELECT worked_well).
    """
    names: set[str] = set()
    for cte in expr.find_all(exp.CTE):
        if cte.alias:
            names.add(str(cte.alias).strip("`").lower())
    for alias in expr.find_all(exp.Alias):
        a = alias.args.get("alias")
        if a is not None:
            name = str(getattr(a, "name", a)).strip("`").lower()
            if name:
                names.add(name)
    return names


def _resolve_referenced_tables(
    refs: set[str],
    columns_by_table: dict[str, set[str]],
) -> set[str]:
    """Map SQL table refs to catalog full_table_id keys."""
    matched: set[str] = set()
    refs_l = {r.lower() for r in refs}
    for fq in columns_by_table:
        short = fq.rsplit(".", 1)[-1].lower()
        fq_l = fq.lower()
        if fq_l in refs_l or short in refs_l:
            matched.add(fq)
    return matched


def validate_against_schema(
    sql: str,
    *,
    allowed_tables: set[str],
    allowed_columns: set[str] | None = None,
    columns_by_table: dict[str, set[str]] | None = None,
) -> list[str]:
    """Return issues for unknown tables/columns or unparseable SQL."""
    issues: list[str] = []
    try:
        expr = parse_bigquery(sql)
    except ValueError as e:
        return [str(e)]

    ensure_read_only(expr)

    tables = table_refs(expr)
    allowed_tables_l = {t.lower() for t in allowed_tables}
    allowed_short = {t.rsplit(".", 1)[-1].lower() for t in allowed_tables_l}

    if allowed_tables_l:
        if not (tables & allowed_tables_l or tables & allowed_short):
            short_list = sorted(allowed_short)
            issues.append(
                "Query must use one of these tables: " + ", ".join(short_list)
            )

    idents = column_names(expr)
    if not idents:
        return issues

    if columns_by_table:
        referenced = _resolve_referenced_tables(tables, columns_by_table)
        if not referenced and len(columns_by_table) == 1:
            referenced = set(columns_by_table)
        elif not referenced:
            referenced = set(columns_by_table.keys()) & allowed_tables

        permitted: set[str] = set()
        for fq in referenced:
            permitted.update(c.lower() for c in columns_by_table.get(fq, set()))
        # CTE names and SELECT aliases are legal identifiers defined by the query
        # itself (Hex-style CTE SQL renames long columns to short aliases).
        permitted.update(defined_aliases(expr))

        unknown = sorted(c for c in idents if c not in permitted)
        if unknown:
            if len(referenced) == 1:
                fq = next(iter(referenced))
                short = fq.rsplit(".", 1)[-1]
                sample = ", ".join(f"`{c}`" for c in unknown[:6])
                issues.append(
                    f"Unknown column(s) for `{short}`: {sample}. "
                    "Use only columns listed under that table in the schema."
                )
            else:
                sample = ", ".join(f"`{c}`" for c in unknown[:6])
                issues.append(
                    f"Unknown column(s) for referenced tables: {sample}. "
                    "Each column must exist on a table used in FROM/JOIN."
                )
    elif allowed_columns:
        unknown = sorted(
            c for c in idents if c not in {x.lower() for x in allowed_columns}
        )
        if unknown:
            sample = ", ".join(f"`{c}`" for c in unknown[:6])
            issues.append(
                f"Unknown column(s) for this schema: {sample}. "
                "Use only column names from the schema."
            )

    return issues


def table_aliases(expr: exp.Expression) -> set[str]:
    """Table names and aliases available for qualified column references."""
    aliases: set[str] = set()
    for table in expr.find_all(exp.Table):
        if table.alias:
            aliases.add(str(table.alias).lower())
        name = str(table.name or "").strip("`").lower()
        if name:
            aliases.add(name)
            if "." in name:
                aliases.add(name.rsplit(".", 1)[-1])
    return aliases


def validate_string_literal_misuse(
    sql: str,
    columns_by_table: dict[str, set[str]] | None = None,
) -> list[str]:
    """Reject COUNT(DISTINCT 'col') and WHERE 'col' — quoted column names as strings."""
    if not columns_by_table:
        return []
    permitted: set[str] = set()
    for cols in columns_by_table.values():
        permitted.update(c.lower() for c in cols)

    try:
        expr = parse_bigquery(sql)
    except ValueError:
        return []

    issues: list[str] = []
    seen: set[str] = set()

    for count in expr.find_all(exp.Count):
        this = count.args.get("this")
        has_distinct = bool(count.args.get("distinct")) or isinstance(this, exp.Distinct)
        if not has_distinct:
            continue
        search_root = this if isinstance(this, exp.Distinct) else count
        for lit in search_root.find_all(exp.Literal):
            if not lit.is_string:
                continue
            name = str(lit.this).strip().lower()
            key = f"count:{name}"
            if key in seen:
                continue
            seen.add(key)
            if name in permitted or re.match(r"^[a-z][a-z0-9_]*$", name):
                issues.append(
                    f"COUNT(DISTINCT '{lit.this}') counts a string literal, not rows — "
                    f"use COUNT(DISTINCT `{lit.this}`) without quotes."
                )

    for where in expr.find_all(exp.Where):
        for lit in where.find_all(exp.Literal):
            if not lit.is_string:
                continue
            name = str(lit.this).strip().lower()
            key = f"where:{name}"
            if key in seen or name not in permitted:
                continue
            seen.add(key)
            issues.append(
                f"WHERE compares the string '{lit.this}' instead of column `{lit.this}` — "
                "remove quotes around column names."
            )

    return issues


def validate_table_qualifiers(
    sql: str,
    known_table_shorts: set[str],
) -> list[str]:
    """Catch table short names used as column prefixes without a FROM/JOIN alias."""
    issues: list[str] = []
    if "${" in sql:
        issues.append(
            "SQL contains unresolved ${model.column} placeholders from join hints. "
            "Expand them to `project.dataset.table`.column or alias.column."
        )
    try:
        expr = parse_bigquery(sql)
    except ValueError as e:
        return issues + [str(e)]

    aliases = table_aliases(expr)
    for col in expr.find_all(exp.Column):
        qual = col.table
        if qual is None:
            continue
        q = str(qual).strip("`").lower()
        if q in known_table_shorts and q not in aliases:
            issues.append(
                f"`{q}.column` is invalid — `{q}` is not a FROM/JOIN alias. "
                f"Use FROM `project.dataset.{q}` AS t then t.column, "
                f"or `project.dataset.{q}`.column with backticks."
            )
    return issues


def _fix_model_placeholders(sql: str, short_to_fq: dict[str, str]) -> str:
    """Expand leftover ${model.column} tokens using the workspace catalog."""
    if "${" not in sql:
        return sql

    def repl(match: re.Match[str]) -> str:
        token = match.group(1).strip()
        if "." in token:
            model, col = token.split(".", 1)
            model_l = model.strip().lower()
            fq = short_to_fq.get(model_l)
            if fq:
                return f"`{fq}`.{col.strip()}"
            return f"{model}.{col.strip()}"
        for fq in short_to_fq.values():
            return f"`{fq}`.{token}"
        return token

    return re.sub(r"\$\{([^}]+)\}", repl, sql)


def fix_table_qualifiers(sql: str, short_to_fq: dict[str, str]) -> str:
    """Rewrite bare table_short.column to `project.dataset.table`.column before BigQuery."""
    if not sql or not short_to_fq:
        return sql

    catalog = {k.lower(): v for k, v in short_to_fq.items()}
    bad_shorts: set[str] = set()
    try:
        expr = parse_bigquery(sql)
        aliases = table_aliases(expr)
        for col in expr.find_all(exp.Column):
            qual = col.table
            if qual is None:
                continue
            q = str(qual).strip("`").lower()
            if q in catalog and q not in aliases:
                bad_shorts.add(q)
    except ValueError:
        bad_shorts = set(catalog)

    out = sql
    for short in sorted(bad_shorts, key=len, reverse=True):
        fq = catalog[short]
        pattern = re.compile(rf"\b{re.escape(short)}\.([a-zA-Z_][a-zA-Z0-9_]*)", re.I)
        out = pattern.sub(rf"`{fq}`.\1", out)

    return _fix_model_placeholders(out, catalog)


def fix_canonical_table_paths(sql: str, short_to_fq: dict[str, str]) -> str:
    """Rewrite wrong project.dataset prefixes to canonical catalog paths (by table name)."""
    if not sql or not short_to_fq:
        return sql

    out = sql
    for short, fq in sorted(short_to_fq.items(), key=lambda x: -len(x[0])):
        fq_l = fq.lower()
        pattern = re.compile(
            rf"`?[\w-]+\.[\w-]+\.{re.escape(short)}`?",
            re.IGNORECASE,
        )

        def repl(match: re.Match[str], canonical: str = fq, canonical_l: str = fq_l) -> str:
            raw = match.group(0).strip("`")
            if raw.lower() == canonical_l:
                return f"`{canonical}`"
            return f"`{canonical}`"

        out = pattern.sub(repl, out)

    return out


def normalize_sql_tables(sql: str, short_to_fq: dict[str, str]) -> str:
    """Apply all table-path fixes before BigQuery execution."""
    sql = fix_canonical_table_paths(sql, short_to_fq)
    sql = fix_table_qualifiers(sql, short_to_fq)
    return sql
