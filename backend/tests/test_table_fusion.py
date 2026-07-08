"""Tests for fused table ranking — golden tables must appear in top-K."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import knowledge_base as kb
import vector_index
from semantic_layer import load_semantic_catalog


def _load_catalog_tables() -> list[SimpleNamespace]:
    catalog = load_semantic_catalog()
    endorsed = {
        "z_academy_users_live_classes_attendance_and_time_spent_details",
        "users_contextual_feedback_details",
        "y_academy_user_daily_engagement_time_spent",
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
                included_for_ai=True,
                endorsed=short in endorsed,
                embedding_json="[]",
                embedding_hash="",
                embedding_model="",
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


class TableFusionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = _load_catalog_tables()
        if not cls.catalog:
            raise unittest.SkipTest("workspace_models.yaml not found")

    def _fused_top_shorts(self, question: str, vector_boost: dict[str, float] | None = None) -> list[str]:
        keywords = kb.extract_keywords(question)
        keyword_scores: dict[str, int] = {}
        knowledges: list[kb.TableKnowledge] = []
        for t in self.catalog:
            k = kb.load_table_knowledge(t)
            knowledges.append(k)
            keyword_scores[k.full_table_id] = kb.score_table_knowledge(question, k, keywords)

        # Simulate vector scores without calling embed API
        original = vector_index._vector_scores_all
        boost = vector_boost or {}

        def _fake_vector(q, tables, klist):
            return {
                t.full_table_id: boost.get(t.full_table_id, 0.15)
                for t in tables
            }

        vector_index._vector_scores_all = _fake_vector
        try:
            if boost:
                for fq, vec in boost.items():
                    if fq in keyword_scores:
                        keyword_scores[fq] = keyword_scores[fq] + int(vec * 200)
            fused = vector_index.rank_all_tables(
                question, self.catalog, knowledges, keyword_scores, top_k=config.ROUTING_TOP_K
            )
        finally:
            vector_index._vector_scores_all = original

        return [m.short_name for m in fused]

    def test_golden_tables_in_fused_top8_keyword_only(self):
        failures: list[str] = []
        for question, expected_short in GOLDEN:
            tops = self._fused_top_shorts(question)
            if expected_short not in tops:
                failures.append(f"{question!r} -> top-{config.ROUTING_TOP_K}={tops[:5]}... (expected {expected_short})")
        if failures:
            self.fail("Fusion rank misses:\n" + "\n".join(failures))

    def test_attendance_beats_cloudwatch_with_vector_boost(self):
        question = "How many users attended live classes yesterday?"
        attend_fq = next(
            t.full_table_id
            for t in self.catalog
            if t.full_table_id.rsplit(".", 1)[-1]
            == "z_academy_users_live_classes_attendance_and_time_spent_details"
        )
        cloud_fq = next(
            (t.full_table_id for t in self.catalog if "cloudwatch" in t.full_table_id and "live_class" in t.full_table_id),
            None,
        )
        boost = {attend_fq: 0.85}
        if cloud_fq:
            boost[cloud_fq] = 0.30
        tops = self._fused_top_shorts(question, vector_boost=boost)
        self.assertEqual(
            tops[0],
            "z_academy_users_live_classes_attendance_and_time_spent_details",
        )

    def test_build_table_card_is_compact(self):
        t = next(
            x for x in self.catalog
            if x.full_table_id.rsplit(".", 1)[-1] == "z_ccbp_academy_users_master_data"
        )
        import kb_articles as kba

        k = kb.load_table_knowledge(t)
        card = kba.build_table_card(k, table_obj=t)
        self.assertIn("Grain:", card)
        self.assertIn("Domain:", card)
        self.assertLess(len(card), 3500)
        self.assertNotIn("## Columns", card)


if __name__ == "__main__":
    unittest.main()
