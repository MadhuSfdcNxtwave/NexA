"""Golden SQL eval — planner + compose + intent validation without BigQuery."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from query_compose import compose_query_plan
from query_planner import analyze_question, sql_plan_shape_mismatch_reason
from semantic_layer import reload_semantic_catalog

DATASET = "kossip-helpers.academy_success_ai_analytics_worksapce"
GOLDEN_PATH = Path(__file__).resolve().parent / "golden_questions.yaml"


def _table(full_short: str) -> SimpleNamespace:
    return SimpleNamespace(
        full_table_id=f"{DATASET}.{full_short}",
        included_for_ai=True,
        column_hints_json="{}",
        column_descriptions_json="{}",
        ai_profile_json="{}",
    )


def _catalog() -> list[SimpleNamespace]:
    return [
        _table("academy_nps_form_responses"),
        _table("nps_form_responses_nov_and_dec_2025"),
        _table("users_contextual_feedback_details"),
        _table("academy_users_day_and_page_wise_time_spent_details"),
        _table("z_ccbp_academy_users_master_data"),
        _table("z_academy_users_live_classes_attendance_and_time_spent_details"),
        _table("y_academy_user_daily_engagement_time_spent"),
    ]


def _nps_columns() -> dict[str, set[str]]:
    return {
        f"{DATASET}.academy_nps_form_responses": {
            "user_id",
            "form_submission_datetime",
            "form_submission_month",
            "rating_on_scale_of_0_to_10",
            "please_share_a_short_noteA_about_what_worked_well_for_you",
            "what_aspects_helped_you_feel_job_ready",
            "what_improvements_would_help_you_feel_more_job_ready",
            "what_limited_the_value_you_expected_from_the_program",
            "what_improvements_would_help_you_recommend_nxtwave_with_more_confidence",
            "please_share_what_specifically_helped_in_your_job_readiness_journey",
            "what_aspects_of_the_program_made_you_feel_confident_to_recommend_us",
            "do_you_feel_the_program_delivers_value_for_the_time_and_money_you_have_invested",
        },
        f"{DATASET}.nps_form_responses_nov_and_dec_2025": {
            "user_id",
            "submitted_at",
            "on_a_scale_of_0_10_how_likely_are_you_to_recommend_nxtwaves_academy_program_to_a_friend_or_peer",
            "please_share_a_short_note_about_what_worked_well_for_you",
            "what_aspects_helped_you_feel_job_ready",
            "what_improvements_would_help_you_feel_more_job_ready",
            "what_limited_the_value_you_expected_from_the_program",
            "what_improvements_would_help_you_recommend_nxtwave_with_more_confidence",
            "please_share_what_specifically_helped_in_your_job_readiness_journey",
            "what_aspects_of_the_program_made_you_feel_confident_to_recommend_us",
            "do_you_feel_the_program_delivers_value_for_the_time_and_money_you_have_invested",
        },
        f"{DATASET}.academy_users_day_and_page_wise_time_spent_details": {
            "user_id",
            "lp_status",
            "time_spent_date",
            "time_spent_page",
            "time_spent_in_mins",
        },
    }


class GoldenSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        reload_semantic_catalog()
        with GOLDEN_PATH.open(encoding="utf-8") as fh:
            cls.cases = yaml.safe_load(fh)

    def _run_case(self, case: dict) -> tuple[str | None, object | None]:
        question = case["question"]
        catalog = _catalog()
        cols = _nps_columns()
        plan = analyze_question(question, catalog, catalog_tables=catalog, columns_by_table=cols)
        if not plan:
            return None, None
        if case.get("skip_compose") or plan.intent == "compound":
            return None, plan
        sql = compose_query_plan(plan, question, catalog, cols, catalog_tables=catalog)
        return sql, plan

    def test_golden_cases(self) -> None:
        for case in self.cases:
            with self.subTest(question=case["question"][:60]):
                sql, plan = self._run_case(case)
                if case.get("expect_intent"):
                    self.assertIsNotNone(plan, case["question"])
                    assert plan is not None
                    self.assertEqual(plan.intent, case["expect_intent"])
                if case.get("expect_model"):
                    self.assertIsNotNone(plan)
                    assert plan is not None
                    self.assertEqual(plan.model_id, case["expect_model"])
                if case.get("expect_measure"):
                    self.assertIsNotNone(plan)
                    assert plan is not None
                    self.assertEqual(plan.measure_id, case["expect_measure"])

                if not sql and case.get("expect_sql_contains"):
                    self.fail(f"No SQL composed for: {case['question']}")

                if sql:
                    sql_l = sql.lower()
                    for table in case.get("expect_tables", []):
                        self.assertIn(table.lower(), sql_l, f"missing table {table}")
                    for fragment in case.get("expect_sql_contains", []):
                        self.assertRegex(
                            sql,
                            fragment,
                            f"missing fragment `{fragment}` in SQL",
                        )
                    for bad in case.get("expect_sql_not_contains", []):
                        self.assertNotIn(bad.lower(), sql_l, f"forbidden `{bad}` in SQL")
                    if plan:
                        self.assertIsNone(
                            sql_plan_shape_mismatch_reason(case["question"], sql, plan),
                            f"plan shape validation failed for: {case['question']}",
                        )

    def test_analyze_question_viz_hint(self) -> None:
        plan = analyze_question(
            "How many active users in learning portal now?",
            _catalog(),
            catalog_tables=_catalog(),
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.viz_hint, "scalar")
        self.assertEqual(plan.measure_id, "active_learning_portal_users")


if __name__ == "__main__":
    unittest.main()
