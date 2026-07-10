"""Tests for contextual feedback detail SQL shape."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from feedback_sql import try_build_feedback_sql


class ContextualFeedbackDetailsTests(unittest.TestCase):
    def test_details_question_returns_when_about_question_answer(self) -> None:
        fq = "proj.ds.users_contextual_feedback_details"
        table = SimpleNamespace(full_table_id=fq)
        cols = {
            fq: {
                "user_id",
                "feedback_id",
                "feedback_trigger",
                "feedback_type",
                "question_id",
                "question_order",
                "question_type",
                "question_text",
                "user_answer",
                "submitted_date",
                "enroll_plans_str",
                "is_valid_question",
                "is_valid_trigger",
            }
        }
        sql = try_build_feedback_sql(
            "Give me the current month's contextual feedback details",
            [table],
            cols,
            relaxed=True,
        )
        self.assertIsNotNone(sql)
        assert sql is not None
        low = sql.lower()
        self.assertIn("when_submitted", low)
        self.assertIn("feedback_about", low)
        self.assertIn("as `question`", low)
        self.assertIn("feedback_answer", low)
        self.assertIn("date_trunc(current_date(), month)", low)
        # Human story columns should appear before opaque ids
        self.assertLess(low.index("when_submitted"), low.index("user_id"))
        self.assertLess(low.index("feedback_about"), low.index("user_id"))
        self.assertLess(low.index("feedback_answer"), low.index("feedback_id"))


if __name__ == "__main__":
    unittest.main()
