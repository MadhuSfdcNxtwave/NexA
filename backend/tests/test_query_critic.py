"""Tests for QueryCriticAgent validation rules."""
from __future__ import annotations

from unittest.mock import patch

from agents.query_critic import QueryCriticAgent

critic = QueryCriticAgent()


def _validate(sql: str, question: str) -> list[str]:
    with patch("bq.dry_run_bytes", return_value=0):
        return critic.validate(sql, question)


def test_select_only_check():
    issues = _validate("DELETE FROM t", "how many users")
    assert any("SELECT" in i for i in issues)


def test_date_filter_required():
    issues = _validate(
        "SELECT COUNT(*) FROM t",
        "how many users attended yesterday",
    )
    assert any("date filter" in i.lower() for i in issues)


def test_breakdown_needs_group_by():
    issues = _validate(
        "SELECT COUNT(DISTINCT user_id) FROM t",
        "placed users by state",
    )
    assert any("GROUP BY" in i for i in issues)


def test_list_not_count():
    issues = _validate(
        "SELECT COUNT(DISTINCT user_id) FROM t",
        "which live class they attended yesterday",
    )
    assert any("list" in i.lower() for i in issues)
