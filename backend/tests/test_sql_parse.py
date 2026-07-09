"""Tests for SQL normalization helpers."""
from __future__ import annotations

from sql_parse import normalize_llm_sql, strip_sql_line_comments


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
