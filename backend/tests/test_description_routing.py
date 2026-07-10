"""Table description + AI overview drive routing scores."""
from __future__ import annotations

import unittest

import knowledge_base as kb


def _k(
    short: str,
    *,
    desc: str = "",
    overview: str = "",
    cols: dict[str, str] | None = None,
) -> kb.TableKnowledge:
    return kb.TableKnowledge(
        full_table_id=f"proj.ds.{short}",
        short_name=short,
        table_description=desc,
        column_descriptions=cols or {},
        column_types={},
        ai_overview=overview,
    )


class TestDescriptionRouting(unittest.TestCase):
    def test_description_beats_name_only(self):
        q = "portal page activity by day"
        keywords = kb.extract_keywords(q)
        with_desc = _k(
            "academy_users_day_and_page_wise_time_spent_details",
            desc="Daily portal page activity — time spent per page on the learning portal",
            overview="Use for page-level portal engagement, not event clicks",
        )
        name_only = _k("y_academy_user_event_engagement_details")
        s_desc = kb.score_table_knowledge(q, with_desc, keywords)
        s_name = kb.score_table_knowledge(q, name_only, keywords)
        self.assertGreater(s_desc, s_name)

    def test_ai_overview_boosts_score(self):
        q = "NPS scores by month"
        keywords = kb.extract_keywords(q)
        bare = _k("academy_nps_form_responses", desc="NPS form responses")
        rich = _k(
            "academy_nps_form_responses",
            desc="NPS form responses",
            overview="Monthly NPS scores and promoter/detractor breakdown for academy users",
        )
        self.assertGreater(
            kb.score_table_knowledge(q, rich, keywords),
            kb.score_table_knowledge(q, bare, keywords),
        )

    def test_answer_kb_includes_description_and_overview(self):
        k = _k(
            "z_ccbp_academy_users_master_data",
            desc="One row per academy user — portal access and pause status",
            overview="Use for active learning portal users",
        )
        text = kb.build_answer_kb_context([k])
        self.assertIn("Description:", text)
        self.assertIn("AI overview:", text)
        self.assertIn("portal", text.lower())


if __name__ == "__main__":
    unittest.main()
