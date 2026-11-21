#!/usr/bin/env python3
"""Write the cleanup record of one GitLab Agent qualification series.

``observe`` runs after ``gitlab_agent.py delete --apply`` and again after the repeat
delete. It binds the installed copy named by ``--skill`` to the target and reads the
Agent, its token, every resource of the connect APPLIED receipt's ownership record,
the Helm release and the run resources, and writes one stage file.
``attach`` builds the ``gitlab_agent_smoke_cleanup`` record from the connect APPLIED
receipt and the two stage files and writes the delete NO_OP receipt with it.
`tools/skillkit/agent_cleanup.py` holds the record logic.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from skillkit.agent_cleanup import (
    AgentCleanupError,
    attach_record,
    cleanup_record,
    connect_ownership,
    observe_stage,
)

OBSERVE: Final = "observe"
ATTACH: Final = "attach"
DEFAULT_TIMEOUT: Final = 300
PASS: Final = "PASS"
BLOCKED: Final = "BLOCKED"
BLOCKED_EXIT: Final = 2
# Ownership keys that select the installed scope for the stage read.
PROJECT_PATH: Final = "project_path"
PROJECT_ID: Final = "project_id"
CLUSTER_UID: Final = "cluster_uid"
ORIGIN: Final = "origin"
RUN_ID: Final = "run_id"
NAMESPACE: Final = "namespace"
AGENT_NAME: Final = "agent_name"
RELEASE: Final = "release"
CREATED: Final = "created"


def build_parser() -> argparse.ArgumentParser:
    """Declare the two subcommands and their file arguments.

    :returns: Parser for ``observe`` and ``attach``.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    observe = commands.add_parser(OBSERVE, help="read one cleanup stage")
    observe.add_argument("--skill", required=True, help="installed skill directory")
    observe.add_argument(
        "--connect-receipt", required=True, help="connect APPLIED receipt"
    )
    observe.add_argument("--out", required=True, help="new stage file to write")
    observe.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT, help="read budget in seconds"
    )
    attach = commands.add_parser(ATTACH, help="attach the record to the repeat receipt")
    attach.add_argument(
        "--connect-receipt", required=True, help="connect APPLIED receipt"
    )
    attach.add_argument(
        "--after", required=True, help="stage file read after the delete"
    )
    attach.add_argument(
        "--repeat", required=True, help="stage file read after the repeat"
    )
    attach.add_argument("--receipt", required=True, help="delete NO_OP receipt")
    attach.add_argument("--out", required=True, help="new receipt file to write")
    return parser


def _read(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path: str, document: Mapping[str, Any]) -> None:
    with Path(path).open("x", encoding="utf-8") as output:
        json.dump(document, output, indent=2, sort_keys=True)
        output.write("\n")


def observe(skill: Path, connect: Mapping[str, Any], timeout: int) -> dict[str, object]:
    """Bind the installed copy to the recorded scope and read one stage.

    :param skill: Installed skill directory whose ``lib`` package is imported.
    :param connect: Connect APPLIED receipt of the series.
    :param timeout: Budget for target resolution and every read, in seconds.
    :returns: Stage document for ``attach``.
    :raises AgentCleanupError: The bound project, cluster, GitLab origin or release
        differs from the ownership record, or an object is still present.
    """  # noqa: DOC502 - observe_stage raises for a present object.
    sys.path.insert(0, str(skill))
    from lib.python.core.cli_api import RequestDeadline
    from lib.python.core.gitlab.cluster_agent.absence import (
        verify_agent_logical_absence,
    )
    from lib.python.core.gitlab.cluster_agent.authority import (
        observe_authorities,
        resolve_agent_target,
    )
    from lib.python.core.gitlab.cluster_agent.selection import (
        ConnectSelection,
        release_name,
    )
    from lib.python.core.kubernetes.run_scope import RunScope

    ownership = connect_ownership(connect)
    deadline = RequestDeadline(time.monotonic() + timeout)
    selected = deadline.call(
        resolve_agent_target, None, None, ownership[PROJECT_PATH], None, dry_run=False
    )
    authorities = observe_authorities(selected, revision=None, deadline=deadline)
    if (
        authorities.project.id != ownership[PROJECT_ID]
        or authorities.cluster_uid != ownership[CLUSTER_UID]
        or authorities.gitlab.session.origin.rstrip("/") != ownership[ORIGIN]
    ):
        raise AgentCleanupError("agent_cleanup_scope_mismatch")
    scope = RunScope(
        ownership[RUN_ID], ownership[NAMESPACE], ownership[CREATED][NAMESPACE]
    )
    agent_name = ownership[AGENT_NAME]
    selection = ConnectSelection(
        authorities.project, agent_name, scope, release_name(scope, agent_name)
    )
    if selection.release != ownership[RELEASE]:
        raise AgentCleanupError("agent_cleanup_release_mismatch")
    return observe_stage(
        authorities.provider,
        authorities.kubernetes,
        ownership,
        lambda: verify_agent_logical_absence(
            authorities.gitlab,
            authorities.kubernetes,
            selection,
            timeout=deadline.remaining(),
        ),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run one subcommand and print its status line as JSON.

    :param argv: Command arguments, or the process arguments when absent.
    :returns: Zero after writing the output file, two when a read or check fails.
    """
    args = build_parser().parse_args(argv)
    try:
        connect = _read(args.connect_receipt)
        if args.command == OBSERVE:
            document = observe(Path(args.skill).resolve(), connect, args.timeout)
        else:
            record = cleanup_record(
                connect, _read(args.after), _read(args.repeat), datetime.now(UTC)
            )
            document = attach_record(_read(args.receipt), record)
        _write(args.out, document)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        print(json.dumps({"status": BLOCKED, "reason": str(exc)}))
        return BLOCKED_EXIT
    print(json.dumps({"status": PASS, "out": args.out}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
