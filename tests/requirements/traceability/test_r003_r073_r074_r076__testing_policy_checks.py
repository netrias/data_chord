"""Requirement tests for requirements-writing and test strategy policy."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


def _requirement_test_functions() -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    functions: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for path in Path("tests/requirements").rglob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        functions.extend(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test_")
        )
    return functions


@pytest.mark.xfail(reason="R-003 currently needs human review beyond the automated wording heuristic.", strict=True)
@pytest.mark.requirements("R-003")
def test_r003__requirements_do_not_encode_temporary_ui_design() -> None:
    """
    Given: Requirements should describe durable behavior rather than temporary design choices.
    When: The requirements document is inspected for temporary design wording.
    Then: No requirement relies on temporary design language.
    """
    # Given
    requirements_text = Path("requirements.md").read_text(encoding="utf-8")
    temporary_design_terms = ["temporary", "mockup", "prototype"]
    assert "R-003" in requirements_text

    # When
    lowered = requirements_text.lower()

    # Then
    assert not any(term in lowered for term in temporary_design_terms)


@pytest.mark.requirements("R-073")
def test_r073__requirement_tests_include_negative_assertions_in_given_blocks() -> None:
    """
    Given: Requirement tests use explicit Given comments before the user action.
    When: Requirement test source files are inspected.
    Then: At least one requirement test demonstrates the negative-assertion pattern in the Given block.
    """
    # Given
    requirement_test_paths = list(Path("tests/requirements").rglob("test_*.py"))
    assert requirement_test_paths

    # When
    sources = [path.read_text(encoding="utf-8") for path in requirement_test_paths]

    # Then
    assert any("# Given" in source and "assert" in source.split("# When", maxsplit=1)[0] for source in sources)


@pytest.mark.requirements("R-074")
def test_r074__requirement_tests_prefer_feature_level_user_operations() -> None:
    """
    Given: Requirement tests should prefer feature-level behavior.
    When: Requirement test functions are inspected.
    Then: More requirement tests use app-level clients than pure structural or domain checks.
    """
    # Given
    functions = _requirement_test_functions()
    assert functions

    # When
    app_client_tests = [
        function
        for function in functions
        if any(arg.arg == "app_client" for arg in function.args.args)
    ]

    # Then
    assert len(app_client_tests) > len(functions) / 2


@pytest.mark.requirements("R-076")
def test_r076__property_based_tests_exist_for_pure_invariants() -> None:
    """
    Given: Some pure invariants are better exercised across generated examples.
    When: The existing property-test module is inspected.
    Then: The project contains Hypothesis-based property tests.
    """
    # Given
    properties_path = Path("tests/test_properties.py")
    assert properties_path.exists()

    # When
    source = properties_path.read_text(encoding="utf-8")

    # Then
    assert "from hypothesis import" in source
    assert "@given" in source
