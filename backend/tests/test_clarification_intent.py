"""Tests for intent clarification before wrong answers."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# ask_clarify pulls bq via join_graph — stub for unit tests.
sys.modules.setdefault("google", MagicMock())
sys.modules.setdefault("google.cloud", MagicMock())
sys.modules["google.cloud.bigquery"] = MagicMock()
sys.modules["bq"] = MagicMock()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ask_clarify import (
    build_intent_clarification,
    should_clarify_before_sql,
    should_skip_clarification,
)


class ClarificationIntentTests(unittest.TestCase):
    def test_portal_activity_not_skipped(self):
        q = "in which activity students are activlly in learningportal"
        self.assertFalse(should_skip_clarification(q))

    def test_clarify_when_event_table_selected(self):
        q = "in which activity students are activlly in learningportal"
        clar = should_clarify_before_sql(
            q,
            selected_table_shorts=["y_academy_user_event_engagement_details"],
        )
        self.assertIsNotNone(clar)
        assert clar is not None
        self.assertTrue(clar.get("confirm_mode"))
        self.assertGreaterEqual(len(clar.get("options") or []), 2)

    def test_no_clarify_when_page_table_selected(self):
        q = "in which activity students are activlly in learningportal"
        clar = should_clarify_before_sql(
            q,
            selected_table_shorts=["academy_users_day_and_page_wise_time_spent_details"],
        )
        self.assertIsNone(clar)

    def test_intent_mismatch_builds_options(self):
        q = "which activity improved nps score"
        clar = build_intent_clarification(
            q,
            reason="breakdown question requires GROUP BY",
            sql="SELECT COUNT(DISTINCT user_id) FROM x",
        )
        self.assertIsNotNone(clar)
        assert clar is not None
        self.assertGreaterEqual(len(clar.get("options") or []), 2)


if __name__ == "__main__":
    unittest.main()
