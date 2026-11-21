#!/usr/bin/env python3
"""Write the cleanup record of one GitLab Agent qualification series.

``observe`` runs after ``gitlab_agent.py delete --apply`` and again after the repeat
delete. It binds the installed copy named by ``--skill`` to the same target the
series used (``--binding``, ``--gitlab``, ``--gitlab-profile``), requires that copy
to be the one that wrote the connect APPLIED receipt, reads the Agent, its token,
every resource of that receipt's ownership record, the Helm release and the run
resources, and writes one stage file.
``attach`` builds the ``gitlab_agent_smoke_cleanup`` record from the connect APPLIED
receipt and the two stage files and writes the delete NO_OP receipt with it.
`tools/skillkit/agent_cleanup.py` holds the record logic and
`tools/skillkit/installed_copy.py` the load guard.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from skillkit.agent_cleanup import (
    AGENT_NAME_KEY,
    CLUSTER_UID_KEY,
    CREATED_KEY,
    NAMESPACE_KEY,
    ORIGIN_KEY,
    PROJECT_ID_KEY,
    PROJECT_PATH_KEY,
    RELEASE_KEY,
    RUN_ID_FIELD,
    AgentCleanupError,
    AgentKeys,
    attach_record,
    cleanup_record,
    connect_ownership,
    observe_stage,
    require_same_copy,
    stage_file,
)
from skillkit.installed_copy import (
    InstalledCopyError,
    bind_skill,
    file_module,
    installed_module,
)

OBSERVE: Final = "observe"
ATTACH: Final = "attach"
SKILL_OPTION: Final = "--skill"
BINDING_OPTION: Final = "--binding"
DEFAULT_TIMEOUT: Final = 300
STATUS_FIELD: Final = "status"
REASON_FIELD: Final = "reason"
OUT_FIELD: Final = "out"
STATUS_MODULE: Final = "lib.python.sys.status"
# Before a copy is bound, BLOCKED and exit_code come from this repository's
# stdlib-only status module, loaded by path so that ``lib`` stays unbound.
REPOSITORY_STATUS_FILE: Final = (
    Path(__file__).resolve().parents[1] / "ci-skills" / Path(*STATUS_MODULE.split("."))
).with_suffix(".py")
REPOSITORY_STATUS_NAME: Final = "_qualify_agent_cleanup_repository_status"


@dataclass(frozen=True)
class InstalledAgent:
    """Modules of the installed copy that this tool calls.

    .. attribute :: skill
        :type: Path

        Installed skill directory the modules load from.

    .. attribute :: modules
        :type: Mapping[str, ModuleType]

        Loaded module per dotted name in ``MODULES``.
    """

    skill: Path
    modules: Mapping[str, ModuleType]

    def __getitem__(self, name: str) -> ModuleType:
        """Return one bound module.

        :param name: Dotted module name from ``MODULES``.
        :returns: The module loaded from ``skill``.
        """
        return self.modules[name]

    @property
    def keys(self) -> AgentKeys:
        """Bind the record and identity keys the installed copy owns.

        :returns: Keys for `tools/skillkit/agent_cleanup.py`.
        """
        recovery = self[RECOVERY_MODULE]
        return AgentKeys(
            recovery.CREDENTIAL,
            recovery.CREDENTIAL_ID,
            self[PROVENANCE_MODULE].DIGEST_VALUE_KEY,
        )


ABSENCE_MODULE: Final = "lib.python.core.gitlab.cluster_agent.absence"
AUTHORITY_MODULE: Final = "lib.python.core.gitlab.cluster_agent.authority"
SELECTION_MODULE: Final = "lib.python.core.gitlab.cluster_agent.selection"
RECOVERY_MODULE: Final = "lib.python.core.gitlab.cluster_agent.recovery"
RUN_SCOPE_MODULE: Final = "lib.python.core.kubernetes.run_scope"
CLI_API_MODULE: Final = "lib.python.core.cli_api"
CLI_MODULE: Final = "lib.python.core.cli"
TARGET_MODULE: Final = "lib.python.core.target"
PROVENANCE_MODULE: Final = "lib.python.core.provenance"
MODULES: Final = (
    STATUS_MODULE,
    ABSENCE_MODULE,
    AUTHORITY_MODULE,
    SELECTION_MODULE,
    RECOVERY_MODULE,
    RUN_SCOPE_MODULE,
    CLI_API_MODULE,
    CLI_MODULE,
    TARGET_MODULE,
    PROVENANCE_MODULE,
)


def bind_installed(skill: Path) -> InstalledAgent:
    """Put ``skill`` first on ``sys.path`` and load every module this tool calls.

    :param skill: Installed skill directory.
    :returns: The loaded modules.
    :raises InstalledCopyError: A module cannot be imported or loads elsewhere.
    """  # noqa: DOC502 - installed_module raises for each module.
    bind_skill(skill)
    return InstalledAgent(
        skill, {name: installed_module(skill, name) for name in MODULES}
    )


def build_parser(
    add_selection: Optional[Callable[[argparse.ArgumentParser], None]] = None,
) -> argparse.ArgumentParser:
    """Declare the two subcommands and their file arguments.

    :param add_selection: Installed ``add_gitlab_selection_arguments``; absent
        before a copy is bound, for ``--help``.
    :returns: Parser for ``observe`` and ``attach``.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    observe = commands.add_parser(OBSERVE, help="read one cleanup stage")
    attach = commands.add_parser(ATTACH, help="attach the record to the repeat receipt")
    for command in (observe, attach):
        command.add_argument(
            SKILL_OPTION, required=True, help="installed skill directory"
        )
        command.add_argument(
            "--connect-receipt", required=True, help="connect APPLIED receipt"
        )
        command.add_argument("--out", required=True, help="new file to write")
    observe.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT, help="read budget in seconds"
    )
    observe.add_argument(
        BINDING_OPTION, metavar="PATH", help="project binding the series used"
    )
    if add_selection is not None:
        add_selection(observe)
    attach.add_argument(
        "--after", required=True, help="stage file read after the delete"
    )
    attach.add_argument(
        "--repeat", required=True, help="stage file read after the repeat"
    )
    attach.add_argument("--receipt", required=True, help="delete NO_OP receipt")
    return parser


def _read(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path: str, document: Mapping[str, Any]) -> None:
    with Path(path).open("x", encoding="utf-8") as output:
        json.dump(document, output, indent=2, sort_keys=True)
        output.write("\n")


def observe(
    installed: InstalledAgent, args: argparse.Namespace, connect: Mapping[str, Any]
) -> dict[str, object]:
    """Bind the installed copy to the recorded scope and read one stage.

    :param installed: Modules of the copy named by ``--skill``.
    :param args: Parsed ``observe`` arguments with the series' target selection.
    :param connect: Connect APPLIED receipt of the series.
    :returns: Stage file document for ``attach``.
    :raises AgentCleanupError: The copy, project, cluster, GitLab origin or release
        differs from the series, or an object is still present.
    """  # noqa: DOC502 - observe_stage raises for a present object.
    keys = installed.keys
    ownership = connect_ownership(connect)
    deadline = installed[CLI_API_MODULE].RequestDeadline(
        time.monotonic() + args.timeout
    )
    authority = installed[AUTHORITY_MODULE]
    selected = deadline.call(
        authority.resolve_agent_target,
        None,
        args.binding,
        ownership[PROJECT_PATH_KEY],
        installed[TARGET_MODULE].GitLabSelection.from_args(args),
        dry_run=False,
    )
    authorities = authority.observe_authorities(
        selected, revision=None, deadline=deadline
    )
    session = authorities.gitlab.session
    require_same_copy(connect, session.skill, keys)
    if (
        authorities.project.id != ownership[PROJECT_ID_KEY]
        or authorities.cluster_uid != ownership[CLUSTER_UID_KEY]
        or session.origin.rstrip("/") != ownership[ORIGIN_KEY]
    ):
        raise AgentCleanupError("agent_cleanup_scope_mismatch")
    scope = installed[RUN_SCOPE_MODULE].RunScope(
        ownership[RUN_ID_FIELD],
        ownership[NAMESPACE_KEY],
        ownership[CREATED_KEY][NAMESPACE_KEY],
    )
    agent_name = ownership[AGENT_NAME_KEY]
    selection_module = installed[SELECTION_MODULE]
    selection = selection_module.ConnectSelection(
        authorities.project,
        agent_name,
        scope,
        selection_module.release_name(scope, agent_name),
    )
    if selection.release != ownership[RELEASE_KEY]:
        raise AgentCleanupError("agent_cleanup_release_mismatch")
    observation = observe_stage(
        authorities.provider,
        authorities.kubernetes,
        ownership,
        lambda: installed[ABSENCE_MODULE].verify_agent_logical_absence(
            authorities.gitlab,
            authorities.kubernetes,
            selection,
            timeout=deadline.remaining(),
        ),
        keys,
    )
    return stage_file(ownership, observation, session.skill, datetime.now(UTC), keys)


def _skill(argv: Sequence[str]) -> Optional[Path]:
    known = argparse.ArgumentParser(add_help=False)
    known.add_argument(SKILL_OPTION)
    value = known.parse_known_args(argv)[0].skill
    return None if value is None else Path(value).resolve()


def _print(status: ModuleType, token: str, field: str, value: object) -> int:
    print(json.dumps({STATUS_FIELD: token, field: value}, sort_keys=True))
    return status.exit_code(token)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run one subcommand and print its status line as JSON.

    :param argv: Command arguments, or the process arguments when absent.
    :returns: Installed exit code of PASS after writing the output file, or of
        BLOCKED when binding, a read or a check fails.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    skill = _skill(arguments)
    if skill is None:
        build_parser().parse_args(arguments)
    try:
        installed = bind_installed(skill)
    except InstalledCopyError as exc:
        status = file_module(REPOSITORY_STATUS_NAME, REPOSITORY_STATUS_FILE)
        return _print(status, status.BLOCKED, REASON_FIELD, str(exc))
    status = installed[STATUS_MODULE]
    args = build_parser(
        installed[CLI_MODULE].add_gitlab_selection_arguments
    ).parse_args(arguments)
    try:
        connect = _read(args.connect_receipt)
        if args.command == OBSERVE:
            document = observe(installed, args, connect)
        else:
            receipt = _read(args.receipt)
            record = cleanup_record(
                connect,
                _read(args.after),
                _read(args.repeat),
                receipt,
                datetime.now(UTC),
                installed.keys,
            )
            document = attach_record(receipt, record)
        _write(args.out, document)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return _print(status, status.BLOCKED, REASON_FIELD, str(exc))
    return _print(status, status.PASS, OUT_FIELD, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
