"""Run the FastAPI application inside the container."""

from __future__ import annotations

import os
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.app.error_handlers import register_api_error_handlers
from src.auth.user_context import (
    InvalidProgrammaticApiKeyError,
    InvalidUserContextError,
    ProgrammaticApiNotConfiguredError,
    bind_programmatic_artifact_user_context,
    bind_programmatic_user_context,
    bind_user_context,
    current_user_context,
    is_programmatic_artifact_path,
    reset_user_context,
)
from src.observability.events import (
    REQUEST_ID_HEADER,
    RequestTrace,
    bind_request_id,
    configure_structured_logging,
    elapsed_ms,
    log_request,
    request_id_from_header,
    reset_request_id,
)
from src.observability.router import observability_router
from src.paths import PROJECT_ROOT, SHARED_STATIC_DIR, get_stage_static_dir
from src.programmatic_api.router import programmatic_api_router
from src.stage_1_upload.router import stage_one_router
from src.stage_2_review_columns.router import stage_two_router
from src.stage_3_harmonize.router import stage_three_router
from src.stage_4_review_results.router import stage_four_router
from src.stage_5_review_summary.router import stage_five_router
from src.storage import WorkflowAccessDeniedError, WorkflowNotFoundError

APP_TITLE = "Data Chord"
APP_DESCRIPTION = "Data harmonization workflow bootstrap application."
_DEFAULT_ASSET_VERSION = "local"
_ASSET_VERSION_VAR = "DATA_CHORD_ASSET_VERSION"
_PROGRAMMATIC_API_MAX_BODY_BYTES = 2 * 1024 * 1024
_PROGRAMMATIC_HARMONIZATION_MAX_BODY_BYTES = 10 * 1024 * 1024


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
    from src.app.dependencies import shutdown_services

    await shutdown_services()


def create_app() -> FastAPI:
    _load_env_file()
    _configure_logging()

    # Validate required configuration at startup (fail-fast)
    from src.settings import validate_required_config

    validate_required_config()
    from src.app.dependencies import validate_runtime_services

    validate_runtime_services()

    app = FastAPI(title=APP_TITLE, description=APP_DESCRIPTION, lifespan=_lifespan)
    app.state.asset_version = _asset_version()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_allow_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(_ProgrammaticRequestBodyLimitMiddleware)
    app.middleware("http")(_bind_user_context)
    register_api_error_handlers(app)
    app.add_exception_handler(WorkflowAccessDeniedError, _workflow_access_denied)
    app.add_exception_handler(WorkflowNotFoundError, _workflow_not_found)

    _register_routes(app)

    return app


def _register_routes(app: FastAPI) -> None:
    app.include_router(observability_router)
    app.include_router(programmatic_api_router)
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


async def _serve_favicon() -> FileResponse:
    return FileResponse(SHARED_STATIC_DIR / "favicon.ico", media_type="image/x-icon")


async def _healthz() -> dict[str, str]:
    return {"status": "ok"}


async def _bind_user_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    with _observe_request(request) as observation:
        response = await _authenticated_response(request, call_next, observation)
        observation.status_code = response.status_code
        response.headers[REQUEST_ID_HEADER] = observation.request_id
        return response


@dataclass
class _RequestObservation:
    request_id: str
    started_at: float
    status_code: int = 500
    user_id: str | None = None


@contextmanager
def _observe_request(request: Request) -> Iterator[_RequestObservation]:
    request_id = request_id_from_header(request.headers.get(REQUEST_ID_HEADER))
    request.state.request_id = request_id
    token = bind_request_id(request_id)
    observation = _RequestObservation(request_id, time.perf_counter())
    try:
        yield observation
    finally:
        if _should_log_request(request.url.path):
            log_request(
                RequestTrace(
                    method=request.method,
                    path=request.url.path,
                    status_code=observation.status_code,
                    duration_ms=elapsed_ms(observation.started_at),
                    request_id=request_id,
                    user_id=observation.user_id,
                )
            )
        reset_request_id(token)


async def _authenticated_response(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
    observation: _RequestObservation,
) -> Response:
    if request.url.path == "/healthz":
        return await call_next(request)
    try:
        token = _bind_authenticated_user_context(request)
    except (ProgrammaticApiNotConfiguredError, InvalidProgrammaticApiKeyError, InvalidUserContextError) as exc:
        return _identity_error_response(request, exc)
    try:
        observation.user_id = current_user_context().user_id
        return await call_next(request)
    finally:
        reset_user_context(token)


def _identity_error_response(request: Request, error: Exception) -> Response:
    if isinstance(error, ProgrammaticApiNotConfiguredError):
        return JSONResponse({"detail": "Programmatic API is not configured."}, status_code=503)
    if isinstance(error, InvalidProgrammaticApiKeyError):
        detail = "Invalid artifact URL." if is_programmatic_artifact_path(request.url.path) else "Invalid API key."
        return JSONResponse({"detail": detail}, status_code=401)
    return Response("Invalid identity headers", status_code=401)


def _bind_authenticated_user_context(request: Request):
    if is_programmatic_artifact_path(request.url.path):
        return bind_programmatic_artifact_user_context(
            request.url.path,
            request.query_params,
        )
    if _is_programmatic_api(request.url.path):
        return bind_programmatic_user_context(request.headers)
    return bind_user_context(request.headers)


class _ProgrammaticRequestBodyLimitMiddleware:
    """Bound API request memory before FastAPI parses the document."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not _requires_body_limit(scope):
            await self._app(scope, receive, send)
            return
        body_limit = (
            _PROGRAMMATIC_HARMONIZATION_MAX_BODY_BYTES
            if scope["path"] == "/api/v1/jobs/harmonize"
            else _PROGRAMMATIC_API_MAX_BODY_BYTES
        )
        messages = await _read_bounded_body(receive, body_limit)
        if messages is None:
            response = JSONResponse(
                {"detail": "Request body is too large."}, status_code=status.HTTP_413_CONTENT_TOO_LARGE
            )
            await response(scope, receive, send)
            return
        message_index = 0

        async def replay_body() -> Message:
            nonlocal message_index
            if message_index >= len(messages):
                return {"type": "http.request", "body": b"", "more_body": False}
            message = messages[message_index]
            message_index += 1
            return message

        await self._app(scope, replay_body, send)


def _is_programmatic_api(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def _should_log_request(path: str) -> bool:
    return path != "/healthz" and path != "/favicon.ico" and not path.startswith("/assets/")


async def _workflow_access_denied(_request: Request, _exc: Exception) -> Response:
    return Response("Forbidden", status_code=403)


async def _workflow_not_found(_request: Request, _exc: Exception) -> Response:
    return Response("Workflow not found", status_code=404)


async def _redirect_to_stage_one() -> RedirectResponse:
    return RedirectResponse(url="/stage-1", status_code=307)


def _requires_body_limit(scope: Scope) -> bool:
    return (
        scope["type"] == "http" and _is_programmatic_api(scope["path"]) and scope["method"] in {"POST", "PUT", "PATCH"}
    )


async def _read_bounded_body(receive: Receive, limit: int) -> list[Message] | None:
    messages: list[Message] = []
    body_size = 0
    while True:
        message = await receive()
        messages.append(message)
        if message["type"] == "http.disconnect":
            break
        if message["type"] != "http.request":
            continue
        body_size += len(message.get("body", b""))
        if body_size > limit:
            return None
        if not message.get("more_body", False):
            break
    return messages


app = create_app()
