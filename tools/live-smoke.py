#!/usr/bin/env python3
"""Run one deterministic live smoke fixture lifecycle across GitHub and GitLab.

The behavior lives in ``tools/skillkit/live_smoke.py``; this wrapper only binds
arguments, resolves the GitLab base URL from the selected target file, and maps
failures to exit codes. Nothing mutates unless ``apply`` or ``cleanup`` runs.

    tools/live-smoke.py plan --phase ci14 --case mirror
    tools/live-smoke.py apply --phase ci14 --case mirror --run-id r20261122a \\
        --github-token-file ~/.ci-skills/github.com.token.env
    tools/live-smoke.py cleanup --run-id r20261122a
    tools/live-smoke.py cleanup --run-id r20261122a --reconcile

``plan`` needs no credentials and creates nothing; it prints the deterministic
names both providers would use. ``apply`` creates the GitHub repository and the
GitLab project, refuses when either name is already present, and writes a ledger
under ``--state-dir``. ``cleanup`` re-reads each object, requires the recorded
native ID, deletes, verifies absence by path and by ID, and repeats the absence
read-back; ``--reconcile`` finishes a cleanup a previous run left partial.
Both fixture systems share the name ``smoke-<phase>-<case>-<run_id>`` and the
GitLab project additionally lives under the ``--group`` namespace, which is
created when absent and never deleted.

Credentials: an explicit ``--github-token-file``/``--gitlab-token-file`` wins,
then the environment (``GH_TOKEN``/``GITHUB_TOKEN``, ``GITLAB_TOKEN``), then the
filed defaults ``~/.ci-skills/github.com.token.env`` and
``~/.ci-skills/gitlab.dcloud.run.token.env``. Token values are never printed.
The GitHub token needs Administration and Contents access for the smoke owner;
the GitLab token needs project creation under the smoke group. The GitLab base
URL comes from ``[gitlab] url`` in ``--target-file`` (default
``~/.ci-skills/target.toml``) unless ``--gitlab-url`` overrides it.

``main`` prints the plan, the ledger or the cleanup evidence and returns 0, or
prints the stable failure reason and returns 2.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import os
import runpy
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final, Optional

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
runpy.run_path(str(TOOLS.parent / "ci-skills" / "bin" / "_bootstrap.py"))

from lib.python.core.target import TargetError, load_gitlab_target
from skillkit.live_smoke import (
    DEFAULT_GITHUB_OWNER,
    DEFAULT_GITHUB_TOKEN_FILE,
    DEFAULT_GITLAB_GROUP,
    GITHUB_API_HOST,
    GITHUB_TOKEN_VARIABLES,
    GITLAB_TOKEN_VARIABLES,
    GitHubSmoke,
    GitLabSmoke,
    LiveSmoke,
    RestClient,
    SmokeError,
    classify_github_token,
    resolve_token,
)

DEFAULT_TARGET_FILE: Final = Path("~/.ci-skills/target.toml").expanduser()
DEFAULT_STATE_DIR: Final = Path(".internal/scratch/live-smoke")
USER_AGENT: Final = "ci-skills-live-smoke"
GITHUB_API_VERSION: Final = "2022-11-28"


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser shared by every entry point.

    :returns: Parser with the plan, apply and cleanup subcommands.
    """
    parser = argparse.ArgumentParser(
        description="Deterministic live smoke fixture lifecycle.",
    )
    parser.add_argument(
        "--owner",
        default=DEFAULT_GITHUB_OWNER,
        help=f"GitHub owner for fixtures (default {DEFAULT_GITHUB_OWNER}).",
    )
    parser.add_argument(
        "--group",
        default=DEFAULT_GITLAB_GROUP,
        help=f"GitLab group path for fixtures (default {DEFAULT_GITLAB_GROUP}).",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=DEFAULT_STATE_DIR,
        help=f"Ledger directory (default {DEFAULT_STATE_DIR}).",
    )
    parser.add_argument(
        "--github-token-file",
        type=Path,
        default=None,
        help="Fine-grained PAT file; overrides environment and filed defaults.",
    )
    parser.add_argument(
        "--gitlab-token-file",
        type=Path,
        default=None,
        help="GitLab token file; overrides the selected target credential and environment.",
    )
    parser.add_argument(
        "--target-file",
        type=Path,
        default=DEFAULT_TARGET_FILE,
        help=f"Target file selecting the GitLab route and credential (default {DEFAULT_TARGET_FILE}).",
    )
    parser.add_argument(
        "--gitlab-url",
        default=None,
        help="GitLab base URL overriding the target; requires an explicit or environment token.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_fixture_arguments(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "--phase", required=True, help="Phase bucket, e.g. ci14."
        )
        subparser.add_argument("--case", required=True, help="Smoke case, e.g. mirror.")
        subparser.add_argument(
            "--run-id",
            default="",
            help="Run identifier; required for apply, optional for plan.",
        )

    plan_parser = subparsers.add_parser("plan", help="Print the fixture plan.")
    add_fixture_arguments(plan_parser)

    apply_parser = subparsers.add_parser("apply", help="Create the fixture set.")
    add_fixture_arguments(apply_parser)

    cleanup_parser = subparsers.add_parser("cleanup", help="Delete the fixture set.")
    cleanup_parser.add_argument("--run-id", required=True, help="Run to close.")
    cleanup_parser.add_argument(
        "--reconcile",
        action="store_true",
        help="Tolerate fixtures already absent instead of aborting.",
    )
    return parser


def resolve_gitlab_binding(arguments: argparse.Namespace) -> tuple[str, Optional[Path]]:
    """Select the GitLab server and its credential source together.

    :param arguments: Parsed GitLab target and credential options.
    :returns: GitLab base URL and selected token file, if any.
    :raises SmokeError: The configured GitLab route cannot be loaded.
    """
    if arguments.gitlab_url:
        return arguments.gitlab_url.rstrip("/"), arguments.gitlab_token_file
    try:
        target = load_gitlab_target(arguments.target_file)
    except TargetError as exc:
        raise SmokeError("gitlab_target_invalid", str(arguments.target_file)) from exc
    return target.gitlab.url, arguments.gitlab_token_file or target.gitlab.token_file


def build_smoke(arguments: argparse.Namespace) -> LiveSmoke:
    """Bind both provider lifecycles from the resolved arguments.

    :param arguments: Parsed command line.
    :returns: Configured smoke session; credentials stay inside it.
    :raises SmokeError: If a credential or the GitLab URL cannot be resolved.
    """
    github_token, github_source = resolve_token(
        arguments.github_token_file,
        GITHUB_TOKEN_VARIABLES,
        DEFAULT_GITHUB_TOKEN_FILE,
        os.environ,
        "github",
    )
    gitlab_url, gitlab_token_file = resolve_gitlab_binding(arguments)
    gitlab_token, _ = resolve_token(
        gitlab_token_file,
        GITLAB_TOKEN_VARIABLES,
        None,
        os.environ,
        "gitlab",
    )
    github_client = RestClient(
        f"https://{GITHUB_API_HOST}",
        {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {github_token}",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        },
    )
    gitlab_client = RestClient(
        gitlab_url,
        {"PRIVATE-TOKEN": gitlab_token, "User-Agent": USER_AGENT},
    )
    github = GitHubSmoke(
        github_client,
        arguments.owner,
        credential_source=github_source,
        token_kind=classify_github_token(github_token),
    )
    gitlab = GitLabSmoke(gitlab_client)
    return LiveSmoke(github, gitlab, arguments.owner, arguments.group)


def emit(document: object) -> None:
    """Print one JSON document followed by a newline.

    :param document: JSON-serializable value to print.
    """
    print(json.dumps(document, indent=2, sort_keys=True))


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the requested lifecycle step.

    :param argv: Argument vector; ``None`` uses ``sys.argv``.
    :returns: 0 on success, 2 with the stable reason printed to stderr.
    """
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "plan":
            smoke = LiveSmoke(
                GitHubSmoke(RestClient("https://invalid", {}), arguments.owner),
                GitLabSmoke(RestClient("https://invalid", {})),
                arguments.owner,
                arguments.group,
            )
            emit(smoke.plan(arguments.phase, arguments.case, arguments.run_id))
            return 0
        smoke = build_smoke(arguments)
        if arguments.command == "apply":
            if not arguments.run_id:
                raise SmokeError("run_id_required", "apply needs --run-id")
            ledger = smoke.apply(
                arguments.phase, arguments.case, arguments.run_id, arguments.state_dir
            )
            emit(ledger.to_json())
            return 0
        ledger = smoke.cleanup(
            arguments.state_dir, arguments.run_id, reconcile=arguments.reconcile
        )
        emit(ledger.to_json())
        return 0
    except SmokeError as exc:
        print(
            f"{exc.reason}: {exc.detail}" if exc.detail else exc.reason, file=sys.stderr
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
