"""Run the FastAPI application inside the container."""

from __future__ import annotations

import logging

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.stage_1_upload.router import STAGE_ONE_STATIC_PATH, stage_one_router, stage_two_router

APP_TITLE = "Data Chord"
APP_DESCRIPTION = "Data harmonization workflow bootstrap application."


def _load_env_file() -> None:
    """why: support local development secrets via a .env file."""
    env_path = Path(".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        _ = os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _configure_logging() -> None:
    """why: ensure consistent logging across the container."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def create_app() -> FastAPI:
    """why: construct the FastAPI instance with routers and middleware."""
    _load_env_file()
    _configure_logging()
    app = FastAPI(title=APP_TITLE, description=APP_DESCRIPTION)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(stage_one_router)
    app.include_router(stage_two_router)
    static_mount = StaticFiles(directory=str(STAGE_ONE_STATIC_PATH))
    app.mount("/assets/stage-1", static_mount, name="stage_one_static")
    app.add_api_route("/", _redirect_to_stage_one, include_in_schema=False)

    return app


async def _redirect_to_stage_one() -> RedirectResponse:
    """why: land visitors on the upload experience immediately."""
    return RedirectResponse(url="/stage-1", status_code=307)


app = create_app()
