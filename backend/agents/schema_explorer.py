"""SchemaExplorerAgent — rich table context for SQL generation.

Replaces the thin schema block with: columns + types + descriptions,
real sample values for STRING filter columns, row counts, and the
table's actual date range. Cached per table with a TTL so repeated
questions don't re-scan BigQuery.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

_DATE_TYPES = {"DATE", "DATETIME", "TIMESTAMP"}

# Prefer business-meaningful date columns over audit columns.
_DATE_COLUMN_PRIORITY = (
    "slot_date",
    "date_of_placement",
    "form_submission_month",
    "applied_datetime",
    "form_submission_datetime",
    "attended_datetime",
    "date",
    "event_date",
    "created_at",
    "date_of_entry",
)

# Skip free-text / identifier-like STRING columns when sampling values —
# their distinct values are useless as WHERE-clause hints.
_SKIP_SAMPLE_RE = re.compile(
    r"(_id$|^id$|uuid|email|phone|mobile|name$|url|link|comment|feedback|note|"
    r"description|answer|question|text|address|reason|remark|message|title)",
    re.I,
)

_MAX_SAMPLE_COLUMNS = 6
_MAX_SAMPLE_VALUES = 6
_SAMPLE_VALUE_MAX_LEN = 40


@dataclass
class TableContext:
    full_table_id: str
    columns: list[dict[str, str]] = field(default_factory=list)
    sample_values: dict[str, list[str]] = field(default_factory=dict)
    row_count: int | None = None
    date_column: str | None = None
    date_min: str | None = None
    date_max: str | None = None
    error: str | None = None

    def to_text(self) -> str:
        lines = [f"TABLE: {self.full_table_id}"]
        if self.columns:
            lines.append("Columns:")
            for col in self.columns:
                desc = f" -- {col['description']}" if col.get("description") else ""
                lines.append(f"  {col['name']} ({col['type']}){desc}")
        if self.sample_values:
            lines.append("Sample values (use EXACTLY in WHERE clauses):")
            for name, values in self.sample_values.items():
                lines.append(f"  {name}: {values}")
        meta: list[str] = []
        if self.row_count is not None:
            meta.append(f"Row count: {self.row_count:,}")
        if self.date_min and self.date_max:
            meta.append(f"Date range: {self.date_min} -> {self.date_max}")
        if meta:
            lines.append(" | ".join(meta))
        if self.date_column:
            lines.append(f"Primary date column: {self.date_column}")
        if self.error:
            lines.append(f"(sampling skipped: {self.error})")
        return "\n".join(lines)


class SchemaExplorerAgent:
    """Builds and caches rich schema context for a set of BigQuery tables."""

    def __init__(self, bq_client: Any = None, *, ttl_hours: float = 6.0):
        self._client = bq_client
        self._ttl = timedelta(hours=ttl_hours)
        self._cache: dict[str, tuple[datetime, TableContext]] = {}

    # -- public API ---------------------------------------------------------

    def build_context(self, table_ids: list[str]) -> str:
        """Formatted context block for the given tables (cached per table)."""
        blocks = [self.explore_table(fq).to_text() for fq in table_ids]
        return "\n\n".join(blocks)

    def explore_table(self, full_table_id: str) -> TableContext:
        cached = self._cache.get(full_table_id)
        if cached and datetime.utcnow() - cached[0] < self._ttl:
            return cached[1]
        ctx = self._explore(full_table_id)
        self._cache[full_table_id] = (datetime.utcnow(), ctx)
        return ctx

    def invalidate(self, full_table_id: str | None = None) -> None:
        if full_table_id is None:
            self._cache.clear()
        else:
            self._cache.pop(full_table_id, None)

    # -- internals ----------------------------------------------------------

    def _bq(self):
        if self._client is not None:
            return self._client
        import bq

        return bq.client()

    def _explore(self, full_table_id: str) -> TableContext:
        ctx = TableContext(full_table_id=full_table_id)
        try:
            tbl = self._bq().get_table(full_table_id)
        except Exception as exc:
            ctx.error = str(exc)[:200]
            return ctx

        ctx.row_count = tbl.num_rows
        ctx.columns = [
            {
                "name": f.name,
                "type": f.field_type,
                "description": (f.description or "").strip(),
            }
            for f in tbl.schema
        ]

        ctx.date_column = self._pick_date_column(tbl.schema)
        string_cols = self._pick_sample_columns(tbl.schema)

        if string_cols or ctx.date_column:
            try:
                self._run_stats_query(ctx, string_cols)
            except Exception as exc:
                ctx.error = str(exc)[:200]
        return ctx

    @staticmethod
    def _pick_date_column(schema: list[Any]) -> str | None:
        date_cols = {f.name: f.field_type for f in schema if f.field_type in _DATE_TYPES}
        if not date_cols:
            return None
        for preferred in _DATE_COLUMN_PRIORITY:
            if preferred in date_cols:
                return preferred
        return next(iter(date_cols))

    @staticmethod
    def _pick_sample_columns(schema: list[Any]) -> list[str]:
        out: list[str] = []
        for f in schema:
            if f.field_type != "STRING" or f.mode == "REPEATED":
                continue
            if _SKIP_SAMPLE_RE.search(f.name):
                continue
            out.append(f.name)
            if len(out) >= _MAX_SAMPLE_COLUMNS:
                break
        return out

    def _run_stats_query(self, ctx: TableContext, string_cols: list[str]) -> None:
        """One query: distinct sample values for STRING cols + date min/max."""
        selects: list[str] = []
        for col in string_cols:
            selects.append(
                f"ARRAY_AGG(DISTINCT `{col}` IGNORE NULLS LIMIT {_MAX_SAMPLE_VALUES})"
                f" AS `sv_{col}`"
            )
        if ctx.date_column:
            selects.append(f"CAST(MIN(`{ctx.date_column}`) AS STRING) AS `dt_min`")
            selects.append(f"CAST(MAX(`{ctx.date_column}`) AS STRING) AS `dt_max`")
        if ctx.row_count in (None, 0):
            selects.append("COUNT(*) AS `rc`")
        if not selects:
            return

        sql = f"SELECT {', '.join(selects)} FROM `{ctx.full_table_id}`"
        import bq

        df = bq.run_query(sql, row_limit=1)
        if df.empty:
            return
        row = df.iloc[0]

        for col in string_cols:
            raw = row.get(f"sv_{col}")
            if raw is None:
                continue
            values = [
                str(v)[:_SAMPLE_VALUE_MAX_LEN]
                for v in list(raw)
                if v is not None and str(v).strip()
            ]
            if values:
                ctx.sample_values[col] = sorted(values)

        if ctx.date_column:
            dt_min, dt_max = row.get("dt_min"), row.get("dt_max")
            if dt_min is not None:
                ctx.date_min = str(dt_min)[:10]
            if dt_max is not None:
                ctx.date_max = str(dt_max)[:10]

        if ctx.row_count in (None, 0) and row.get("rc") is not None:
            ctx.row_count = int(row["rc"])


_default_agent: SchemaExplorerAgent | None = None


def get_schema_explorer() -> SchemaExplorerAgent:
    """Process-wide singleton so the TTL cache is shared across requests."""
    global _default_agent
    if _default_agent is None:
        import config

        ttl = float(getattr(config, "SCHEMA_CACHE_TTL_HOURS", 6))
        _default_agent = SchemaExplorerAgent(ttl_hours=ttl)
    return _default_agent
