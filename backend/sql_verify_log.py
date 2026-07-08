"""Persist SQL verification attempts for audit and future tuning."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import config
import llm
from db import SqlVerificationLog


@dataclass
class SqlAuditContext:
    db: Any
    project_id: int | None = None
    user_id: int | None = None


def log_sql_verification(
    audit: SqlAuditContext | None,
    *,
    question: str,
    sql: str,
    attempt: int,
    phase: str,
    passed: bool,
    issues: list[str] | None = None,
    source: str = "",
    plan: dict[str, Any] | None = None,
) -> None:
    if not audit or not audit.db:
        return
    payload: dict[str, Any] = {"issues": issues or []}
    if plan:
        payload["plan"] = plan
    row = SqlVerificationLog(
        project_id=audit.project_id,
        user_id=audit.user_id,
        question=(question or "")[:4000],
        sql=(sql or "")[:8000],
        attempt=max(1, attempt),
        phase=(phase or "unknown")[:40],
        passed=bool(passed),
        issues_json=json.dumps(payload),
        source=(source or "")[:40],
        llm_provider=config.SQL_PROVIDER or config.FETCH_PROVIDER,
        llm_model=config.FETCH_MODEL,
    )
    audit.db.add(row)
    try:
        audit.db.commit()
    except Exception:
        audit.db.rollback()


def llm_review_sql(
    question: str,
    sql: str,
    schema_text: str,
    project_context: str = "",
    *,
    audit: SqlAuditContext | None = None,
    attempt: int = 1,
    source: str = "",
) -> dict:
    """Ask the LLM whether SQL correctly answers the question; log the outcome."""
    review = llm.verify_sql(question, sql, schema_text, project_context)
    log_sql_verification(
        audit,
        question=question,
        sql=sql,
        attempt=attempt,
        phase="llm_review",
        passed=bool(review.get("pass")),
        issues=review.get("issues") or [],
        source=source,
    )
    return review
