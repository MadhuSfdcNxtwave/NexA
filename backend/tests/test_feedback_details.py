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

    def test_domain_sql_does_not_count_for_details(self) -> None:
        from domain_sql import resolve_domain_sql
        from types import SimpleNamespace

        fq = "proj.ds.users_contextual_feedback_details"
        table = SimpleNamespace(
            full_table_id=fq,
            column_descriptions_json="",
        )
        out = resolve_domain_sql(
            "Give me the current month's contextual feedback details",
            [table],
        )
        self.assertIsNotNone(out)
        sql, _t, reason = out
        self.assertNotIn("COUNT(", sql.upper())
        self.assertIn("when_submitted", sql.lower())
        self.assertIn("feedback_answer", sql.lower())
        self.assertIn("details", reason.lower())

    def test_calendar_feature_count_filters_not_whole_table(self) -> None:
        from domain_sql import resolve_domain_sql
        from feedback_sql import feature_scope_terms, try_build_feedback_sql
        from memory_lookup import sql_matches_question_intent
        from measure_router import try_build_measure_plan

        fq = "kossip-helpers.academy_success_ai_analytics_worksapce.users_contextual_feedback_details"
        table = SimpleNamespace(
            full_table_id=fq,
            included_for_ai=True,
            description="",
            column_descriptions_json="{}",
            column_hints_json="{}",
            ai_overview="",
        )
        cols = {
            fq: {
                "user_id",
                "feedback_id",
                "feedback_trigger",
                "question_text",
                "user_answer",
                "question_type",
                "submitted_date",
                "is_valid_trigger",
            }
        }
        q = (
            "what is the feedback on calender page in learning portal "
            "how many we recieved on that new feature"
        )
        self.assertIn("calender", feature_scope_terms(q))
        self.assertIn("calendar", feature_scope_terms(q))

        sql = try_build_feedback_sql(q, [table], cols)
        self.assertIsNotNone(sql)
        assert sql is not None
        low = sql.lower()
        self.assertIn("calendar", low)
        self.assertIn("calender", low)
        self.assertIn("feedback_trigger", low)
        self.assertIn("feedback_responses", low)
        self.assertNotRegex(
            sql,
            r"(?is)SELECT\s+COUNT\s*\(\s*DISTINCT\s+`?user_id`?\s*\)\s+AS\s+`?unique_users`?\s+FROM",
        )
        self.assertTrue(sql_matches_question_intent(q, sql))

        bad = (
            "SELECT COUNT(DISTINCT `user_id`) AS `unique_users`\n"
            f"FROM `{fq}`"
        )
        self.assertFalse(sql_matches_question_intent(q, bad))

        self.assertIsNone(try_build_measure_plan(q, [table], catalog_tables=[table]))

        # Patch domain pin so resolve_domain_sql can see the feedback table.
        import ask_plan

        prev = ask_plan.domain_table_override
        ask_plan.domain_table_override = lambda _q, _t: [fq]  # type: ignore
        try:
            out = resolve_domain_sql(q, [table])
        finally:
            ask_plan.domain_table_override = prev
        self.assertIsNotNone(out)
        assert out is not None
        domain_sql, _t, reason = out
        self.assertIn("calendar", domain_sql.lower())
        self.assertIn("feedback", reason.lower())
        self.assertNotEqual(
            domain_sql.strip(),
            f"SELECT COUNT(DISTINCT `user_id`) AS `unique_users`\nFROM `{fq}`",
        )


if __name__ == "__main__":
    unittest.main()
