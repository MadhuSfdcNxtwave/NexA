"""Placement count SQL + year/this-year date filter tests."""
from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from placement_sql import is_placement_count_question, try_build_placement_sql
from question_dates import (
    resolve_calendar_year_range,
    resolve_question_date_range,
    resolve_relative_range,
)

FQ = "kossip-helpers.academy_success_ai_analytics_worksapce.y_academy_users_placements_details"


class PlacementSqlTests(unittest.TestCase):
    def test_this_year_range(self):
        ref = date(2026, 7, 10)
        self.assertEqual(
            resolve_relative_range("how many students got jobs this year", today=ref),
            (date(2026, 1, 1), ref),
        )

    def test_calendar_year_2026(self):
        ref = date(2026, 7, 10)
        self.assertEqual(
            resolve_calendar_year_range("how many students placed in 2026", today=ref),
            (date(2026, 1, 1), ref),
        )
        self.assertEqual(
            resolve_question_date_range("how many students placed in 2026", today=ref),
            (date(2026, 1, 1), ref),
        )

    def test_placement_this_year_sql(self):
        q = "@y_academy_users_placements_details how many students got jobs this year"
        self.assertTrue(is_placement_count_question(q))
        sql = try_build_placement_sql(
            q,
            [SimpleNamespace(full_table_id=FQ)],
            {FQ: {"user_id", "date_of_placement"}},
        )
        self.assertIsNotNone(sql)
        assert sql is not None
        self.assertIn("date_of_placement", sql)
        self.assertIn("2026-01-01", sql)
        self.assertIn("COUNT(DISTINCT `user_id`)", sql)

    def test_placement_in_2026_sql(self):
        q = "how many students placed in 2026"
        sql = try_build_placement_sql(
            q,
            [SimpleNamespace(full_table_id=FQ)],
            {FQ: {"user_id", "date_of_placement"}},
        )
        self.assertIsNotNone(sql)
        assert sql is not None
        self.assertIn("date_of_placement", sql)
        self.assertIn("2026-01-01", sql)
        self.assertIn("BETWEEN", sql.upper())


if __name__ == "__main__":
    unittest.main()
