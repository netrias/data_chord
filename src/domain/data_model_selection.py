"""Selected target data model version used across mapping and harmonization."""

from __future__ import annotations

from dataclasses import dataclass

LATEST_VERSION = "latest"


@dataclass(frozen=True)
class DataModelSelection:
    """Canonical representation for the chosen target data model version."""

    key: str
    version_number: int | None = None

    @classmethod
    def from_version_number(cls, key: str, version_number: int | None) -> DataModelSelection:
        return cls(key=key, version_number=version_number)

    @property
    def target_version(self) -> str:
        return str(self.version_number) if self.version_number is not None else LATEST_VERSION

    @property
    def version_label(self) -> str:
        return self.target_version


def version_number_from_label(version_label: str) -> int | None:
    cleaned = version_label.removeprefix("v")
    return int(cleaned) if cleaned.isdigit() else None


__all__ = ["DataModelSelection", "LATEST_VERSION", "version_number_from_label"]
