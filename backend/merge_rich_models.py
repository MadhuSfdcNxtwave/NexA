"""Merge rich Hex YAML blocks into workspace_models.yaml (replace stubs by model id)."""
from __future__ import annotations

import re
from pathlib import Path

from model_yaml import _split_model_documents

BACKEND = Path(__file__).parent
WORKSPACE = BACKEND / "workspace_models.yaml"
RICH_DIR = BACKEND / "rich_models"
EXTRACT = BACKEND / "model_catalog_extract.txt"


def _raw_from_text(text: str) -> dict[str, str]:
    text = re.sub(r"</?timestamp>.*?</timestamp>\s*", "", text, flags=re.S)
    text = re.sub(r"<user_query>\s*", "", text)
    text = re.sub(r"\s*</user_query>\s*$", "", text)
    out: dict[str, str] = {}
    for block in _split_model_documents(text):
        m = re.match(r"id:\s*(\S+)", block)
        if m:
            out[m.group(1)] = block
    return out


def _raw_from_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return _raw_from_text(path.read_text(encoding="utf-8"))


def main() -> None:
    base = _raw_from_file(WORKSPACE)
    rich: dict[str, str] = {}
    rich.update(_raw_from_file(EXTRACT))

    if RICH_DIR.is_dir():
        for path in sorted(RICH_DIR.glob("*.yaml")):
            block = path.read_text(encoding="utf-8").strip()
            m = re.match(r"id:\s*(\S+)", block)
            if m:
                rich[m.group(1)] = block

    merged = {**base, **rich}
    order = list(base.keys())
    for mid in rich:
        if mid not in order:
            order.append(mid)

    combined = "\n\n".join(merged[mid] for mid in order if mid in merged) + "\n"
    WORKSPACE.write_text(combined, encoding="utf-8")
    print(f"Merged {len(merged)} models ({len(rich)} rich overrides) -> {WORKSPACE}")


if __name__ == "__main__":
    main()
