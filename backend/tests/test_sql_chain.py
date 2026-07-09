"""Tests for SQL chain planning and compound compose."""
from __future__ import annotations

from types import SimpleNamespace

from query_compose import compose_query_plan
from query_planner import analyze_question
from semantic_layer import reload_semantic_catalog
from sql_chain import needs_single_join_compound, plan_steps

DATASET = "kossip-helpers.academy_success_ai_analytics_worksapce"


def _table(short: str) -> SimpleNamespace:
    return SimpleNamespace(full_table_id=f"{DATASET}.{short}")


def setup_module() -> None:
    reload_semantic_catalog()


def test_compound_attendance_portal_is_single_join():
    q = "How many users attended live classes yesterday and have learning portal access?"
    assert needs_single_join_compound(q)
    assert plan_steps(q, "") == []


def test_compound_attendance_portal_sql():
    catalog = [
        _table("z_academy_users_live_classes_attendance_and_time_spent_details"),
        _table("z_ccbp_academy_users_master_data"),
    ]
    q = "How many users attended live classes yesterday and have learning portal access?"
    plan = analyze_question(q, catalog, catalog_tables=catalog)
    assert plan is not None
    assert plan.intent == "compound"
    sql = compose_query_plan(plan, q, catalog, {}, catalog_tables=catalog)
    assert sql is not None
    assert "JOIN" in sql.upper()
    assert "attendance_status" in sql
    assert "pause_status" in sql
    assert "COUNT(DISTINCT" in sql.upper()


def test_and_split_plans_chain_for_independent_metrics():
    q = "How many active users in learning portal and how many unique NPS responses"
    assert not needs_single_join_compound(q)
    steps = plan_steps(q, "")
    assert len(steps) >= 2
    assert any("portal" in s["question"].lower() for s in steps)
    assert any("nps" in s["question"].lower() for s in steps)


def test_month_compare_chain():
    q = "Average NPS in June and average NPS in July"
    steps = plan_steps(q, "")
    assert len(steps) >= 2
