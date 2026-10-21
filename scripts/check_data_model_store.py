#!/usr/bin/env python3
"""Build every model from a Data Model Store stage; ReferenceModel validates each itself."""

from __future__ import annotations

import os
import sys
import time

from src.domain.data_model_version_reference import DataModelVersionReference
from src.integrations.data_model_store_api import (
    CollapsedPlacement,
    DataModelStoreApiRepository,
)


def _check_one(
    repository: DataModelStoreApiRepository, reference: DataModelVersionReference
) -> tuple[str, tuple[CollapsedPlacement, ...] | None]:
    """Returns a failure message, or the keys that collapsed on success."""
    label = f"{reference.data_model_key}/{reference.external_version_number}"
    started = time.time()
    try:
        model = repository.load_model(reference)
    except Exception as error:  # noqa: BLE001 - the point is to report any failure
        print(f"{label:24} FAILED  {type(error).__name__}")
        return f"{label}: {type(error).__name__}: {error}", None

    pv_cdes = sum(1 for cde in model.catalog if model.pvs.get(cde.cde_key))
    value_count = sum(len(values) for values in model.pvs.values.values())
    shared = repository.last_collapsed
    print(
        f"{label:24} {len(model.catalog):>6} {value_count:>8} "
        f"{pv_cdes:>4} {len(model.catalog) - pv_cdes:>5} {len(shared):>7} "
        f"{time.time() - started:>5.1f}s"
    )
    return "", shared


def _report_collapsed(label: str, shared: tuple[CollapsedPlacement, ...]) -> None:
    differing = [placement for placement in shared if len(placement.descriptions) > 1]
    print(f"  {label:24} {len(shared):>4} shared keys, {len(differing)} describe it differently")
    for placement in differing:
        print(f"      {placement.cde_key}: {placement.descriptions}")


def main() -> int:
    url = os.environ.get("DMS_URL", "").strip()
    key = os.environ.get("DMS_KEY", "").strip()
    if not url or not key:
        print("Set DMS_URL and DMS_KEY")
        return 2

    repository = DataModelStoreApiRepository(url, key)
    summaries = repository.list_models()
    print(f"list_models: {len(summaries)} models\n")

    header = f"{'model/version':24} {'cdes':>6} {'values':>8} {'pv':>4} {'pass':>5} {'shared':>7} {'s':>6}"
    print(header)
    print("-" * len(header))

    failures: list[str] = []
    collapsed: list[tuple[str, tuple[CollapsedPlacement, ...]]] = []
    for summary in summaries:
        for version in summary.versions:
            reference = DataModelVersionReference(
                data_model_key=summary.data_model_key,
                external_version_number=version.external_version_number,
            )
            failure, shared = _check_one(repository, reference)
            if failure:
                failures.append(failure)
            elif shared:
                label = f"{reference.data_model_key}/{reference.external_version_number}"
                collapsed.append((label, shared))

    print()
    if collapsed:
        print("CDE keys that appeared under more than one node:")
        for label, shared in collapsed:
            _report_collapsed(label, shared)
        print()

    if failures:
        print(f"FAILED: {len(failures)}")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("every model built a valid ReferenceModel")
    return 0


if __name__ == "__main__":
    sys.exit(main())
