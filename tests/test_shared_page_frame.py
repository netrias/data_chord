"""Feature proof for the shared workflow page frame."""

from __future__ import annotations

import re

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

_PROGRESS_LINK_RE = re.compile(
    r'<a class="step-link" data-url="([^"]+)"(?: href="([^"]+)")?'
)
_STAGE_PATHS = ("/stage-1", "/stage-2", "/stage-3", "/stage-4", "/stage-5")


@pytest.mark.parametrize(
    ("path", "active_stage", "completed_connectors", "action_label"),
    [
        ("/stage-1", "upload", 0, "Map →"),
        ("/stage-2", "mapping", 1, "Harmonize →"),
        ("/stage-3", "harmonize", 2, "Verify →"),
        ("/stage-4", "verify", 3, "Review →"),
        ("/stage-5", "review", 4, "Harmonize New Data →"),
    ],
)
async def test_each_stage_renders_the_same_workflow_frame(
    app_client: AsyncClient,
    path: str,
    active_stage: str,
    completed_connectors: int,
    action_label: str,
) -> None:
    # Given a stage with one expected active workflow step
    assert active_stage in {"upload", "mapping", "harmonize", "verify", "review"}

    # When the stage page is rendered
    response = await app_client.get(path)

    # Then the shared frame has all five steps, the correct progress, and the stage action
    assert response.status_code == 200
    assert response.text.count('class="progress-tracker"') == 1
    assert response.text.count("data-stage=") == 5
    assert f'class="step active" data-stage="{active_stage}"' in response.text
    assert response.text.count('aria-current="step"') == 1
    connectors = re.findall(r'<li class="connector[^>]+>', response.text)
    assert len(connectors) == 4
    assert all('role="presentation" aria-hidden="true"' in connector for connector in connectors)
    assert response.text.count('class="connector complete"') == completed_connectors
    assert action_label in response.text


@pytest.mark.parametrize(
    ("page_path", "asset_paths", "application_paths"),
    [
        (
            "/stage-1",
            (
                "/assets/stage-1/stage_1_upload.css",
                "/assets/stage-1/data_model_popup.css",
                "/assets/stage-1/stage_1_upload.js",
            ),
            ("/stage-1/upload", "/stage-1/analyze"),
        ),
        (
            "/stage-2",
            (
                "/assets/stage-1/stage_1_upload.css",
                "/assets/stage-2/stage_2_mappings.css",
                "/assets/stage-2/stage_2_mappings.js",
            ),
            ("/stage-2/choices", "/stage-3/harmonize", "/stage-3"),
        ),
        (
            "/stage-3",
            (
                "/assets/stage-3/stage_3_harmonize.css",
                "/assets/stage-3/harmonize-animation.css",
                "/assets/stage-3/stage_3_harmonize.js",
            ),
            ("/stage-3/harmonize", "/stage-4", "/stage-2"),
        ),
        (
            "/stage-4",
            (
                "/assets/stage-1/stage_1_upload.css",
                "/assets/stage-4/stage_4_review.css",
                "/assets/stage-4/stage_4_review.js",
            ),
            ("/stage-4/rows", "/stage-5"),
        ),
        (
            "/stage-5",
            (
                "/assets/stage-5/stage_5_review.css",
                "/assets/stage-5/stage_5_review.js",
            ),
            ("/stage-5/summary", "/stage-5/download", "/stage-1"),
        ),
    ],
)
async def test_stage_pages_keep_application_urls_on_the_browser_origin(
    app_client: AsyncClient,
    page_path: str,
    asset_paths: tuple[str, ...],
    application_paths: tuple[str, ...],
) -> None:
    # Given: the container sees an internal HTTP origin behind a public reverse proxy
    internal_origin = "http://internal-service:8000"

    # When: the page and its stage-owned static files are requested
    response = await app_client.get(f"{internal_origin}{page_path}")
    asset_responses = [await app_client.get(asset_path) for asset_path in asset_paths]

    # Then: the page uses browser-origin paths and never exposes the container origin
    assert response.status_code == 200
    assert internal_origin not in response.text
    for expected_path in (*asset_paths, *application_paths):
        assert expected_path in response.text
    assert all(asset_response.status_code == 200 for asset_response in asset_responses)


@pytest.mark.parametrize(
    ("query", "expected_suffix"),
    [
        ("", ""),
        ("?file_id=not-a-workflow", ""),
        (
            "?file_id=0123456789abcdef0123456789abcdef&file_id=fedcba9876543210fedcba9876543210",
            "",
        ),
        ("?file_id=0123456789abcdef0123456789abcdef", "?file_id=0123456789abcdef0123456789abcdef"),
    ],
)
async def test_progress_links_render_only_a_valid_workflow_id(
    app_client: AsyncClient,
    query: str,
    expected_suffix: str,
) -> None:
    # Given: Stage 5 is requested with a missing, invalid, or valid workflow id
    expected_links = [(f"{path}{expected_suffix}", f"{path}{expected_suffix}") for path in _STAGE_PATHS]

    # When: the server renders every reachable progress link
    response = await app_client.get(f"/stage-5{query}")

    # Then: every link contains exactly the validated workflow context
    assert response.status_code == 200
    assert _PROGRESS_LINK_RE.findall(response.text) == expected_links
