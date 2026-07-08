"""Golden routing tests — verify table selection for canonical questions."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from ask_plan import build_ask_plan
from semantic_layer import load_semantic_catalog


def _load_catalog_tables() -> list[SimpleNamespace]:
    catalog = load_semantic_catalog()
    endorsed = {
        "z_academy_users_live_classes_attendance_and_time_spent_details",
        "academy_nbfc_renewals_conversion_details",
        "users_contextual_feedback_details",
        "y_academy_user_daily_engagement_time_spent",
        "y_academy_users_placements_details",
    }
    tables: list[SimpleNamespace] = []
    seen: set[str] = set()
    for sem in catalog.values():
        if sem.full_table_id in seen:
            continue
        seen.add(sem.full_table_id)
        short = sem.short_name
        col_desc = {d.id: d.description or d.id for d in sem.dimensions}
        tables.append(
            SimpleNamespace(
                full_table_id=sem.full_table_id,
                description=sem.description,
                ai_overview=sem.description,
                column_descriptions_json=json.dumps(col_desc),
                column_hints_json="{}",
                ai_profile_json="{}",
                included_for_ai=True,
                endorsed=short in endorsed,
            )
        )
    return tables


GOLDEN = [
    (
        "How many users attended live classes yesterday?",
        "z_academy_users_live_classes_attendance_and_time_spent_details",
    ),
    (
        "Contextual feedback emoji ratings last week",
        "users_contextual_feedback_details",
    ),
    (
        "Active users on platform yesterday",
        "y_academy_user_daily_engagement_time_spent",
    ),
    (
        "how many users have learning portal active",
        "z_ccbp_academy_users_master_data",
    ),
    (
        "Average placement CTC by company",
        "y_academy_users_placements_details",
    ),
    (
        "Job applications count by company",
        "z_ccbp_academy_users_jobs_details",
    ),
]


class GoldenRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = _load_catalog_tables()
        if not cls.catalog:
            raise unittest.SkipTest("workspace_models.yaml not found")

    def setUp(self):
        self._old_mode = config.TABLE_ROUTER_MODE
        self._old_embed = config.EMBEDDING_RETRIEVAL_ENABLED
        self._old_kb = config.KB_AI_ROUTING
        self._old_fusion = config.ROUTING_FUSION_ENABLED
        config.TABLE_ROUTER_MODE = "retrieval"
        config.EMBEDDING_RETRIEVAL_ENABLED = False
        config.KB_AI_ROUTING = False
        config.ROUTING_FUSION_ENABLED = False

    def tearDown(self):
        config.TABLE_ROUTER_MODE = self._old_mode
        config.EMBEDDING_RETRIEVAL_ENABLED = self._old_embed
        config.KB_AI_ROUTING = self._old_kb
        config.ROUTING_FUSION_ENABLED = self._old_fusion

    def test_golden_routing(self):
        failures: list[str] = []
        for question, expected_short in GOLDEN:
            plan = build_ask_plan(question, self.catalog, "")
            picked = [fq.rsplit(".", 1)[-1] for fq in plan.selected_full_ids]
            if expected_short not in picked:
                failures.append(f"{question!r} -> {picked} (expected {expected_short})")
        if failures:
            self.fail("Routing mismatches:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
