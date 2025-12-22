"""Run the FastAPI application inside the container."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from src.stage_1_upload.router import STAGE_ONE_STATIC_PATH, stage_one_router
from src.stage_2_review_columns.router import STAGE_TWO_STATIC_PATH, stage_two_router
from src.stage_3_harmonize.router import STAGE_THREE_STATIC_PATH, stage_three_router
from src.stage_4_review_results.router import STAGE_FOUR_STATIC_PATH, stage_four_router
from src.stage_5_review_summary.router import STAGE_FIVE_STATIC_PATH, stage_five_router

SHARED_STATIC_PATH: Path = Path(__file__).resolve().parents[2] / "src" / "shared" / "static"

APP_TITLE = "Data Chord"
APP_DESCRIPTION = "Data harmonization workflow bootstrap application."


def _is_dev_mode() -> bool:
    """why: check if running in development mode for cache control."""
    return os.getenv("DEV_MODE", "").lower() == "true"


class DevStaticFiles(StaticFiles):
    """why: disable caching in dev mode to avoid stale JS/CSS issues."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if _is_dev_mode():
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response


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


def _cors_allow_origins() -> list[str]:
    """why: allow tightening CORS in hosted environments without breaking local dev.

    NOTE: Defaults to '*' for local development convenience. In production,
    set CORS_ALLOW_ORIGINS to explicit origins (e.g., 'https://app.example.com').
    """
    raw = os.getenv("CORS_ALLOW_ORIGINS", "*").strip()
    if not raw:
        return ["*"]
    if raw == "*":
        return ["*"]

    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return origins or ["*"]


def create_app() -> FastAPI:
    """why: construct the FastAPI instance with routers and middleware."""
    _load_env_file()
    _configure_logging()
    app = FastAPI(title=APP_TITLE, description=APP_DESCRIPTION)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_allow_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(stage_one_router)
    app.include_router(stage_two_router)
    app.include_router(stage_three_router)
    app.include_router(stage_four_router)
    app.include_router(stage_five_router)
    app.mount("/assets/shared", DevStaticFiles(directory=str(SHARED_STATIC_PATH)), name="shared_static")
    app.mount("/assets/stage-1", DevStaticFiles(directory=str(STAGE_ONE_STATIC_PATH)), name="stage_one_static")
    app.mount("/assets/stage-2", DevStaticFiles(directory=str(STAGE_TWO_STATIC_PATH)), name="stage_two_static")
    app.mount("/assets/stage-3", DevStaticFiles(directory=str(STAGE_THREE_STATIC_PATH)), name="stage_three_static")
    app.mount("/assets/stage-4", DevStaticFiles(directory=str(STAGE_FOUR_STATIC_PATH)), name="stage_four_static")
    app.mount("/assets/stage-5", DevStaticFiles(directory=str(STAGE_FIVE_STATIC_PATH)), name="stage_five_static")
    app.add_api_route("/", _redirect_to_stage_one, include_in_schema=False)

    return app


async def _redirect_to_stage_one() -> RedirectResponse:
    """why: land visitors on the upload experience immediately."""
    return RedirectResponse(url="/stage-1", status_code=307)


app = create_app()
