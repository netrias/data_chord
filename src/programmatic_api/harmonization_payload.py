"""Decode the pinned client's bounded gzip harmonization envelope."""

from __future__ import annotations

import gzip
import json
import zlib
from io import BytesIO

from pydantic import ValidationError

from src.programmatic_api.schemas import HarmonizationSubmissionRequest

_MAX_DECOMPRESSED_BYTES = 64 * 1024 * 1024


class InvalidHarmonizationPayloadError(ValueError):
    """Raised when a request is not the current gzip document contract."""


class HarmonizationPayloadTooLargeError(InvalidHarmonizationPayloadError):
    """Raised before JSON parsing when gzip expansion exceeds the safe limit."""


def decode_harmonization_payload(body: bytes) -> HarmonizationSubmissionRequest:
    try:
        with gzip.GzipFile(fileobj=BytesIO(body), mode="rb") as compressed:
            raw_json = compressed.read(_MAX_DECOMPRESSED_BYTES + 1)
    except (EOFError, gzip.BadGzipFile, OSError, zlib.error) as exc:
        raise InvalidHarmonizationPayloadError("harmonization payload is not valid gzip") from exc
    if len(raw_json) > _MAX_DECOMPRESSED_BYTES:
        raise HarmonizationPayloadTooLargeError("harmonization payload is too large")
    try:
        payload = json.loads(raw_json)
        return HarmonizationSubmissionRequest.model_validate(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise InvalidHarmonizationPayloadError("harmonization payload is invalid") from exc


__all__ = [
    "HarmonizationPayloadTooLargeError",
    "InvalidHarmonizationPayloadError",
    "decode_harmonization_payload",
]
