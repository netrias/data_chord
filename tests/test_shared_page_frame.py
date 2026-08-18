"""Feature proof for the shared workflow page frame."""

from __future__ import annotations

import re

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


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
