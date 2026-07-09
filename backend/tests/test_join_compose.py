"""Tests for join templates and date typo tolerance."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from join_compose import (
    compose_attendance_list_sql,
    compose_nps_by_gender_sql,
    compose_placed_by_state_sql,
    try_compose_join_sql,
)
from question_dates import resolve_relative_range
from sql_composer import _dedupe_clauses

DATASET = "kossip-helpers.academy_success_ai_analytics_worksapce"


def _table(short: str) -> SimpleNamespace:
    return SimpleNamespace(full_table_id=f"{DATASET}.{short}")


def _catalog() -> list[SimpleNamespace]:
    return [
        _table("z_academy_users_live_classes_attendance_and_time_spent_details"),
        _table("y_academy_users_placements_details"),
        _table("z_ccbp_academy_users_master_data"),
        _table("academy_nps_form_responses"),
        _table("z_ccbp_academy_users_jobs_details"),
    ]


def test_yesterday_typo_resolves():
    ref = date(2026, 7, 8)
    assert resolve_relative_range("which live class they attended yestarday", today=ref) == (
        date(2026, 7, 7),
        date(2026, 7, 7),
    )


def test_dedupe_where_clauses():
    clauses = [
        "(`pause_status` IS NULL)",
        "`pause_status` IS NULL",
        "(`learning_portal_onboarding_access_given_datetime` IS NOT NULL)",
        "`learning_portal_onboarding_access_given_datetime` IS NOT NULL",
    ]
    deduped = _dedupe_clauses(clauses)
    assert len(deduped) == 2


def test_attendance_list_sql_with_yesterday_typo():
    catalog = _catalog()
    sql = compose_attendance_list_sql(
        "which live class they attended yestarday",
        catalog,
    )
    assert sql is not None
    assert "GROUP BY" in sql
    assert "cohort_name" in sql
    assert "DATE(" in sql or "DATE '" in sql
    assert "attendance_status" in sql


def test_placed_by_state_join_sql():
    catalog = _catalog()
    sql = compose_placed_by_state_sql("how many placed users by state", catalog)
    assert sql is not None
    assert "LEFT JOIN" in sql
    assert "academy_user_profile_basic_details" in sql
    assert "COALESCE(NULLIF(TRIM(UPPER" in sql
    assert "GROUP BY" in sql
    assert "placed_users" in sql


def test_nps_by_gender_join_sql():
    catalog = _catalog()
    sql = compose_nps_by_gender_sql("Average NPS by gender", catalog)
    assert sql is not None
    assert "academy_nps_form_responses" in sql
    assert "z_ccbp_academy_users_master_data" in sql
    assert "INNER JOIN" in sql
    assert "gender" in sql
    assert "AVG" in sql
    assert "GROUP BY" in sql


def test_try_compose_join_sql_priority():
    catalog = _catalog()
    sql = try_compose_join_sql("placed users by state", catalog)
    assert sql is not None
    assert "academy_user_profile_basic_details" in sql
