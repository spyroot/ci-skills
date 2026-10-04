#!/usr/bin/env python3
"""Enforce the pinned Automation Standard's adjacent function documentation."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

from validation_cli import add_options, emit


REQUIRED_FIELDS = (
    "summary",
    "arguments",
    "environment inputs",
    "stdout",
    "stderr",
    "exit classes",
    "side effects",
    "idempotency",
    "cleanup",
)
NONTRIVIAL_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.With,
    ast.AsyncWith,
    ast.Match,
    ast.Raise,
    ast.Await,
    ast.Yield,
    ast.YieldFrom,
)


def _nontrivial(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Classify branches and I/O orchestration; pure, no environment or cleanup."""
    return len(node.body) > 3 or any(
        isinstance(child, NONTRIVIAL_NODES) for child in ast.walk(node)
    )


def _adjacent_fields(lines: list[str], first_line: int) -> set[str]:
    """Read the comment directly above a definition; pure, no side effects."""
    found: set[str] = set()
    index = first_line - 2
    while index >= 0 and lines[index].lstrip().startswith("#"):
        content = lines[index].lstrip().removeprefix("#").strip().lower()
        for field in REQUIRED_FIELDS:
            if content.startswith(field + ":") and content[len(field) + 1 :].strip():
                found.add(field)
        index -= 1
    return found


def check(root: Path) -> list[str]:
    """Check production Python functions; root arg, no environment or output.

    Returns exact violations; invalid source raises. Side effects: none.
    Idempotency: same tree gives same result. Cleanup: none.
    """
    sources = (
        root / "skills" / "ci-skills" / "scripts",
        root / "tools",
    )
    if any(not source.is_dir() for source in sources):
        raise ValueError("production_sources_missing")
    problems: list[str] = []
    for path in sorted(path for source in sources for path in source.rglob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        tree = ast.parse("\n".join(lines), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) or not _nontrivial(node):
                continue
            first_line = (
                node.decorator_list[0].lineno if node.decorator_list else node.lineno
            )
            missing = set(REQUIRED_FIELDS) - _adjacent_fields(lines, first_line)
            if missing:
                relative = path.relative_to(root).as_posix()
                problems.append(
                    f"{relative}:{first_line}:{node.name}:missing:{','.join(sorted(missing))}"
                )
    return problems


def main() -> int:
    """Emit source contract status; argv only, stdout JSON, optional safe logs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    add_options(parser)
    args = parser.parse_args()
    try:
        problems = check(args.root.resolve())
    except (OSError, ValueError, SyntaxError) as exc:
        problems = [f"source_unreadable:{type(exc).__name__}"]
    emit(args, {"status": "PASS" if not problems else "BLOCKED", "problems": problems})
    return 0 if not problems else 2


if __name__ == "__main__":
    raise SystemExit(main())
