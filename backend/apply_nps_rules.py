"""Apply detailed NPS business rules to NPS workspace tables (local + live)."""
from __future__ import annotations

import os

import httpx
from sqlalchemy import select

from db import SessionLocal, WorkspaceTable, init_db

NPS_RULES = """Grain: One row represents one form submission. A user may submit the form multiple times.
Use COUNT(*) when the user asks for responses, submissions, or feedback entries.
Use COUNT(DISTINCT user_id) when the user asks for users, students, responders, or unique participants.
Use rating_on_scale_of_0_to_10 as the primary field for all NPS calculations.
Ignore NULL values in rating_on_scale_of_0_to_10 when calculating averages, NPS score, or NPS category counts.
Classify NPS ratings as:
Promoters: Rating 9–10
Passives: Rating 7–8
Detractors: Rating 0–6
If the user asks for promoter count, return the count of responses (or distinct users if the question mentions users) where rating_on_scale_of_0_to_10 BETWEEN 9 AND 10.
If the user asks for detractor count, return the count of responses (or distinct users if the question mentions users) where rating_on_scale_of_0_to_10 BETWEEN 0 AND 6.
If the user asks for passive count, return the count of responses (or distinct users if the question mentions users) where rating_on_scale_of_0_to_10 BETWEEN 7 AND 8.
If the user asks for NPS Score, calculate: ((Promoters - Detractors) / Total Valid Responses) × 100 where Total Valid Responses includes only ratings from 0–10.
If the user asks for rating distribution, group by rating_on_scale_of_0_to_10.
If the user asks for comments, improvements, or feedback themes, use the corresponding text columns and perform text/topic analysis instead of numerical aggregation.
For multi-select question columns (stored as arrays or JSON arrays), UNNEST/SPLIT the values before counting individual options.
Apply date filters using form_submission_datetime (or form_submission_month for calendar months) only when the user explicitly specifies a time period.
Always use exact schema column names while generating SQL.
When both response count and user count are meaningful, default to response count unless the user explicitly asks for users, students, or unique responders."""

SHORTS = (
    "academy_nps_form_responses",
    "nps_form_responses_nov_and_dec_2025",
)

API = (os.environ.get("NEXA_API_URL") or "https://nexa-gays.onrender.com").rstrip("/")
EMAIL = (os.environ.get("NEXA_ADMIN_EMAIL") or "admin@example.com").strip()
PASSWORD = os.environ.get("NEXA_ADMIN_PASSWORD") or "change-me"


def apply_local() -> int:
    init_db()
    db = SessionLocal()
    n = 0
    try:
        for t in db.scalars(select(WorkspaceTable)).all():
            short = t.full_table_id.rsplit(".", 1)[-1]
            if short in SHORTS:
                t.business_rules = NPS_RULES
                n += 1
        db.commit()
    finally:
        db.close()
    return n


def apply_remote() -> int:
    n = 0
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=60.0)) as client:
        tok = client.post(
            f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD}
        ).json()["token"]
        headers = {"Authorization": f"Bearer {tok}"}
        tables = client.get(f"{API}/workspace/tables", headers=headers).json()
        for t in tables:
            short = t["full_table_id"].rsplit(".", 1)[-1]
            if short not in SHORTS:
                continue
            r = client.patch(
                f"{API}/workspace/tables/{t['id']}",
                headers=headers,
                json={"business_rules": NPS_RULES},
            )
            r.raise_for_status()
            n += 1
            print(f"  ok {short}")
    return n


if __name__ == "__main__":
    print("local", apply_local())
    print("remote", apply_remote())
