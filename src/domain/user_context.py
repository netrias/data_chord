"""Request-scoped user identity for workflow authorization.

Axis of change: how authenticated request identity becomes the app's
UserContext without coupling route code to a specific auth provider.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar, Token

from src.domain.storage import UserContext

ALB_IDENTITY_HEADER = "x-amzn-oidc-identity"
LOCAL_USER_ID = "local-user"

_current_user: ContextVar[UserContext | None] = ContextVar(
    "current_user",
    default=None,
)


def current_user_context() -> UserContext:
    user = _current_user.get()
    return user if user is not None else UserContext(user_id=LOCAL_USER_ID)


def bind_user_context(headers: Mapping[str, str]) -> Token[UserContext | None]:
    return _current_user.set(_user_context_from_headers(headers))


def reset_user_context(token: Token[UserContext | None]) -> None:
    _current_user.reset(token)


def _user_context_from_headers(headers: Mapping[str, str]) -> UserContext:
    user_id = headers.get(ALB_IDENTITY_HEADER, "").strip()
    if not user_id:
        return UserContext(user_id=LOCAL_USER_ID)
    return UserContext(user_id=user_id)


__all__ = [
    "ALB_IDENTITY_HEADER",
    "LOCAL_USER_ID",
    "bind_user_context",
    "current_user_context",
    "reset_user_context",
]
