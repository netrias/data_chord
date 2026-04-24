"""Report production Python definitions that are only referenced by tests.

This is intentionally conservative. It ignores framework entry points that are
called indirectly, such as FastAPI route handlers and Pydantic validators.
"""

from __future__ import annotations

import argparse
import ast
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = ("src", "backend", "scripts")
TEST_ROOTS = ("tests",)
TEXT_SUFFIXES = {".css", ".html", ".js", ".json", ".md", ".mjs", ".sh", ".toml", ".yaml", ".yml"}
PYTHON_SUFFIX = ".py"


@dataclass(frozen=True)
class Definition:
    name: str
    qualified_name: str
    path: Path
    line_number: int
    kind: str
    framework_entrypoint: bool


@dataclass(frozen=True)
class Finding:
    definition: Definition
    production_references: int
    test_references: int


class _DefinitionCollector(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self._path = path
        self._class_stack: list[str] = []
        self.definitions: list[Definition] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._add_definition(node.name, node.lineno, "class", _has_framework_decorator(node.decorator_list))
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._add_function_definition(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._add_function_definition(node)
        self.generic_visit(node)

    def _add_function_definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if node.name.startswith("__") and node.name.endswith("__"):
            return
        kind = "method" if self._class_stack else "function"
        self._add_definition(node.name, node.lineno, kind, _has_framework_decorator(node.decorator_list))

    def _add_definition(self, name: str, line_number: int, kind: str, framework_entrypoint: bool) -> None:
        qualified_name = ".".join([*self._class_stack, name])
        self.definitions.append(
            Definition(
                name=name,
                qualified_name=qualified_name,
                path=self._path,
                line_number=line_number,
                kind=kind,
                framework_entrypoint=framework_entrypoint,
            )
        )


class _ReferenceCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: dict[str, int] = {}

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self._record(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._record(node.attr)
        self.generic_visit(node)

    def _record(self, name: str) -> None:
        self.names[name] = self.names.get(name, 0) + 1


def _has_framework_decorator(decorators: Sequence[ast.expr]) -> bool:
    return any(_is_route_decorator(decorator) or _is_pydantic_validator(decorator) for decorator in decorators)


def _is_route_decorator(decorator: ast.expr) -> bool:
    if not isinstance(decorator, ast.Call):
        return False
    func = decorator.func
    return isinstance(func, ast.Attribute) and func.attr in {"delete", "get", "patch", "post", "put"}


def _is_pydantic_validator(decorator: ast.expr) -> bool:
    candidate = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(candidate, ast.Name):
        return candidate.id in {"field_validator", "model_validator", "validator"}
    if isinstance(candidate, ast.Attribute):
        return candidate.attr in {"field_validator", "model_validator", "validator"}
    return False


def _iter_files(roots: Iterable[str]) -> Iterable[Path]:
    for root in roots:
        root_path = REPO_ROOT / root
        if not root_path.exists():
            continue
        for path in root_path.rglob("*"):
            if path.is_file() and (path.suffix == PYTHON_SUFFIX or path.suffix in TEXT_SUFFIXES):
                yield path


def _collect_definitions(paths: Iterable[Path]) -> list[Definition]:
    definitions: list[Definition] = []
    for path in paths:
        if path.suffix != PYTHON_SUFFIX:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        collector = _DefinitionCollector(path)
        collector.visit(tree)
        definitions.extend(collector.definitions)
    return definitions


def _collect_references(paths: Iterable[Path]) -> dict[str, int]:
    references: dict[str, int] = {}
    for path in paths:
        if path.suffix == PYTHON_SUFFIX:
            path_references = _collect_python_references(path)
        else:
            path_references = _collect_text_references(path)
        for name, count in path_references.items():
            references[name] = references.get(name, 0) + count
    return references


def _collect_python_references(path: Path) -> dict[str, int]:
    tree = ast.parse(path.read_text(), filename=str(path))
    collector = _ReferenceCollector()
    collector.visit(tree)
    return collector.names


def _collect_text_references(path: Path) -> dict[str, int]:
    text = path.read_text(errors="ignore")
    tokens: dict[str, int] = {}
    current: list[str] = []
    for char in text:
        if char.isalnum() or char == "_":
            current.append(char)
            continue
        if current:
            token = "".join(current)
            tokens[token] = tokens.get(token, 0) + 1
            current = []
    if current:
        token = "".join(current)
        tokens[token] = tokens.get(token, 0) + 1
    return tokens


def _find_test_only_definitions() -> list[Finding]:
    production_files = list(_iter_files(PRODUCTION_ROOTS))
    test_files = list(_iter_files(TEST_ROOTS))
    definitions = _collect_definitions(production_files)
    production_references = _collect_references(production_files)
    test_references = _collect_references(test_files)

    findings: list[Finding] = []
    for definition in definitions:
        if definition.framework_entrypoint:
            continue
        production_count = production_references.get(definition.name, 0)
        test_count = test_references.get(definition.name, 0)
        if production_count == 0 and test_count > 0:
            findings.append(Finding(definition, production_count, test_count))
    return sorted(findings, key=lambda item: (str(item.definition.path), item.definition.line_number))


def _format_finding(finding: Finding) -> str:
    definition = finding.definition
    relative_path = definition.path.relative_to(REPO_ROOT)
    return (
        f"{relative_path}:{definition.line_number}: {definition.kind} "
        f"{definition.qualified_name} is referenced by tests only "
        f"(test references: {finding.test_references})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    findings = _find_test_only_definitions()
    if not findings:
        print("No production Python definitions are referenced only by tests.")
        return 0
    print("Production Python definitions referenced only by tests:")
    for finding in findings:
        print(f"- {_format_finding(finding)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
