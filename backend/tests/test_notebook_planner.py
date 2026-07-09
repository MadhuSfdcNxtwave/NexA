"""Tests for Hex-style notebook step planning."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from notebook_planner import plan_notebook_steps


def _t(short: str) -> SimpleNamespace:
    return SimpleNamespace(full_table_id=f"p.d.{short}")


class NotebookPlannerTests(unittest.TestCase):
    def test_compound_attendance_portal_three_cells(self):
        q = "How many users attended live classes yesterday and have learning portal access?"
        steps = plan_notebook_steps(q, "", selected_tables=[_t("z_academy_users_live_classes_attendance_and_time_spent_details"), _t("z_ccbp_academy_users_master_data")])
        self.assertGreaterEqual(len(steps), 3)
        self.assertEqual(steps[-1]["kind"], "final")
        self.assertIn("JOIN", steps[-1]["label"])

    def test_nps_by_gender_join_steps(self):
        q = "Average NPS by gender"
        steps = plan_notebook_steps(
            q,
            "",
            selected_tables=[_t("academy_nps_form_responses"), _t("z_ccbp_academy_users_master_data")],
            join_relations=[SimpleNamespace(source="academy_nps_form_responses", target="z_ccbp_academy_users_master_data")],
        )
        self.assertGreaterEqual(len(steps), 3)
        self.assertEqual(steps[-1]["kind"], "final")

    def test_independent_metrics_still_chain(self):
        q = "How many active users in learning portal and how many unique NPS responses"
        steps = plan_notebook_steps(q, "")
        self.assertGreaterEqual(len(steps), 2)


if __name__ == "__main__":
    unittest.main()
