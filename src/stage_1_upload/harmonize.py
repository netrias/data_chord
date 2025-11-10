"""Trigger harmonization jobs via the Netrias client SDK."""

from __future__ import annotations

import importlib
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HarmonizeResult:
    """why: capture essential metadata about a harmonization run."""

    job_id: str
    status: str
    detail: str


class HarmonizeClientProtocol(Protocol):
    """why: describe the subset of the SDK we rely on."""

    def configure(self, *, api_key: str) -> None:
        ...

    def harmonize(
        self,
        *,
        file_path: str,
        target_schema: str,
        manual_overrides: dict[str, str],
    ) -> Mapping[str, object] | object:
        ...


class NetriasClientConstructor(Protocol):
    """why: describe how to instantiate the Netrias client."""

    def __call__(self) -> HarmonizeClientProtocol:
        ...


class HarmonizeService:
    """why: abstract the Netrias client and allow graceful fallbacks."""

    def __init__(self) -> None:
        self._api_key: str | None = os.getenv("NETRIAS_API_KEY")
        self._client: HarmonizeClientProtocol | None = self._build_client()

    def _build_client(self) -> HarmonizeClientProtocol | None:
        try:
            module = importlib.import_module("netrias_client")
            client_cls = cast(NetriasClientConstructor, getattr(module, "NetriasClient"))
        except (ModuleNotFoundError, AttributeError):
            return None
        if not self._api_key:
            logger.warning("NETRIAS_API_KEY missing; harmonize calls will be stubbed.")
            return None
        client = client_cls()  # type: ignore[call-arg]
        client.configure(api_key=self._api_key)
        return client

    def run(
        self,
        *,
        file_path: Path,
        target_schema: str,
        manual_overrides: dict[str, str],
    ) -> HarmonizeResult:
        job_id = uuid4().hex
        if not self._client:
            detail = "Netrias client unavailable; returning a stubbed job."
            logger.warning(detail, extra={"job_id": job_id})
            return HarmonizeResult(job_id=job_id, status="queued", detail=detail)

        try:
            raw_response = self._client.harmonize(
                file_path=str(file_path),
                target_schema=target_schema,
                manual_overrides=manual_overrides,
            )
            if isinstance(raw_response, Mapping):
                response_dict = dict(cast(Mapping[str, object], raw_response))
                detail = str(response_dict.get("message", "Harmonization started"))
                status = str(response_dict.get("status", "running"))
                remote_job_id = str(response_dict.get("job_id", job_id))
            else:
                detail = str(getattr(raw_response, "message", "Harmonization started"))
                status = str(getattr(raw_response, "status", "running"))
                remote_job_id = str(getattr(raw_response, "job_id", job_id))
            return HarmonizeResult(job_id=str(remote_job_id), status=str(status), detail=str(detail))
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Harmonize call failed; falling back to stub", exc_info=exc)
            return HarmonizeResult(job_id=job_id, status="failed", detail=str(exc))
