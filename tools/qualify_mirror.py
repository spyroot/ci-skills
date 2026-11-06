#!/usr/bin/env python3
"""Run the one bounded mirror qualification for candidate receipt renewal.

The caller passes the fixture identity that
`docs/2026-10-31/live-smoke-inventory.md` lists for `test-mirror-push` with
`--project`, `--destination`, `--source-id` and `--seed-ref`; `--skill` names the
installed copy under test. The behavior lives in
`tools/skillkit/mirror_qualification.py`.

    tools/qualify_mirror.py --skill <installed skill directory> \\
        --work-dir <private directory> --project hott/test-mirror-push \\
        --destination spyroot/test-mirror-push --source-id 261 \\
        --seed-ref branches:main:<sha> --seed-ref tags:smoke-seed:<sha> --run-id <id>

    tools/qualify_mirror.py <the same arguments> --apply

The first command is a dry run, the default: it runs the live plans and the
wrong-digest rejection, creates and deletes nothing, and writes no ledger;
``--plan-only`` selects the same mode explicitly. The second creates the mirror
and destination, applies the repeat, and deletes both. ``--reconcile`` finishes
the cleanup that the ledger in ``--work-dir`` records as pending. ``--help`` needs
neither ``--skill`` nor the installed copy; it shows ``--<GITLAB_TARGET_OPTION>``
where the installed catalog names the GitLab instance option.

Run it from the directory whose target file the qualification must use: the
installed command and the in-process provider both resolve the target from the
working directory of ``tools/qualify_mirror.py``. SIGINT or SIGTERM stops the
mode without starting a deletion; with a ledger held, ``MirrorQualification``
writes the ledger again and the reason names ``--reconcile``.

``main`` prints the plan or the final ledger and returns 0, or prints the
stopping reason and returns 2; the script exits with that value.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from importlib import util
from pathlib import Path
from types import ModuleType
from typing import Final, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from skillkit.mirror_qualification import (
    DEFAULT_SETTLE_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    RECONCILE_OPTION,
    STATUS_FIELD,
    STATUS_MODULE,
    InstalledMirrorCommand,
    InstalledStatus,
    MirrorQualification,
    NativeMirrorProvider,
    QualificationError,
    QualificationInputs,
    SeedRef,
    installed_bindings,
    terminate_as_interrupt,
)

WORK_DIR_HELP: Final = (
    "directory for receipts, outputs and the ledger; created if absent and reused "
    "if present; a ledger in it stops a new run and is what --reconcile reads"
)
HELP_OPTIONS: Final = frozenset({"-h", "--help"})
PLAN_ONLY_OPTION: Final = "--plan-only"
APPLY_MODE_OPTION: Final = "--apply"
# Shown by --help, which runs before the installed catalog is imported.
GITLAB_OPTION_PLACEHOLDER: Final = "--<GITLAB_TARGET_OPTION>"
# _blocked() runs when the installed copy could not be bound. It reads BLOCKED and
# exit_code from this repository's copy of the status module, loaded by file path
# under REPOSITORY_STATUS_NAME so that ``lib`` stays the installed package.
REPOSITORY_SKILL: Final = Path(__file__).resolve().parents[1] / "ci-skills"
REPOSITORY_STATUS_FILE: Final = REPOSITORY_SKILL.joinpath(
    *STATUS_MODULE.split(".")
).with_suffix(".py")
REPOSITORY_STATUS_NAME: Final = "_qualify_mirror_repository_status"
REASON_FIELD: Final = "reason"
PLAN_FIELD: Final = "plan"
LEDGER_FIELD: Final = "ledger"


def skill_parser() -> argparse.ArgumentParser:
    """Declare ``--skill``; ``main`` parses it before it imports the installed catalog.

    :returns: Parser holding only ``--skill``, without help.
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--skill", required=True, help="installed ci-skills directory")
    return parser


def build_parser(gitlab_option: str) -> argparse.ArgumentParser:
    """Declare the fixture identity and run controls.

    :param gitlab_option: ``GITLAB_TARGET_OPTION`` of the installed catalog, or
        ``GITLAB_OPTION_PLACEHOLDER`` for ``--help``.
    :returns: Parser for the qualification command.
    """
    parser = argparse.ArgumentParser(
        description="Run one bounded mirror qualification from an installed copy. "
        "Without --apply or --reconcile it is a dry run.",
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
        help="GitLab instance passed to the installed command, which validates it; "
        "the option name is GITLAB_TARGET_OPTION of the installed catalog",
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
        PLAN_ONLY_OPTION,
        action="store_true",
        help="dry run, the default: run the read-only steps and the wrong-digest "
        "rejection; create, delete and write no ledger",
    )
    mode.add_argument(
        APPLY_MODE_OPTION,
        action="store_true",
        help="create the mirror and destination, apply the repeat, then delete "
        "both by native ID",
    )
    mode.add_argument(
        RECONCILE_OPTION,
        action="store_true",
        help="finish the pending cleanup of the ledger in --work-dir, "
        "for its recorded native IDs only",
    )
    return parser


def repository_status() -> ModuleType:
    """Load ``REPOSITORY_STATUS_FILE`` as ``REPOSITORY_STATUS_NAME``.

    ``repository_status`` does not add the module to ``sys.modules``;
    ``status.py`` imports nothing.

    :returns: The repository's status module.
    :raises ImportError: ``importlib.util`` returns no spec or loader for the file.
    """
    spec = util.spec_from_file_location(REPOSITORY_STATUS_NAME, REPOSITORY_STATUS_FILE)
    if spec is None or spec.loader is None:
        raise ImportError(str(REPOSITORY_STATUS_FILE), name=REPOSITORY_STATUS_NAME)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _blocked(reason: str) -> int:
    """Print a stopping reason found before the installed copy is bound.

    :param reason: Stopping reason.
    :returns: Exit code of ``BLOCKED`` from the repository's status module.
    """
    status = repository_status()
    print(json.dumps({STATUS_FIELD: status.BLOCKED, REASON_FIELD: reason}))
    return status.exit_code(status.BLOCKED)


def _report(status: InstalledStatus, token: str, field: str, value: object) -> int:
    """Print one result with an installed status token.

    :param status: Tokens and exit mapping bound from the installed copy.
    :param token: Installed status token of the result.
    :param field: Result field carrying ``value``.
    :param value: Plan, ledger or stopping reason.
    :returns: Installed exit code of ``token``.
    """
    print(json.dumps({STATUS_FIELD: token, field: value}, sort_keys=True))
    return status.exit_code(token)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Bind the installed copy, parse inputs, run the selected mode and print it.

    ``-h`` or ``--help`` prints the help and exits 0 before ``--skill`` is read
    or the installed copy is imported. Otherwise ``installed_bindings`` puts the
    installed skill directory first on ``sys.path`` before any ``lib`` import, so
    ``main`` reads the GitLab option, the status tokens and the provider from the
    installed copy.

    :param argv: Arguments. When the value is ``None``, ``main`` reads
        ``sys.argv[1:]``.
    :returns: 0 after PASS or PLANNED, 2 when a step stops the run.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not HELP_OPTIONS.isdisjoint(arguments):
        parser = build_parser(GITLAB_OPTION_PLACEHOLDER)
        parser.print_help()
        parser.exit()
    known, _ = skill_parser().parse_known_args(arguments)
    skill = Path(known.skill).expanduser().resolve()
    try:
        bindings = installed_bindings(skill)
    except QualificationError as exc:
        return _blocked(str(exc))
    status = bindings.status
    args = build_parser(bindings.gitlab_option).parse_args(arguments)
    work_dir = Path(args.work_dir).expanduser().resolve()
    try:
        if not args.reconcile:
            work_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _report(
            status,
            status.blocked,
            REASON_FIELD,
            f"work_dir_unavailable:{type(exc).__name__}",
        )
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
            status,
            bindings.fields,
        )
        with terminate_as_interrupt():
            if args.apply:
                token, field, value = status.passed, LEDGER_FIELD, qualification.run()
            elif args.reconcile:
                token, field = status.passed, LEDGER_FIELD
                value = qualification.reconcile()
            else:
                token, field, value = status.planned, PLAN_FIELD, qualification.plan()
    except QualificationError as exc:
        return _report(status, status.blocked, REASON_FIELD, str(exc))
    return _report(status, token, field, value)


if __name__ == "__main__":
    sys.exit(main())
