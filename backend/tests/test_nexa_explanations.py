"""Tests for Jarvis-style knowledge answers and NPS improvement breakdown."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from metrics_registry import glossary_context_for_question, match_glossary_terms, reload_registry
from memory_lookup import sql_intent_mismatch_reason
from nps_sql import is_nps_improvement_question, try_build_nps_sql
from question_intent import detect_intent, is_knowledge_question, question_wants_breakdown
from query_planner import classify_intent
from term_resolver import resolve

DATASET = "kossip-helpers.academy_success_ai_analytics_worksapce"


def _table(short: str) -> SimpleNamespace:
    return SimpleNamespace(
        full_table_id=f"{DATASET}.{short}",
        included_for_ai=True,
        description="",
        column_descriptions_json="{}",
        column_hints_json="{}",
        ai_overview="",
    )


class NexaExplanationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        reload_registry()

    def test_knowledge_intent_for_nps_definition(self):
        self.assertTrue(is_knowledge_question("what is NPS"))
        self.assertEqual(detect_intent("what is NPS", has_thread_history=False), "knowledge_query")

    def test_data_intent_for_nps_metric(self):
        self.assertFalse(is_knowledge_question("what is nps score for march 2026"))
        self.assertEqual(
            detect_intent("how many nps responses in march", has_thread_history=False),
            "data_query",
        )

    def test_which_activity_is_breakdown(self):
        q = "which activity improved nps score"
        self.assertTrue(question_wants_breakdown(q))
        self.assertEqual(classify_intent(q), "breakdown")

    def test_glossary_matches_nps_improvement(self):
        q = "which activity improved nps score"
        matches = match_glossary_terms(q)
        self.assertTrue(any(t.id == "nps_improvement_by_aspect" for t, _ in matches))

    def test_nps_improvement_sql_has_group_by(self):
        q = "which activity improved nps score"
        tables = [_table("academy_nps_form_responses")]
        cols = {
            f"{DATASET}.academy_nps_form_responses": {
                "user_id",
                "rating_on_scale_of_0_to_10",
                "what_aspects_of_the_program_made_you_feel_confident_to_recommend_us",
                "form_submission_month",
            },
        }
        sql = try_build_nps_sql(q, tables, cols)
        self.assertIsNotNone(sql)
        assert sql is not None
        self.assertIn("GROUP BY", sql.upper())
        self.assertIn("avg_nps_rating", sql.lower())
        self.assertNotIn("COUNT(DISTINCT `user_id`)", sql)

    def test_scalar_count_rejected_for_which_activity(self):
        q = "which activity improved nps score"
        bad_sql = (
            f"SELECT COUNT(DISTINCT user_id) AS unique_responders "
            f"FROM `{DATASET}.academy_nps_form_responses`"
        )
        reason = sql_intent_mismatch_reason(q, bad_sql)
        self.assertIsNotNone(reason)

    def test_glossary_context_for_question(self):
        ctx, ids = glossary_context_for_question("what is nps score")
        self.assertTrue(ctx)
        self.assertTrue(any("nps" in i.lower() for i in ids))


if __name__ == "__main__":
    unittest.main()
