"""Tests for learning portal activity routing and SQL."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from join_compose import compose_portal_activity_by_page_sql, compose_portal_activity_attendance_pct_sql
from query_planner import analyze_question, classify_intent
from semantic_layer import reload_semantic_catalog
from table_routing import pin_table
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


class PortalActivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        reload_semantic_catalog()

    def test_classify_portal_activity_as_breakdown(self):
        q = "in which activity students are activlly in learningportal"
        self.assertEqual(classify_intent(q), "breakdown")

    def test_pin_portal_page_table_not_event_engagement(self):
        q = "in which activity students are activlly in learningportal"
        tables = [
            _table("y_academy_user_event_engagement_details"),
            _table("academy_users_day_and_page_wise_time_spent_details"),
        ]
        pinned = pin_table(q, tables)
        self.assertEqual(
            pinned,
            [f"{DATASET}.academy_users_day_and_page_wise_time_spent_details"],
        )

    def test_glossary_resolves_group_by_page(self):
        q = "in which activity students are activlly in learningportal"
        catalog = [
            _table("academy_users_day_and_page_wise_time_spent_details"),
            _table("y_academy_user_event_engagement_details"),
        ]
        cols = {
            f"{DATASET}.academy_users_day_and_page_wise_time_spent_details": {
                "user_id", "lp_status", "time_spent_page", "time_spent_date", "time_spent_in_mins",
            },
        }
        resolved = resolve(q, catalog, catalog_tables=catalog, columns_by_table=cols)
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.model_id, "academy_users_day_and_page_wise_time_spent_details")
        self.assertIn("time_spent_page", resolved.group_by)

    def test_portal_activity_sql(self):
        q = "in which activity students are activlly in learningportal"
        tables = [_table("academy_users_day_and_page_wise_time_spent_details")]
        sql = compose_portal_activity_by_page_sql(q, tables)
        self.assertIsNotNone(sql)
        assert sql is not None
        self.assertIn("time_spent_page", sql)
        self.assertIn("GROUP BY", sql.upper())
        self.assertIn("lp_status", sql)
        self.assertNotIn("COUNT(*)", sql)

    def test_portal_attendance_pct_sql(self):
        q = "learning portal activity and attendance percentage"
        tables = [
            _table("academy_users_day_and_page_wise_time_spent_details"),
            _table("z_academy_users_live_classes_attendance_and_time_spent_details"),
        ]
        sql = compose_portal_activity_attendance_pct_sql(q, tables)
        self.assertIsNotNone(sql)
        assert sql is not None
        self.assertIn("live_class_attendance_pct", sql)
        self.assertIn("JOIN", sql.upper())

    def test_events_portal_attendance_compound(self):
        from table_routing import compound_domain_table_shorts, is_compound_domain_question
        from metrics_registry import match_glossary_terms, reload_registry

        reload_registry()
        q = "events happend in learning portal and attendence percentage"
        self.assertTrue(is_compound_domain_question(q))
        shorts = compound_domain_table_shorts(q)
        self.assertIn("academy_users_day_and_page_wise_time_spent_details", shorts)
        self.assertIn("z_academy_users_live_classes_attendance_and_time_spent_details", shorts)
        matches = match_glossary_terms(q)
        self.assertTrue(any(t.id == "portal_activity_attendance_pct" for t, _ in matches))
        tables = [_table(s) for s in shorts]
        sql = compose_portal_activity_attendance_pct_sql(q, tables)
        self.assertIsNotNone(sql)


if __name__ == "__main__":
    unittest.main()
