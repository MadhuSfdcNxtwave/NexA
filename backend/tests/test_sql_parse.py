"""Tests for SQL normalization helpers."""
from __future__ import annotations

from sql_parse import (
    normalize_llm_sql,
    normalize_user_id_joins,
    strip_sql_line_comments,
)


def test_strip_line_comments_inside_case():
    raw = (
        "CASE WHEN rating >= 9 THEN 1 -- Promoter "
        "WHEN rating <= 6 THEN -1 -- Detractor ELSE 0 END"
    )
    cleaned = strip_sql_line_comments(raw)
    assert "--" not in cleaned
    assert "WHEN rating <= 6" in cleaned
    assert "ELSE 0 END" in cleaned


def test_strip_line_comments_preserves_quoted_dashes():
    raw = "SELECT 'a--b' AS x -- trailing"
    cleaned = strip_sql_line_comments(raw)
    assert "'a--b'" in cleaned
    assert "trailing" not in cleaned


def test_normalize_llm_sql_strips_comments_before_parse():
    raw = (
        "SELECT CASE WHEN x >= 9 THEN 1 -- promoter "
        "WHEN x <= 6 THEN -1 ELSE 0 END AS nps FROM t"
    )
    cleaned = normalize_llm_sql(raw)
    assert "promoter" not in cleaned
    assert "WHEN x <= 6" in cleaned


def test_normalize_llm_sql_preserves_question_order_column():
    raw = (
        "SELECT `user_id`, `question_order`, `feedback_id` "
        "FROM `p.d.users_contextual_feedback_details` "
        "ORDER BY `submitted_date` DESC"
    )
    cleaned = normalize_llm_sql(raw)
    assert "`question_order`" in cleaned
    assert "question_ order" not in cleaned


def test_normalize_llm_sql_still_splits_glued_clauses():
    raw = "SELECT * FROM tWHERE x = 1ORDER BY y LIMIT 10"
    cleaned = normalize_llm_sql(raw)
    assert "FROM t WHERE" in cleaned or "t WHERE" in cleaned
    assert "1 ORDER BY" in cleaned or "1 ORDER" in cleaned


def test_normalize_user_id_joins_both_sides():
    sql = "LEFT JOIN p ON s.`user_id` = p.`user_id`"
    out = normalize_user_id_joins(sql)
    assert "REPLACE(s.`user_id`, '-', '') = REPLACE(p.`user_id`, '-', '')" in out


def test_normalize_user_id_joins_one_sided():
    sql = "ON REPLACE(n.`user_id`, '-', '') = m.`user_id`"
    out = normalize_user_id_joins(sql)
    assert "REPLACE(n.`user_id`, '-', '') = REPLACE(m.`user_id`, '-', '')" in out


def test_normalize_user_id_joins_hex_hints():
    hint = '${user_id} = ${academy_user_profile_basic_details.user_id}'
    out = normalize_user_id_joins(hint)
    assert "REPLACE(${user_id}, '-', '')" in out
    assert "REPLACE(${academy_user_profile_basic_details.user_id}, '-', '')" in out


def test_normalize_user_id_joins_preserves_extra_and():
    sql = (
        "ON s.`user_id` = p.`user_id` "
        "AND DATE_TRUNC(s.`month`, MONTH) = p.`month`"
    )
    out = normalize_user_id_joins(sql)
    assert "REPLACE(s.`user_id`, '-', '') = REPLACE(p.`user_id`, '-', '')" in out
    assert "DATE_TRUNC(s.`month`, MONTH) = p.`month`" in out
