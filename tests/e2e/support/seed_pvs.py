"""Seed column assignments and permissible values for Playwright E2E tests."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# noqa: E402 - ensure repo root is on sys.path before importing application modules.
from src.domain.column_assignment import ColumnAssignment  # noqa: E402
from src.domain.data_model_cache import get_session_cache  # noqa: E402
from src.domain.pv_persistence import save_pv_manifest_to_disk  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file-id", required=True)
    parser.add_argument("--column-id", type=int, required=True)
    parser.add_argument("--column-name", required=True)
    parser.add_argument("--cde-key", required=True)
    parser.add_argument("--harmonization", default="harmonizable")
    parser.add_argument("--values", nargs="+", required=True)
    args = parser.parse_args()

    cache = get_session_cache(args.file_id)
    cache.set_column_assignments({
        args.column_id: ColumnAssignment(
            args.column_id,
            args.column_name,
            args.cde_key,
            args.harmonization,
        )
    })
    pv_map = {args.cde_key: frozenset(args.values)}
    cache.set_pvs_batch(pv_map)
    save_pv_manifest_to_disk(args.file_id, cache, pv_map)


if __name__ == "__main__":
    main()
