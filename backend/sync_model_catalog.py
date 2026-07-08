"""Import Hex-style model YAML from a TSV catalog (table_name + yaml columns).

Usage (from backend/):
  python sync_model_catalog.py model_catalog.tsv
  python sync_model_catalog.py --from-transcript

Writes workspace_models.yaml and runs the workspace import (descriptions,
column metadata, join hints, AI overviews, vector embeddings).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import credentials

credentials.bootstrap_gcp_credentials()

from db import SessionLocal
from main import _import_models_impl
from schemas import ModelYamlImportIn

BACKEND = Path(__file__).parent
YAML_OUT = BACKEND / "workspace_models.yaml"
CATALOG_TSV = BACKEND / "model_catalog.tsv"
_TRANSCRIPT_CANDIDATES = [
    Path.home()
    / ".cursor"
    / "projects"
    / "c-Users-Nxtwave-Desktop-UAT-VS-CODE-NexA"
    / "agent-transcripts"
    / "cf70fb4e-3686-4283-bb16-b4d563023b4a"
    / "cf70fb4e-3686-4283-bb16-b4d563023b4a.jsonl",
    Path(__file__).resolve().parents[1]
    / ".cursor"
    / "projects"
    / "c-Users-Nxtwave-Desktop-UAT-VS-CODE-NexA"
    / "agent-transcripts"
    / "cf70fb4e-3686-4283-bb16-b4d563023b4a"
    / "cf70fb4e-3686-4283-bb16-b4d563023b4a.jsonl",
]


def _normalize_yaml(text: str) -> str:
    """Fix TSV/Excel escaping so yaml.safe_load works."""
    out = (text or "").strip()
    if out.startswith('"') and out.endswith('"'):
        out = out[1:-1]
    out = out.replace('""', '"')
    out = re.sub(r'visibility:\s*""internal""', 'visibility: "internal"', out)
    out = re.sub(r'expr_sql:\s*""`', 'expr_sql: "`', out)
    out = out.replace('\\"', '"')
    return out.strip()


def _split_catalog_rows(text: str) -> list[tuple[str, str]]:
    """Parse tab-separated catalog: short_name<TAB>yaml_block."""
    lines = text.splitlines()
    if not lines:
        return []

    # Drop header row if present.
    start = 0
    if lines[0].lower().startswith("table name"):
        start = 1

    rows: list[tuple[str, str]] = []
    current_name: str | None = None
    current_yaml: list[str] = []

    row_start = re.compile(r"^([a-z][a-z0-9_]*)\t(.*)$", re.I)

    for line in lines[start:]:
        m = row_start.match(line)
        if m:
            if current_name and current_yaml:
                rows.append((current_name, _normalize_yaml("\n".join(current_yaml))))
            current_name = m.group(1).strip()
            rest = m.group(2).strip()
            current_yaml = [rest] if rest else []
        elif current_name:
            current_yaml.append(line)

    if current_name and current_yaml:
        rows.append((current_name, _normalize_yaml("\n".join(current_yaml))))

    return rows


def _clean_catalog_text(text: str) -> str:
    text = re.sub(r"</?timestamp>.*?</timestamp>\s*", "", text, flags=re.S)
    text = re.sub(r"<user_query>\s*", "", text)
    text = re.sub(r"\s*</user_query>\s*$", "", text)
    return text.strip()


def _score_catalog_text(text: str) -> int:
    score = len(text)
    if "Table Name" in text and "YAML file" in text:
        score += 50_000
    if "y_academy_users_placements_details" in text:
        score += 10_000
    if "academy_dpd_salesforce_task_details" in text:
        score += 5_000
    if "z_ccbp_users_cloudwatch_interactions_with_job_readiness" in text:
        score += 5_000
    score += text.count("\tid: ") * 500
    return score


def _extract_from_transcript() -> str:
    best = ""
    best_score = 0
    for transcript in _TRANSCRIPT_CANDIDATES:
        if not transcript.is_file():
            continue
        for line in transcript.read_text(encoding="utf-8").splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("role") != "user":
                continue
            for part in obj.get("message", {}).get("content", []):
                text = part.get("text", "") if isinstance(part, dict) else ""
                if not text or "id:" not in text:
                    continue
                score = _score_catalog_text(text)
                if score > best_score:
                    best_score = score
                    best = text
    if best:
        return _clean_catalog_text(best)
    raise SystemExit(
        "Could not find TSV catalog in transcript. "
        f"Save it to {CATALOG_TSV} and run: python sync_model_catalog.py"
    )


def _yaml_blocks_to_file(blocks: list[str]) -> str:
    return "\n\n".join(b for b in blocks if b.strip()) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync workspace model catalog")
    parser.add_argument(
        "tsv_path",
        nargs="?",
        help="Path to TSV file (table_name<TAB>yaml). Omit with --from-transcript.",
    )
    parser.add_argument(
        "--from-transcript",
        action="store_true",
        help="Read latest TSV catalog from agent transcript",
    )
    parser.add_argument(
        "--no-import",
        action="store_true",
        help="Only write workspace_models.yaml, do not import to DB",
    )
    args = parser.parse_args()

    if args.from_transcript:
        raw = _extract_from_transcript()
    elif args.tsv_path:
        raw = Path(args.tsv_path).read_text(encoding="utf-8")
    elif CATALOG_TSV.is_file():
        raw = CATALOG_TSV.read_text(encoding="utf-8")
        print(f"Using {CATALOG_TSV}")
    else:
        parser.error("Provide tsv_path, --from-transcript, or create model_catalog.tsv")

    rows = _split_catalog_rows(raw)
    if not rows:
        raise SystemExit("No model rows parsed from catalog")

    yaml_blocks = [yaml for _name, yaml in rows if yaml.startswith("id:")]
    combined = _yaml_blocks_to_file(yaml_blocks)
    YAML_OUT.write_text(combined, encoding="utf-8")
    print(f"Wrote {len(yaml_blocks)} model(s) to {YAML_OUT}")

    if args.no_import:
        return

    db = SessionLocal()
    try:
        result = _import_models_impl(
            ModelYamlImportIn(yaml=combined, generate_overviews=True),
            db,
        )
    finally:
        db.close()

    print(f"Imported {len(result.tables)} table(s)")
    for t in result.tables:
        status = "created" if t.created else "updated"
        print(
            f"  {t.model_id}: {t.full_table_id} ({status}, "
            f"{t.columns_imported} cols, {t.relations_imported} relations)"
        )
    if result.errors:
        print("Errors:")
        for err in result.errors:
            print(f"  - {err}")


if __name__ == "__main__":
    main()
