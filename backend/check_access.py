"""Run: python check_access.py  — full BigQuery access audit for NexA."""
from __future__ import annotations

import json
import os

import config
import credentials
import bq
from google.cloud import bigquery

TARGET_DS = "kossip-helpers.academy_success_ai_analytics_worksapce"
TARGET_TABLE = f"{TARGET_DS}.z_ccbp_academy_users_master_data"


def test(name: str, fn) -> tuple[str, str]:
    try:
        r = fn()
        if isinstance(r, list):
            return "OK", f"{len(r)} items"
        if hasattr(r, "schema"):
            return "OK", f"{len(r.schema)} cols, rows={r.num_rows}"
        if hasattr(r, "dataset_id"):
            return "OK", r.full_dataset_id
        return "OK", str(r)[:80]
    except Exception as e:
        msg = str(e)
        if "403" in msg or "Access Denied" in msg or "denied" in msg.lower():
            return "403", msg.split(";")[0][:140]
        if "404" in msg or "Not found" in msg:
            return "404", msg[:140]
        return "ERR", msg[:140]


def main() -> None:
    credentials.bootstrap_gcp_credentials()
    client = bigquery.Client(project=config.GCP_PROJECT, location=config.BQ_LOCATION)

    print("=" * 60)
    print("NEXA SERVICE ACCOUNT ACCESS AUDIT")
    print("=" * 60)
    print("GCP_PROJECT:", config.GCP_PROJECT)
    print("BQ_DEFAULT_DATASET:", config.BQ_DEFAULT_DATASET)
    print("credentials_ready:", credentials.credentials_ready)
    print("credentials_message:", credentials.credentials_message)
    print("GOOGLE_APPLICATION_CREDENTIALS:", os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))
    print("service_account_email:", credentials.service_account_email())
    key_path = credentials.expected_key_path()
    if key_path and os.path.isfile(key_path):
        with open(key_path, encoding="utf-8") as f:
            print("key file email:", json.load(f).get("client_email"))
    print()

    print("--- A. Project-wide dataset list ---")
    try:
        datasets = bq.list_datasets()
        print(f"Visible datasets: {len(datasets)}")
        for d in datasets:
            print("  +", d["full_id"])
    except Exception as e:
        print("FAIL:", e)
        datasets = []

    print("\n--- B. Target dataset (academy_success) ---")
    for label, fn in [
        ("get_dataset", lambda: client.get_dataset(TARGET_DS)),
        ("list_tables", lambda: list(client.list_tables(TARGET_DS))),
    ]:
        status, detail = test(label, fn)
        print(f"  {label}: {status} -> {detail}")

    print("\n--- C. Target table (master_data) ---")
    for label, fn in [
        ("get_table (metadata)", lambda: client.get_table(TARGET_TABLE)),
        ("preview SELECT 1", lambda: bq.preview_table(TARGET_TABLE, 1)),
    ]:
        status, detail = test(label, fn)
        print(f"  {label}: {status} -> {detail}")

    print("\n--- D. Other datasets (can we SELECT rows?) ---")
    for d in datasets:
        ds_id = d["full_id"]
        try:
            tables = bq.list_tables_in_dataset(ds_id)
            print(f"  {d['dataset_id']}: {len(tables)} tables", end="")
            if tables:
                st, _ = test("", lambda t=tables[0]["full_table_id"]: bq.preview_table(t, 1))
                print(f" | sample SELECT: {st}")
            else:
                print()
        except Exception as e:
            print(f"  {d['dataset_id']}: list FAIL -> {str(e)[:80]}")

    print("\n--- E. NexA warehouse scope ---")
    wh = bq.warehouse_datasets()
    cat_ds, cat_tables = bq.warehouse_catalog()
    for ds in cat_ds:
        n = len(cat_tables.get(ds["full_id"], []))
        print(f"  {ds['full_id']}: {n} tables")

    print("\n--- SUMMARY ---")
    academy_in_list = any("academy_success" in d["full_id"] for d in datasets)
    st_meta, _ = test("meta", lambda: client.get_table(TARGET_TABLE))
    st_sel, _ = test("sel", lambda: bq.preview_table(TARGET_TABLE, 1))
    print("academy_success in dataset list:", academy_in_list)
    print("master_data metadata:", st_meta)
    print("master_data SELECT:", st_sel)
    if st_meta == "OK" and st_sel == "OK":
        print("VERDICT: Full access — NexA should work.")
    elif st_meta == "OK":
        print("VERDICT: Metadata only — need dataViewer + jobUser for queries.")
    else:
        print("VERDICT: No access on target dataset for", credentials.service_account_email())


if __name__ == "__main__":
    main()
