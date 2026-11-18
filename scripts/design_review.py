"""Report design questions on added lines between two Git revisions."""

import argparse
import json
import os
import re
import subprocess
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from design_review_python import Kind, Signal, scan

_BROWSER = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"}
_HUNK = re.compile(rb"^@@ -[\d,]+ \+(\d+)(?:,(\d+))? @@", re.MULTILINE)


@dataclass(frozen=True)
class Change:
    path: Path
    old: str
    new: str

    def added_lines(self) -> set[int] | None:
        if not int(self.old, 16):
            return None  # The entire file is new.
        patch = _git("diff", "--no-ext-diff", "--no-textconv", "--no-color", "--unified=0", self.old, self.new)
        lines: set[int] = set()
        for start, length in _HUNK.findall(patch):
            lines.update(range(int(start), int(start) + int(length or 1)))
        return lines

    @classmethod
    def between(cls, base: str, head: str) -> Iterator[Self]:
        diff = _git("diff", "--raw", "-z", "--no-abbrev", "--find-renames", "--diff-filter=AMRT", f"{base}...{head}")
        records = iter(diff.rstrip(b"\0").split(b"\0"))
        for header in filter(None, records):
            _, mode, old, new, status = header.decode("ascii").split()
            path = Path(os.fsdecode(next(records)))
            if status.startswith("R"):
                path = Path(os.fsdecode(next(records)))
            if mode.startswith("100") and old != new and path.suffix in {".py", *_BROWSER}:
                yield cls(path, old, new)


def _git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])


def _scan(change: Change) -> Iterator[Signal]:
    source = _git("cat-file", "blob", change.new)
    if change.path.suffix == ".py":
        yield from scan(change.path, source)
        return
    parser = Path(__file__).with_name("design_review_js.mjs")
    raw = subprocess.check_output(["node", parser, change.path], input=source)
    for item in json.loads(raw):
        yield Signal(**item)


def _touched(signal: Signal, added: set[int] | None) -> bool:
    return added is None or not added.isdisjoint(range(signal.line, signal.end + 1))


def _preview(value: str) -> str:
    return repr(value[:56]) + ("…" if len(value) > 56 else "")


@dataclass
class Report:
    design: list[str]
    literals: list[str]

    def add_scope(self, path: Path, scope: str, signals: list[Signal], added: set[int] | None) -> None:
        where = f"    {ascii(str(path))} {ascii(scope)}"
        changed = [s for s in signals if _touched(s, added)]
        for signal in changed:
            if signal.kind == Kind.DOMAIN:
                self.design.append(f"{where} L{signal.line} {signal.detail}")
        loops = [s for s in signals if s.kind == Kind.LOOP]
        if len(loops) >= 2 and any(s.kind == Kind.LOOP for s in changed):
            lines = ", ".join(str(s.line) for s in loops)
            self.design.append(f"{where} {len(loops)} loops on lines {lines}")
        strings = [f"L{s.line} {_preview(s.detail)}" for s in changed if s.kind == Kind.STRING]
        if strings:
            self.literals.extend((where, "      " + ", ".join(strings)))

    def render(self) -> str:
        return "\n".join((
            "## Advisory design review", "",
            f"Domain type and loop questions: {len(self.design)}.", "", *self.design, "",
            "<details><summary>Changed strings by scope</summary>", "", *self.literals, "", "</details>",
        ))


def _report(base: str, head: str) -> Report:
    report = Report([], [])
    for change in Change.between(base, head):
        groups: defaultdict[str, list[Signal]] = defaultdict(list)
        added = change.added_lines()
        for signal in _scan(change):
            groups[signal.scope].append(signal)
        for scope, signals in groups.items():
            report.add_scope(change.path, scope, signals, added)
    return report


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("base", help="PR base revision or branch")
    cli.add_argument("head", nargs="?", default="HEAD")
    args = cli.parse_args()
    print(_report(args.base, args.head).render())
