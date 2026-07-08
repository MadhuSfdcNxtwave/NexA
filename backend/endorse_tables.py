"""Endorse canonical workspace tables for AI routing priority."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import ProjectTable, SessionLocal, init_db

_DATASET = "kossip-helpers.academy_success_ai_analytics_worksapce"

CANONICAL_SHORT_NAMES = (
    "z_academy_users_live_classes_attendance_and_time_spent_details",
    "academy_nbfc_renewals_conversion_details",
    "users_contextual_feedback_details",
    "y_academy_user_daily_engagement_time_spent",
    "y_academy_users_placements_details",
    "academy_nps_form_responses",
    "z_ccbp_academy_users_jobs_details",
    "z_ccbp_academy_users_master_data",
)


def endorse_canonical_tables() -> int:
    init_db()
    updated = 0
    with SessionLocal() as db:
        for short in CANONICAL_SHORT_NAMES:
            fq = f"{_DATASET}.{short}"
            rows = (
                db.query(ProjectTable)
                .filter(ProjectTable.full_table_id.in_([fq, short]))
                .all()
            )
            for row in rows:
                if not row.endorsed:
                    row.endorsed = True
                    updated += 1
        db.commit()
    return updated


if __name__ == "__main__":
    n = endorse_canonical_tables()
    print(f"Endorsed {n} table row(s).")
