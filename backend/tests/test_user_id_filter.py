"""Tests for pasted user-id detection and SQL filter injection."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from user_id_filter import (
    ensure_user_id_filter,
    extract_user_ids_from_text,
    resolve_user_ids,
    user_id_in_sql,
)


class UserIdFilterTests(unittest.TestCase):
    def test_extract_single_hyphenated(self):
        q = "feedback for 550e8400-e29b-41d4-a716-446655440000 please"
        ids = extract_user_ids_from_text(q)
        self.assertEqual(ids, ["550e8400e29b41d4a716446655440000"])

    def test_extract_multiple_mixed(self):
        q = (
            "these users 550e8400-e29b-41d4-a716-446655440000 "
            "and 551e8400e29b41d4a716446655440001 — are they placed?"
        )
        ids = extract_user_ids_from_text(q)
        self.assertEqual(len(ids), 2)
        self.assertEqual(ids[0], "550e8400e29b41d4a716446655440000")
        self.assertEqual(ids[1], "551e8400e29b41d4a716446655440001")

    def test_resolve_reuses_prior_on_followup(self):
        prior_q = "status of 550e8400-e29b-41d4-a716-446655440000"
        follow = "what is their feedback?"
        ids = resolve_user_ids(follow, prior_question=prior_q, prior_sql="")
        self.assertEqual(ids, ["550e8400e29b41d4a716446655440000"])

    def test_resolve_does_not_reuse_without_reference(self):
        prior_q = "status of 550e8400-e29b-41d4-a716-446655440000"
        follow = "how many active learning portal users?"
        ids = resolve_user_ids(follow, prior_question=prior_q, prior_sql="")
        self.assertEqual(ids, [])

    def test_ensure_injects_into_simple_select(self):
        sql = (
            "SELECT COUNT(*) AS c\n"
            "FROM `p.d.users_contextual_feedback_details`\n"
            "WHERE TRUE"
        )
        ids = ["550e8400e29b41d4a716446655440000"]
        out = ensure_user_id_filter(sql, ids)
        self.assertIn("REPLACE(CAST(`user_id` AS STRING), '-', '') IN (", out)
        self.assertIn("550e8400e29b41d4a716446655440000", out)

    def test_ensure_injects_into_cte(self):
        sql = """
WITH scored AS (
  SELECT user_id, question_text
  FROM `p.d.users_contextual_feedback_details`
  WHERE submitted_date IS NOT NULL
)
SELECT * FROM scored
""".strip()
        ids = ["550e8400e29b41d4a716446655440000", "551e8400e29b41d4a716446655440001"]
        out = ensure_user_id_filter(sql, ids)
        # Filter should land on the base table WHERE inside the CTE.
        self.assertIn("REPLACE(CAST(`user_id` AS STRING), '-', '') IN (", out)
        self.assertIn("550e8400e29b41d4a716446655440000", out)
        self.assertIn("551e8400e29b41d4a716446655440001", out)
        # BEFORE the CTE closes / outer select
        self.assertLess(
            out.lower().index("replace(cast(`user_id`"),
            out.lower().index("select * from scored"),
        )

    def test_feedback_sql_applies_pasted_ids(self):
        from feedback_sql import try_build_feedback_sql

        fq = "p.d.users_contextual_feedback_details"
        table = SimpleNamespace(full_table_id=fq)
        cols = {
            fq: {
                "user_id",
                "feedback_id",
                "feedback_trigger",
                "question_text",
                "user_answer",
                "submitted_date",
                "is_valid_trigger",
            }
        }
        q = (
            "feedback for 550e8400-e29b-41d4-a716-446655440000 "
            "on calendar page how many"
        )
        sql = try_build_feedback_sql(q, [table], cols)
        self.assertIsNotNone(sql)
        assert sql is not None
        self.assertIn("550e8400e29b41d4a716446655440000", sql)
        self.assertIn("calendar", sql.lower())
        self.assertIn(user_id_in_sql(["550e8400e29b41d4a716446655440000"]).split(" IN ")[0], sql)


if __name__ == "__main__":
    unittest.main()
