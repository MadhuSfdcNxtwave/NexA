"""TemplatePromoterAgent — promote repeated successful queries to learned templates."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select

import config
from agents.pattern_miner import get_pattern_miner
from db import LearnedTemplate, SessionLocal, SqlVerificationLog


class TemplatePromoterAgent:
    def __init__(self, *, min_occurrences: int | None = None, lookback_days: int = 30):
        self._min_occurrences = min_occurrences or int(
            getattr(config, "TEMPLATE_PROMOTER_THRESHOLD", 3)
        )
        self._lookback_days = lookback_days

    def run_promotion_cycle(self, db: Any | None = None) -> list[str]:
        own_session = db is None
        if own_session:
            db = SessionLocal()
        promoted: list[str] = []
        try:
            cutoff = datetime.utcnow() - timedelta(days=self._lookback_days)
            existing = {
                (row or "").strip().lower()
                for row in db.scalars(select(LearnedTemplate.trigger_question)).all()
            }

            rows = db.execute(
                select(
                    SqlVerificationLog.question,
                    SqlVerificationLog.sql,
                    func.count().label("freq"),
                    func.max(SqlVerificationLog.result_row_count).label("row_count"),
                )
                .where(SqlVerificationLog.passed.is_(True))
                .where(SqlVerificationLog.created_at >= cutoff)
                .where(SqlVerificationLog.question != "")
                .where(SqlVerificationLog.sql != "")
                .group_by(SqlVerificationLog.question, SqlVerificationLog.sql)
                .having(func.count() >= self._min_occurrences)
            ).all()

            miner = get_pattern_miner()
            for row in rows:
                question = (row.question or "").strip()
                sql = (row.sql or "").strip()
                if not question or not sql:
                    continue
                norm = question.lower().rstrip("?.!").strip()
                if norm in existing:
                    continue
                name = _template_name(question)
                miner.promote_to_template(
                    db,
                    question=question,
                    sql=sql,
                    row_count=int(row.row_count) if row.row_count else None,
                    name=name,
                )
                promoted.append(question)
                existing.add(norm)
            return promoted
        finally:
            if own_session and db is not None:
                db.close()


def _template_name(question: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", question.lower())[:60].strip("_")
    return slug or "learned_template"
