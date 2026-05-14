"""Create an XLSX workbook fixture for Playwright E2E tests."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    workbook = Workbook()
    keep = cast(Worksheet, workbook.active)
    keep.title = "Keep"
    keep.append(["status"])
    keep.append(["unchanged"])
    patients = cast(Worksheet, workbook.create_sheet("Patients"))
    patients.append(["col_a", "col_b"])
    patients.append(["alpha", "value, one"])
    patients.append(["alpha", "value two"])
    patients.append(["beta", "value three"])
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)


if __name__ == "__main__":
    main()
