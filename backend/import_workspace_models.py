"""Import backend/workspace_models.yaml into the workspace catalog.

Updates column descriptions, join hints, table descriptions, and AI overviews.

Run from backend/:
  python import_workspace_models.py
"""
from __future__ import annotations

import argparse
from pathlib import Path

import credentials

credentials.bootstrap_gcp_credentials()

from db import SessionLocal
from main import _import_models_impl
from schemas import ModelYamlImportIn

YAML_PATH = Path(__file__).parent / "workspace_models.yaml"


def main() -> None:
    parser = argparse.ArgumentParser(description="Import workspace_models.yaml")
    parser.add_argument(
        "--no-overviews",
        action="store_true",
        help="Skip BigQuery profiling + AI overview generation (faster bulk import)",
    )
    args = parser.parse_args()

    if not YAML_PATH.is_file():
        raise SystemExit(f"Missing {YAML_PATH}")

    text = YAML_PATH.read_text(encoding="utf-8")
    db = SessionLocal()
    try:
        result = _import_models_impl(
            ModelYamlImportIn(yaml=text, generate_overviews=not args.no_overviews),
            db,
        )
    finally:
        db.close()

    print(f"Imported {len(result.tables)} table(s)")
    for t in result.tables:
        status = "created" if t.created else "updated"
        overview = "overview ok" if t.overview_generated else "overview skipped/failed"
        print(
            f"  {t.model_id}: {t.full_table_id} ({status}, "
            f"{t.columns_imported} cols, {t.relations_imported} relations, {overview})"
        )
    print(f"Join hints updated: {result.join_hints_updated}")
    if result.errors:
        print("Errors:")
        for err in result.errors:
            print(f"  - {err}")


if __name__ == "__main__":
    main()
