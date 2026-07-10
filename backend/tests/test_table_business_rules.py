"""Tests for per-table business_rules override of default filters."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from table_business_rules import (
    format_table_rules_block,
    rules_skip_default_filters,
    table_skips_default_filters,
)


class TableBusinessRulesTests(unittest.TestCase):
    def test_detects_no_filter_phrases(self):
        self.assertTrue(
            rules_skip_default_filters(
                "Every row is an active portal user — do not add WHERE filters."
            )
        )
        self.assertTrue(rules_skip_default_filters("master itself is active users"))
        self.assertTrue(rules_skip_default_filters("filters: none"))
        self.assertFalse(rules_skip_default_filters("Prefer user_id for counts"))
        self.assertFalse(rules_skip_default_filters(""))

    def test_table_skips_when_rules_set(self):
        t = SimpleNamespace(
            business_rules="Every row is an active learning portal user. Do not add WHERE filters."
        )
        self.assertTrue(table_skips_default_filters(t))
        self.assertFalse(table_skips_default_filters(SimpleNamespace(business_rules="")))

    def test_format_block(self):
        t = SimpleNamespace(
            full_table_id="p.d.z_ccbp_academy_users_master_data",
            business_rules="Do not add WHERE filters.\nCount all rows.",
        )
        block = format_table_rules_block([t])
        self.assertIn("RULES for", block)
        self.assertIn("Do not add WHERE filters", block)
        self.assertIn("Do NOT add pause_status", block)

    def test_mandatory_preamble_and_prepend(self):
        from table_business_rules import (
            build_mandatory_rules_preamble,
            prepend_rules_to_schema,
        )

        t = SimpleNamespace(
            full_table_id="p.d.academy_nps_form_responses",
            business_rules="Promoters: Rating 9–10\nUse COUNT(*) for responses.",
        )
        preamble = build_mandatory_rules_preamble([t])
        self.assertIn("MANDATORY", preamble)
        self.assertIn("Promoters", preamble)
        schema = prepend_rules_to_schema("schema body here", [t])
        self.assertTrue(schema.startswith("# ==="))
        self.assertIn("schema body here", schema)

    def test_sql_filters_empty_when_rules_skip(self):
        from table_routing import sql_filters_for_table

        t = SimpleNamespace(
            full_table_id="p.d.z_ccbp_academy_users_master_data",
            business_rules="Every row is an active portal user — do not add WHERE filters.",
            column_descriptions_json='{"pause_status":"x","learning_portal_onboarding_access_given_datetime":"y","user_id":"z"}',
        )
        self.assertEqual(sql_filters_for_table("how many active portal users", t), [])


if __name__ == "__main__":
    unittest.main()
