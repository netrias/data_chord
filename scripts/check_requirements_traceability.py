"""Check and report requirements-to-test traceability.

The checker intentionally relies on a small convention:

- requirements live in ``requirements.md`` as flat ``R-###.`` entries
- requirement tests live under ``tests/requirements``
- requirement tests use ``@pytest.mark.requirements("R-###", ...)``
- Playwright requirement tests include bracketed IDs in the test title
- marked tests include Given/When/Then lines in the test docstring or comments

This keeps the report useful without asking the script to infer intent from
arbitrary test code.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_PATH = REPO_ROOT / "requirements.md"
REQUIREMENTS_TESTS_DIR = REPO_ROOT / "tests" / "requirements"
DEFAULT_EXCEPTIONS_PATH = REQUIREMENTS_TESTS_DIR / "traceability_exceptions.json"
DEFAULT_REPORT_PATH = REQUIREMENTS_TESTS_DIR / "TRACEABILITY.md"

REQUIREMENT_RE = re.compile(r"^(R-\d{3})\. (.*)$")
HEADING_RE = re.compile(r"^## (.+)$")
GIVEN_WHEN_THEN_RE = re.compile(r"^\s*(Given|When|Then):\s*(.+?)\s*$")
PLAYWRIGHT_TEST_RE = re.compile(
    r"\b(?P<call>test(?:\.fail)?)\(\s*(?P<quote>['\"])(?P<title>.*?)(?P=quote)\s*,",
    re.DOTALL,
)
PLAYWRIGHT_REQUIREMENT_RE = re.compile(r"R-\d{3}")
PLAYWRIGHT_GIVEN_WHEN_THEN_RE = re.compile(r"//\s*(Given|When|Then):\s*(.+?)\s*(?:\n|$)")


@dataclass(frozen=True)
class Requirement:
    requirement_id: str
    category: str
    text: str
    line_number: int


@dataclass(frozen=True)
class TestCoverage:
    requirement_ids: tuple[str, ...]
    path: Path
    test_name: str
    given: str | None
    when: str | None
    then: str | None
    line_number: int
    expected_failure: str | None

    @property
    def node_id(self) -> str:
        relative_path = self.path.relative_to(REPO_ROOT)
        return f"{relative_path}::{self.test_name}"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--requirements",
        type=Path,
        default=REQUIREMENTS_PATH,
        help="Path to requirements.md",
    )
    parser.add_argument(
        "--tests-dir",
        type=Path,
        default=REQUIREMENTS_TESTS_DIR,
        help="Directory containing requirement tests",
    )
    parser.add_argument(
        "--exceptions",
        type=Path,
        default=DEFAULT_EXCEPTIONS_PATH,
        help="JSON file containing explicitly pending requirement IDs",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help="Markdown report output path",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write the Markdown traceability report",
    )
    return parser.parse_args()


def _parse_requirements(path: Path) -> dict[str, Requirement]:
    lines = path.read_text(encoding="utf-8").splitlines()
    requirements: dict[str, Requirement] = {}
    current_category = "Uncategorized"
    index = 0

    while index < len(lines):
        line = lines[index]
        heading_match = HEADING_RE.match(line)
        if heading_match:
            current_category = heading_match.group(1)
            index += 1
            continue

        requirement_match = REQUIREMENT_RE.match(line)
        if not requirement_match:
            index += 1
            continue

        requirement_id, first_text = requirement_match.groups()
        text_parts = [first_text]
        line_number = index + 1
        index += 1

        while index < len(lines):
            next_line = lines[index]
            if (
                next_line == ""
                or HEADING_RE.match(next_line)
                or REQUIREMENT_RE.match(next_line)
                or next_line.startswith("V-")
            ):
                break
            text_parts.append(next_line.strip())
            index += 1

        requirements[requirement_id] = Requirement(
            requirement_id=requirement_id,
            category=current_category,
            text=" ".join(part for part in text_parts if part).strip(),
            line_number=line_number,
        )

    return requirements


def _load_pending_exceptions(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    raw = json.loads(path.read_text(encoding="utf-8"))
    pending = raw.get("pending", {})
    if not isinstance(pending, dict):
        raise ValueError(f"{path} must contain an object field named 'pending'")

    result: dict[str, str] = {}
    for requirement_id, reason in pending.items():
        if not isinstance(requirement_id, str) or not isinstance(reason, str):
            raise ValueError(f"{path} pending entries must map strings to strings")
        result[requirement_id] = reason
    return result


def _requirements_marker_args(decorator: ast.expr) -> tuple[str, ...]:
    if not isinstance(decorator, ast.Call):
        return ()
    if not isinstance(decorator.func, ast.Attribute):
        return ()
    if decorator.func.attr != "requirements":
        return ()
    mark_attr = decorator.func.value
    if not isinstance(mark_attr, ast.Attribute) or mark_attr.attr != "mark":
        return ()
    if not isinstance(mark_attr.value, ast.Name) or mark_attr.value.id != "pytest":
        return ()

    requirement_ids: list[str] = []
    for arg in decorator.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            requirement_ids.append(arg.value)
    return tuple(requirement_ids)


def _is_pytest_mark_call(decorator: ast.expr, marker_name: str) -> bool:
    if not isinstance(decorator, ast.Call):
        return False
    if not isinstance(decorator.func, ast.Attribute):
        return False
    if decorator.func.attr != marker_name:
        return False
    mark_attr = decorator.func.value
    if not isinstance(mark_attr, ast.Attribute) or mark_attr.attr != "mark":
        return False
    return isinstance(mark_attr.value, ast.Name) and mark_attr.value.id == "pytest"


def _xfail_reason(decorators: list[ast.expr]) -> str | None:
    for decorator in decorators:
        if not _is_pytest_mark_call(decorator, "xfail"):
            continue
        assert isinstance(decorator, ast.Call)
        reason = "Expected failure"
        strict = False
        for keyword in decorator.keywords:
            if keyword.arg == "reason" and isinstance(keyword.value, ast.Constant):
                if isinstance(keyword.value.value, str):
                    reason = keyword.value.value
            if keyword.arg == "strict" and isinstance(keyword.value, ast.Constant):
                strict = keyword.value.value is True
        if not strict:
            reason = f"{reason} [traceability error: xfail must be strict=True]"
        return reason
    return None


def _extract_given_when_then(docstring: str | None) -> tuple[str | None, str | None, str | None]:
    if docstring is None:
        return None, None, None

    found: dict[str, str] = {}
    for line in docstring.splitlines():
        match = GIVEN_WHEN_THEN_RE.match(line)
        if match:
            label, text = match.groups()
            found[label] = text

    return found.get("Given"), found.get("When"), found.get("Then")


def _extract_playwright_given_when_then(body: str) -> tuple[str | None, str | None, str | None]:
    found: dict[str, str] = {}
    for match in PLAYWRIGHT_GIVEN_WHEN_THEN_RE.finditer(body):
        label, text = match.groups()
        found[label] = text.strip()
    return found.get("Given"), found.get("When"), found.get("Then")


def _iter_test_functions(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test_")
    ]


def _extract_requirement_ids(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, ...]:
    for decorator in node.decorator_list:
        marker_ids = _requirements_marker_args(decorator)
        if marker_ids:
            return marker_ids
    return ()


def _validate_coverage_convention(coverage: TestCoverage) -> list[str]:
    errors: list[str] = []
    missing_doc_parts = [
        label
        for label, value in (("Given", coverage.given), ("When", coverage.when), ("Then", coverage.then))
        if value is None
    ]
    if missing_doc_parts:
        errors.append(f"{coverage.node_id} is missing docstring fields: {', '.join(missing_doc_parts)}")
    if coverage.expected_failure and "traceability error" in coverage.expected_failure:
        errors.append(f"{coverage.node_id} uses xfail without strict=True")

    lowered_name = coverage.test_name.lower()
    for requirement_id in coverage.requirement_ids:
        compact_fragment = requirement_id.lower().replace("-", "")
        dashed_fragment = requirement_id.lower()
        if compact_fragment not in lowered_name and dashed_fragment not in lowered_name:
            errors.append(
                f"{coverage.node_id} marks {requirement_id}, but the test name does not include "
                f"'{compact_fragment}' or '{dashed_fragment}'"
            )
    return errors


def _collect_pytest_coverages(tests_dir: Path) -> tuple[list[TestCoverage], list[str]]:
    coverages: list[TestCoverage] = []
    errors: list[str] = []
    for path in sorted(tests_dir.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in _iter_test_functions(tree):
            requirement_ids = _extract_requirement_ids(node)
            if not requirement_ids:
                continue

            given, when, then = _extract_given_when_then(ast.get_docstring(node))
            coverage = TestCoverage(
                requirement_ids=requirement_ids,
                path=path,
                test_name=node.name,
                given=given,
                when=when,
                then=then,
                line_number=node.lineno,
                expected_failure=_xfail_reason(node.decorator_list),
            )
            coverages.append(coverage)
            errors.extend(_validate_coverage_convention(coverage))

    return coverages, errors


def _iter_playwright_blocks(source: str) -> list[tuple[str, str, int, str | None]]:
    blocks: list[tuple[str, str, int, str | None]] = []
    matches = list(PLAYWRIGHT_TEST_RE.finditer(source))
    for index, match in enumerate(matches):
        title = " ".join(match.group("title").split())
        requirement_ids = PLAYWRIGHT_REQUIREMENT_RE.findall(title)
        if not requirement_ids:
            continue

        line_number = source.count("\n", 0, match.start()) + 1
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        body = source[match.end():next_start]
        expected_failure = "Expected Playwright failure" if match.group("call") == "test.fail" else None
        blocks.append((title, body, line_number, expected_failure))
    return blocks


def _collect_playwright_coverages(tests_dir: Path) -> tuple[list[TestCoverage], list[str]]:
    coverages: list[TestCoverage] = []
    errors: list[str] = []
    playwright_paths = sorted(
        {
            *tests_dir.rglob("*.spec.js"),
            *tests_dir.rglob("*.spec.mjs"),
            *tests_dir.rglob("*.e2e.js"),
            *tests_dir.rglob("*.e2e.mjs"),
        }
    )
    for path in playwright_paths:
        source = path.read_text(encoding="utf-8")
        for title, body, line_number, expected_failure in _iter_playwright_blocks(source):
            requirement_ids = tuple(PLAYWRIGHT_REQUIREMENT_RE.findall(title))
            given, when, then = _extract_playwright_given_when_then(body)
            coverage = TestCoverage(
                requirement_ids=requirement_ids,
                path=path,
                test_name=title,
                given=given,
                when=when,
                then=then,
                line_number=line_number,
                expected_failure=expected_failure,
            )
            coverages.append(coverage)
            errors.extend(_validate_coverage_convention(coverage))
    return coverages, errors


def _collect_test_coverages(tests_dir: Path) -> tuple[list[TestCoverage], list[str]]:
    pytest_coverages, pytest_errors = _collect_pytest_coverages(tests_dir)
    playwright_coverages, playwright_errors = _collect_playwright_coverages(tests_dir)
    return [*pytest_coverages, *playwright_coverages], [*pytest_errors, *playwright_errors]


def _index_coverages_by_requirement(coverages: list[TestCoverage]) -> dict[str, list[TestCoverage]]:
    by_requirement: dict[str, list[TestCoverage]] = {}
    for coverage in coverages:
        for requirement_id in coverage.requirement_ids:
            by_requirement.setdefault(requirement_id, []).append(coverage)
    return by_requirement


def _build_report(
    requirements: dict[str, Requirement],
    coverages_by_requirement: dict[str, list[TestCoverage]],
    pending: dict[str, str],
) -> str:
    lines: list[str] = [
        "# Requirements Traceability Report",
        "",
        "Generated by `scripts/check_requirements_traceability.py`.",
        "",
        "## Summary",
        "",
    ]

    covered_count = sum(
        1
        for requirement_id in requirements
        if any(coverage.expected_failure is None for coverage in coverages_by_requirement.get(requirement_id, []))
    )
    expected_failure_count = sum(
        1
        for requirement_id in requirements
        if coverages_by_requirement.get(requirement_id)
        and not any(coverage.expected_failure is None for coverage in coverages_by_requirement[requirement_id])
    )
    pending_count = sum(
        1
        for requirement_id in requirements
        if not coverages_by_requirement.get(requirement_id) and requirement_id in pending
    )
    gap_count = len(requirements) - covered_count - expected_failure_count - pending_count

    lines.extend([
        f"- Requirements: {len(requirements)}",
        f"- Covered: {covered_count}",
        f"- Expected failing: {expected_failure_count}",
        f"- Pending: {pending_count}",
        f"- Gaps: {gap_count}",
        "",
        "## Requirements",
        "",
    ])

    current_category: str | None = None
    for requirement in requirements.values():
        if requirement.category != current_category:
            current_category = requirement.category
            lines.extend([f"### {current_category}", ""])

        coverages = coverages_by_requirement.get(requirement.requirement_id, [])
        has_passing_coverage = any(coverage.expected_failure is None for coverage in coverages)
        if has_passing_coverage:
            status = "Covered"
        elif coverages:
            status = "Expected failing"
        elif requirement.requirement_id in pending:
            status = "Pending"
        else:
            status = "Gap"

        lines.extend([
            f"#### {requirement.requirement_id}: {requirement.text}",
            "",
            f"Status: {status}",
            f"Source: [requirements.md:{requirement.line_number}](../../requirements.md#L{requirement.line_number})",
            "",
        ])

        if coverages:
            lines.append("Tests:")
            for coverage in coverages:
                relative_path = coverage.path.relative_to(REPO_ROOT)
                lines.append(
                    f"- [{relative_path}:{coverage.line_number}](../../{relative_path}#L{coverage.line_number})"
                    f"::{coverage.test_name}"
                )
                if coverage.expected_failure:
                    lines.append(f"  - Expected failure: {coverage.expected_failure}")
                lines.append(f"  - Given: {coverage.given}")
                lines.append(f"  - When: {coverage.when}")
                lines.append(f"  - Then: {coverage.then}")
            lines.append("")
        elif requirement.requirement_id in pending:
            lines.extend([f"Pending reason: {pending[requirement.requirement_id]}", ""])
        else:
            lines.extend(["No covering requirement test found.", ""])

    return "\n".join(lines).rstrip() + "\n"


def _find_errors(
    requirements: dict[str, Requirement],
    coverages_by_requirement: dict[str, list[TestCoverage]],
    pending: dict[str, str],
    convention_errors: list[str],
) -> list[str]:
    errors = list(convention_errors)

    for requirement_id in sorted(coverages_by_requirement):
        if requirement_id not in requirements:
            for coverage in coverages_by_requirement[requirement_id]:
                errors.append(f"{coverage.node_id} references unknown requirement {requirement_id}")

    for requirement_id in sorted(pending):
        if requirement_id not in requirements:
            errors.append(f"traceability exception references unknown requirement {requirement_id}")

    for requirement_id in requirements:
        if coverages_by_requirement.get(requirement_id):
            continue
        if requirement_id in pending:
            continue
        errors.append(f"{requirement_id} has no requirement test and no pending exception")

    return errors


def main() -> int:
    args = _parse_args()
    requirements = _parse_requirements(args.requirements)
    pending = _load_pending_exceptions(args.exceptions)
    coverages, convention_errors = _collect_test_coverages(args.tests_dir)
    coverages_by_requirement = _index_coverages_by_requirement(coverages)

    report = _build_report(requirements, coverages_by_requirement, pending)
    if args.write:
        args.report.write_text(report, encoding="utf-8")

    errors = _find_errors(requirements, coverages_by_requirement, pending, convention_errors)
    if errors:
        for error in errors:
            print(f"traceability error: {error}", file=sys.stderr)
        return 1

    if not args.write:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
