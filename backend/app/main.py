"""Run the FastAPI application inside the container."""

from __future__ import annotations

import os
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse, Response
from starlette.types import Scope

from src.domain.observability import (
    REQUEST_ID_HEADER,
    RequestTrace,
    bind_request_id,
    configure_structured_logging,
    elapsed_ms,
    log_request,
    request_id_from_header,
    reset_request_id,
)
from src.domain.paths import PROJECT_ROOT, SHARED_STATIC_DIR, get_stage_static_dir
from src.domain.storage import WorkflowAccessDeniedError, WorkflowNotFoundError
from src.domain.user_context import (
    InvalidUserContextError,
    bind_user_context,
    current_user_context,
    reset_user_context,
)
from src.observability.router import observability_router
from src.stage_1_upload.router import stage_one_router
from src.stage_2_review_columns.router import stage_two_router
from src.stage_3_harmonize.router import stage_three_router
from src.stage_4_review_results.router import stage_four_router
from src.stage_5_review_summary.router import stage_five_router

APP_TITLE = "Data Chord"
APP_DESCRIPTION = "Data harmonization workflow bootstrap application."
_DEFAULT_ASSET_VERSION = "local"
_ASSET_VERSION_VAR = "DATA_CHORD_ASSET_VERSION"


def _is_dev_mode() -> bool:
    return os.getenv("DEV_MODE", "").lower() == "true"


class AppStaticFiles(StaticFiles):
    """Keep deployed browsers from running stale stage JavaScript after rollout."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if _is_dev_mode():
            response.headers["Cache-Control"] = "no-store, must-revalidate"
        else:
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


def _asset_version() -> str:
    version = os.getenv(_ASSET_VERSION_VAR, _DEFAULT_ASSET_VERSION).strip()
    return version or _DEFAULT_ASSET_VERSION


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
    configure_structured_logging()


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
    app.state.asset_version = _asset_version()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_allow_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.middleware("http")(_bind_user_context)
    app.add_exception_handler(WorkflowAccessDeniedError, _workflow_access_denied)
    app.add_exception_handler(WorkflowNotFoundError, _workflow_not_found)

    app.include_router(observability_router)
    app.include_router(stage_one_router)
    app.include_router(stage_two_router)
    app.include_router(stage_three_router)
    app.include_router(stage_four_router)
    app.include_router(stage_five_router)

    app.mount("/assets/shared", AppStaticFiles(directory=str(SHARED_STATIC_DIR)), name="shared_static")
    stage_name_map = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
    for stage in range(1, 6):
        app.mount(
            f"/assets/stage-{stage}",
            AppStaticFiles(directory=str(get_stage_static_dir(stage))),
            name=f"stage_{stage_name_map[stage]}_static",
        )

    app.add_api_route("/favicon.ico", _serve_favicon, include_in_schema=False)
    app.add_api_route("/healthz", _healthz, include_in_schema=False)
    app.add_api_route("/", _redirect_to_stage_one, include_in_schema=False)

    return app


async def _serve_favicon() -> FileResponse:
    return FileResponse(SHARED_STATIC_DIR / "favicon.ico", media_type="image/x-icon")


async def _healthz() -> dict[str, str]:
    return {"status": "ok"}


async def _bind_user_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    request_id = request_id_from_header(request.headers.get(REQUEST_ID_HEADER))
    request_token = bind_request_id(request_id)
    started_at = time.perf_counter()
    status_code = 500
    user_id: str | None = None
    user_token = None
    try:
        try:
            user_token = bind_user_context(request.headers)
            user_id = current_user_context().user_id
        except InvalidUserContextError:
            status_code = 401
            response = Response("Invalid identity headers", status_code=status_code)
        else:
            # ContextVar keeps route signatures clean while still scoping storage
            # authorization to the current request.
            response = await call_next(request)
            status_code = response.status_code
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
    finally:
        if user_token is not None:
            reset_user_context(user_token)
        if _should_log_request(request.url.path):
            log_request(
                RequestTrace(
                    method=request.method,
                    path=request.url.path,
                    status_code=status_code,
                    duration_ms=elapsed_ms(started_at),
                    request_id=request_id,
                    user_id=user_id,
                )
            )
        reset_request_id(request_token)


def _should_log_request(path: str) -> bool:
    return path != "/healthz" and path != "/favicon.ico" and not path.startswith("/assets/")


async def _workflow_access_denied(_request: Request, _exc: Exception) -> Response:
    return Response("Forbidden", status_code=403)


async def _workflow_not_found(_request: Request, _exc: Exception) -> Response:
    return Response("Workflow not found", status_code=404)


async def _redirect_to_stage_one() -> RedirectResponse:
    return RedirectResponse(url="/stage-1", status_code=307)


app = create_app()
