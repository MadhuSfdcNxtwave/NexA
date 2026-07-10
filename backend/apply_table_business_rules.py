"""Apply generated_table_business_rules.json to local DB and/or remote API."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

RULES_PATH = Path(__file__).parent / "generated_table_business_rules.json"
API = (os.environ.get("NEXA_API_URL") or "https://nexa-gays.onrender.com").rstrip("/")
EMAIL = (os.environ.get("NEXA_ADMIN_EMAIL") or "admin@example.com").strip()
PASSWORD = os.environ.get("NEXA_ADMIN_PASSWORD") or "change-me"
TARGET = (os.environ.get("NEXA_RULES_TARGET") or "both").strip().lower()  # local|remote|both


def apply_local(rules_by_fq: dict) -> tuple[int, int]:
    from db import SessionLocal, WorkspaceTable, init_db
    from sqlalchemy import select

    init_db()
    db = SessionLocal()
    updated = missing = 0
    try:
        tables = db.scalars(select(WorkspaceTable)).all()
        by_fq = {t.full_table_id: t for t in tables}
        for fq, payload in rules_by_fq.items():
            t = by_fq.get(fq)
            if not t:
                missing += 1
                continue
            t.business_rules = payload["business_rules"]
            updated += 1
        db.commit()
    finally:
        db.close()
    return updated, missing


def apply_remote(rules_by_fq: dict) -> tuple[int, int, int]:
    import httpx

    updated = missing = failed = 0
    with httpx.Client(timeout=httpx.Timeout(180.0, connect=60.0)) as client:
        login = client.post(f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD})
        login.raise_for_status()
        token = login.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        tables = client.get(f"{API}/workspace/tables", headers=headers).json()
        by_fq = {t["full_table_id"]: t for t in tables}
        for fq, payload in rules_by_fq.items():
            t = by_fq.get(fq)
            if not t:
                missing += 1
                continue
            r = client.patch(
                f"{API}/workspace/tables/{t['id']}",
                headers=headers,
                json={"business_rules": payload["business_rules"]},
            )
            if r.status_code == 200:
                updated += 1
                print(f"  ok {payload['short']}")
            else:
                failed += 1
                print(f"  FAIL {payload['short']}: {r.status_code} {r.text[:160]}")
    return updated, missing, failed


def main() -> None:
    if not RULES_PATH.is_file():
        print(f"Missing {RULES_PATH} — run analyze_and_generate_table_rules.py first")
        sys.exit(1)
    data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    print(f"Loaded rules for {len(data)} tables; target={TARGET}")

    if TARGET in ("local", "both"):
        u, m = apply_local(data)
        print(f"local: updated={u} missing={m}")

    if TARGET in ("remote", "both"):
        print(f"remote: {API}")
        u, m, f = apply_remote(data)
        print(f"remote: updated={u} missing={m} failed={f}")


if __name__ == "__main__":
    main()
