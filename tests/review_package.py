"""Read the same multipart fields that the Stage 5 browser receives."""

import json
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser

from httpx import Response


@dataclass(frozen=True)
class ReviewPackage:
    summary: dict
    archive: bytes

    @classmethod
    def read(cls, response: Response) -> "ReviewPackage":
        response.raise_for_status()
        header = f"Content-Type: {response.headers['content-type']}\r\n\r\n".encode()
        message = BytesParser(policy=policy.default).parsebytes(header + response.content)
        parts = {part.get_param("name", header="Content-Disposition"): part.get_payload(decode=True)
                 for part in message.iter_parts()}
        summary = parts["summary"]
        archive = parts["download"]
        assert isinstance(summary, bytes) and isinstance(archive, bytes)
        return cls(json.loads(summary), archive)
