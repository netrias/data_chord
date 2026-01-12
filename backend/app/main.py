"""Run the FastAPI application inside the container."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from src.domain.paths import PROJECT_ROOT, SHARED_STATIC_DIR, get_stage_static_dir
from src.stage_1_upload.router import stage_one_router
from src.stage_2_review_columns.router import stage_two_router
from src.stage_3_harmonize.router import stage_three_router
from src.stage_4_review_results.router import stage_four_router
from src.stage_5_review_summary.router import stage_five_router

APP_TITLE = "Data Chord"
APP_DESCRIPTION = "Data harmonization workflow bootstrap application."


def _is_dev_mode() -> bool:
    return os.getenv("DEV_MODE", "").lower() == "true"


class DevStaticFiles(StaticFiles):
    """Disable browser caching in dev mode so JS/CSS changes appear immediately."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if _is_dev_mode():
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response


def _should_skip_env_line(line: str) -> bool:
    return not line or line.startswith("#") or "=" not in line


def _parse_env_value(value: str) -> str:
    return value.strip().strip('"').strip("'")


def _set_env_if_missing(key: str, value: str) -> None:
    if key not in os.environ:
        os.environ[key] = _parse_env_value(value)


def _load_env_file() -> None:
    """Shell env vars take precedence; .env is for local dev secrets only."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if _should_skip_env_line(line):
            continue
        key, value = line.split("=", 1)
        _set_env_if_missing(key.strip(), value)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _cors_allow_origins() -> list[str]:
    """Defaults to '*' for local dev; set CORS_ALLOW_ORIGINS in production."""
    raw = os.getenv("CORS_ALLOW_ORIGINS", "*").strip()
    if raw in ("", "*"):
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()] or ["*"]


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Manage app lifecycle: shutdown cleanup for HTTP clients."""
    yield
    # Shutdown: clean up resources
    from src.domain.dependencies import cleanup_services

    cleanup_services()


def create_app() -> FastAPI:
    _load_env_file()
    _configure_logging()

    # Validate required configuration at startup (fail-fast)
    from src.domain.config import validate_required_config

    validate_required_config()

    app = FastAPI(title=APP_TITLE, description=APP_DESCRIPTION, lifespan=_lifespan)

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

    app.mount("/assets/shared", DevStaticFiles(directory=str(SHARED_STATIC_DIR)), name="shared_static")
    stage_name_map = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
    for stage in range(1, 6):
        app.mount(
            f"/assets/stage-{stage}",
            DevStaticFiles(directory=str(get_stage_static_dir(stage))),
            name=f"stage_{stage_name_map[stage]}_static",
        )

    app.add_api_route("/", _redirect_to_stage_one, include_in_schema=False)

    return app


async def _redirect_to_stage_one() -> RedirectResponse:
    return RedirectResponse(url="/stage-1", status_code=307)


app = create_app()
