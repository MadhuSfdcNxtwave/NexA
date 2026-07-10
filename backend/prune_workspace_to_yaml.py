"""Prune workspace catalog to tables defined in workspace_models.yaml.

Removes extras added by SYNC_WORKSPACE_FROM_DATASET (full BQ dataset sync).

Run from backend/:
  python prune_workspace_to_yaml.py
  python prune_workspace_to_yaml.py --dry-run
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml
from sqlalchemy import select

import model_yaml
from db import SessionLocal, WorkspaceTable

YAML_PATH = Path(__file__).parent / "workspace_models.yaml"


def yaml_full_table_ids(text: str) -> set[str]:
    ids: set[str] = set()
    for chunk in model_yaml._split_model_documents(text):
        doc = yaml.safe_load(chunk)
        if not isinstance(doc, dict):
            continue
        if str(doc.get("type") or "").strip().lower() == "logical_model":
            continue
        if not (doc.get("base_sql_table") or doc.get("base_table") or doc.get("sql_table")):
            continue
        try:
            model = model_yaml.parse_model_document(doc)
        except model_yaml.ModelYamlError:
            continue
        ids.add(model["full_table_id"])
    return ids


def prune(*, dry_run: bool = False) -> dict:
    if not YAML_PATH.is_file():
        raise SystemExit(f"Missing {YAML_PATH}")
    keep = yaml_full_table_ids(YAML_PATH.read_text(encoding="utf-8"))
    db = SessionLocal()
    try:
        tables = list(db.scalars(select(WorkspaceTable)).all())
        remove = [t for t in tables if t.full_table_id not in keep]
        kept = [t for t in tables if t.full_table_id in keep]
        removed_names = [t.full_table_id.rsplit(".", 1)[-1] for t in remove]
        if not dry_run:
            for t in remove:
                db.delete(t)
            db.commit()
        return {
            "yaml_tables": len(keep),
            "before": len(tables),
            "kept": len(kept),
            "removed": len(remove),
            "removed_names": sorted(removed_names),
            "dry_run": dry_run,
        }
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Prune workspace tables to YAML models")
    parser.add_argument("--dry-run", action="store_true", help="List extras without deleting")
    args = parser.parse_args()
    result = prune(dry_run=args.dry_run)
    print(
        f"YAML models: {result['yaml_tables']} unique tables | "
        f"catalog before: {result['before']} | "
        f"{'would remove' if args.dry_run else 'removed'}: {result['removed']} | "
        f"kept: {result['kept']}"
    )
    for name in result["removed_names"]:
        print(f"  - {name}")


if __name__ == "__main__":
    main()
