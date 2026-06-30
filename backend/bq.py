"""BigQuery access. Schema is built per-project from that project's
configured table list, so each workspace only ever sees its own tables.

Security: the real guard is the service account's read-only IAM role. The
SELECT-only check here is a secondary net.
"""
from __future__ import annotations

import re

import pandas as pd
from google.cloud import bigquery

import config

_client: bigquery.Client | None = None


def client() -> bigquery.Client:
    global _client
    if _client is None:
        _client = bigquery.Client(
            project=config.GCP_PROJECT or None, location=config.BQ_LOCATION
        )
    return _client


def schema_for_tables(full_table_ids: list[str], join_hints: str = "") -> str:
    """Build the model-facing schema text for a specific set of tables."""
    bq = client()
    blocks: list[str] = []
    for fq in full_table_ids:
        try:
            tbl = bq.get_table(fq)  # accepts "project.dataset.table"
        except Exception as e:  # missing table / no access — surface, don't crash
            blocks.append(f"TABLE `{fq}`  (could not load: {e})")
            continue
        cols = []
        for f in tbl.schema:
            desc = f" -- {f.description}" if f.description else ""
            cols.append(f"  {f.name} {f.field_type}{desc}")
        tdesc = f"  # {tbl.description}\n" if tbl.description else ""
        blocks.append(f"TABLE `{fq}`\n{tdesc}" + "\n".join(cols))

    schema = "\n\n".join(blocks) if blocks else "(no tables configured for this project)"
    if join_hints.strip():
        schema += "\n\n# How these tables relate (use these for JOINs)\n" + join_hints.strip()
    return schema


_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


def _strip_sql(sql: str) -> str:
    sql = re.sub(r"^```(?:sql)?|```$", "", sql.strip(), flags=re.IGNORECASE | re.MULTILINE)
    return sql.strip().rstrip(";").strip()


def validate_select_only(sql: str) -> str:
    sql = _strip_sql(sql)
    head = sql.lstrip("(").lstrip().upper()
    if not (head.startswith("SELECT") or head.startswith("WITH")):
        raise ValueError("Only SELECT / WITH queries are allowed.")
    if _FORBIDDEN.search(sql):
        raise ValueError("Query contains a non-read keyword and was blocked.")
    if ";" in sql:
        raise ValueError("Multiple statements are not allowed.")
    return sql


def dry_run_bytes(sql: str) -> int:
    cfg = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    return client().query(sql, job_config=cfg).total_bytes_processed or 0


def run_query(sql: str, row_limit: int = 2000) -> pd.DataFrame:
    sql = validate_select_only(sql)
    cfg = bigquery.QueryJobConfig(maximum_bytes_billed=config.MAX_BYTES_BILLED)
    df = client().query(sql, job_config=cfg).result().to_dataframe()
    return df.head(row_limit)
