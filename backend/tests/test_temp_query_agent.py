"""Tests for temp query agent + last-N-months NPS."""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.temp_query_agent import plan_question, run_temp_query_agent, should_run_temp_agent
from memory_lookup import sql_intent_mismatch_reason
from question_dates import resolve_relative_range

DATASET = "kossip-helpers.academy_success_ai_analytics_worksapce"


def _table(short: str) -> SimpleNamespace:
    return SimpleNamespace(full_table_id=f"{DATASET}.{short}")


class TempAgentNpsTests(unittest.TestCase):
    def test_last_three_months_range(self):
        start, end = resolve_relative_range(
            "what are last three months nps scores",
            today=date(2026, 7, 9),
        )
        self.assertEqual(start, date(2026, 4, 1))
        self.assertEqual(end, date(2026, 6, 30))

    def test_plan_nps_monthly(self):
        plan = plan_question("what are last three months nps scores")
        self.assertEqual(plan.metric, "nps_score")
        self.assertEqual(plan.breakdown, "month")
        self.assertTrue(should_run_temp_agent("what are last three months nps scores"))

    def test_agent_composes_monthly_nps(self):
        q = "what are last three months nps scores"
        tables = [_table("academy_nps_form_responses")]
        cols = {
            f"{DATASET}.academy_nps_form_responses": {
                "user_id",
                "rating_on_scale_of_0_to_10",
                "form_submission_month",
            },
        }
        result = run_temp_query_agent(q, tables, cols)
        self.assertIsNotNone(result.sql)
        assert result.sql is not None
        self.assertIn("nps_score", result.sql)
        self.assertIn("GROUP BY", result.sql.upper())
        self.assertIn("form_submission_month", result.sql)
        self.assertNotIn("unique_responders", result.sql)

    def test_reject_unique_responders_for_nps_scores(self):
        q = "what are last three months nps scores"
        bad = (
            f"SELECT COUNT(DISTINCT user_id) AS unique_responders "
            f"FROM `{DATASET}.academy_nps_form_responses`"
        )
        reason = sql_intent_mismatch_reason(q, bad)
        self.assertIsNotNone(reason)

    def test_placement_last_month_with_lpa(self):
        q = "how many students placed in last month above 8 lpa"
        plan = plan_question(q)
        self.assertEqual(plan.metric, "placement_count")
        self.assertTrue(any("8" in f for f in plan.filters))
        tables = [_table("y_academy_users_placements_details")]
        result = run_temp_query_agent(q, tables, {})
        self.assertIsNotNone(result.sql)
        assert result.sql is not None
        start, end = resolve_relative_range(q, today=date.today())
        self.assertIn(start.isoformat(), result.sql)
        self.assertIn("ctc_in_lpa", result.sql)
        self.assertIn(">= 8", result.sql)


if __name__ == "__main__":
    unittest.main()
