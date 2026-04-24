"""Requirement tests for architecture and boundary conventions."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


def _imports_for(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
    return imports


@pytest.mark.requirements("R-024", "R-062", "R-065")
def test_r024_r062_r065__column_assignment_contract_is_owned_by_domain_and_used_at_boundaries() -> None:
    """
    Given: Column assignment is a durable workflow concept used across stages.
    When: The relevant modules are inspected.
    Then: The canonical model lives in domain and stage/persistence modules import it at boundaries.
    """
    # Given
    domain_model = Path("src/domain/column_assignment.py")
    stage_three = Path("src/stage_3_harmonize/router.py")
    pv_persistence = Path("src/domain/pv_persistence.py")
    assert domain_model.exists()

    # When
    domain_text = domain_model.read_text(encoding="utf-8")
    stage_three_imports = _imports_for(stage_three)
    persistence_imports = _imports_for(pv_persistence)

    # Then
    assert "class ColumnAssignment:" in domain_text
    assert "build_column_assignments" in domain_text
    assert "src.domain.column_assignment" in stage_three_imports
    assert "src.domain.column_assignment" in persistence_imports


@pytest.mark.requirements("R-063")
def test_r063__stage_specific_templates_and_static_assets_stay_inside_stage_modules() -> None:
    """
    Given: Stage-specific UI files exist for the workflow.
    When: Stage module directories are inspected.
    Then: Each stage keeps its templates and static assets inside that stage's module.
    """
    # Given
    stage_dirs = sorted(Path("src").glob("stage_*"))
    assert stage_dirs

    # When
    stage_asset_dirs = [(stage_dir / "templates", stage_dir / "static") for stage_dir in stage_dirs]

    # Then
    for templates_dir, static_dir in stage_asset_dirs:
        assert templates_dir.exists()
        assert static_dir.exists()


@pytest.mark.requirements("R-066")
def test_r066__workflow_artifacts_use_storage_abstractions() -> None:
    """
    Given: Workflow artifacts are saved and loaded across stages.
    When: Storage modules and callers are inspected.
    Then: They use the project storage abstractions rather than each stage owning ad hoc persistence.
    """
    # Given
    file_store = Path("src/domain/storage/file_store.py")
    upload_storage = Path("src/domain/storage/upload_storage.py")
    stage_five = Path("src/stage_5_review_summary/router.py")
    assert file_store.exists()

    # When
    stage_five_imports = _imports_for(stage_five)

    # Then
    assert "class FileStore:" in file_store.read_text(encoding="utf-8")
    assert "class UploadStorage:" in upload_storage.read_text(encoding="utf-8")
    assert "src.domain.storage" in stage_five_imports
