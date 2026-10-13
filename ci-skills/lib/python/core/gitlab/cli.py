"""GitLab target setup and job command adapters.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import MutableMapping, Sequence
from typing import Optional

from lib.python.access.gitlab import check_gitlab_operation_access
from lib.python.access.gitlab_credentials import bind_gitlab_session
from lib.python.core.catalog import (
    COMMAND_BY_KIND,
    describe,
    missing_required_options,
)
from lib.python.core.cli import _failure, output_mode
from lib.python.core.gitlab.gitlab_api import GlabAPIClient
from lib.python.core.gitlab.gitlab_session import BoundGitLabSession
from lib.python.core.json_types import (
    JSONObject,
    JSONValue,
    copy_json,
    json_integer,
    json_object,
    json_objects,
    json_string,
)
from lib.python.core.logging import log_event
from lib.python.core.project_binding import BINDING_ENV
from lib.python.core.project_binding import resolve_target as resolve_project_target
from lib.python.core.report import emit, report
from lib.python.core.target import (
    GitLabOperationTarget,
    TargetError,
    select_gitlab_reference,
)
from lib.python.sys.status import (
    BLOCKED,
    DRY_RUN,
    PASS,
    exit_code,
)


def resolve_gitlab_target(
    explicit_target: Optional[str],
    explicit_binding: Optional[str],
    *,
    dry_run: bool = False,
) -> tuple[GitLabOperationTarget, str]:
    """Resolve GitLab settings from a target file or project binding.

    :param explicit_target: Internal target-file override, or ``None``.
    :param explicit_binding: Project-binding override, or ``None``.
    :param dry_run: Whether binding resolution may omit live credential-file checks.
    :returns: GitLab operation target and reportable selection source.
    :raises TargetError: If configuration, GitLab settings, or source identity
        cannot be resolved.
    """
    selected = resolve_project_target(
        explicit_target,
        explicit_binding,
        dry_run=dry_run,
        required_surfaces=("gitlab",),
    )
    if selected.gitlab is None or selected.source_file is None:
        raise TargetError("gitlab_target_unresolved")
    if selected.source_kind == "binding":
        selector = "argv:--binding" if explicit_binding else f"env:{BINDING_ENV}"
        source = f"{selector} -> {selected.target_reference}"
    else:
        source = selected.source_kind or "target"
    return (
        GitLabOperationTarget(selected.gitlab, selected.source_file.resolve()),
        source,
    )


def setup_gitlab_operation(
    explicit_target: Optional[str],
    explicit_binding: Optional[str],
    *,
    project: Optional[str],
    group: Optional[str],
    revision: Optional[str],
    api_client: Optional[GlabAPIClient] = None,
) -> tuple[BoundGitLabSession, JSONObject]:
    """Bind a live GitLab target and return its existing access read-back.

    :param explicit_target: Operator-selected target file, or normal resolution.
    :param explicit_binding: Operator-selected project binding, or normal resolution.
    :param project: Optional project path or numeric ID override.
    :param group: Optional group path or numeric ID override.
    :param revision: Optional claimed source revision of the installed skill.
    :param api_client: Existing API client, or the access check's default client.
    :returns: The same bound session used for access and its receipt, including
        a BLOCKED receipt when provider read-back does not prove access.
    :raises Exception: The original setup failure, carrying its target,
        credential or access stage in ``gitlab_setup_source``.
    """  # noqa: DOC503 - retain the original setup exception and type
    source = "target"
    try:
        target, target_source = resolve_gitlab_target(
            explicit_target, explicit_binding, dry_run=False
        )
        target_kind, reference = select_gitlab_reference(
            target, project=project, group=group
        )
        source = "credential"
        session = bind_gitlab_session(
            target,
            target_kind=target_kind,
            target_reference=reference,
            target_source=target_source,
            revision=revision,
        )
        source = "access"
        return session, check_gitlab_operation_access(session, api_client=api_client)
    except Exception as exc:
        exc.__dict__["gitlab_setup_source"] = source
        raise


def _job_plan(
    target: GitLabOperationTarget,
    target_source: str,
    project: str,
    filters: JSONObject,
    probes: Sequence[str],
) -> MutableMapping[str, JSONValue]:
    """Describe job reads without credentials, network access or output files.

    :param target: Resolved GitLab configuration used to name the endpoint.
    :param target_source: Reportable target-selection source.
    :param project: Exact project selected by the URL or list inputs.
    :param filters: Command-specific filters to include unchanged.
    :param probes: Reads that the selected operation would perform.
    :returns: The shared dry-run report shape for job get and list.
    """
    data = report("gitlab_job", target.gitlab.url, filters, [], [])
    data.update(
        {
            "status": DRY_RUN,
            "target_source": target_source,
            "access": {
                "status": DRY_RUN,
                "target": {"kind": "project", "reference": project},
                "observed_capability": [],
            },
            "collection_probes": ["exact GitLab identity and project reads", *probes],
        }
    )
    return data


def _emit_job(args: argparse.Namespace, data: JSONObject, started: float) -> int:
    """Render one job report and its optional diagnostic event.

    :param args: Output and diagnostic settings parsed by the job command.
    :param data: Complete report with a shared status value.
    :param started: Monotonic start time for elapsed diagnostic metadata.
    :returns: The status-derived process exit code.
    :raises OSError: An explicitly selected output or log file cannot be written.
    :raises TypeError: The report contains data the selected serializer cannot encode.
    :raises ValueError: The report contains a JSON cycle.
    """  # noqa: DOC502 - shared serialization and file writes propagate failures.
    rendered = emit(data, output_mode(args), args.output_dir)
    log_event(
        args,
        "gitlab_job",
        "result",
        json_string(data["status"]),
        elapsed=time.monotonic() - started,
    )
    sys.stdout.write(rendered)
    return exit_code(json_string(data["status"]))


def execute_gitlab_job(args: argparse.Namespace) -> int:
    """Route one job get or bounded list through the GitLab authority.

    :param args: Parsed job command and universal output options.
    :returns: Shared status exit code.
    """
    from lib.python.core.gitlab.job_read import collect_gitlab_job, gitlab_job_reference

    kind = "gitlab_job"
    if args.describe:
        sys.stdout.write(json.dumps(describe(COMMAND_BY_KIND[kind]), indent=2) + "\n")
        return 0
    if args.action == "list":
        return execute_gitlab_job_list(args)
    if args.dry_run and (args.output_dir or args.log_file):
        return _failure(args, kind, "arguments", "dry_run_cannot_write_output")
    missing = missing_required_options(COMMAND_BY_KIND[kind], args)
    if missing:
        return _failure(
            args, kind, "arguments", f"{', '.join(missing)} is required for get"
        )
    if any(
        (
            args.project,
            args.status,
            args.pipeline_id,
            args.ref,
            args.last,
            args.from_time,
            args.to_time,
            args.name_glob,
            args.limit != 50,
            args.stuck_after != 600,
        )
    ):
        return _failure(args, kind, "arguments", "list_options_require_list")
    source = "target"
    started = time.monotonic()
    try:
        target, target_source = resolve_gitlab_target(
            args.target, args.binding, dry_run=args.dry_run
        )
        source = "arguments"
        project, _job_id = gitlab_job_reference(args.job_url, target.gitlab.host)
        filters = {"job_url": args.job_url, "search": args.search}
        if args.dry_run:
            data = _job_plan(
                target,
                target_source,
                project,
                filters,
                ["job, pipeline, runner and bounded trace reads"],
            )
        else:
            source = "credential"
            session = bind_gitlab_session(
                target,
                target_kind="project",
                target_reference=project,
                target_source=f"{target_source};argv:--job-url",
                revision=args.revision,
            )
            source = "access"
            gate = check_gitlab_operation_access(session)
            if gate["status"] != PASS:
                data = report(
                    kind, target.gitlab.url, filters, [], json_objects(gate["errors"])
                )
                data["status"] = BLOCKED
            else:
                source = "collect_gitlab_job"
                data = collect_gitlab_job(
                    target, args, credential=dict(session.environment)
                )
            data["access"] = gate
            data["execution_host"] = session.execution_host
            data["tested_revision"] = session.skill["revision"]["value"]
            data["skill"] = copy_json(session.skill)
            data["target_source"] = session.target_source
        return _emit_job(args, data, started)
    except (TargetError, RuntimeError, ValueError, OSError, TypeError, KeyError) as exc:
        return _failure(args, kind, source, str(exc))
    except Exception:  # noqa: BLE001 - preserve the machine result on provider failures
        return _failure(args, kind, source, "unexpected_runtime_failure")


def execute_gitlab_job_list(args: argparse.Namespace) -> int:
    """Bind one project and render the reusable bounded job-list read.

    :param args: Parsed list filters and universal output options.
    :returns: Shared status exit code.

    Target, provider, and collection exceptions become structured failure reports.
    """  # noqa: DOC501, DOC503 - the target-kind validation is caught by this adapter.
    from lib.python.core.gitlab.gitlab_jobs import JobQuery, list_jobs
    from lib.python.core.target import select_gitlab_reference

    kind = "gitlab_job"
    if args.job_url:
        return _failure(args, kind, "arguments", "job_url_requires_get")
    if args.dry_run and (args.output_dir or args.log_file):
        return _failure(args, kind, "arguments", "dry_run_cannot_write_output")
    source = "arguments"
    started = time.monotonic()
    try:
        query = JobQuery.from_args(args)
        source = "target"
        target, target_source = resolve_gitlab_target(
            args.target, args.binding, dry_run=args.dry_run
        )
        selected_kind, project = select_gitlab_reference(target, project=args.project)
        if selected_kind != "project":
            raise TargetError("gitlab_project_required_for_job_list")
        filters = {"project": project, **query.public()}
        if args.dry_run:
            data = _job_plan(
                target,
                target_source,
                project,
                filters,
                ["bounded GitLab project jobs pages"],
            )
        else:
            source = "credential"
            session = bind_gitlab_session(
                target,
                target_kind="project",
                target_reference=project,
                target_source=(
                    f"{target_source};argv:--project" if args.project else target_source
                ),
                revision=args.revision,
            )
            source = "access"
            gate = check_gitlab_operation_access(session)
            if gate["status"] == PASS:
                source = "list_jobs"
                result = list_jobs(
                    session, json_integer(json_object(gate["target"])["id"]), query
                )
                data = report(
                    kind,
                    target.gitlab.url,
                    filters,
                    [json_object(copy_json(record)) for record in result.records],
                    result.errors,
                )
                if result.first_page_failed:
                    data["status"] = BLOCKED
                truncated = result.truncated
            else:
                data = report(
                    kind, target.gitlab.url, filters, [], json_objects(gate["errors"])
                )
                data["status"] = BLOCKED
                truncated = False
            data["access"] = {
                "status": gate["status"],
                "identity": gate["identity"],
                "target": gate["target"],
                "observed_capability": gate["observed_capability"],
            }
            data["execution_host"] = session.execution_host
            revision = session.skill["revision"]
            data["tested_revision"] = (
                revision["value"] if revision["verified"] else None
            )
            data["skill"] = copy_json(session.skill)
            data["target_source"] = session.target_source
        data["operation"] = "list"
        data["truncated"] = truncated if not args.dry_run else False
        return _emit_job(args, data, started)
    except (TargetError, RuntimeError, ValueError, OSError, TypeError, KeyError) as exc:
        return _failure(args, kind, source, str(exc))
    except Exception:  # noqa: BLE001 - preserve the machine result on provider failures
        return _failure(args, kind, source, "unexpected_runtime_failure")
