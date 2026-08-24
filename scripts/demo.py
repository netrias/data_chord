#!/usr/bin/env python3
"""Prepare and run the isolated Data Chord demo."""

from __future__ import annotations

import asyncio
import os

import uvicorn


def main() -> None:
    os.environ["DATA_CHORD_MODE"] = "demo"
    os.environ["DATA_CHORD_PROFILE"] = "portable"

    from src.app.demo_mode import prepare_demo_runtime

    asyncio.run(prepare_demo_runtime())

    from backend.app.main import app

    uvicorn.run(app, host="0.0.0.0", port=8000, proxy_headers=True)


if __name__ == "__main__":
    main()
