"""Tests for breakdown follow-up guard."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from question_intent import question_is_breakdown_followup, question_wants_breakdown


class BreakdownFollowupTests(unittest.TestCase):
    def test_by_company_alone_not_followup(self):
        q = "How many job applications were submitted in June 2026 by company?"
        self.assertTrue(question_wants_breakdown(q))
        self.assertFalse(question_is_breakdown_followup(q, prior_sql="SELECT 1"))

    def test_by_company_with_prior_reference_is_followup(self):
        q = "Break that down by company"
        self.assertTrue(
            question_is_breakdown_followup(
                q,
                prior_sql="SELECT COUNT(*) FROM `p.d.jobs`",
                prior_question="How many applications?",
            )
        )

    def test_fresh_avg_by_company_not_followup(self):
        q = "What is the average placement CTC by company?"
        self.assertFalse(question_is_breakdown_followup(q, prior_sql="SELECT 1"))


if __name__ == "__main__":
    unittest.main()
