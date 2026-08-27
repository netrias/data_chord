"""Transport-neutral results produced by the harmonization application."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.harmonization import HarmonizationManifestSummary, HarmonizeStatus


@dataclass(frozen=True)
class HarmonizationWorkflowResult:
    """The result needed to complete a durable harmonization job."""

    job_id: str
    status: HarmonizeStatus
    detail: str
    job_id_available: bool = False
    manifest_summary: HarmonizationManifestSummary | None = None


__all__ = ["HarmonizationWorkflowResult"]
