"""Tests for semantic measure routing."""
from __future__ import annotations

from types import SimpleNamespace

from measure_router import try_build_measure_plan

DATASET = "kossip-helpers.academy_success_ai_analytics_worksapce"


def _table(short: str) -> SimpleNamespace:
    return SimpleNamespace(full_table_id=f"{DATASET}.{short}")


def test_avg_nps_question_does_not_fall_back_to_unrelated_count():
    catalog = [
        _table("acad_new_live_classes_batch_registration_form_responses"),
        _table("academy_nps_form_responses"),
    ]
    plan = try_build_measure_plan(
        "Average NPS by gender",
        catalog,
        catalog_tables=catalog,
    )
    assert plan is not None
    assert plan.table_short == "academy_nps_form_responses"
    assert plan.measure.id == "avg_nps_rating"
