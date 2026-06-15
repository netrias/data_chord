"""Request-scoped user identity for workflow authorization.

Axis of change: how authenticated request identity becomes the app's
UserContext without coupling route code to a specific auth provider.
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from collections.abc import Mapping
from contextvars import ContextVar, Token
from functools import lru_cache

import requests
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey

from src.settings import get_expected_alb_arn
from src.storage import UserContext

ALB_IDENTITY_HEADER = "x-amzn-oidc-identity"
ALB_DATA_HEADER = "x-amzn-oidc-data"
LOCAL_USER_ID = "local-user"
_ALB_JWT_ALGORITHM = "ES256"
_ALB_KEY_TIMEOUT_SECONDS = 3

_current_user: ContextVar[UserContext | None] = ContextVar(
    "current_user",
    default=None,
)


class InvalidUserContextError(RuntimeError):
    """Raised when request identity headers cannot be trusted."""

    pass


def current_user_context() -> UserContext:
    user = _current_user.get()
    # Local development has no ALB identity headers, but storage still needs a
    # stable owner id so the same authorization paths run in every environment.
    return user if user is not None else UserContext(user_id=LOCAL_USER_ID)


def bind_user_context(headers: Mapping[str, str]) -> Token[UserContext | None]:
    return _current_user.set(_user_context_from_headers(headers))


def reset_user_context(token: Token[UserContext | None]) -> None:
    _current_user.reset(token)


def _user_context_from_headers(headers: Mapping[str, str]) -> UserContext:
    expected_alb_arn = get_expected_alb_arn()
    signed_claims = headers.get(ALB_DATA_HEADER, "").strip()
    unsigned_user_id = headers.get(ALB_IDENTITY_HEADER, "").strip()
    if signed_claims:
        claims = _verified_alb_claims(signed_claims, expected_alb_arn)
        signed_user_id = _string_claim(claims, "sub")
        if signed_user_id is None:
            raise InvalidUserContextError("ALB identity claims are missing sub")
        # ALB sends both headers, but only x-amzn-oidc-data is signed; require
        # the unsigned convenience header to agree before trusting it.
        if unsigned_user_id and unsigned_user_id != signed_user_id:
            raise InvalidUserContextError("ALB identity header does not match signed claims")
        return UserContext(user_id=signed_user_id, email=_string_claim(claims, "email"))
    if expected_alb_arn and unsigned_user_id:
        # In hosted mode, accepting only the unsigned identity header would let
        # callers spoof workflow ownership if traffic reached the app directly.
        raise InvalidUserContextError("ALB identity header is missing signed claims")
    if not unsigned_user_id:
        return UserContext(user_id=LOCAL_USER_ID)
    return UserContext(user_id=unsigned_user_id)


def _verified_alb_claims(encoded_jwt: str, expected_alb_arn: str | None) -> Mapping[str, object]:
    header_segment, payload_segment, signature_segment = _jwt_segments(encoded_jwt)
    header = _json_segment(header_segment)
    payload = _json_segment(payload_segment)
    if header.get("alg") != _ALB_JWT_ALGORITHM:
        raise InvalidUserContextError("Unsupported ALB identity signature algorithm")
    signer = _string_claim(header, "signer")
    if not signer:
        raise InvalidUserContextError("ALB identity claims are missing signer")
    if expected_alb_arn is not None and signer != expected_alb_arn:
        # Pinning the signer prevents a valid token from another load balancer
        # from being replayed against this app.
        raise InvalidUserContextError("ALB identity signer does not match expected load balancer")
    _verify_expiration(header)
    key_id = _string_claim(header, "kid")
    if not key_id:
        raise InvalidUserContextError("ALB identity claims are missing key id")
    public_key = _public_key_for_header(key_id, signer)
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    _verify_signature(public_key, signing_input, _base64url_decode(signature_segment))
    return payload


def _jwt_segments(encoded_jwt: str) -> tuple[str, str, str]:
    segments = encoded_jwt.split(".")
    if len(segments) != 3:
        raise InvalidUserContextError("ALB identity claims are not a JWT")
    return segments[0], segments[1], segments[2]


def _json_segment(segment: str) -> Mapping[str, object]:
    try:
        data = json.loads(_base64url_decode(segment))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidUserContextError("ALB identity claims contain invalid JSON") from exc
    if not isinstance(data, Mapping):
        raise InvalidUserContextError("ALB identity claims contain invalid data")
    return data


def _verify_expiration(header: Mapping[str, object]) -> None:
    expires_at = _expiration_from_header(header.get("exp"))
    if expires_at is None:
        raise InvalidUserContextError("ALB identity claims are missing expiration")
    if expires_at <= int(time.time()):
        raise InvalidUserContextError("ALB identity claims are expired")


def _expiration_from_header(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


@lru_cache(maxsize=32)
def _public_key_for_header(key_id: str, signer: str) -> EllipticCurvePublicKey:
    region = _region_from_alb_arn(signer)
    try:
        response = requests.get(
            f"https://public-keys.auth.elb.{region}.amazonaws.com/{key_id}",
            timeout=_ALB_KEY_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        public_key = serialization.load_pem_public_key(response.content)
    except requests.RequestException as exc:
        raise InvalidUserContextError("ALB identity public key could not be fetched") from exc
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise InvalidUserContextError("ALB identity public key is invalid") from exc
    if not isinstance(public_key, EllipticCurvePublicKey):
        raise InvalidUserContextError("ALB identity key is not an EC public key")
    return public_key


def _region_from_alb_arn(signer: str) -> str:
    arn_parts = signer.split(":")
    if len(arn_parts) < 6 or arn_parts[0] != "arn" or arn_parts[2] != "elasticloadbalancing":
        raise InvalidUserContextError("ALB identity signer is not a load balancer ARN")
    return arn_parts[3]


def _verify_signature(public_key: EllipticCurvePublicKey, signing_input: bytes, signature: bytes) -> None:
    try:
        public_key.verify(_signature_for_cryptography(signature), signing_input, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError) as exc:
        raise InvalidUserContextError("ALB identity signature is invalid") from exc


def _signature_for_cryptography(signature: bytes) -> bytes:
    if len(signature) != 64:
        return signature
    # ALB provides ES256 signatures as raw r+s bytes, while cryptography expects
    # DER-encoded DSS signatures.
    r = int.from_bytes(signature[:32], byteorder="big")
    s = int.from_bytes(signature[32:], byteorder="big")
    return utils.encode_dss_signature(r, s)


def _base64url_decode(value: str) -> bytes:
    padded = value + ("=" * (-len(value) % 4))
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise InvalidUserContextError("ALB identity claims are not base64url encoded") from exc


def _string_claim(claims: Mapping[str, object], key: str) -> str | None:
    value = claims.get(key)
    return value if isinstance(value, str) and value.strip() else None


__all__ = [
    "ALB_DATA_HEADER",
    "ALB_IDENTITY_HEADER",
    "InvalidUserContextError",
    "LOCAL_USER_ID",
    "bind_user_context",
    "current_user_context",
    "reset_user_context",
]
