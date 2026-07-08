"""Shared helper for exhaustive, entity-level bucketed COUNT SQL.

Hand-written COUNTIF(...) buckets tend to count rows, not distinct entities, and
silently drop any row whose bucketing column is NULL/unmatched — so a dashboard's
buckets stop summing to its own stated total with no indication anything is missing.
This is the one place that pattern gets built correctly; callers describe buckets
declaratively instead of writing COUNTIF/COUNT(DISTINCT) by hand each time.
"""
from __future__ import annotations


def build_bucketed_count_sql(
    fq: str,
    id_col: str,
    total_alias: str,
    buckets: list[tuple[str, str]],
    *,
    where: list[str] | None = None,
    unrecorded_alias: str = "unrecorded",
    extra_select: list[str] | None = None,
) -> str:
    """SELECT a COUNT(DISTINCT id_col) total plus exhaustive, entity-level buckets.

    `buckets` is [(alias, condition_sql), ...]; conditions should be mutually
    exclusive (caller's responsibility — this cannot detect overlapping buckets).
    Every bucket and the total are counted at the same `id_col` granularity, and a
    catch-all `unrecorded_alias` bucket is added automatically — NOT any bucket
    condition, NULL-safe via IFNULL — so total == sum(bucket columns) always holds.
    No entity can be silently dropped just because its bucketing value is NULL or
    doesn't match any bucket.
    """
    safe_conditions = [f"IFNULL({cond}, FALSE)" for _, cond in buckets]

    select_lines = list(extra_select or [])
    select_lines.append(f"COUNT(DISTINCT `{id_col}`) AS {total_alias}")
    for alias, cond in buckets:
        select_lines.append(f"COUNT(DISTINCT IF({cond}, `{id_col}`, NULL)) AS {alias}")
    catch_all = "NOT (" + " OR ".join(safe_conditions) + ")" if safe_conditions else "TRUE"
    select_lines.append(f"COUNT(DISTINCT IF({catch_all}, `{id_col}`, NULL)) AS {unrecorded_alias}")

    where_sql = ("\nWHERE " + "\n  AND ".join(where)) if where else ""
    return "SELECT\n  " + ",\n  ".join(select_lines) + f"\nFROM `{fq}`{where_sql}"
