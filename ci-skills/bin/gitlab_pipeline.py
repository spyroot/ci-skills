#!/usr/bin/env python3
"""Read, start, or watch an exact GitLab pipeline.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from typing import Optional

import _bootstrap  # noqa: F401
from lib.python.access.gitlab import check_gitlab_operation_access
from lib.python.access.gitlab_credentials import bind_gitlab_session
from lib.python.core.catalog import describe
from lib.python.core.cli import _failure, output_mode, parser
from lib.python.core.gitlab.cli import resolve_gitlab_target
from lib.python.core.gitlab.gitlab_actions import ActionError
from lib.python.core.gitlab.gitlab_api import GitLabAPIError
from lib.python.core.gitlab.gitlab_pipeline_start import (
    DEFAULT_REQUEST_TIMEOUT,
    PIPELINE_KIND,
    start_workflow,
)
from lib.python.core.gitlab.gitlab_pipeline_watch import (
    DEFAULT_WATCH_INTERVAL,
    DEFAULT_WATCH_TIMEOUT,
    execute_pipeline_watch,
    watch_failure_report,
)
from lib.python.core.gitlab.gitlab_pipelines import (
    MAX_JOB_PAGES,
    PAGE_SIZE,
    attach_pipeline_provenance,
    read_pipeline,
)
from lib.python.core.json_types import (
    JSONObject,
    json_integer,
    json_object,
    json_objects,
    json_string,
)
from lib.python.core.logging import log_event
from lib.python.core.report import emit, report
from lib.python.core.runtime import sanitize
from lib.python.core.target import (
    GitLabOperationTarget,
    TargetError,
    select_gitlab_reference,
)
from lib.python.sys.status import BLOCKED, DRY_RUN, PASS, exit_code

KIND = PIPELINE_KIND
SCRIPT = "gitlab_pipeline.py"


def build_parser() -> argparse.ArgumentParser:
    """Declare read, start, and bounded watch interfaces.

    :returns: Parser preserving the historical bare ``--pipeline-id`` read.
    """
    cli = parser(
        "Read, start, or watch one selected GitLab pipeline. "
        "For start, --run-id also identifies the confirmed operation: "
        "reuse it for a retry, or change it to request a new identical build.",
        kind=KIND,
    )
    cli.set_defaults(kind=KIND)
    cli.add_argument(
        "action",
        nargs="?",
        choices=("get", "start", "watch"),
        default="get",
        help="operation; omitted keeps the existing pipeline read",
    )
    cli.add_argument(
        "--project",
        metavar="PATH_OR_ID",
        help="exact project path or numeric ID (defaults to gitlab.project in target)",
    )
    cli.add_argument(
        "--pipeline-id",
        metavar="ID",
        type=int,
        help="numeric pipeline ID for get or watch",
    )
    cli.add_argument(
        "--ref",
        help="pipeline ref for start; defaults to the project's live default branch",
    )
    cli.add_argument(
        "--variable",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="pipeline variable; repeat for additional variables",
    )
    cli.add_argument(
        "--require-protected-ref",
        action="store_true",
        help="require the resolved start ref to be a protected branch",
    )
    cli.add_argument(
        "--expected-sha",
        metavar="SHA",
        help="require start ref to resolve to this full lowercase commit SHA",
    )
    cli.add_argument(
        "--related-name",
        metavar="REGEX",
        help="watch newer same-ref root-project pipelines with matching names",
    )
    cli.add_argument(
        "--interval",
        type=float,
        metavar="SECONDS",
        help=f"watch poll interval (default {DEFAULT_WATCH_INTERVAL:g})",
    )
    cli.add_argument(
        "--timeout",
        type=int,
        metavar="SECONDS",
        help=(
            f"watch deadline (default {DEFAULT_WATCH_TIMEOUT:g}); start request "
            f"deadline (default {DEFAULT_REQUEST_TIMEOUT})"
        ),
    )
    cli.add_argument(
        "--live-plan",
        action="store_true",
        help="resolve the start ref and emit an apply-ready digest without writing",
    )
    cli.add_argument(
        "--apply", action="store_true", help="start the confirmed pipeline"
    )
    cli.add_argument(
        "--confirm-plan", metavar="SHA256", help="exact digest returned by --live-plan"
    )
    cli.add_argument(
        "--receipt-out", metavar="PATH", help="write one portable live result"
    )
    return cli


def _reject_unrelated(args: argparse.Namespace, action: str) -> None:
    """Reject flags that belong to a different pipeline operation.

    :param args: Parsed pipeline arguments.
    :param action: Selected get, start, or watch operation.
    :raises ActionError: An option cannot affect the selected operation.
    """
    start_only = {
        "ref": args.ref,
        "variable": bool(args.variable),
        "require_protected_ref": args.require_protected_ref,
        "expected_sha": args.expected_sha,
        "live_plan": args.live_plan,
        "apply": args.apply,
        "confirm_plan": args.confirm_plan,
    }
    watch_only = {"related_name": args.related_name, "interval": args.interval}
    if action != "start" and any(start_only.values()):
        raise ActionError("pipeline_start_option_requires_start")
    if action != "watch" and any(watch_only.values()):
        raise ActionError("pipeline_watch_option_requires_watch")
    if action == "start" and args.pipeline_id is not None:
        raise ActionError("pipeline_id_not_valid_for_start")
    if action in {"get", "watch"} and args.pipeline_id is None:
        raise ActionError("pipeline_id_required")
    if args.pipeline_id is not None and args.pipeline_id <= 0:
        raise ActionError("pipeline_id_must_be_positive")
    if action == "get" and args.receipt_out:
        raise ActionError("receipt_out_requires_start_or_watch")
    if action == "get" and args.timeout is not None:
        raise ActionError("timeout_requires_start_or_watch")


def _select_target(
    args: argparse.Namespace,
) -> tuple[GitLabOperationTarget, str, str]:
    """Resolve the exact GitLab project without credential binding.

    :param args: Target, binding, dry-run, and project selectors.
    :returns: Target, target source, and selected project reference.
    :raises TargetError: Selection is missing, invalid, or not project-scoped.
    """
    target, source = resolve_gitlab_target(
        args.target, args.binding, dry_run=args.dry_run
    )
    target_kind, reference = select_gitlab_reference(target, project=args.project)
    if target_kind != "project":
        raise TargetError("pipeline_requires_project_target")
    return target, source, reference


def _get(args: argparse.Namespace) -> int:
    """Preserve the existing bounded pipeline read behavior.

    :param args: Validated get arguments.
    :returns: Status-derived command exit code.
    :raises TargetError: Target or project selection fails.
    :raises RuntimeError: Credential binding or access verification fails.
    :raises ValueError: Provider evidence has an invalid shape.
    """
    started = time.monotonic()
    target, target_source, reference = _select_target(args)
    filters: JSONObject = {"project": reference, "pipeline_id": args.pipeline_id}
    if args.dry_run:
        job_limit = PAGE_SIZE * MAX_JOB_PAGES
        data = report(KIND, target.gitlab.url, filters, [], [])
        data["status"] = DRY_RUN
        data["target_source"] = target_source
        data["access"] = {
            "status": DRY_RUN,
            "target": {"kind": "project", "reference": reference},
            "observed_capability": [],
        }
        data["collection_probes"] = [
            "exact GitLab identity and project reads",
            f"pipeline and up to {job_limit} job reads with one overflow probe",
        ]
    else:
        session = bind_gitlab_session(
            target,
            target_kind="project",
            target_reference=reference,
            target_source=(
                f"{target_source};argv:--project" if args.project else target_source
            ),
            revision=args.revision,
        )
        gate = check_gitlab_operation_access(session)
        if gate["status"] != PASS:
            data = report(
                KIND,
                target.gitlab.url,
                filters,
                [],
                json_objects(gate["errors"]),
            )
            data["status"] = BLOCKED
        else:
            record, errors = read_pipeline(
                session,
                json_integer(json_object(gate["target"])["id"]),
                args.pipeline_id,
            )
            data = report(KIND, target.gitlab.url, filters, [record], errors)
        attach_pipeline_provenance(data, session, gate)
    rendered = emit(data, output_mode(args), args.output_dir)
    status = json_string(data["status"])
    log_event(args, KIND, "result", status, elapsed=time.monotonic() - started)
    sys.stdout.write(rendered)
    return exit_code(status)


def _watch(args: argparse.Namespace) -> int:
    """Run the exact bounded pipeline graph watch.

    :param args: Validated watch arguments.
    :returns: PASS for complete success, PARTIAL otherwise, or BLOCKED on failure.
    :raises ActionError: Watch timing or provider evidence is invalid.
    :raises TargetError: Target or project selection fails.
    :raises GitLabAPIError: Authentication or the root pipeline read fails.
    """
    target, target_source, reference = _select_target(args)
    return execute_pipeline_watch(
        args,
        target,
        target_source,
        reference,
        request_timeout=DEFAULT_REQUEST_TIMEOUT,
        settled=lambda line: sys.stderr.write(line + "\n"),
    )


def _pipeline_failure(args: argparse.Namespace, reason: str) -> int:
    """Preserve the selected operation's report shape for preflight failures.

    :param args: Parsed operation and output controls.
    :param reason: Sanitized failure description.
    :returns: Blocked exit status after rendering without persisting invalid inputs.
    :raises OSError: Even the final blocked report cannot be written to stdout.
    """  # noqa: DOC503 - final stdout writes can still fail.
    if args.action != "watch":
        return _failure(args, KIND, f"pipeline.{args.action}", reason)
    data = watch_failure_report(args, reason)
    try:
        sys.stdout.write(emit(data, output_mode(args)))
    except (RuntimeError, OSError):
        sys.stdout.write(json.dumps(data, sort_keys=True) + "\n")
    return exit_code(BLOCKED)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Dispatch the selected pipeline operation through its exact boundary.

    :param argv: Optional command arguments; defaults to process arguments.
    :returns: Status-derived command exit code or structured failure code.
    :raises SystemExit: If argparse handles help or rejects invalid arguments.
    """  # noqa: DOC503 - argparse exits propagate.
    args = build_parser().parse_args(argv)
    if args.describe:
        sys.stdout.write(json.dumps(describe(SCRIPT), indent=2, sort_keys=True) + "\n")
        return 0
    try:
        _reject_unrelated(args, args.action)
        if (
            args.action != "start"
            and args.dry_run
            and (args.output_dir or args.log_file or args.receipt_out)
        ):
            raise ActionError("dry_run_cannot_write_output")
        if args.action == "start":
            return start_workflow(args).execute()
        if args.action == "watch":
            return _watch(args)
        return _get(args)
    except (
        ActionError,
        GitLabAPIError,
        TargetError,
        RuntimeError,
        ValueError,
        OSError,
        TypeError,
        KeyError,
    ) as exc:
        return _pipeline_failure(args, sanitize(str(exc), 240))
    except Exception:  # noqa: BLE001 - keep unexpected failures machine-readable
        return _pipeline_failure(args, "unexpected_runtime_failure")


if __name__ == "__main__":
    raise SystemExit(main())
