"""Tests for last-N-days date preference and user-id drill-down follow-ups."""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from question_dates import resolve_relative_range
from question_intent import (
    expand_drill_down_followup,
    is_drill_down_data_request,
    rewrite_aggregate_to_user_list_sql,
)


class DateRangePreferenceTests(unittest.TestCase):
    def test_last_2_days_overrides_yesterday(self):
        q = "How many users attended live classes yesterday? i want last 2 days data"
        start, end = resolve_relative_range(q, today=date(2026, 7, 9))
        self.assertEqual(start, date(2026, 7, 8))
        self.assertEqual(end, date(2026, 7, 9))

    def test_yesterday_alone(self):
        start, end = resolve_relative_range(
            "attended live classes yesterday", today=date(2026, 7, 9)
        )
        self.assertEqual(start, date(2026, 7, 8))
        self.assertEqual(end, date(2026, 7, 8))

    def test_last_7_days(self):
        start, end = resolve_relative_range("last 7 days", today=date(2026, 7, 9))
        self.assertEqual(start, date(2026, 7, 3))
        self.assertEqual(end, date(2026, 7, 9))

    def test_last_month_is_previous_calendar_month(self):
        # Today is July 9, 2026 → last month = June 2026
        start, end = resolve_relative_range(
            "how many students placed in last month above 8 lpa",
            today=date(2026, 7, 9),
        )
        self.assertEqual(start, date(2026, 6, 1))
        self.assertEqual(end, date(2026, 6, 30))

    def test_last_three_months(self):
        start, end = resolve_relative_range(
            "what are last three months nps scores",
            today=date(2026, 7, 9),
        )
        self.assertEqual(start, date(2026, 4, 1))
        self.assertEqual(end, date(2026, 6, 30))

    def test_last_30_days_still_rolling(self):
        start, end = resolve_relative_range("last 30 days", today=date(2026, 7, 9))
        self.assertEqual(start, date(2026, 6, 10))
        self.assertEqual(end, date(2026, 7, 9))

    def test_last_month_in_january_crosses_year(self):
        start, end = resolve_relative_range("last month", today=date(2026, 1, 15))
        self.assertEqual(start, date(2025, 12, 1))
        self.assertEqual(end, date(2025, 12, 31))


class DrillDownFollowupTests(unittest.TestCase):
    def test_give_with_user_id_detected(self):
        self.assertTrue(is_drill_down_data_request("give with user id"))
        self.assertTrue(is_drill_down_data_request("give user ids"))
        self.assertTrue(is_drill_down_data_request("show me the user_id"))

    def test_expand_reuses_prior_topic(self):
        prior_q = "How many users attended live classes yesterday? i want last 2 days data"
        prior_sql = (
            "SELECT COUNT(DISTINCT `user_id`) AS `unique_users`\n"
            "FROM `proj.ds.z_academy_users_live_classes_attendance_and_time_spent_details`\n"
            "WHERE DATE(`slot_date`) BETWEEN DATE '2026-07-08' AND DATE '2026-07-09'\n"
            "  AND `attendance_status` = 'JOINED'"
        )
        expanded = expand_drill_down_followup("give with user id", prior_q, prior_sql)
        self.assertIn("user_id", expanded.lower())
        self.assertIn("SAME table", expanded)
        self.assertIn("live_classes_attendance", expanded)

    def test_rewrite_count_to_user_list(self):
        prior_sql = (
            "SELECT COUNT(DISTINCT `user_id`) AS `unique_users`\n"
            "FROM `proj.ds.z_academy_users_live_classes_attendance_and_time_spent_details`\n"
            "WHERE DATE(`slot_date`) = DATE '2026-07-08'\n"
            "  AND `attendance_status` = 'JOINED'"
        )
        out = rewrite_aggregate_to_user_list_sql(prior_sql)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertIn("SELECT DISTINCT `user_id`", out)
        self.assertIn("attendance_status", out)
        self.assertIn("slot_date", out)
        self.assertNotIn("COUNT", out.upper())


if __name__ == "__main__":
    unittest.main()
