"""Enforce the pinned Automation Standard's adjacent function documentation."""

from __future__ import annotations

import ast
import re
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Final

from validation_cli import ValidationArgumentParser, add_options, emit

REQUIRED_FIELDS: Final[tuple[str, ...]] = (
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
NONTRIVIAL_NODES: Final[tuple[type[ast.AST], ...]] = (
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
ASSIGNMENT_NODES: Final[tuple[type[ast.AST], ...]] = (
    ast.Assign,
    ast.AnnAssign,
    ast.AugAssign,
    ast.NamedExpr,
)
TRANSFORMATION_NODES: Final[tuple[type[ast.AST], ...]] = (
    ast.BinOp,
    ast.BoolOp,
    ast.Compare,
    ast.Subscript,
    ast.DictComp,
    ast.ListComp,
    ast.SetComp,
    ast.GeneratorExp,
)


# Summary: visit only the function's own syntax; Arguments: function AST node
# Environment inputs: none; Stdout: none; Stderr: none
# Exit classes: yields AST nodes; Side effects: none
# Idempotency: same syntax yields same order; Cleanup: none
def _own_nodes(node: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterator[ast.AST]:
    """Yield syntax owned by one function without visiting nested definitions.

    :param node: Parsed function to traverse.
    :returns: Syntax nodes in depth-first order.
    """
    pending = list(reversed(node.body))
    while pending:
        child = pending.pop()
        yield child
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        pending.extend(reversed(list(ast.iter_child_nodes(child))))


# Summary: classify decisions and multi-step work; Arguments: function AST node
# Environment inputs: none; Stdout: none; Stderr: none
# Exit classes: boolean classification; Side effects: none
# Idempotency: same syntax gives same answer; Cleanup: none
def _nontrivial(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Recognize decisions and multi-step work without a line-count cutoff.

    :param node: Parsed function to classify.
    :returns: Whether the function contains substantive behavior.
    """
    nodes = list(_own_nodes(node))
    calls = sum(isinstance(child, ast.Call) for child in nodes)
    assignments = sum(isinstance(child, ASSIGNMENT_NODES) for child in nodes)
    transforms = any(isinstance(child, TRANSFORMATION_NODES) for child in nodes)
    return (
        any(isinstance(child, NONTRIVIAL_NODES) for child in nodes)
        or any(isinstance(child, ast.comprehension) for child in nodes)
        or calls >= 2
        or (calls >= 1 and assignments >= 1)
        or (assignments >= 2 and transforms)
    )


# Summary: parse adjacent nonempty labels; Arguments: source lines and definition line
# Environment inputs: none; Stdout: none; Stderr: none
# Exit classes: set of present fields; Side effects: none
# Idempotency: same source gives same fields; Cleanup: none
def _adjacent_fields(lines: Sequence[str], first_line: int) -> set[str]:
    """Read adjacent semicolon-separated labeled clauses with nonempty values.

    :param lines: Source file split into physical lines.
    :param first_line: One-based line of the definition or first decorator.
    :returns: Required field names found immediately above that line.
    """
    found: set[str] = set()
    index = first_line - 2
    while index >= 0 and lines[index].lstrip().startswith("#"):
        content = lines[index].lstrip().removeprefix("#").strip()
        for clause in content.split(";"):
            label, separator, value = clause.partition(":")
            field = label.strip().lower()
            if separator and field in REQUIRED_FIELDS and value.strip():
                found.add(field)
        index -= 1
    return found


# Summary: name declared function parameters; Arguments: function AST node
# Environment inputs: none; Stdout: none; Stderr: none
# Exit classes: parameter-name tuple; Side effects: none
# Idempotency: same signature gives same names; Cleanup: none
def _parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, ...]:
    """List every declared parameter, including variadic parameters.

    :param node: Parsed function whose signature is inspected.
    :returns: Parameter names in signature order.
    """
    args = node.args
    names = [item.arg for item in (*args.posonlyargs, *args.args)]
    if names and names[0] in {"self", "cls"}:
        names.pop(0)
    if args.vararg:
        names.append(args.vararg.arg)
    names.extend(item.arg for item in args.kwonlyargs)
    if args.kwarg:
        names.append(args.kwarg.arg)
    return tuple(names)


# Summary: identify explicit exception classes; Arguments: function AST node
# Environment inputs: none; Stdout: none; Stderr: none
# Exit classes: set of exception names; Side effects: none
# Idempotency: same syntax gives same names; Cleanup: none
def _raised_classes(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Identify exception classes constructed in explicit raise statements.

    :param node: Parsed function whose own raise statements are inspected.
    :returns: Names of statically identifiable exception classes.
    """
    names: set[str] = set()
    for item in _own_nodes(node):
        if not isinstance(item, ast.Raise) or item.exc is None:
            continue
        exception = item.exc.func if isinstance(item.exc, ast.Call) else item.exc
        if isinstance(exception, ast.Name) and exception.id.endswith(
            ("Error", "Exception")
        ):
            names.add(exception.id)
        elif isinstance(exception, ast.Attribute):
            names.add(exception.attr)
    return names


# Summary: validate reST function docs; Arguments: function AST node
# Environment inputs: none; Stdout: none; Stderr: none
# Exit classes: issue list; Side effects: none
# Idempotency: same source gives same issues; Cleanup: none
def _docstring_issues(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Check Python documentation against the function signature and body.

    :param node: Parsed function with its source docstring.
    :returns: Missing, duplicate, or unknown reStructuredText field errors.
    """
    doc = ast.get_docstring(node)
    if not doc:
        return ["docstring_missing"]
    problems: list[str] = []
    if not doc.splitlines()[0].strip() or doc.splitlines()[0].lstrip().startswith(":"):
        problems.append("summary_missing")
    documented = re.findall(r"(?m)^[ \t]*:param[ \t]+(\w+):[ \t]*(\S.*?)[ \t]*$", doc)
    names = [name for name, _description in documented]
    declared = _parameters(node)
    for name in declared:
        if names.count(name) == 0:
            problems.append(f"param_missing:{name}")
        elif names.count(name) > 1:
            problems.append(f"param_duplicate:{name}")
    for name in sorted(set(names) - set(declared)):
        problems.append(f"param_unknown:{name}")
        if names.count(name) > 1:
            problems.append(f"param_duplicate:{name}")
    own_nodes = list(_own_nodes(node))
    returns_value = any(
        isinstance(item, (ast.Yield, ast.YieldFrom))
        or (
            isinstance(item, ast.Return)
            and item.value is not None
            and not (isinstance(item.value, ast.Constant) and item.value.value is None)
        )
        for item in own_nodes
    )
    if returns_value and not re.search(r"(?m)^[ \t]*:returns:[ \t]*\S", doc):
        problems.append("returns_missing")
    documented_raises = set(
        re.findall(r"(?m)^[ \t]*:raises[ \t]+([\w.]+):[ \t]*\S", doc)
    )
    for name in sorted(_raised_classes(node) - documented_raises):
        problems.append(f"raises_missing:{name}")
    return problems


# Summary: locate changed Python source lines; Arguments: checkout and base SHA
# Environment inputs: Git object database; Stdout: none; Stderr: none
# Exit classes: changed line map or Git failure; Side effects: read-only
# Idempotency: same commits give same lines; Cleanup: Git child exits
def _changed_lines(root: Path, base: str) -> dict[str, set[int]]:
    """Find added and modified lines for reST checks in one revision range.

    :param root: Git checkout containing the proposed changes.
    :param base: Exact base commit of the proposed change.
    :returns: Current-file line numbers changed since the base commit.
    :raises ValueError: The base is not an exact commit identifier.
    :raises subprocess.CalledProcessError: Git cannot read the revision range.
    """
    if re.fullmatch(r"[0-9a-f]{40}", base) is None:
        raise ValueError("base_revision_invalid")
    result = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "diff",
            "--unified=0",
            "--no-ext-diff",
            f"{base}...HEAD",
            "--",
            "skills/ci-skills/scripts",
            "tools",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    changed: dict[str, set[int]] = {}
    path: str | None = None
    for line in result.stdout.splitlines():
        if line.startswith("+++ "):
            path = line.removeprefix("+++ b/") if line.startswith("+++ b/") else None
        elif line.startswith("@@ ") and path is not None:
            match = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if match is None:
                continue
            start = int(match.group(1))
            count = int(match.group(2) or "1")
            changed.setdefault(path, set()).update(range(start, start + max(count, 1)))
    return changed


# Summary: check production function contracts; Arguments: repository root
# Environment inputs: tracked source tree; Stdout: none; Stderr: none
# Exit classes: violation list or parse/read error; Side effects: read-only
# Idempotency: same tree gives same list; Cleanup: file handles close
def check(root: Path, *, base: str | None = None) -> list[str]:
    """Check adjacent fields globally and reST for changed functions.

    :param root: Repository whose skill and maintenance tools are checked.
    :param base: Exact base commit; omitted checks reST on every function.
    :returns: Exact source locations and missing field names.
    :raises ValueError: A required production source directory is absent.
    :raises OSError: A source file cannot be read.
    :raises SyntaxError: A source file cannot be parsed.
    :raises subprocess.CalledProcessError: Git cannot read the base range.
    """
    sources = (
        root / "skills" / "ci-skills" / "scripts",
        root / "tools",
    )
    if any(not source.is_dir() for source in sources):
        raise ValueError("production_sources_missing")
    changed = _changed_lines(root, base) if base is not None else None
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
            relative = path.relative_to(root).as_posix()
            touches_function = changed is None or bool(
                changed.get(relative, set())
                & set(range(first_line, (node.end_lineno or node.lineno) + 1))
            )
            for issue in _docstring_issues(node) if touches_function else ():
                problems.append(f"{relative}:{first_line}:{node.name}:{issue}")
    return problems


# Summary: report source contract status; Arguments: CLI argv
# Environment inputs: selected repository; Stdout: JSON; Stderr: safe log
# Exit classes: 0 pass, 2 violation; Side effects: optional log append
# Idempotency: result repeats, log appends; Cleanup: log file closes
def main() -> int:
    """Emit function documentation status for the selected repository.

    :returns: Zero on pass or two when source documentation is invalid.
    """
    parser = ValidationArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--base", help="exact base commit for changed-code reST checks")
    add_options(parser)
    args = parser.parse_args()
    try:
        problems = check(args.root.resolve(), base=args.base)
    except (OSError, ValueError, SyntaxError, subprocess.CalledProcessError) as exc:
        problems = [f"source_unreadable:{type(exc).__name__}"]
    emit(args, {"status": "PASS" if not problems else "BLOCKED", "problems": problems})
    return 0 if not problems else 2


if __name__ == "__main__":
    raise SystemExit(main())
