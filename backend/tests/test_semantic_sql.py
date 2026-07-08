"""Semantic SQL composition smoke tests."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from measure_router import try_build_measure_plan
from sql_composer import compose_sql


class SemanticSqlTests(unittest.TestCase):
    def test_compose_count_with_date_filter(self):
        table = SimpleNamespace(
            full_table_id="proj.ds.y_academy_user_daily_engagement_time_spent",
            column_descriptions_json='{"calendar_date": "activity date"}',
            ai_profile_json="{}",
        )
        question = "How many active users on platform yesterday?"
        plan = try_build_measure_plan(question, [table])
        self.assertIsNotNone(plan)
        sql = compose_sql(plan, question, table)
        self.assertIn("COUNT", sql.upper())
        self.assertIn("y_academy_user_daily_engagement_time_spent", sql)
        self.assertIn("DATE", sql.upper())
        self.assertIn("calendar_date", sql.lower())

    def test_live_class_attendance_yesterday(self):
        table = SimpleNamespace(
            full_table_id=(
                "kossip-helpers.academy_success_ai_analytics_worksapce."
                "z_academy_users_live_classes_attendance_and_time_spent_details"
            ),
            column_descriptions_json='{"slot_date": "date", "user_id": "id", "attendance_status": "status"}',
            ai_profile_json="{}",
        )
        question = "How many users attended live classes yesterday?"
        plan = try_build_measure_plan(question, [table])
        self.assertIsNotNone(plan)
        self.assertIn("live_classes_attendance", plan.table_short)
        sql = compose_sql(plan, question, table)
        self.assertIn("COUNT(DISTINCT", sql.upper())
        self.assertIn("slot_date", sql.lower())
        self.assertIn("JOINED", sql)
        self.assertIn("2026-07-06", sql)

    def test_learning_portal_active_users(self):
        table = SimpleNamespace(
            full_table_id=(
                "kossip-helpers.academy_success_ai_analytics_worksapce."
                "academy_users_day_and_page_wise_time_spent_details"
            ),
            column_descriptions_json=json.dumps(
                {
                    "user_id": "id",
                    "lp_status": "ACTIVE EXPIRED REVOKED",
                }
            ),
            ai_profile_json="{}",
        )
        question = "how many users have learning potal active"
        plan = try_build_measure_plan(question, [table])
        self.assertIsNotNone(plan)
        sql = compose_sql(plan, question, table)
        self.assertIn("day_and_page_wise_time_spent", sql)
        self.assertIn("lp_status", sql.lower())
        self.assertIn("ACTIVE", sql)


if __name__ == "__main__":
    unittest.main()
