"""Tests for staged selection, thread lock, and raw contextual-feedback export."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from agents.answer_shape import (
    detect_answer_shape,
    is_thread_continuity_followup,
    wants_raw_tabular_data,
)
from agents.selection_agent import run_selection_agent
from ask_plan import AskPlan, TableMatch, build_ask_plan, _tables_from_prior_sql
from feedback_sql import try_build_feedback_sql
from memory_lookup import sql_intent_mismatch_reason
from table_routing import pin_table, resolve_table_id, score_adjustment


DATASET = "kossip-helpers.academy_success_ai_analytics_worksapce"


def _table(short: str, *, description: str = "", business_rules: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        full_table_id=f"{DATASET}.{short}",
        included_for_ai=True,
        description=description,
        ai_overview="",
        business_rules=business_rules,
        column_descriptions_json="{}",
        column_hints_json="{}",
        endorsed=False,
    )


class AnswerShapeTests(unittest.TestCase):
    def test_raw_signals(self) -> None:
        self.assertTrue(wants_raw_tabular_data("Just give the raw data."))
        self.assertTrue(wants_raw_tabular_data("Give me data table to export csv."))
        self.assertTrue(wants_raw_tabular_data("I need field-wise data."))
        self.assertTrue(
            wants_raw_tabular_data("Give me the current month contextual feedback details.")
        )
        self.assertFalse(wants_raw_tabular_data("How many contextual feedback responses?"))

    def test_thread_continuity(self) -> None:
        prior = f"SELECT * FROM `{DATASET}.users_contextual_feedback_details` LIMIT 10"
        self.assertTrue(is_thread_continuity_followup("Just give the raw data.", prior_sql=prior))
        self.assertTrue(is_thread_continuity_followup("export csv", prior_sql=prior))
        self.assertFalse(is_thread_continuity_followup("how many NPS promoters?", prior_sql=prior))


class RoutingAliasTests(unittest.TestCase):
    def test_pin_resolves_z_prefix(self) -> None:
        tables = [
            _table(
                "z_users_contextual_feedback_details",
                description="Contextual in-app feedback by feedback_trigger",
            ),
            _table("live_classes_user_feedback_responses", description="Live class feedback"),
        ]
        pinned = pin_table("Give me the current month contextual feedback details.", tables)
        self.assertEqual(len(pinned), 1)
        self.assertIn("contextual_feedback", pinned[0])

    def test_resolve_table_id(self) -> None:
        tables = [_table("z_users_contextual_feedback_details")]
        fq = resolve_table_id(tables, "users_contextual_feedback_details")
        self.assertIsNotNone(fq)
        self.assertIn("z_users_contextual_feedback_details", fq or "")

    def test_score_penalizes_confusables(self) -> None:
        q = "current month contextual feedback details"
        self.assertGreater(
            score_adjustment(q, "users_contextual_feedback_details"),
            score_adjustment(q, "live_classes_user_feedback_responses"),
        )
        self.assertLess(score_adjustment(q, "y_academy_user_event_engagement_details"), 0)


class SelectionAgentTests(unittest.TestCase):
    def test_contextual_feedback_selected(self) -> None:
        import knowledge_base as kb

        tables = [
            _table(
                "z_users_contextual_feedback_details",
                description="Captures contextual in-app user feedback. feedback_trigger.",
                business_rules="Use submitted_date for month filters. Prefer row-level for details.",
            ),
            _table("live_classes_user_feedback_responses", description="Post live class feedback"),
            _table(
                "y_academy_user_event_engagement_details",
                description="Event engagement and attendance",
            ),
        ]
        knowledges = [kb.load_table_knowledge(t) for t in tables]
        matches = [
            TableMatch(
                full_table_id=k.full_table_id,
                short_name=k.short_name,
                score=10,
                table_description=k.table_description,
            )
            for k in knowledges
        ]
        # Put wrong table first to ensure description confirm overrides.
        matches[0], matches[1] = matches[1], matches[0]
        sel = run_selection_agent(
            "Give me the current month contextual feedback details.",
            tables,
            matches,
            knowledges,
        )
        self.assertIsNotNone(sel)
        assert sel is not None
        self.assertTrue(sel.selected_full_ids)
        self.assertIn("contextual_feedback", sel.selected_full_ids[0])
        self.assertEqual(sel.answer_shape.mode, "raw")
        self.assertTrue(sel.rules_block)


class RawFeedbackSqlTests(unittest.TestCase):
    def test_raw_sql_not_aggregate(self) -> None:
        tables = [_table("users_contextual_feedback_details")]
        cols = {
            tables[0].full_table_id: {
                "user_id",
                "feedback_id",
                "feedback_trigger",
                "question_text",
                "user_answer",
                "submitted_date",
                "question_type",
            }
        }
        sql = try_build_feedback_sql(
            "Give me the current month contextual feedback details.",
            tables,
            cols,
            relaxed=True,
        )
        self.assertIsNotNone(sql)
        assert sql is not None
        self.assertNotIn("GROUP BY", sql.upper())
        self.assertIn("submitted_date", sql)
        self.assertIn("question_text", sql)

    def test_intent_rejects_aggregate_for_raw(self) -> None:
        reason = sql_intent_mismatch_reason(
            "I need field-wise data for CSV export",
            "SELECT COUNT(DISTINCT user_id) AS unique_users FROM t",
        )
        self.assertIsNotNone(reason)


class ThreadLockPlanTests(unittest.TestCase):
    def test_followup_reuses_prior_table(self) -> None:
        tables = [
            _table(
                "users_contextual_feedback_details",
                description="Contextual feedback",
            ),
            _table("live_classes_user_feedback_responses", description="Live class"),
        ]
        prior = (
            f"SELECT user_id, question_text FROM `{DATASET}.users_contextual_feedback_details` "
            "WHERE DATE(submitted_date) BETWEEN DATE_TRUNC(CURRENT_DATE(), MONTH) "
            "AND LAST_DAY(CURRENT_DATE(), MONTH) LIMIT 100"
        )
        plan = build_ask_plan(
            "Just give the raw data.",
            tables,
            prior_sql=prior,
        )
        self.assertTrue(plan.selected_full_ids)
        self.assertIn("contextual_feedback", plan.selected_full_ids[0])
        self.assertIn("continuity", plan.routing_reason.lower())


if __name__ == "__main__":
    unittest.main()
