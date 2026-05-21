"""Persist selected workbook sheet metadata for a Playwright upload."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import src.domain.dependencies as dependencies  # noqa: E402
from src.domain.workflow_artifact_store import save_upload_metadata  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file-id", required=True)
    parser.add_argument("--sheet-name", required=True)
    args = parser.parse_args()

    upload_storage = dependencies.get_upload_storage()
    workflow_storage = dependencies.get_workflow_storage()
    user = dependencies.get_user_context()

    meta = upload_storage.select_sheet(args.file_id, args.sheet_name)
    save_upload_metadata(workflow_storage, user, upload_storage, meta)


if __name__ == "__main__":
    main()
