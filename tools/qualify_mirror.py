#!/usr/bin/env python3
"""Run the one bounded mirror qualification for candidate receipt renewal.

The fixture identity comes from `docs/2026-10-31/live-smoke-inventory.md`
(`test-mirror-push`); the candidate is the installed copy named by `--skill`.
The behavior lives in `tools/skillkit/mirror_qualification.py`.

    tools/qualify_mirror.py --skill ~/.claude/skills/ci-skills \\
        --work-dir .internal/log/bug-133/mirror --project hott/test-mirror-push \\
        --destination spyroot/test-mirror-push --source-id 261 \\
        --seed-ref branches:main:<sha> --seed-ref tags:smoke-seed:<sha> --run-id <id>

Run it from the directory whose target file the qualification must use: the
installed command and the in-process provider both resolve the target from the
caller's working directory. ``--plan-only`` stops after the wrong-digest rejection
and writes no ledger. ``--reconcile`` finishes the pending cleanup recorded by the
ledger in ``--work-dir``.

Exit 0 prints the final ledger, or the plan with ``--plan-only``; exit 2 prints the
stopping reason.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from skillkit.mirror_qualification import (
    DEFAULT_SETTLE_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    EXIT_BLOCKED,
    EXIT_SUCCESS,
    STATUS_BLOCKED,
    STATUS_PASS,
    STATUS_PLANNED,
    InstalledMirrorCommand,
    MirrorQualification,
    NativeMirrorProvider,
    QualificationError,
    QualificationInputs,
    SeedRef,
    installed_bindings,
)

WORK_DIR_HELP: Final = (
    "directory for receipts, outputs and the ledger; created if absent and reused "
    "if present; a ledger in it stops a new run and is what --reconcile reads"
)


def skill_parser() -> argparse.ArgumentParser:
    """Declare ``--skill``, read before the installed option names are known.

    :returns: Parser holding only ``--skill``, without help.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--skill", required=True, help="installed ci-skills directory")
    return parser


def build_parser(gitlab_option: str) -> argparse.ArgumentParser:
    """Declare the fixture identity and run controls.

    :param gitlab_option: ``GITLAB_TARGET_OPTION`` of the installed catalog.
    :returns: Parser for the qualification command.
    """
    parser = argparse.ArgumentParser(
        description="Run one bounded mirror qualification from an installed copy.",
        parents=[skill_parser()],
    )
    parser.add_argument("--work-dir", required=True, help=WORK_DIR_HELP)
    parser.add_argument("--project", required=True, help="retained GitLab source path")
    parser.add_argument(
        "--destination", required=True, help="disposable GitHub destination path"
    )
    parser.add_argument(
        "--source-id", required=True, type=int, help="native source project ID"
    )
    parser.add_argument(
        "--seed-ref",
        action="append",
        required=True,
        metavar="KIND:NAME:COMMIT",
        help="retained source ref that must not move; KIND is branches or tags",
    )
    parser.add_argument("--run-id", required=True, help="diagnostic run identifier")
    parser.add_argument(
        gitlab_option,
        dest="gitlab",
        help="GitLab instance passed to the installed command, which validates it",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="seconds per command",
    )
    parser.add_argument(
        "--settle-seconds",
        type=int,
        default=DEFAULT_SETTLE_SECONDS,
        help="pause before repeat steps",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--plan-only",
        action="store_true",
        help="run the read-only steps and the wrong-digest rejection; write no ledger",
    )
    mode.add_argument(
        "--reconcile",
        action="store_true",
        help="finish the pending cleanup of the ledger in --work-dir, "
        "for its recorded native IDs only",
    )
    return parser


def _blocked(reason: str) -> int:
    """Print the stopping reason.

    :param reason: Stopping reason.
    :returns: Exit code 2.
    """
    print(json.dumps({"status": STATUS_BLOCKED, "reason": reason}))
    return EXIT_BLOCKED


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Bind the installed copy, parse inputs, run the selected mode and print it.

    The installed skill directory goes on ``sys.path`` before any ``lib`` import, so
    ``--gitlab`` and the provider come from the installed copy.

    :param argv: Arguments, or None for the process arguments.
    :returns: 0 after PASS or PLANNED, 2 when a step stops the run.
    """
    known, _ = skill_parser().parse_known_args(argv)
    skill = Path(known.skill).expanduser().resolve()
    try:
        bindings = installed_bindings(skill)
    except QualificationError as exc:
        return _blocked(str(exc))
    args = build_parser(bindings.gitlab_option).parse_args(argv)
    work_dir = Path(args.work_dir).expanduser().resolve()
    try:
        if not args.reconcile:
            work_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _blocked(f"work_dir_unavailable:{type(exc).__name__}")
    try:
        inputs = QualificationInputs(
            skill=skill,
            work_dir=work_dir,
            project=args.project,
            destination=args.destination,
            source_id=args.source_id,
            seed_refs=tuple(SeedRef.parse(text) for text in args.seed_ref),
            run_id=args.run_id,
            gitlab_option=bindings.gitlab_option,
            gitlab=args.gitlab,
            timeout=args.timeout,
            settle_seconds=args.settle_seconds,
        )
        qualification = MirrorQualification(
            inputs,
            InstalledMirrorCommand(inputs),
            lambda label: NativeMirrorProvider.bind(inputs, bindings, label),
        )
        if args.plan_only:
            result = {"status": STATUS_PLANNED, "plan": qualification.plan()}
        elif args.reconcile:
            result = {"status": STATUS_PASS, "ledger": qualification.reconcile()}
        else:
            result = {"status": STATUS_PASS, "ledger": qualification.run()}
    except QualificationError as exc:
        return _blocked(str(exc))
    print(json.dumps(result, sort_keys=True))
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
