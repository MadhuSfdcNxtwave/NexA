"""Tests for KB article builder and routing validation."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import kb_articles as kba
import knowledge_base as kb
from ask_plan import AskPlan, TableMatch
from semantic_layer import load_semantic_catalog


def _master_table() -> SimpleNamespace:
    catalog = load_semantic_catalog()
    sem = catalog.get("z_ccbp_academy_users_master_data")
    if not sem:
        raise unittest.SkipTest("master_data model missing")
    col_desc = {d.id: d.description or d.id for d in sem.dimensions}
    return SimpleNamespace(
        full_table_id=sem.full_table_id,
        description=sem.description,
        ai_overview=sem.description,
        column_descriptions_json=json.dumps(col_desc),
        column_hints_json="{}",
        included_for_ai=True,
        endorsed=True,
    )


class KbArticleTests(unittest.TestCase):
    def test_build_article_contains_table_and_columns(self):
        table = _master_table()
        knowledge = kb.load_table_knowledge(table)
        article = kba.build_table_kb_article(knowledge, table_obj=table)
        self.assertIn("z_ccbp_academy_users_master_data", article)
        self.assertIn("full_table_id:", article)
        self.assertIn("user_id", article)
        self.assertIn("What this table contains", article)

    def test_build_table_card_compact(self):
        table = _master_table()
        knowledge = kb.load_table_knowledge(table)
        card = kba.build_table_card(knowledge, table_obj=table)
        self.assertIn("Grain:", card)
        self.assertIn("z_ccbp_academy_users_master_data", card)
        self.assertLess(len(card), 3500)

    def test_plan_columns_for_portal_active(self):
        table = _master_table()
        knowledge = kb.load_table_knowledge(table)
        cols, filters, measure = kba.plan_columns_for_table(
            "how many users have learning portal active", table, knowledge
        )
        self.assertIn(table.full_table_id, cols)
        self.assertEqual(measure, "active_learning_portal_users")

    @patch("llm.route_with_kb")
    def test_route_question_validates_columns(self, mock_route):
        table = _master_table()
        knowledge = kb.load_table_knowledge(table)
        mock_route.return_value = {
            "tables": [knowledge.full_table_id],
            "columns": {knowledge.full_table_id: ["user_id", "fake_column_xyz"]},
            "filters": ["pause_status IS NULL"],
            "measure": "active_learning_portal_users",
            "reason": "User master for portal active count",
        }
        matches = [
            TableMatch(
                full_table_id=knowledge.full_table_id,
                short_name=knowledge.short_name,
                score=100,
            )
        ]
        old = config.KB_AI_ROUTING
        config.KB_AI_ROUTING = True
        try:
            result = kba.route_question(
                "how many users have learning portal active",
                matches,
                [knowledge],
                [table],
            )
        finally:
            config.KB_AI_ROUTING = old

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.tables, [knowledge.full_table_id])
        self.assertIn("user_id", result.columns.get(knowledge.full_table_id, []))
        self.assertNotIn("fake_column_xyz", result.columns.get(knowledge.full_table_id, []))
        self.assertEqual(result.measure, "active_learning_portal_users")

    def test_apply_kb_columns_marks_selected(self):
        fq = "proj.ds.z_ccbp_academy_users_master_data"
        column_matches = {
            fq: [
                kb.ColumnMatch("user_id", 10, "id", selected=False),
                kb.ColumnMatch("pause_status", 5, "status", selected=False),
            ]
        }
        kba.apply_kb_columns_to_matches(column_matches, {fq: ["user_id", "pause_status"]})
        selected = [c.name for c in column_matches[fq] if c.selected]
        self.assertEqual(selected, ["user_id", "pause_status"])


if __name__ == "__main__":
    unittest.main()
