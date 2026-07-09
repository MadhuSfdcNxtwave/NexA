"""Tests for model-facing query planner and compose strategies."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from query_compose import compose_query_plan
from query_planner import (
    classify_intent,
    extract_topic,
    is_nps_topic_feedback_question,
    try_build_query_plan,
)
from semantic_layer import reload_semantic_catalog


NPS_Q = (
    "What is the feedback we got till now on notice board feature in NPS form responses?"
)
DATASET = "kossip-helpers.academy_success_ai_analytics_worksapce"


def _nps_tables() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            full_table_id=f"{DATASET}.academy_nps_form_responses",
            included_for_ai=True,
            column_hints_json="{}",
            column_descriptions_json="{}",
            ai_profile_json="{}",
        ),
        SimpleNamespace(
            full_table_id=f"{DATASET}.nps_form_responses_nov_and_dec_2025",
            included_for_ai=True,
            column_hints_json="{}",
            column_descriptions_json="{}",
            ai_profile_json="{}",
        ),
        SimpleNamespace(
            full_table_id=f"{DATASET}.users_contextual_feedback_details",
            included_for_ai=True,
            column_hints_json="{}",
            column_descriptions_json="{}",
            ai_profile_json="{}",
        ),
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
    }


class QueryPlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        reload_semantic_catalog()

    def test_classify_nps_topic_intent(self) -> None:
        self.assertEqual(classify_intent(NPS_Q), "topic_search")
        self.assertTrue(is_nps_topic_feedback_question(NPS_Q))
        self.assertEqual(extract_topic(NPS_Q), "notice board")

    def test_aggregate_question_not_topic(self) -> None:
        q = "What is the average NPS score by gender?"
        self.assertNotEqual(classify_intent(q), "topic_search")

    def test_nps_topic_plan_uses_union_model(self) -> None:
        tables = _nps_tables()
        plan = try_build_query_plan(
            NPS_Q,
            tables,
            catalog_tables=tables,
            columns_by_table=_nps_columns(),
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.intent, "topic_search")
        self.assertEqual(plan.model_id, "nps_all_form_responses")
        self.assertEqual(plan.topic, "notice board")

    def test_nps_topic_sql_union_and_regexp(self) -> None:
        tables = _nps_tables()
        cols = _nps_columns()
        plan = try_build_query_plan(NPS_Q, tables, catalog_tables=tables, columns_by_table=cols)
        self.assertIsNotNone(plan)
        sql = compose_query_plan(plan, NPS_Q, tables, cols, catalog_tables=tables)
        self.assertIsNotNone(sql)
        assert sql is not None
        low = sql.lower()
        self.assertIn("union all", low)
        self.assertIn("academy_nps_form_responses", low)
        self.assertIn("nps_form_responses_nov_and_dec_2025", low)
        self.assertIn("regexp_contains", low)
        self.assertIn("notice ?board", sql)
        self.assertIn("notice_board_mentions", sql)
        self.assertIn("endorsed AS", sql)
        self.assertIn("old AS", sql)
        self.assertNotIn("users_contextual_feedback_details", low)
        self.assertNotIn("count(distinct", low)

    def test_contextual_feedback_not_picked_for_nps_topic(self) -> None:
        from measure_router import try_build_measure_plan

        tables = _nps_tables()
        plan = try_build_measure_plan(NPS_Q, tables, catalog_tables=tables)
        self.assertIsNone(plan)

    def test_planner_sql_passes_validation_with_one_workspace_table(self) -> None:
        from ask_pipeline import _column_hints_map, _infer_hints_for_tables, _try_planner_sql

        tables = [_nps_tables()[0]]
        pool = _nps_tables()
        cols = _nps_columns()
        inferred, _ = _infer_hints_for_tables(tables)
        hints = _column_hints_map(tables)
        sql, reason, picked = _try_planner_sql(
            NPS_Q, tables, hints, inferred, cols, catalog_tables=pool
        )
        self.assertIsNotNone(sql, msg=reason)
        assert sql is not None
        self.assertIn("UNION ALL", sql)
        self.assertIn("nps_form_responses_nov_and_dec_2025", sql)
        shorts = {t.full_table_id.rsplit(".", 1)[-1] for t in (picked or [])}
        self.assertIn("nps_form_responses_nov_and_dec_2025", shorts)

    def test_stale_single_table_nps_topic_cache_rejected(self) -> None:
        from memory_lookup import stored_answer_matches_question

        stale_sql = (
            "SELECT `user_id`, `rating_on_scale_of_0_to_10` AS nps_rating "
            "FROM `kossip-helpers.academy_success_ai_analytics_worksapce.academy_nps_form_responses` "
            "WHERE REGEXP_CONTAINS(CONCAT(COALESCE(`please_share_a_short_noteA_about_what_worked_well_for_you`, ''), "
            r"' '), r'(?i)(notice board)') LIMIT 200"
        )
        self.assertFalse(
            stored_answer_matches_question(
                NPS_Q,
                sql=stale_sql,
                columns=["user_id", "nps_rating", "feedback_note"],
                rows=[{"user_id": "u1", "nps_rating": 9}],
            )
        )


if __name__ == "__main__":
    unittest.main()
