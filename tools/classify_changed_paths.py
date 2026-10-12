#!/usr/bin/env python3
"""Classify the paths a revision range changed, including the ones git hides.

Validation skips the Python checks when a range touched only Markdown. Deciding
that from `git diff --name-only` understates the range twice over: the default
`--diff-filter=ACMR` drops deletions and type changes entirely, and `--name-only`
reports only the DESTINATION of a rename. So deleting a module, or renaming
`a.py` to `a.md`, both read as "Markdown only" and skip every Python gate --
measured on a fixture repository, an isolated `.py` deletion yields no paths at
all under ACMR, and an isolated `.py` -> `.md` rename yields just the `.md`
under ACMR, ACDMRT and an unfiltered `--name-only` alike.

Reading `--name-status` and counting both sides of a rename or copy record is
the only form that sees all of it.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import MutableSequence, Sequence
from pathlib import Path
from typing import TypedDict

MARKDOWN_SUFFIXES = (".md", ".markdown")
# Add, copy, delete, modify, rename, type-change. Named rather than defaulted so
# a future reader can see that deletions and type changes are deliberate.
DIFF_FILTER = "ACDMRT"


class ChangedPaths(TypedDict):
    """Describe both sides of every changed Git path in a revision range.

    :param schema_version: Declared changed-path result version.
    :param kind: Result discriminator consumed by the CI adapter.
    :param base: Caller-selected base revision.
    :param head: Caller-selected candidate revision.
    :param changed_count: Number of unique changed paths.
    :param non_markdown_count: Number of paths requiring non-documentation gates.
    :param changed: Sorted unique changed paths, including both rename endpoints.
    :param non_markdown: Sorted changed paths that are not Markdown documents.

    .. attribute :: schema_version
        :type: str

        Declared changed-path result version.

    .. attribute :: kind
        :type: str

        Result discriminator consumed by the CI adapter.

    .. attribute :: base
        :type: str

        Caller-selected base revision.

    .. attribute :: head
        :type: str

        Caller-selected candidate revision.

    .. attribute :: changed_count
        :type: int

        Number of unique changed paths.

    .. attribute :: non_markdown_count
        :type: int

        Number of paths requiring non-documentation gates.

    .. attribute :: changed
        :type: Sequence[str]

        Sorted unique changed paths, including both rename endpoints.

    .. attribute :: non_markdown
        :type: Sequence[str]

        Sorted changed paths that are not Markdown documents.
    """

    schema_version: str
    kind: str
    base: str
    head: str
    changed_count: int
    non_markdown_count: int
    changed: Sequence[str]
    non_markdown: Sequence[str]


class ClassifyError(RuntimeError):
    """The revision range could not be read."""


def _records(output: bytes) -> Sequence[tuple[str, Sequence[str]]]:
    """Decode NUL-delimited Git name-status records, including both rename paths.

    :param output: Raw ``git diff --name-status -z`` standard output.
    :returns: Ordered status and path-list pairs from the diff.
    """
    fields = [field for field in output.split(b"\0") if field != b""]
    records: MutableSequence[tuple[str, Sequence[str]]] = []
    index = 0
    while index < len(fields):
        status = fields[index].decode("utf-8", errors="replace")
        index += 1
        wanted = 2 if status[:1] in {"R", "C"} else 1
        paths = [
            field.decode("utf-8", errors="replace")
            for field in fields[index : index + wanted]
        ]
        index += wanted
        if paths:
            records.append((status, paths))
    return records


def is_markdown(path: str) -> bool:
    """Check whether a path has a case-insensitive Markdown suffix.

    :param path: Repository-relative changed path to classify.
    :returns: True for a .md or .markdown suffix.
    """
    return path.lower().endswith(MARKDOWN_SUFFIXES)


def classify(root: Path, base: str, head: str) -> ChangedPaths:
    """Classify every changed path in a Git revision range.

    :param root: Repository root containing the compared revisions.
    :param base: Base revision for the three-dot diff.
    :param head: Head revision for the three-dot diff.
    :returns: Sorted changed paths, non-Markdown paths, and their counts.
    :raises ClassifyError: If Git cannot read the revision range.
    """
    result = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "diff",
            "--name-status",
            f"--diff-filter={DIFF_FILTER}",
            "-z",
            f"{base}...{head}",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ClassifyError(f"cannot read range {base}...{head}")

    paths: MutableSequence[str] = []
    for _status, record in _records(result.stdout):
        paths.extend(record)
    unique = sorted(set(paths))
    non_markdown = sorted(path for path in unique if not is_markdown(path))
    return {
        "schema_version": "1.0",
        "kind": "changed_paths",
        "base": base,
        "head": head,
        "changed_count": len(unique),
        "non_markdown_count": len(non_markdown),
        "non_markdown": non_markdown,
        "changed": unique,
    }


def main() -> int:
    """Render changed-path classification and optional GitHub workflow outputs.

    :returns: Zero on a readable range, or exits two if classification fails.
    """
    cli = argparse.ArgumentParser(
        description="Classify changed paths for a revision range.",
        epilog="Example: classify_changed_paths.py --root . --base BASE --head HEAD --json",
    )
    cli.add_argument("--root", required=True, metavar="PATH", help="repository root")
    cli.add_argument("--base", required=True, metavar="REV", help="base revision")
    cli.add_argument("--head", required=True, metavar="REV", help="head revision")
    modes = cli.add_mutually_exclusive_group()
    modes.add_argument("--json", action="store_true", help="print JSON")
    modes.add_argument("--yaml", action="store_true", help="print YAML")
    cli.add_argument(
        "--github-output",
        metavar="PATH",
        help="append base_sha, changed_count and non_markdown_count for a workflow step",
    )
    args = cli.parse_args()
    try:
        data = classify(Path(args.root).resolve(), args.base, args.head)
    except (ClassifyError, OSError) as exc:
        cli.exit(2, f"BLOCKED: {exc}\n")
    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8") as handle:
            handle.write(f"base_sha={data['base']}\n")
            handle.write(f"changed_count={data['changed_count']}\n")
            handle.write(f"non_markdown_count={data['non_markdown_count']}\n")
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    elif args.yaml:
        import yaml

        print(yaml.safe_dump(data, sort_keys=True), end="")
    else:
        print(
            f"Changed paths: {data['changed_count']} "
            f"({data['non_markdown_count']} non-Markdown)"
        )
        for path in data["non_markdown"]:
            print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
