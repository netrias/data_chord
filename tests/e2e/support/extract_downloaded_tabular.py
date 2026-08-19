"""Extract a tabular file from a Stage 5 download archive."""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip-path", required=True, type=Path)
    parser.add_argument("--suffix", choices=(".csv", ".tsv"), required=True)
    parser.add_argument("--output-path", required=True, type=Path)
    args = parser.parse_args()

    with zipfile.ZipFile(args.zip_path, "r") as archive:
        entry_names = archive.namelist()
        entry_name = next((name for name in entry_names if name.endswith(args.suffix)), None)
        if entry_name is None:
            available_entries = ", ".join(entry_names)
            raise ValueError(f"No {args.suffix} found in download zip. Entries: {available_entries}")
        args.output_path.write_bytes(archive.read(entry_name))


if __name__ == "__main__":
    main()
