"""Tests for PatternMinerAgent with mock verification logs."""
from __future__ import annotations

import os

os.environ.setdefault("SYNC_WORKSPACE_FROM_DATASET", "0")

from agents.pattern_miner import (
    PatternMinerAgent,
    extract_keywords,
    keyword_overlap_score,
)
from db import Base, LearnedTemplate, SessionLocal, SqlVerificationLog, engine
from db import _migrate_learned_templates


def _ensure_tables() -> None:
    Base.metadata.create_all(
        engine,
        tables=[SqlVerificationLog.__table__, LearnedTemplate.__table__],
    )
    _migrate_learned_templates()


def _seed_logs(db) -> None:
    pairs = [
        (
            "How many active users in learning portal?",
            "SELECT COUNT(DISTINCT user_id) FROM master WHERE pause_status IS NULL",
            34791,
        ),
        (
            "How many active users in learning portal?",
            "SELECT COUNT(DISTINCT user_id) FROM master WHERE pause_status IS NULL",
            34791,
        ),
        (
            "How many placed users by state?",
            "SELECT state, COUNT(DISTINCT user_id) FROM placements GROUP BY state",
            28,
        ),
    ]
    for question, sql, rows in pairs:
        db.add(
            SqlVerificationLog(
                question=question,
                sql=sql,
                passed=True,
                result_row_count=rows,
                phase="execute",
                source="domain",
            )
        )
    db.commit()


def test_extract_keywords_strips_stop_words():
    kw = extract_keywords("How many active users in learning portal?")
    assert "how" not in kw
    assert "many" not in kw
    assert "active" in kw
    assert "users" in kw
    assert "learning" in kw
    assert "portal" in kw


def test_keyword_overlap_score():
    a = extract_keywords("active portal users count")
    b = extract_keywords("how many active users in learning portal")
    score = keyword_overlap_score(a, b)
    assert score >= 0.65


def test_find_matching_pattern_from_logs():
    _ensure_tables()
    db = SessionLocal()
    try:
        db.query(SqlVerificationLog).delete()
        db.query(LearnedTemplate).delete()
        db.commit()
        _seed_logs(db)

        miner = PatternMinerAgent(match_threshold=0.65)
        miner.refresh(db)

        match = miner.find_matching_pattern(
            "how many active users on learning portal now"
        )
        assert match is not None
        assert match.score >= 0.65
        assert "COUNT(DISTINCT user_id)" in match.sql
        assert match.source == "log"
    finally:
        db.close()


def test_promote_to_template_and_match():
    _ensure_tables()
    db = SessionLocal()
    try:
        db.query(LearnedTemplate).delete()
        db.commit()

        miner = PatternMinerAgent(match_threshold=0.65)
        miner.promote_to_template(
            db,
            question="placed users by state",
            sql="SELECT state, COUNT(*) FROM p GROUP BY state",
            row_count=28,
            name="placed_by_state",
        )

        match = miner.find_matching_pattern("how many placed users by state")
        assert match is not None
        assert match.source == "template"
        assert match.template_id is not None
        assert "GROUP BY state" in match.sql
    finally:
        db.close()


def test_no_match_below_threshold():
    _ensure_tables()
    db = SessionLocal()
    try:
        db.query(SqlVerificationLog).delete()
        db.commit()
        _seed_logs(db)

        miner = PatternMinerAgent(match_threshold=0.65)
        miner.refresh(db)

        match = miner.find_matching_pattern("what is the weather today")
        assert match is None
    finally:
        db.close()
