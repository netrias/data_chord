"""Centralize project path resolution to avoid fragile parent traversals."""

from __future__ import annotations

from pathlib import Path

# Anchor from this file's location: src/domain/paths.py
_THIS_FILE = Path(__file__).resolve()
_DOMAIN_DIR = _THIS_FILE.parent
_SRC_DIR = _DOMAIN_DIR.parent
PROJECT_ROOT = _SRC_DIR.parent

# Shared static assets
SHARED_STATIC_DIR = _SRC_DIR / "shared" / "static"


def get_stage_static_dir(stage_number: int) -> Path:
    stage_name = {
        1: "stage_1_upload",
        2: "stage_2_review_columns",
        3: "stage_3_harmonize",
        4: "stage_4_review_results",
        5: "stage_5_review_summary",
    }.get(stage_number)
    if stage_name is None:
        msg = f"Invalid stage number: {stage_number}"
        raise ValueError(msg)
    return _SRC_DIR / stage_name / "static"
