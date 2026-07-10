"""Tests for pre-SQL query shape + thread drill-down continuity."""
from __future__ import annotations

import unittest
from datetime import date

from agents.answer_shape import detect_answer_shape, is_thread_continuity_followup
from agents.query_shape import detect_query_shape
from question_intent import (
    is_drill_down_data_request,
    rewrite_aggregate_to_user_list_sql,
)


class QueryShapeTests(unittest.TestCase):
    def test_date_wise_month(self):
        shape = detect_query_shape("show feedback count month wise")
        self.assertTrue(shape.wants_group_by)
        self.assertEqual(shape.date_grain, "month")

    def test_group_by_gender(self):
        shape = detect_query_shape("how many students by gender")
        self.assertTrue(shape.wants_group_by)
        self.assertTrue(shape.wants_count)
        self.assertIn("gender", shape.group_hints)

    def test_year_filter(self):
        shape = detect_query_shape("how many students placed in 2026")
        self.assertTrue(shape.wants_date_filter)
        self.assertIsNotNone(shape.date_range)
        self.assertEqual(shape.date_range[0], date(2026, 1, 1))

    def test_followup_list_shape(self):
        prior = "SELECT COUNT(DISTINCT user_id) FROM `p.d.t` WHERE x = 1"
        shape = detect_query_shape("show their user ids", prior_sql=prior)
        self.assertTrue(shape.is_followup_list)
        self.assertTrue(shape.wants_list)
        checkpoint = shape.to_schema_checkpoint()
        self.assertIn("FOLLOW-UP", checkpoint)
        self.assertIn("NOT COUNT", checkpoint)


class DrillDownContinuityTests(unittest.TestCase):
    def test_their_userid_is_drill_down(self):
        self.assertTrue(is_drill_down_data_request("show their userid"))
        self.assertTrue(is_drill_down_data_request("show their user ids"))
        self.assertTrue(is_drill_down_data_request("give me their user_id"))

    def test_rewrite_keeps_where(self):
        prior = (
            "SELECT COUNT(DISTINCT `user_id`) AS c\n"
            "FROM `proj.ds.feedback`\n"
            "WHERE DATE(submitted_date) BETWEEN '2026-07-01' AND '2026-07-09'"
        )
        out = rewrite_aggregate_to_user_list_sql(prior)
        self.assertIsNotNone(out)
        self.assertIn("SELECT DISTINCT `user_id`", out)
        self.assertIn("BETWEEN '2026-07-01' AND '2026-07-09'", out)
        self.assertNotIn("COUNT", out)
        self.assertIn("LIMIT 500 OFFSET 0", out)

    def test_continuity_locks_prior_table(self):
        prior = "SELECT COUNT(*) FROM `p.d.feedback`"
        self.assertTrue(
            is_thread_continuity_followup("show their user ids", prior_sql=prior)
        )
        shape = detect_answer_shape("show their user ids", prior_sql=prior)
        self.assertNotEqual(shape.mode, "aggregate")

    def test_names_join_profile_basic_details(self):
        from question_intent import (
            is_drill_down_data_request,
            question_wants_user_names,
            rewrite_aggregate_to_user_list_sql,
        )

        q = "give there names and uid of 139 attended"
        self.assertTrue(question_wants_user_names(q))
        self.assertTrue(is_drill_down_data_request(q))

        class T:
            def __init__(self, fq):
                self.full_table_id = fq

        fact = "proj.ds.z_academy_users_live_classes_attendance_and_time_spent_details"
        profile = "proj.ds.academy_user_profile_basic_details"
        prior = (
            f"SELECT COUNT(DISTINCT `user_id`) AS c FROM `{fact}` "
            "WHERE `attendance_status` = 'JOINED' "
            "AND DATE(`slot_date`) = DATE '2026-07-09'"
        )
        cols = {
            fact: {"user_id", "attendance_status", "slot_date"},
            profile: {"user_id", "first_name", "last_name"},
        }
        out = rewrite_aggregate_to_user_list_sql(
            prior,
            question=q,
            included_tables=[T(fact), T(profile)],
            columns_by_table=cols,
        )
        self.assertIsNotNone(out)
        self.assertIn("academy_user_profile_basic_details", out)
        self.assertIn("user_name", out)
        self.assertIn("first_name", out)
        self.assertIn("JOINED", out)
        self.assertIn("2026-07-09", out)
        self.assertIn("LEFT JOIN", out)

    def test_id_list_followup_chips(self):
        from presentation import suggest_id_list_followups

        chips = suggest_id_list_followups(
            "give those user id",
            sql="SELECT DISTINCT user_id FROM y_academy_users_placements_details",
            selected_tables=["y_academy_users_placements_details"],
        )
        self.assertIn("next page", chips)
        self.assertIn("export as CSV", chips)
        self.assertTrue(any("placement" in c for c in chips))

    def test_next_page_offsets(self):
        from question_intent import is_list_pagination_request, parse_list_page_request

        list_sql = (
            "SELECT DISTINCT `user_id`\n"
            "FROM `p.d.t`\n"
            "WHERE x = 1\n"
            "ORDER BY `user_id`\n"
            "LIMIT 500 OFFSET 0"
        )
        self.assertTrue(is_list_pagination_request("next page"))
        info = parse_list_page_request("next page", prior_sql=list_sql)
        self.assertEqual(info["page"], 2)
        page2 = rewrite_aggregate_to_user_list_sql(
            list_sql, page=info["page"], page_size=info["page_size"]
        )
        self.assertIn("LIMIT 500 OFFSET 500", page2)
        page3 = rewrite_aggregate_to_user_list_sql(list_sql, page=3, page_size=500)
        self.assertIn("LIMIT 500 OFFSET 1000", page3)


if __name__ == "__main__":
    unittest.main()
