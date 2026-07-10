"""Prune live workspace catalog to workspace_models.yaml tables (no redeploy)."""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import yaml

import model_yaml

API = (os.environ.get("NEXA_API_URL") or "https://nexa-gays.onrender.com").rstrip("/")
EMAIL = (os.environ.get("NEXA_ADMIN_EMAIL") or "admin@example.com").strip()
PASSWORD = os.environ.get("NEXA_ADMIN_PASSWORD") or "change-me"
YAML_PATH = Path(__file__).parent / "workspace_models.yaml"


def yaml_keep() -> set[str]:
    text = YAML_PATH.read_text(encoding="utf-8")
    keep: set[str] = set()
    for chunk in model_yaml._split_model_documents(text):
        doc = yaml.safe_load(chunk)
        if not isinstance(doc, dict):
            continue
        if str(doc.get("type") or "").lower() == "logical_model":
            continue
        if not (doc.get("base_sql_table") or doc.get("base_table") or doc.get("sql_table")):
            continue
        try:
            m = model_yaml.parse_model_document(doc)
            keep.add(m["full_table_id"])
        except model_yaml.ModelYamlError:
            continue
    return keep


def main() -> None:
    keep = yaml_keep()
    print(f"YAML keep set: {len(keep)} tables")
    print(f"API: {API}")

    with httpx.Client(timeout=httpx.Timeout(180.0, connect=60.0)) as client:
        login = client.post(
            f"{API}/auth/login",
            json={"email": EMAIL, "password": PASSWORD},
        )
        login.raise_for_status()
        token = login.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Prefer dedicated prune endpoint if deployed
        pr = client.post(f"{API}/workspace/models/prune-to-yaml", headers=headers, json={})
        if pr.status_code == 200:
            print("Used prune endpoint:", pr.json())
            return

        print(f"prune endpoint unavailable ({pr.status_code}) — deleting extras via API")
        tables = client.get(f"{API}/workspace/tables", headers=headers).json()
        print(f"before: {len(tables)}")
        removed = 0
        for t in tables:
            fq = t["full_table_id"]
            if fq in keep:
                continue
            d = client.delete(f"{API}/workspace/tables/{t['id']}", headers=headers)
            if d.status_code == 200:
                removed += 1
                print(f"  removed {fq.rsplit('.', 1)[-1]}")
            else:
                print(f"  FAIL {fq}: {d.status_code} {d.text[:120]}")

        tables2 = client.get(f"{API}/workspace/tables", headers=headers).json()
        print(f"after: {len(tables2)} (removed {removed})")


if __name__ == "__main__":
    main()
