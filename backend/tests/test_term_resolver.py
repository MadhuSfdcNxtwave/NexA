"""Tests for glossary-backed term resolver and metrics registry."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from metrics_registry import load_glossary, match_glossary_terms, reload_registry
from semantic_layer import reload_semantic_catalog
from term_resolver import resolve

DATASET = "kossip-helpers.academy_success_ai_analytics_worksapce"


def _table(short: str) -> SimpleNamespace:
    return SimpleNamespace(
        full_table_id=f"{DATASET}.{short}",
        included_for_ai=True,
    )


def _catalog() -> list[SimpleNamespace]:
    return [
        _table("z_ccbp_academy_users_master_data"),
        _table("academy_users_day_and_page_wise_time_spent_details"),
        _table("academy_nps_form_responses"),
        _table("nps_form_responses_nov_and_dec_2025"),
        _table("z_ccbp_academy_users_jobs_details"),
    ]


class TermResolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        reload_semantic_catalog()
        reload_registry()

    def test_glossary_loads(self) -> None:
        glossary = load_glossary()
        self.assertIn("active_learning_portal_users", glossary)
        self.assertIn("nps_topic_feedback", glossary)

    def test_active_portal_resolves_to_master_data(self) -> None:
        q = "How many active users in learning portal now?"
        hits = match_glossary_terms(q)
        self.assertTrue(any(t.id == "active_learning_portal_users" for t, _ in hits))
        resolved = resolve(q, _catalog(), catalog_tables=_catalog())
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.model_id, "z_ccbp_academy_users_master_data")
        self.assertEqual(resolved.measure_id, "active_learning_portal_users")
        self.assertIn("glossary:", resolved.trace[0])

    def test_nps_topic_resolves_to_union_model(self) -> None:
        q = "What is the feedback on notice board in NPS form responses?"
        resolved = resolve(q, _catalog(), catalog_tables=_catalog())
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.intent, "topic_search")
        self.assertEqual(resolved.model_id, "nps_all_form_responses")
        self.assertEqual(resolved.topic, "notice board")

    def test_job_applicants_glossary(self) -> None:
        q = "How many distinct users applied to at least one job?"
        resolved = resolve(q, _catalog(), catalog_tables=_catalog())
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.model_id, "z_ccbp_academy_users_jobs_details")


if __name__ == "__main__":
    unittest.main()
