"""Import workspace_models.yaml into a remote NexA API (live) without redeploy.

Does NOT restart the server. Logged-in users keep their JWT sessions.
Only updates table/column descriptions + join hints in the DB.

Usage (from backend/):
  set NEXA_API_URL=https://nexa-gays.onrender.com
  set NEXA_ADMIN_EMAIL=admin@example.com
  set NEXA_ADMIN_PASSWORD=your-password
  python import_remote_yaml.py

Optional:
  set NEXA_PRUNE=1   # also remove non-YAML tables (use carefully while testers are active)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import yaml

import model_yaml

YAML_PATH = Path(__file__).parent / "workspace_models.yaml"
API = (os.environ.get("NEXA_API_URL") or "https://nexa-gays.onrender.com").rstrip("/")
EMAIL = (os.environ.get("NEXA_ADMIN_EMAIL") or "").strip()
PASSWORD = os.environ.get("NEXA_ADMIN_PASSWORD") or ""
PRUNE = os.environ.get("NEXA_PRUNE", "").strip().lower() in ("1", "true", "yes")


def cleaned_yaml_chunks(text: str) -> list[str]:
    """Drop logical models / docs without base_sql_table so older live parsers succeed."""
    chunks = model_yaml._split_model_documents(text)
    keep: list[str] = []
    skipped = 0
    for chunk in chunks:
        doc = yaml.safe_load(chunk)
        if not isinstance(doc, dict):
            skipped += 1
            continue
        if str(doc.get("type") or "").strip().lower() == "logical_model":
            skipped += 1
            continue
        if not (doc.get("base_sql_table") or doc.get("base_table") or doc.get("sql_table")):
            skipped += 1
            continue
        try:
            model_yaml.parse_model_document(doc)
        except model_yaml.ModelYamlError:
            skipped += 1
            continue
        keep.append(chunk.strip())
    print(f"Prepared {len(keep)} models for import (skipped {skipped})")
    return keep


def main() -> None:
    if not EMAIL or not PASSWORD:
        print(
            "Set NEXA_ADMIN_EMAIL and NEXA_ADMIN_PASSWORD "
            "(admin account on the live server)."
        )
        sys.exit(1)
    if not YAML_PATH.is_file():
        print(f"Missing {YAML_PATH}")
        sys.exit(1)

    raw = YAML_PATH.read_text(encoding="utf-8")
    chunks = cleaned_yaml_chunks(raw)
    if not chunks:
        print("No importable models after cleanup")
        sys.exit(1)

    print(f"API: {API}")

    with httpx.Client(timeout=httpx.Timeout(600.0, connect=90.0)) as client:
        health = client.get(f"{API}/health")
        print(f"health: {health.status_code} {health.text[:120]}")
        health.raise_for_status()

        login = client.post(
            f"{API}/auth/login",
            json={"email": EMAIL, "password": PASSWORD},
        )
        if login.status_code != 200:
            print(f"login failed: {login.status_code} {login.text[:300]}")
            sys.exit(1)
        token = login.json()["token"]
        user = login.json().get("user") or {}
        if user.get("role") != "admin":
            print(f"Account {EMAIL} is not admin (role={user.get('role')})")
            sys.exit(1)
        print(f"logged in as admin: {user.get('email')}")

        headers = {"Authorization": f"Bearer {token}"}
        print("Importing YAML (descriptions) — no server restart…")
        batch_size = 8
        total_tables = 0
        all_errors: list[str] = []
        join_updated = False
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            print(f"  batch {i // batch_size + 1}: {len(batch)} models…")
            imp = client.post(
                f"{API}/workspace/models/import",
                headers=headers,
                json={"yamls": batch, "generate_overviews": False},
            )
            if imp.status_code != 200:
                print(f"import failed: {imp.status_code} {imp.text[:500]}")
                sys.exit(1)
            data = imp.json()
            tables = data.get("tables") or []
            total_tables += len(tables)
            all_errors.extend(data.get("errors") or [])
            join_updated = join_updated or bool(data.get("join_hints_updated"))
            print(f"    ok — {len(tables)} imported this batch")

        print(
            f"Imported {total_tables} model(s) total; "
            f"join_hints_updated={join_updated}"
        )
        if all_errors:
            print(f"Warnings ({len(all_errors)}):")
            for e in all_errors[:15]:
                print(f"  - {e}")

        if PRUNE:
            print("Pruning catalog to YAML tables…")
            pr = client.post(
                f"{API}/workspace/models/prune-to-yaml",
                headers=headers,
                json={},
            )
            if pr.status_code == 404:
                print(
                    "prune endpoint not on this deploy yet — skip prune "
                    "(redeploy later for Keep only YAML tables)."
                )
            elif pr.status_code != 200:
                print(f"prune failed: {pr.status_code} {pr.text[:300]}")
                sys.exit(1)
            else:
                p = pr.json()
                print(
                    f"Pruned: kept {p.get('kept')} · removed {p.get('removed')} "
                    f"(yaml_tables={p.get('yaml_tables')})"
                )
        else:
            print("Skip prune (testers undisturbed).")

    print("Done. Testers stay logged in — only catalog descriptions were updated.")


if __name__ == "__main__":
    main()
