#!/usr/bin/env python3
"""Prepare and run the isolated Data Chord demo."""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence

import uvicorn


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _parse_args(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare and run the packaged Data Chord demo")
    # Docker must accept host traffic; demo-local overrides this with loopback.
    parser.add_argument("--host", default="0.0.0.0")  # nosec B104
    parser.add_argument("--port", type=_port, default=8000)
    parser.add_argument("--reload", action="store_true")
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> None:
    args = _parse_args(arguments)
    os.environ["DATA_CHORD_MODE"] = "demo"
    os.environ["DATA_CHORD_PROFILE"] = "portable"

    from src.app.demo_mode import prepare_demo_runtime

    asyncio.run(prepare_demo_runtime())

    if args.reload:
        uvicorn.run(
            "backend.app.main:app",
            host=args.host,
            port=args.port,
            proxy_headers=True,
            reload=True,
            reload_excludes=[".venv"],
        )
        return
    uvicorn.run(
        "backend.app.main:app",
        host=args.host,
        port=args.port,
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
