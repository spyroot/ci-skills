#!/usr/bin/env python3
"""Run the one bounded mirror qualification for candidate receipt renewal.

The fixture identity comes from `docs/2026-10-31/live-smoke-inventory.md`
(`test-mirror-push`); the candidate is the installed copy named by `--skill`.
The behavior lives in `tools/skillkit/mirror_qualification.py`.

    tools/qualify_mirror.py --skill ~/.claude/skills/ci-skills \\
        --work-dir .internal/log/bug-133/mirror --project hott/test-mirror-push \\
        --destination spyroot/test-mirror-push --source-id 261 \\
        --seed-ref branches:main:<sha> --seed-ref tags:smoke-seed:<sha> --run-id <id>

Exit 0 prints the final allocation ledger; exit 2 prints the stopping reason.

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
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from skillkit.mirror_qualification import (
    EXIT_BLOCKED,
    EXIT_SUCCESS,
    InstalledMirrorCommand,
    MirrorQualification,
    NativeMirrorProvider,
    QualificationError,
    QualificationInputs,
    SeedRef,
)


def build_parser() -> argparse.ArgumentParser:
    """Declare the fixture identity and run controls.

    :returns: Parser for the qualification command.
    """
    parser = argparse.ArgumentParser(
        description="Run one bounded mirror qualification from an installed copy."
    )
    parser.add_argument("--skill", required=True, help="installed ci-skills directory")
    parser.add_argument(
        "--work-dir", required=True, help="new directory for receipts and the ledger"
    )
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
        "--gitlab",
        help="GitLab instance passed to the installed command, which validates it",
    )
    parser.add_argument("--timeout", type=int, default=300, help="seconds per command")
    parser.add_argument(
        "--settle-seconds", type=int, default=30, help="pause before repeat steps"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse inputs, run the qualification and print its ledger or reason.

    :param argv: Arguments, or None for the process arguments.
    :returns: 0 after PASS, 2 when a step stops the run.
    """
    args = build_parser().parse_args(argv)
    work_dir = Path(args.work_dir).expanduser().resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        inputs = QualificationInputs(
            skill=Path(args.skill).expanduser().resolve(),
            work_dir=work_dir,
            project=args.project,
            destination=args.destination,
            source_id=args.source_id,
            seed_refs=tuple(SeedRef.parse(text) for text in args.seed_ref),
            run_id=args.run_id,
            gitlab=args.gitlab,
            timeout=args.timeout,
            settle_seconds=args.settle_seconds,
        )
        ledger = MirrorQualification(
            inputs,
            InstalledMirrorCommand(inputs),
            lambda: NativeMirrorProvider(inputs),
        ).run()
    except QualificationError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}))
        return EXIT_BLOCKED
    print(json.dumps({"status": "PASS", "ledger": ledger}, sort_keys=True))
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
