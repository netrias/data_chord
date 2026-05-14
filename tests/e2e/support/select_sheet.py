"""Persist selected workbook sheet metadata for a Playwright upload."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.domain.dependencies import UPLOAD_BASE_DIR  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file-id", required=True)
    parser.add_argument("--sheet-name", required=True)
    args = parser.parse_args()

    meta_path = UPLOAD_BASE_DIR / "meta" / f"{args.file_id}.json"
    payload = json.loads(meta_path.read_text())
    sheet_names = payload.get("sheet_names", [])
    if args.sheet_name not in sheet_names:
        raise ValueError(f"Unknown worksheet: {args.sheet_name}")
    payload["selected_sheet"] = args.sheet_name
    meta_path.write_text(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
