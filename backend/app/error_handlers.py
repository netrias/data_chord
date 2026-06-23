"""Generic API error boundary for safe responses and operator-rich logs."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import FastAPI, Request, status
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from src.auth.user_context import current_user_context
from src.observability.events import (
    CLIENT_EVENTS_ENDPOINT,
    REQUEST_ID_HEADER,
    log_api_request_failed,
)

GENERIC_API_ERROR_DETAIL = "We couldn't process this request. Please try again."


def register_api_error_handlers(app: FastAPI) -> None:
    """Keep API error response policy in one place instead of scattering it through routes."""
    app.add_exception_handler(RequestValidationError, _request_validation_failed)
    app.add_exception_handler(StarletteHTTPException, _http_request_failed)
    app.add_exception_handler(Exception, _unexpected_api_request_failed)


async def _request_validation_failed(request: Request, exc: Exception) -> Response:
    """Log validation specifics while returning the same safe detail shape as other API failures."""
    if not isinstance(exc, RequestValidationError):
        raise exc
    if not _uses_generic_api_error(request):
        return await request_validation_exception_handler(request, exc)
    log_api_request_failed(
        method=request.method,
        path=request.url.path,
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        error_type=type(exc).__name__,
        request_id=_request_id_for_log(request),
        validation_errors=_validation_error_mappings(exc),
        user=current_user_context(),
    )
    return _generic_api_error_response(status.HTTP_422_UNPROCESSABLE_CONTENT, request)


async def _http_request_failed(request: Request, exc: Exception) -> Response:
    """Preserve route status codes, but keep route-level details in logs rather than responses."""
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    if not _uses_generic_api_error(request):
        return await http_exception_handler(request, exc)
    log_api_request_failed(
        method=request.method,
        path=request.url.path,
        status_code=exc.status_code,
        error_type=type(exc).__name__,
        request_id=_request_id_for_log(request),
        error_detail=exc.detail,
        user=current_user_context(),
    )
    return _generic_api_error_response(exc.status_code, request)


async def _unexpected_api_request_failed(request: Request, exc: Exception) -> Response:
    """Give users a stable 500 while logging the traceback for operators."""
    if not _uses_generic_api_error(request):
        raise exc
    log_api_request_failed(
        method=request.method,
        path=request.url.path,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_type=type(exc).__name__,
        user=current_user_context(),
        request_id=_request_id_for_log(request),
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return _generic_api_error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, request)


def _generic_api_error_response(status_code: int, request: Request) -> JSONResponse:
    """Attach request id to the safe response so client events can correlate with server logs."""
    response = JSONResponse({"detail": GENERIC_API_ERROR_DETAIL}, status_code=status_code)
    request_id = _request_id_for_log(request)
    if request_id is not None:
        response.headers[REQUEST_ID_HEADER] = request_id
    return response


def _request_id_for_log(request: Request) -> str | None:
    """Read request id from request.state because exception logging can outlive the ContextVar."""
    request_id = getattr(request.state, "request_id", None)
    return request_id if isinstance(request_id, str) else None


def _uses_generic_api_error(request: Request) -> bool:
    """Apply generic details to JSON endpoints while leaving HTML page handlers alone."""
    path = request.url.path
    if path == CLIENT_EVENTS_ENDPOINT:
        return True
    if not path.startswith("/stage-"):
        return False
    if request.method != "GET":
        return True
    return path.count("/") > 1


def _validation_error_mappings(exc: RequestValidationError) -> list[dict[str, object]]:
    """Normalize Pydantic errors before logging so unsafe raw payloads stay out of logs."""
    return [
        {str(key): value for key, value in error.items()}
        for error in exc.errors()
        if isinstance(error, Mapping)
    ]


__all__ = ["GENERIC_API_ERROR_DETAIL", "register_api_error_handlers"]
