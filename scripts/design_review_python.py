"""Collect source locations for the three advisory design questions."""

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Kind(StrEnum):
    STRING = "string"
    DOMAIN = "domain"
    LOOP = "loop"


@dataclass(frozen=True)
class Signal:
    scope: str
    line: int
    end: int
    kind: Kind
    detail: str = ""


_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_ID_TYPES = {"file_id": "DatasetWorkflowId", "dataset_workflow_id": "DatasetWorkflowId", "column_key": "ColumnKey"}


def _annotation(node: ast.expr | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _compares_status(node: ast.Compare, states: frozenset[str]) -> bool:
    def is_state_literal(variable: ast.expr, literal: ast.expr) -> bool:
        return (isinstance(variable, ast.Name) and variable.id in states
                and isinstance(literal, ast.Constant) and isinstance(literal.value, str))

    previous = node.left
    for other in node.comparators:
        if is_state_literal(previous, other) or is_state_literal(other, previous):
            return True
        previous = other
    return False


@dataclass(frozen=True)
class Scope:
    name: str
    function: bool = False
    check_ids: bool = False
    states: frozenset[str] = frozenset()


def _scope(node: ast.AST, parent: Scope, application: bool) -> Scope:
    if isinstance(node, ast.ClassDef):
        return Scope(f"{parent.name}.{node.name}@{node.lineno}")
    if not isinstance(node, (*_FUNCTIONS, ast.Lambda)):
        return parent
    name = getattr(node, "name", "<lambda>")
    location = f"{parent.name}.{name}@{node.lineno}:{node.col_offset + 1}"
    states = frozenset(
        arg.arg for arg in ast.iter_child_nodes(node.args)
        if isinstance(arg, ast.arg) and _annotation(arg.annotation) == "HarmonizeStatus"
    )
    decorators = [] if isinstance(node, ast.Lambda) else node.decorator_list
    route = any(
        isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
        and d.func.attr in {"get", "post", "put", "patch", "delete", "head", "options", "api_route"}
        for d in decorators
    )
    return Scope(location, function=True, check_ids=application and not route, states=states)


def scan(path: Path, source: bytes) -> Iterator[Signal]:
    tree = ast.parse(source, filename=str(path))
    yield from _walk(tree, Scope("<module>"), path.parts[0] == "src")


def _walk(node: ast.AST, parent: Scope, application: bool) -> Iterator[Signal]:
    scope = _scope(node, parent, application)
    yield from _signals(node, scope)
    children = ast.iter_child_nodes(node)
    if isinstance(node, (ast.Module, ast.ClassDef, *_FUNCTIONS)) and ast.get_docstring(node) is not None:
        children = (child for child in children if child is not node.body[0])
    for child in children:
        yield from _walk(child, scope, application)


def _signals(node: ast.AST, scope: Scope) -> Iterator[Signal]:
    if isinstance(node, ast.arg):
        annotation = _annotation(node.annotation)
        if scope.check_ids and annotation == "str" and node.arg in _ID_TYPES:
            yield Signal(scope.name, node.lineno, node.end_lineno or node.lineno, Kind.DOMAIN,
                         f"{node.arg} → {_ID_TYPES[node.arg]}")
    elif isinstance(node, ast.Compare) and _compares_status(node, scope.states):
        yield Signal(scope.name, node.lineno, node.end_lineno or node.lineno, Kind.DOMAIN, "use HarmonizeStatus")
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield Signal(scope.name, node.lineno, node.end_lineno or node.lineno, Kind.STRING, node.value)
    elif scope.function and isinstance(node, (ast.For, ast.AsyncFor, ast.While, ast.comprehension)):
        line = node.target.lineno if isinstance(node, ast.comprehension) else node.lineno
        yield Signal(scope.name, line, line, Kind.LOOP)
