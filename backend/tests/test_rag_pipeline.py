"""Tests for universal RAG compose path."""
from __future__ import annotations

from types import SimpleNamespace

from query_compose import compose_query_plan
from query_planner import analyze_question
from semantic_layer import reload_semantic_catalog, semantic_by_model_id
from term_resolver import resolve

DATASET = "kossip-helpers.academy_success_ai_analytics_worksapce"


def _table(short: str) -> SimpleNamespace:
    return SimpleNamespace(full_table_id=f"{DATASET}.{short}")


def setup_module() -> None:
    reload_semantic_catalog()


def test_nps_model_has_master_relation():
    sem = semantic_by_model_id("academy_nps_form_responses")
    assert sem is not None
    assert sem.relations
    targets = {r.target_model_id for r in sem.relations}
    assert "z_ccbp_academy_users_master_data" in targets


def test_resolve_nps_by_gender_sets_group_by():
    catalog = [
        _table("academy_nps_form_responses"),
        _table("z_ccbp_academy_users_master_data"),
    ]
    resolved = resolve("Average NPS by gender", catalog, catalog_tables=catalog)
    assert resolved is not None
    assert resolved.model_id == "academy_nps_form_responses"
    assert resolved.measure_id == "avg_nps_rating"
    assert "gender" in resolved.group_by


def test_compose_nps_by_gender_join_sql():
    catalog = [
        _table("academy_nps_form_responses"),
        _table("z_ccbp_academy_users_master_data"),
    ]
    plan = analyze_question("Average NPS by gender", catalog, catalog_tables=catalog)
    assert plan is not None
    sql = compose_query_plan(
        plan,
        "Average NPS by gender",
        catalog,
        {},
        catalog_tables=catalog,
    )
    assert sql is not None
    assert "academy_nps_form_responses" in sql
    assert "z_ccbp_academy_users_master_data" in sql
    assert "gender" in sql
    assert "AVG" in sql
    assert "GROUP BY" in sql


def test_compose_active_portal_users():
    catalog = [_table("z_ccbp_academy_users_master_data")]
    plan = analyze_question(
        "How many active users in learning portal now?",
        catalog,
        catalog_tables=catalog,
    )
    assert plan is not None
    sql = compose_query_plan(
        plan,
        "How many active users in learning portal now?",
        catalog,
        {},
        catalog_tables=catalog,
    )
    assert sql is not None
    assert "COUNT" in sql.upper()
    assert "z_ccbp_academy_users_master_data" in sql
