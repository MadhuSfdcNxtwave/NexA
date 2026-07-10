"""NPS promoter / detractor template SQL tests."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from nps_sql import try_build_nps_sql

FQ = "kossip-helpers.academy_success_ai_analytics_worksapce.academy_nps_form_responses"
COLS = {
    FQ: {
        "user_id",
        "rating_on_scale_of_0_to_10",
        "form_submission_month",
        "form_submission_datetime",
    }
}


def _tables():
    return [SimpleNamespace(full_table_id=FQ)]


class NpsPromoterSqlTests(unittest.TestCase):
    def test_promoters_june_2026(self):
        sql = try_build_nps_sql(
            "in nps give me promoters for june 2026",
            _tables(),
            COLS,
        )
        self.assertIsNotNone(sql)
        assert sql is not None
        self.assertIn("BETWEEN 9 AND 10", sql)
        self.assertIn("2026-06-01", sql)
        self.assertIn("COUNT(*)", sql)
        self.assertNotIn("unique_responders", sql)

    def test_unique_promoter_users(self):
        sql = try_build_nps_sql(
            "how many unique users are promoters in june 2026",
            _tables(),
            COLS,
        )
        self.assertIsNotNone(sql)
        assert sql is not None
        self.assertIn("COUNT(DISTINCT `user_id`)", sql)
        self.assertIn("BETWEEN 9 AND 10", sql)

    def test_detractors(self):
        sql = try_build_nps_sql("nps detractors for may 2026", _tables(), COLS)
        self.assertIsNotNone(sql)
        assert sql is not None
        self.assertIn("BETWEEN 0 AND 6", sql)


if __name__ == "__main__":
    unittest.main()
