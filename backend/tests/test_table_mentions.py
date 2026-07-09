"""Tests for Hex-style @table mentions and user table pins."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from table_mentions import apply_table_pins, parse_mention_tokens, resolve_table_token, strip_mentions


def _table(fq: str, *, included: bool = True, endorsed: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        full_table_id=fq,
        included_for_ai=included,
        endorsed=endorsed,
        description="",
        column_descriptions_json="{}",
        column_hints_json="{}",
        ai_overview="",
        ai_profile_json="{}",
        embedding_json="[]",
    )


class TableMentionTests(unittest.TestCase):
    def test_parse_and_strip_mentions(self):
        q = "@z_ccbp_academy_users_master_data how many active portal users"
        self.assertEqual(parse_mention_tokens(q), ["z_ccbp_academy_users_master_data"])
        self.assertEqual(
            strip_mentions(q),
            "how many active portal users",
        )

    def test_resolve_short_name(self):
        tables = [
            _table("p.d.z_ccbp_academy_users_master_data"),
            _table("p.d.academy_users_day_and_page_wise_time_spent_details"),
        ]
        fq = resolve_table_token("z_ccbp_academy_users_master_data", tables)
        self.assertEqual(fq, "p.d.z_ccbp_academy_users_master_data")

    def test_apply_pins_merges_api_and_mention(self):
        tables = [
            _table("p.d.z_ccbp_academy_users_master_data"),
            _table("p.d.nps_all_form_responses"),
        ]
        clean, pins, reason = apply_table_pins(
            "@nps_all_form_responses average score",
            tables,
            pinned_table_ids=["p.d.z_ccbp_academy_users_master_data"],
        )
        self.assertIn("p.d.z_ccbp_academy_users_master_data", pins)
        self.assertIn("p.d.nps_all_form_responses", pins)
        self.assertNotIn("@", clean)
        self.assertIn("User", reason)

    def test_excluded_table_not_pinned(self):
        tables = [
            _table("p.d.noisy_table", included=False),
            _table("p.d.z_ccbp_academy_users_master_data"),
        ]
        _, pins, _ = apply_table_pins("@noisy_table count", tables)
        self.assertEqual(pins, [])


if __name__ == "__main__":
    unittest.main()
