"""Tests for centralized table routing rules."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from table_routing import match_routing_rule, pin_table, sql_filters_for_table, validate_sql_table_choice
from table_routing import (
    compound_domain_table_ids,
    is_compound_domain_question,
)
from types import SimpleNamespace


class TableRoutingTests(unittest.TestCase):
    def test_portal_active_pins_master_data(self):
        tables = [
            SimpleNamespace(
                full_table_id="p.d.academy_users_day_and_page_wise_time_spent_details",
            ),
            SimpleNamespace(
                full_table_id="p.d.z_ccbp_academy_users_master_data",
            ),
            SimpleNamespace(
                full_table_id="p.d.all_users_question_wise_responses_summary_details_for_question_set_units",
            ),
        ]
        q = "how many users have learning potal active"
        self.assertEqual(
            pin_table(q, tables),
            ["p.d.z_ccbp_academy_users_master_data"],
        )
        rule = match_routing_rule(q)
        self.assertIsNotNone(rule)
        self.assertEqual(rule.id, "learning_portal_active_users")

    def test_lp_status_pins_engagement_table(self):
        tables = [
            SimpleNamespace(
                full_table_id="p.d.academy_users_day_and_page_wise_time_spent_details",
            ),
            SimpleNamespace(
                full_table_id="p.d.z_ccbp_academy_users_master_data",
            ),
        ]
        q = "how many users have lp_status ACTIVE"
        self.assertEqual(
            pin_table(q, tables),
            ["p.d.academy_users_day_and_page_wise_time_spent_details"],
        )
        rule = match_routing_rule(q)
        self.assertIsNotNone(rule)
        self.assertEqual(rule.id, "learning_portal_lp_status_active")

    def test_portal_access_granted_pins_master(self):
        tables = [
            SimpleNamespace(
                full_table_id="p.d.academy_users_day_and_page_wise_time_spent_details",
            ),
            SimpleNamespace(
                full_table_id="p.d.z_ccbp_academy_users_master_data",
            ),
        ]
        q = "how many users have learning portal access"
        self.assertEqual(
            pin_table(q, tables),
            ["p.d.z_ccbp_academy_users_master_data"],
        )
        rule = match_routing_rule(q)
        self.assertIsNotNone(rule)
        self.assertEqual(rule.id, "learning_portal_access_granted")

    def test_engagement_sql_filters(self):
        table = SimpleNamespace(
            full_table_id="p.d.academy_users_day_and_page_wise_time_spent_details",
            column_descriptions_json='{"lp_status":"x","user_id":"z"}',
            ai_profile_json="{}",
        )
        filters = sql_filters_for_table("how many users with lp_status ACTIVE", table)
        self.assertIn("`lp_status` = 'ACTIVE'", filters)

    def test_master_active_portal_sql_filters(self):
        table = SimpleNamespace(
            full_table_id="p.d.z_ccbp_academy_users_master_data",
            column_descriptions_json='{"pause_status":"x","learning_portal_onboarding_access_given_datetime":"y","user_id":"z"}',
            ai_profile_json="{}",
        )
        filters = sql_filters_for_table("how many active users in learning portal now", table)
        self.assertIn("`pause_status` IS NULL", filters)
        self.assertTrue(any("learning_portal_onboarding" in f for f in filters))


    def test_rejects_wrong_table_sql(self):
        ok, reason = validate_sql_table_choice(
            "how many active users in learning portal now",
            "SELECT COUNT(DISTINCT user_id) FROM `p.d.all_users_question_wise_responses_summary_details_for_question_set_units`",
        )
        self.assertFalse(ok)
        self.assertIn("expected table", reason)

    def test_compound_skips_single_pin(self):
        tables = [
            SimpleNamespace(
                full_table_id="p.d.z_academy_users_live_classes_attendance_and_time_spent_details",
            ),
            SimpleNamespace(
                full_table_id="p.d.z_ccbp_academy_users_master_data",
            ),
        ]
        q = "How many users attended live classes yesterday and have learning portal access?"
        self.assertTrue(is_compound_domain_question(q))
        self.assertEqual(pin_table(q, tables), [])
        ids = compound_domain_table_ids(q, tables)
        self.assertEqual(len(ids), 2)

    def test_compound_requires_join_in_sql(self):
        ok, reason = validate_sql_table_choice(
            "users attended live classes yesterday and have learning portal access",
            "SELECT COUNT(DISTINCT user_id) FROM `p.d.z_academy_users_live_classes_attendance_and_time_spent_details`",
        )
        self.assertFalse(ok)
        self.assertIn("JOIN", reason)


if __name__ == "__main__":
    unittest.main()
