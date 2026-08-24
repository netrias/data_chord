"""Feature tests for request-scoped workflow ownership."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from httpx import AsyncClient
from starlette.datastructures import Headers

from src.auth.user_context import (
    ALB_DATA_HEADER,
    ALB_IDENTITY_HEADER,
    LOCAL_USER_ID,
    TRUSTED_PROXY_USER_HEADER,
    InvalidUserContextError,
    _user_context_from_headers,
    current_user_context,
)
from tests.conftest import TEST_CSV_CONTENT_TYPE, TEST_TARGET_SCHEMA

pytestmark = pytest.mark.asyncio


async def test_trusted_proxy_identity_controls_workflow_ownership(
    app_client: AsyncClient,
    sample_csv_path: Path,
) -> None:
    # Given: Alice uploaded a workflow and Bob has not been granted access
    upload_response = await app_client.post(
        "/stage-1/upload",
        headers={TRUSTED_PROXY_USER_HEADER: "alice"},
        files={"file": (sample_csv_path.name, sample_csv_path.read_bytes(), TEST_CSV_CONTENT_TYPE)},
    )
    assert upload_response.status_code == 201
    file_id = upload_response.json()["file_id"]

    # When: Bob tries to analyze Alice's upload by guessing the file id
    response = await app_client.post(
        "/stage-1/analyze",
        headers={TRUSTED_PROXY_USER_HEADER: "bob"},
        json={"file_id": file_id, "data_model_key": TEST_TARGET_SCHEMA, "external_version_number": "11.0.4"},
    )

    # Then: workflow storage denies access before the file is processed
    assert response.status_code == 403


async def test_shared_identity_is_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: no request identity headers are present
    monkeypatch.setenv("DATA_CHORD_PROFILE", "portable")
    monkeypatch.setenv("DATA_CHORD_IDENTITY_SOURCE", "shared")
    assert _user_context_from_headers(Headers()).user_id != ""

    # When: local/test code asks for the request user
    user = _user_context_from_headers(Headers())

    # Then: the documented shared fallback owner is stable
    assert user.user_id == LOCAL_USER_ID


async def test_portable_profile_shares_workflows_across_proxy_identities(
    app_client: AsyncClient,
    sample_csv_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given Alice uploaded a workflow to one shared portable installation.
    monkeypatch.setenv("DATA_CHORD_PROFILE", "portable")
    monkeypatch.setenv("DATA_CHORD_IDENTITY_SOURCE", "shared")
    monkeypatch.setattr("src.app.dependencies._workflow_cleanup", None)
    upload_response = await app_client.post(
        "/stage-1/upload",
        headers={ALB_IDENTITY_HEADER: "alice"},
        files={
            "file": (
                sample_csv_path.name,
                sample_csv_path.read_bytes(),
                TEST_CSV_CONTENT_TYPE,
            )
        },
    )
    assert upload_response.status_code == 201
    file_id = upload_response.json()["file_id"]

    # When Bob uses the same workflow through the public request boundary.
    response = await app_client.post(
        "/stage-1/analyze",
        headers={ALB_IDENTITY_HEADER: "bob"},
        json={
            "file_id": file_id,
            "data_model_key": TEST_TARGET_SCHEMA,
            "external_version_number": "11.0.4",
        },
    )

    # Then the application treats both proxy identities as the shared local owner.
    assert response.status_code == 200


async def test_signed_alb_claims_control_workflow_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: hosted config expects one ALB signer and the request includes signed ALB claims
    alb_arn = "arn:aws:elasticloadbalancing:us-east-2:123456789012:loadbalancer/app/data-chord/abc123"
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    token = _signed_alb_token(private_key, alb_arn, {"sub": "alice", "email": "alice@example.test"})
    monkeypatch.setenv("DATA_CHORD_ALB_ARN", alb_arn)
    monkeypatch.setenv("DATA_CHORD_IDENTITY_SOURCE", "signed_alb")
    monkeypatch.setattr("src.auth.user_context._public_key_for_header", lambda _key_id, _signer: public_key)

    # When: the app binds user context from headers
    user = _user_context_from_headers(Headers({ALB_DATA_HEADER: token, ALB_IDENTITY_HEADER: "alice"}))

    # Then: the signed claim is the source of truth
    assert user.user_id == "alice"
    assert user.email == "alice@example.test"


async def test_signed_alb_claims_reject_another_alb_signer(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a valid token was signed for an ALB other than the configured ALB.
    expected_alb_arn = "arn:aws:elasticloadbalancing:us-east-2:123456789012:loadbalancer/app/data-chord/expected"
    other_alb_arn = "arn:aws:elasticloadbalancing:us-east-2:123456789012:loadbalancer/app/data-chord/other"
    private_key = ec.generate_private_key(ec.SECP256R1())
    token = _signed_alb_token(private_key, other_alb_arn, {"sub": "alice"})
    monkeypatch.setenv("DATA_CHORD_ALB_ARN", expected_alb_arn)
    monkeypatch.setenv("DATA_CHORD_IDENTITY_SOURCE", "signed_alb")
    monkeypatch.setattr(
        "src.auth.user_context._public_key_for_header",
        lambda _key_id, _signer: private_key.public_key(),
    )

    # When the request boundary verifies the signed identity.
    with pytest.raises(InvalidUserContextError) as error:
        _user_context_from_headers(Headers({ALB_DATA_HEADER: token}))

    # Then it rejects the other signer before accepting its valid signature.
    assert str(error.value) == "ALB identity signer does not match expected load balancer"


async def test_hosted_identity_header_requires_signed_alb_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: hosted config expects ALB-signed claims
    monkeypatch.setenv(
        "DATA_CHORD_ALB_ARN",
        "arn:aws:elasticloadbalancing:us-east-2:123456789012:loadbalancer/app/data-chord/abc123",
    )
    monkeypatch.setenv("DATA_CHORD_IDENTITY_SOURCE", "signed_alb")

    # When / Then: a caller cannot spoof identity with the unsigned identity header alone
    with pytest.raises(InvalidUserContextError):
        _user_context_from_headers(Headers({ALB_IDENTITY_HEADER: "alice"}))


async def test_signed_alb_claims_must_match_identity_header(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: signed ALB claims identify Alice but the unsigned header says Bob
    alb_arn = "arn:aws:elasticloadbalancing:us-east-2:123456789012:loadbalancer/app/data-chord/abc123"
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    token = _signed_alb_token(private_key, alb_arn, {"sub": "alice"})
    monkeypatch.setenv("DATA_CHORD_ALB_ARN", alb_arn)
    monkeypatch.setenv("DATA_CHORD_IDENTITY_SOURCE", "signed_alb")
    monkeypatch.setattr("src.auth.user_context._public_key_for_header", lambda _key_id, _signer: public_key)

    # When / Then: the mismatch is rejected instead of trusting the spoofable value
    with pytest.raises(InvalidUserContextError):
        _user_context_from_headers(Headers({ALB_DATA_HEADER: token, ALB_IDENTITY_HEADER: "bob"}))


@pytest.mark.parametrize(
    "headers",
    [
        Headers(),
        Headers({TRUSTED_PROXY_USER_HEADER: ""}),
        Headers(
            raw=[
                (TRUSTED_PROXY_USER_HEADER.encode(), b"alice"),
                (TRUSTED_PROXY_USER_HEADER.encode(), b"bob"),
            ]
        ),
        Headers({TRUSTED_PROXY_USER_HEADER: "alice\nadmin"}),
    ],
)
async def test_trusted_proxy_rejects_missing_or_ambiguous_identity(
    headers: Headers,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given trusted-proxy mode receives no single safe identity value.
    monkeypatch.setenv("DATA_CHORD_IDENTITY_SOURCE", "trusted_proxy")

    # When / Then the request boundary rejects it before creating user context.
    with pytest.raises(InvalidUserContextError):
        _user_context_from_headers(headers)


async def test_unbound_trusted_proxy_context_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a hosted process uses trusted-proxy identity with no bound request.
    monkeypatch.setenv("DATA_CHORD_IDENTITY_SOURCE", "trusted_proxy")

    # When / Then trusted code cannot silently become the shared local user.
    with pytest.raises(InvalidUserContextError):
        current_user_context()


async def test_health_check_bypasses_invalid_identity_only(
    app_client: AsyncClient,
) -> None:
    # Given a trusted-proxy request has an invalid blank identity.
    invalid_identity = {TRUSTED_PROXY_USER_HEADER: ""}

    # When the caller checks health and then requests a business page.
    health = await app_client.get("/healthz", headers=invalid_identity)
    business = await app_client.get("/", headers=invalid_identity, follow_redirects=False)

    # Then health remains usable, but the business route fails closed.
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert business.status_code == 401


def _signed_alb_token(
    private_key: ec.EllipticCurvePrivateKey,
    signer: str,
    claims: dict[str, object],
) -> str:
    header = {
        "alg": "ES256",
        "kid": "test-key",
        "signer": signer,
        "exp": int(time.time()) + 300,
    }
    header_segment = _base64url_json(header)
    payload_segment = _base64url_json(claims)
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    der_signature = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der_signature)
    raw_signature = r.to_bytes(32, byteorder="big") + s.to_bytes(32, byteorder="big")
    return f"{header_segment}.{payload_segment}.{_base64url(raw_signature)}"


def _base64url_json(data: dict[str, object]) -> str:
    return _base64url(json.dumps(data, separators=(",", ":")).encode("utf-8"))


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")
