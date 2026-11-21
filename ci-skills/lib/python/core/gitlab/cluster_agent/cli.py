"""Parse the installed GitLab Agent operation and its confirmation controls."""

from __future__ import annotations

import time
from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from typing import Final

from lib.python.core.action_modes import (
    GITLAB_MODE_POLICY,
    MAX_ACTION_TIMEOUT,
    ActionMode,
    validate_action_mode,
)
from lib.python.core.catalog import (
    COMMANDS,
    ROUTED_RESULT_SCHEMA_VERSION,
    UNIVERSAL_OPTIONS,
    describe,
)
from lib.python.core.cli import _failure, parser
from lib.python.core.cli_api import APIError, RequestDeadline
from lib.python.core.gitlab.cluster_agent.authority import (
    observe_authorities,
    resolve_agent_target,
)
from lib.python.core.gitlab.cluster_agent.receipt import agent_result
from lib.python.core.gitlab.cluster_agent.workflow import run_agent_workflow
from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.core.report import emit_result, report
from lib.python.core.runner_identity import validate_project_name
from lib.python.core.schema import validate_specification
from lib.python.core.target import GitLabSelection
from lib.python.sys.status import DRY_RUN, PASS

COMMAND: Final = "gitlab_agent.py"
KIND: Final = "gitlab_agent"
INTENT_SCHEMA: Final = "gitlab-agent.schema.json"
RESULT_SCHEMA: Final = "results/gitlab-agent.schema.json"
VERIFY: Final = "verify"
_BOOLEAN_OPTIONS: Final = frozenset(("--live-plan", "--apply"))


def build_parser() -> ArgumentParser:
    """Use the approved Agent catalog and common installed CLI options.

    :returns: Parser for connect, verify, and delete.
    """
    entry = COMMANDS[COMMAND]
    result = parser(entry["purpose"], kind=KIND)
    result.prog = COMMAND
    result.add_argument("action", nargs="?", choices=tuple(entry["subcommands"]))
    for option, help_text in entry["options"].items():
        if option in UNIVERSAL_OPTIONS:
            continue
        if option in _BOOLEAN_OPTIONS:
            result.add_argument(option, action="store_true", help=help_text)
        elif option == "--timeout":
            result.add_argument(
                option, type=int, default=MAX_ACTION_TIMEOUT, help=help_text
            )
        else:
            result.add_argument(option, help=help_text)
    return result


def execution_mode(args: Namespace) -> ActionMode:
    """Reject inappropriate controls before consulting any provider.

    :param args: Parsed Agent and common CLI controls.
    :returns: Offline, live plan, read-only verify, or confirmed apply mode.
    :raises ValueError: An operation is missing or its controls are inconsistent.
    """
    if args.action is None:
        raise ValueError("agent_operation_required")
    read_only = args.action == VERIFY
    if read_only and (args.apply or args.confirm_plan or args.live_plan):
        raise ValueError("agent_verify_requires_read_only_mode")
    if args.apply and (not args.confirm_plan or not args.run_id):
        raise ValueError("agent_apply_requires_plan_digest_and_run_id")
    return validate_action_mode(
        apply=args.apply,
        dry_run=args.dry_run,
        live_plan=args.live_plan,
        confirmed=bool(args.confirm_plan),
        receipt_requested=bool(args.receipt_out),
        output_requested=bool(args.output_dir or args.log_file),
        timeout=args.timeout,
        policy=GITLAB_MODE_POLICY,
        read_only=read_only,
    )


def normalized_intent(args: Namespace, project: str) -> JSONObject:
    """Bind user naming separately from the selected provider project.

    :param args: Parsed operation and caller-supplied selectors.
    :param project: Project reference resolved by the shared target selector.
    :returns: Normalized intent validated by the installed Agent schema.
    :raises ValueError: A selected name or field conflicts with the approved contract.
    """  # noqa: DOC502 - Shared intent and consumer-name validation can fail.
    intent: JSONObject = {
        "operation": args.action,
        "project": project,
        "project_name": validate_project_name(args.project_name),
        "agent_name": args.agent_name,
        "run_id": args.run_id or None,
        "namespace": args.namespace,
        "job_url": args.job_url,
    }
    validate_specification(intent, INTENT_SCHEMA)
    return intent


def offline_result(args: Namespace) -> JSONObject:
    """Resolve selected intent without binding credentials or contacting providers.

    :param args: Parsed offline Agent operation and target selectors.
    :returns: Schema-validated, non-mutating operation result.
    :raises ValueError: The target or intent is invalid for an Agent operation.
    """  # noqa: DOC502,DOC503 - Shared target and schema readers propagate errors.
    selected = resolve_agent_target(
        args.target,
        args.binding,
        args.project,
        GitLabSelection.from_args(args),
        dry_run=True,
    )
    target = selected.target
    intent = normalized_intent(args, selected.project)
    result = report(KIND, target.require_gitlab().url, intent, [], [])
    result.update(
        schema_version=ROUTED_RESULT_SCHEMA_VERSION,
        status=DRY_RUN,
        target_source=target.target_reference,
        operation=args.action,
        plan_digest=None,
        mutated=False,
        cleanup={"status": PASS, "failures": []},
    )
    if target.routing is not None:
        result["routing"] = target.routing.as_dict()
    validate_specification(result, RESULT_SCHEMA)
    return result


def _live_result(args: Namespace, mode: ActionMode) -> JSONObject:
    if args.action == VERIFY:
        raise ValueError("agent_ci_trusted_producers_missing")
    deadline = RequestDeadline(time.monotonic() + args.timeout)
    selected = deadline.call(
        resolve_agent_target,
        args.target,
        args.binding,
        args.project,
        GitLabSelection.from_args(args),
        dry_run=False,
    )
    intent = normalized_intent(args, selected.project)
    authorities = observe_authorities(
        selected, revision=args.revision, deadline=deadline
    )
    outcome = run_agent_workflow(
        authorities,
        intent,
        mode,
        deadline=deadline,
        confirmation=args.confirm_plan,
    )
    return agent_result(authorities, intent, outcome)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse an installed Agent invocation and render its selected report.

    :param argv: Explicit command arguments or process arguments when absent.
    :returns: Canonical command exit status.
    """  # noqa: DOC503 - Explicit output errors propagate.
    args = build_parser().parse_args(argv)
    if args.describe:
        return emit_result(describe(COMMAND), args)
    try:
        mode = execution_mode(args)
        result = (
            offline_result(args)
            if mode == ActionMode.OFFLINE
            else _live_result(args, mode)
        )
    except (APIError, KubernetesError, OSError, TypeError, ValueError) as exc:
        return _failure(args, KIND, "operation", str(exc))
    return emit_result(result, args)
